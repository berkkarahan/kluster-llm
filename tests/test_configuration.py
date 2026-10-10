"""Configuration contracts across CLI, persistence, runtime and protocol adapters."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from kllm.modules.inference.interactors.run_settings_http import RunSettingsHandlers
from kllm.modules.inference.interactors.effective_config import apply_active_defaults, effective_configuration
from kllm.modules.inference.interactors.engine_parameters import parse_server_help, validate_engine_args
from kllm.modules.inference.interactors.extra_args import ExtraArgsError, sanitize_extra_args, tokenize_extra_args
from kllm.modules.inference.interactors.run_launch import build_master_launch
from kllm.modules.inference.entities import LegacyRunBody, RunBody
from test_plan_run_params import client as client, _fake_plan, _model_file


def test_v2_rejects_unknown_fields_and_alias_conflicts():
    with pytest.raises(ValueError, match="extra_forbidden"):
        RunBody(model="toy.gguf", temperatre=0.2)
    with pytest.raises(ValueError, match="conflicting"):
        RunBody(model="toy.gguf", temperature=0.2, extra_args=["--temp", "0.8"])
    body = RunBody(model="toy.gguf", extra_args=["--temp=0.2", "-b", "1024"])
    assert body.temperature == 0.2
    assert body.batch_size == 1024
    assert body.extra_args == []
    legacy = LegacyRunBody(
        model="toy.gguf", temperature=0.2, extra_args=["--temp", "0.8"], old_field=True
    )
    assert legacy.temperature == 0.8


@pytest.mark.parametrize(
    "tokens",
    [
        ["--lora", "/tmp/my adapter.gguf"],
        ["--lora", r"C:\models\my adapter.gguf"],
        ["--frequency-penalty", "-0.5"],
        ["--chat-template-kwargs", '{"key":"a; b", "enable_thinking":false}'],
        ["--control-vector-layer-range", "0", "12"],
    ],
)
def test_api_arguments_are_literal_vectors(tokens):
    assert sanitize_extra_args(tokens) == tokens


def test_cli_argument_strings_are_parsed_only_at_the_cli_boundary():
    assert tokenize_extra_args(['--lora "/tmp/my adapter.gguf"', "--frequency-penalty -0.5"]) == [
        "--lora",
        "/tmp/my adapter.gguf",
        "--frequency-penalty",
        "-0.5",
    ]
    with pytest.raises(ExtraArgsError, match="managed"):
        sanitize_extra_args(["--api-key=secret"])


def test_engine_catalog_checks_arity_choices_and_removed_flags():
    catalog = parse_server_help("""--flash-attn [on|off|auto]       set attention
--lora FNAME                     load adapter
--control-vector-layer-range START END
                                start and end inclusive
--draft-max N                    removed; use --spec-draft-n-max
""")
    validate_engine_args(
        ["--lora", "/tmp/a b.gguf", "--control-vector-layer-range", "0", "12"], catalog
    )
    for tokens in (["--unknown"], ["--draft-max", "3"], ["--lora"], ["--flash-attn", "maybe"]):
        with pytest.raises(ExtraArgsError):
            validate_engine_args(tokens, catalog)


def test_saved_settings_merge_sparse_overrides_and_null_resets():
    class Store:
        @staticmethod
        def get_model_settings(model):
            return {
                "parameter_set": {
                    "context": 8192,
                    "temperature": 0.2,
                    "extra_args": ["--seed", "17"],
                    "remember": True,
                }
            }

    service = RunSettingsHandlers(Store(), lambda _: Path("toy.gguf"), lambda: 8)
    body = service.resolve(RunBody(model="toy.gguf", temperature=None))
    assert body.context == 8192
    assert body.temperature is None
    assert body.seed == 17
    assert body.remember is False
    assert body._sources["context"] == "saved"
    assert body._sources["temperature"] == "override"
    clean = service.resolve(RunBody(model="toy.gguf", settings_source="defaults"))
    assert clean.context == 4096
    assert clean.seed is None


def test_cli_posts_sparse_overrides_and_preserves_false(monkeypatch):
    from kllm.cli import main
    from kllm.modules.inference import router_cli as inference

    payloads = []

    @contextmanager
    def fake_client(_port):
        class Response:
            status_code = 200

            def json(self):
                return {"plan": _fake_plan()}

        class Client:
            def post(self, path, json):
                payloads.append((path, json))
                return Response()

        yield Client()

    monkeypatch.setattr(inference, "_client", fake_client)
    assert (
        main(["run", "--model", "toy.gguf", "--no-enable-thinking", "--no-vision", "--seed", "17"])
        == 0
    )
    assert payloads[-1] == (
        "/v2/inference/runs",
        {
            "model": "toy.gguf",
            "settings_source": "saved",
            "enable_thinking": False,
            "vision": False,
            "seed": 17,
        },
    )
    assert (
        main(["plan", "--model", "toy.gguf", "--settings", "defaults", "--extra-arg=--temp 0.2"])
        == 0
    )
    assert payloads[-1][1] == {
        "model": "toy.gguf",
        "settings_source": "defaults",
        "temperature": 0.2,
        "extra_args": [],
    }


def test_v2_planning_inherits_saved_values_without_mutating_preferences(client):
    c, agent, tmp = client
    _model_file(tmp)
    saved = c.put(
        "/v2/inference/model-settings/toy.gguf",
        json={"parameter_set": {"context": 8192, "temperature": 0.2}},
    )
    assert saved.status_code == 200, saved.text
    before = agent.STATE.last_settings
    before_name = agent.STATE.model_name
    before_file = (Path(agent.model_settings_store._store_path())).read_bytes()
    with patch.object(
        agent.native, "plan_split", return_value=_fake_plan(context_effective=8192)
    ) as planner:
        result = c.post("/v2/inference/plans", json={"model": "toy.gguf"})
    assert result.status_code == 200, result.text
    assert planner.call_args.kwargs["context"] == 8192
    assert result.json()["effective_config"]["generation_defaults"]["temperature"] == 0.2
    assert agent.STATE.last_settings == before
    assert agent.STATE.model_name == before_name
    assert Path(agent.model_settings_store._store_path()).read_bytes() == before_file


def test_unknown_engine_flag_fails_before_stopping_active_model(client, monkeypatch):
    c, agent, tmp = client
    _model_file(tmp)
    monkeypatch.setattr(
        "kllm.modules.inference.interactors.engine_parameters.engine_catalog",
        lambda: parse_server_help("--threads N    thread count"),
    )
    agent.STATE.process_pid = 424242
    agent.STATE.process_role = "master"
    agent.STATE.active_config = {"process_pid": 424242, "model": "active.gguf"}
    with patch.object(agent.native, "stop_process") as stop:
        result = c.post(
            "/v2/inference/runs", json={"model": "toy.gguf", "extra_args": ["--does-not-exist"]}
        )
    assert result.status_code == 422, result.text
    assert "not supported" in result.text
    stop.assert_not_called()
    assert agent.STATE.process_pid == 424242
    assert agent.STATE.active_config["model"] == "active.gguf"


@pytest.mark.parametrize(
    "protocol,output,stop",
    [
        ("chat", "max_tokens", "stop"),
        ("responses", "max_output_tokens", "stop"),
        ("messages", "max_tokens", "stop_sequences"),
    ],
)
def test_active_defaults_and_client_override_precedence(protocol, output, stop):
    snapshot = {
        "generation_defaults": {
            "temperature": 0.2,
            "max_output_tokens": 100,
            "stop": ["END"],
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_budget": 128,
        }
    }
    body = apply_active_defaults({}, snapshot, protocol)
    assert body["temperature"] == 0.2
    assert body[output] == 100
    assert body[stop] == ["END"]
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    if protocol != "messages":
        assert body["reasoning_budget_tokens"] == 128
    override = apply_active_defaults(
        {
            "temperature": 0.7,
            output: 32,
            stop: [],
            "chat_template_kwargs": {"enable_thinking": True},
        },
        snapshot,
        protocol,
    )
    assert override["temperature"] == 0.7
    assert override[output] == 32
    assert override[stop] == []
    assert override["chat_template_kwargs"]["enable_thinking"] is True


def test_messages_rejects_unsupported_request_sampling_instead_of_ignoring_it():
    with pytest.raises(ValueError, match="Messages does not support"):
        apply_active_defaults({"frequency_penalty": 0.5}, {"generation_defaults": {}}, "messages")


def test_new_launch_parameters_and_effective_snapshot():
    body = RunBody(
        model="toy.gguf",
        seed=17,
        max_output_tokens=128,
        frequency_penalty=-0.5,
        threads_batch=1,
        stop=["END"],
        draft_max=7,
    )
    plan = _fake_plan()
    config = build_master_launch(
        body,
        plan,
        model_path=Path("/tmp/toy.gguf"),
        rpc_servers=[],
        infer_port=8080,
        repo_root=None,
        gpu_env={},
        extra_args=[],
    )
    for flag, value in (
        ("--seed", "17"),
        ("--n-predict", "128"),
        ("--frequency-penalty", "-0.5"),
        ("--threads-batch", "1"),
    ):
        assert config["extra_args"][config["extra_args"].index(flag) + 1] == value
    snapshot = effective_configuration(body, plan, Path("/tmp/toy.gguf"))
    assert snapshot["generation_defaults"]["stop"] == ["END"]
    assert snapshot["values"]["draft_max"] == 7
    assert snapshot["values"]["threads_batch"] == 1
    assert snapshot["values"]["context_per_request"] == 4096


def test_run_snapshot_survives_draft_edits_and_is_cleared_on_stop(client):
    c, agent, tmp = client
    _model_file(tmp)
    with (
        patch.object(agent.native, "plan_split", return_value=_fake_plan()),
        patch.object(
            agent.native,
            "start_master",
            return_value={"pid": 42, "argv": [], "binary": "llama-server", "role": "master"},
        ),
        patch.object(agent.native, "process_alive", return_value=True),
        patch("kllm.agent.runtime.ensure_rpc_peers_reachable"),
        patch.object(agent.native, "stop_process"),
    ):
        ran = c.post("/v2/inference/runs", json={"model": "toy.gguf", "temperature": 0.2})
        assert ran.status_code == 200, ran.text
        snapshot = ran.json()["effective_config"]
        assert snapshot["process_pid"] == 42
        assert snapshot["run_id"]
        assert snapshot["generation_defaults"]["temperature"] == 0.2
        saved = c.put(
            "/v2/inference/model-settings/toy.gguf", json={"parameter_set": {"temperature": 1.5}}
        )
        assert saved.status_code == 200, saved.text
        assert agent.STATE.active_config == snapshot
        assert c.post("/v2/inference/runs/stop").status_code == 200
        assert agent.STATE.active_config is None


def test_disabled_auxiliary_paths_do_not_launch_or_affect_cache_validation():
    body = RunBody(
        model="toy.gguf",
        vision=False,
        mmproj="/missing/projector.gguf",
        speculative="off",
        draft_model="/missing/draft.gguf",
        flash_attention="off",
        cache_type_v="f16",
    )
    config = build_master_launch(
        body,
        _fake_plan(),
        model_path=Path("/tmp/toy.gguf"),
        rpc_servers=[],
        infer_port=8080,
        repo_root=None,
        gpu_env={},
        extra_args=[],
    )
    assert "mmproj" not in config
    assert "draft_model" not in config
    assert "--spec-draft-n-max" not in config["extra_args"]


def test_selected_template_capabilities_use_selected_preset():
    from kllm.modules.inference.interactors.chat_templates import selected_template_text

    assert "enable_thinking" in selected_template_text("preset:qwen3-0.6b", None, "")
    assert selected_template_text("chatml", None, "reasoning_effort") == ""
    assert selected_template_text("default", None, "embedded") == "embedded"


def test_independent_deployment_limits_respect_node_caps_and_real_budgets(client, monkeypatch):
    from kllm.modules.inference.interactors import run_planning
    from kllm.shared import config
    from test_memory_estimation import synthetic_gguf
    from test_required_node_planning import node

    c, _, tmp = client
    synthetic_gguf(_model_file(tmp))
    current = {**config.load_config(), "ram_percent": 70, "vram_percent": 60}
    monkeypatch.setattr(config, "load_config", lambda: current)
    monkeypatch.setattr(
        run_planning, "cluster_nodes", lambda _: [node("master1", master=True, gpu=4000, ram=6000)]
    )
    result = c.post(
        "/v2/inference/plans",
        json={"model": "toy.gguf", "device": "gpu", "vram_percent": 40, "ram_percent": 90},
    )
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["effective_memory_limits"]["master1"] == {"vram_percent": 40, "ram_percent": 70}
    assert plan["node_gpu_budget_bytes"]["master1"] == 1600 * 1024**2
    assert plan["node_ram_budget_bytes"]["master1"] == 4200 * 1024**2
