from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.auth as auth_mod
    import kllm.agent as agent_mod
    import kllm.state as state_mod

    importlib.reload(state_mod)
    importlib.reload(auth_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    agent_mod.STATE.role = "master"
    agent_mod.STATE.node_id = "master1"
    agent_mod.STATE.members = {
        "w1": {
            "node_id": "w1",
            "role": "worker",
            "host": "127.0.0.1",
            "total_ram_bytes": 32 * 1024**3,
            "available_ram_bytes": 16 * 1024**3,
            "vram_bytes": 12 * 1024**3,
            "backend": "vulkan",
        }
    }
    agent_mod.STATE.save()
    return TestClient(agent_mod.app), agent_mod, tmp_path


def _model_file(tmp_path: Path) -> Path:
    import os

    models = Path(os.environ["KLLM_HOME"]) / "models"
    models.mkdir(parents=True, exist_ok=True)
    gguf = models / "toy.gguf"
    gguf.write_bytes(b"x" * 1024)
    return gguf


def _fake_plan(**overrides):
    base = {
        "tensor_split": [0.6, 0.4],
        "tensor_split_csv": "0.6000,0.4000",
        "budgets_bytes": [1, 1],
        "node_ids": ["w1"],
        "total_budget_bytes": 10_000_000_000,
        "model_size_bytes": 1024,
        "kv_estimate_bytes": 1000,
        "estimated_layers": 32,
        "ngl": 32,
        "split_mode": "layer",
        "device_requested": "gpu",
        "device_effective": "gpu",
        "node_devices": [],
        "fit_status": "fits",
        "estimated_bytes": 5000,
        "budget_bytes": 10_000_000_000,
        "hint": "",
        "context_effective": 4096,
        "warning": None,
        "rpc_endpoints": ["127.0.0.1:50052"],
    }
    base.update(overrides)
    return base


def _payload(**overrides):
    body = {
        "model": "toy.gguf",
        "context": 4096,
        "context_auto": False,
        "cache_type_k": "q8_0",
        "cache_type_v": "q8_0",
        "parallel_slots": "auto",
        "batch_size": "auto",
        "micro_batch": "auto",
        "speculative": "auto",
        "draft_model": None,
        "vision": False,
        "mmproj": None,
        "device": "gpu",
        "vram_percent": 80,
        "chat_template": "default",
        "chat_template_text": None,
        "mmap_mlock": "auto",
        "checkpoints": "auto",
        "cache_ram": "auto",
        "threads": 8,
        "extra_args": [],
        "remember": True,
        "advanced_open": False,
    }
    body.update(overrides)
    return body


def test_plan_response_contract_keys(client):
    c, agent, tmp = client
    _model_file(tmp)
    with patch.object(agent.native, "plan_split", return_value=_fake_plan()):
        r = c.post("/v1/plan", json=_payload())
    assert r.status_code == 200
    data = r.json()
    for key in ("fit_status", "estimated_bytes", "budget_bytes", "context_effective"):
        assert key in data


def test_preview_never_overwrites_applied_settings_or_remembered_model(client):
    c, agent, tmp = client
    _model_file(tmp)
    agent.STATE.last_settings = {"model": "serving.gguf", "context": 2048}
    agent.STATE.last_plan = {"fit_status": "fits", "model": "serving.gguf"}
    agent.STATE.model_name = "serving.gguf"
    with patch.object(agent.native, "plan_split", return_value=_fake_plan()):
        r = c.post("/v1/plan?preview=true", json=_payload(context=8192, flash_attention="off", cache_type_v="f16"))
    assert r.status_code == 200, r.text
    assert r.json()["resolved_launch_settings"]["flash_attention"] == "off"
    assert agent.STATE.last_settings["context"] == 2048
    assert agent.STATE.last_plan["model"] == "serving.gguf"
    assert agent.STATE.model_name == "serving.gguf"
    assert c.get("/v1/model-settings/toy.gguf").json()["updated_at"] is None


@pytest.mark.parametrize("endpoint", ["/v1/plan?preview=true", "/v2/inference/plans?preview=true"])
def test_required_node_api_uses_real_planner_and_preserves_preview_state(client, monkeypatch, endpoint):
    from test_memory_estimation import synthetic_gguf
    from test_required_node_planning import node
    from kllm import run_planning
    from kllm.core.placement_models import PlacementNodeInput

    c, agent, tmp = client
    synthetic_gguf(_model_file(tmp))
    nodes = [PlacementNodeInput.from_mapping(n) for n in (
        node("master1", master=True, gpu=4000), node("w1", gpu=1000),
    )]
    monkeypatch.setattr(run_planning, "cluster_nodes", lambda _: nodes)
    before = agent.STATE.last_plan
    result = c.post(endpoint, json=_payload(required_nodes=["w1", "master1"]))
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["fit_status"] != "overflow"
    assert plan["required_nodes"] == ["master1", "w1"]
    assert {a["node_id"] for a in plan["device_allocations"]} == {"master1", "w1"}
    assert agent.STATE.last_plan == before
    assert c.post(endpoint, json=_payload(required_nodes=["missing"])).status_code == 400
    assert c.post(endpoint, json=_payload(required_nodes=["w1"], node_shares={"w1": 1})).status_code in (400, 422)


def test_real_plan_components_auto_context_and_unpriced_arguments(client, monkeypatch):
    from test_memory_estimation import COMPONENTS, capacity, synthetic_gguf
    from kllm import run_planning

    c, agent, tmp = client
    path = _model_file(tmp)
    synthetic_gguf(path, "qwen35moe")
    monkeypatch.setattr(run_planning, "cluster_nodes", lambda _: [capacity("master1")])
    agent.STATE.last_settings = {"model": "serving.gguf", "context": 2048}
    result = c.post("/v1/plan?preview=true", json=_payload(
        context_auto=True, context=512, device="cpu", batch_size=16384, micro_batch=16384)).json()
    assert result["context_effective"] == 8192
    resolved = result["resolved_launch_settings"]
    assert resolved["context"] == resolved["batch_size"] == resolved["micro_batch"] == 8192
    assert result["estimate_complete"] and result["estimate_warnings"] == []
    assert result["estimated_bytes"] == sum(result[c] for c in COMPONENTS)
    assert result["estimated_bytes"] == result["cpu_alloc_bytes"] + result["vram_alloc_bytes"]
    assert agent.STATE.last_settings["context"] == 2048
    unknown = c.post("/v1/plan?preview=true", json=_payload(device="cpu", extra_args=["--embedding"])).json()
    assert unknown["estimate_complete"] is False
    assert any("--embedding" in w for w in unknown["estimate_warnings"])


@pytest.mark.parametrize("endpoint", ["/v1/plan?preview=true", "/v1/run"])
def test_flash_off_quantized_v_rejected_before_launch(client, endpoint):
    c, agent, tmp = client
    _model_file(tmp)
    with patch.object(agent.native, "start_master") as launch:
        invalid = c.post(endpoint, json=_payload(flash_attention="off", cache_type_v="q8_0"))
    assert invalid.status_code == 400
    assert "quantized V cache requires Flash Attention" in invalid.text
    launch.assert_not_called()


def test_equivalent_extra_args_use_same_resolved_estimate(client, monkeypatch):
    from test_memory_estimation import capacity, synthetic_gguf
    from kllm import run_planning

    c, _, tmp = client
    synthetic_gguf(_model_file(tmp))
    monkeypatch.setattr(run_planning, "cluster_nodes", lambda _: [capacity("master1")])
    direct = c.post("/v1/plan?preview=true", json=_payload(
        device="cpu", context=1024, batch_size=2048, micro_batch=1024, parallel_slots=2,
        cache_type_k="q4_0", cache_type_v="f16", flash_attention="off")).json()
    extra = c.post("/v1/plan?preview=true", json=_payload(device="cpu", extra_args=[
        "--ctx-size=1024", "-b", "2048", "-ub", "1024", "-np", "2",
        "-ctk", "q4_0", "-ctv", "f16", "-fa", "off"])).json()
    assert direct["estimated_bytes"] == extra["estimated_bytes"]
    for key in ("context", "parallel_slots", "batch_size", "micro_batch", "flash_attention", "kv_unified"):
        assert direct["resolved_launch_settings"][key] == extra["resolved_launch_settings"][key]


def test_template_selection_and_threads_capabilities(client, monkeypatch):
    c, agent, tmp = client
    _model_file(tmp)
    from kllm.chat_templates import PRESETS
    from kllm.run_settings import available_threads
    from kllm.run_settings import RunBody
    from kllm.run_launch import build_master_launch
    info = c.get("/v1/run-capabilities", params={"model": "toy.gguf"})
    assert info.status_code == 200, info.text
    assert info.json()["threads_max"] == available_threads()
    assert {p["id"] for p in info.json()["templates"]} == set(PRESETS)
    if agent.native.resolve_llama_server():
        assert c.post("/v1/chat-template/validate", json={"text": "{{ messages[0]['content'] }}"}).json()["valid"]
        assert c.post("/v1/chat-template/validate", json={"text": "{{ broken"}).status_code == 400
    bad = c.put("/v1/model-settings/toy.gguf", json={"parameter_set": {"chat_template": "made-up"}})
    assert bad.status_code == 422
    plan = _fake_plan()
    launch = build_master_launch(RunBody(model="toy.gguf", chat_template="preset:qwen35-4b"),
                                 plan, model_path=Path("missing.gguf"), rpc_servers=[], infer_port=8080,
                                 repo_root=None, gpu_env={}, extra_args=[])
    assert launch["chat_template_file"].endswith("Qwen3.5-4B.jinja")
    assert launch["jinja"] is True
    assert not any(a == "--chat-template" for a in launch["extra_args"])


def test_thread_limit_and_template_settings_round_trip(client, monkeypatch):
    from kllm import run_settings

    monkeypatch.setattr(run_settings, "available_threads", lambda: 4)
    assert run_settings.RunBody(model="toy.gguf").threads == 4
    with pytest.raises(ValueError, match="threads"):
        run_settings.RunBody(model="toy.gguf", threads=5)
    from kllm.run_planning import resolve_launch_settings
    monkeypatch.setattr("kllm.run_settings_resolution.available_threads", lambda: 4)
    with pytest.raises(ValueError, match="threads"):
        resolve_launch_settings(run_settings.RunBody(model="toy.gguf", extra_args=["--threads", "5"]))
    c, _, tmp = client
    _model_file(tmp)
    saved = c.put("/v1/model-settings/toy.gguf", json={"parameter_set": {
        "threads": 4, "chat_template": "preset:qwen3-0.6b", "flash_attention": "off", "cache_type_v": "f16",
        "node_shares": {"worker": 0.3, "master1": 0.7},
    }})
    assert saved.status_code == 200, saved.text
    restored = c.get("/v1/model-settings/toy.gguf").json()["parameter_set"]
    assert restored["flash_attention"] == "off"
    assert restored["chat_template"] == "preset:qwen3-0.6b"
    assert restored["node_shares"] == {"worker": 0.3, "master1": 0.7}


def test_run_settings_cannot_be_mutated_after_validation():
    from pydantic import ValidationError

    from kllm.run_settings import RunBody

    settings = RunBody(model="toy.gguf")
    with pytest.raises(ValidationError):
        settings.context = 8192


@pytest.mark.parametrize("legacy", ["row", "tensor"])
def test_dashboard_layer_overrides_legacy_config_at_plan_and_run_boundary(client, legacy):
    c, agent, tmp = client
    _model_file(tmp)
    assert c.put("/v1/config", json={"device": "gpu", "split_mode": legacy}).status_code == 200
    payload = _payload(split_mode="layer")
    with patch.object(agent.native, "plan_split", return_value=_fake_plan()) as planner, patch.object(
        agent.native, "start_master",
        return_value={"pid": 42, "argv": [], "binary": "llama-server", "role": "master"},
    ) as launch, patch.object(agent.native, "process_alive", return_value=True), patch(
        "kllm.agent.ensure_rpc_peers_reachable"
    ):
        planned = c.post("/v1/plan", json=payload)
        assert planned.status_code == 200, planned.text
        assert planned.json()["split_mode"] == "layer"
        ran = c.post("/v1/run", json=payload)
    assert ran.status_code == 200, ran.text
    assert ran.json()["plan"]["split_mode"] == "layer"
    # Membership/capacity refresh may recompute the plan in the background.
    # Every computation must retain the dashboard's explicit layer setting.
    modes = [call.kwargs["launch_settings"]["split_mode"] for call in planner.call_args_list]
    assert len(modes) >= 2 and set(modes) == {"layer"}
    assert launch.call_args.kwargs["split_mode"] == "layer"
    assert c.get("/v1/config").json()["split_mode"] == legacy

    # Explicit local-only callers still retain their supported mode.
    with patch.object(agent.native, "plan_split", return_value=_fake_plan(rpc_endpoints=[])):
        local = c.post("/v1/plan", json=_payload(
            split_mode=legacy, cache_type_k="f16", cache_type_v="f16"
        ))
    assert local.status_code == 200, local.text
    assert local.json()["split_mode"] == legacy


def test_plan_invalid_context_returns_400(client):
    c, agent, tmp = client
    _model_file(tmp)
    r = c.post("/v1/plan", json=_payload(context=256))
    assert r.status_code == 400
    assert "context" in r.text.lower() or "512" in r.text


def test_run_overflow_blocked(client):
    c, agent, tmp = client
    _model_file(tmp)
    with patch.object(
        agent.native,
        "plan_split",
        return_value=_fake_plan(fit_status="overflow", hint="too big"),
    ):
        r = c.post("/v1/run", json=_payload())
    assert r.status_code == 400
    assert "overflow" in r.text.lower() or "too big" in r.text.lower()
    assert agent.STATE.process_pid is None


def test_run_tight_includes_warning(client):
    c, agent, tmp = client
    _model_file(tmp)
    with patch.object(
        agent.native,
        "plan_split",
        return_value=_fake_plan(fit_status="tight", hint="tight fit"),
    ), patch.object(
        agent.native,
        "start_master",
        return_value={"pid": 42, "argv": [], "binary": "llama-server", "role": "master"},
    ), patch.object(
        agent.native,
        "process_alive",
        return_value=True,
    ), patch(
        "kllm.agent.ensure_rpc_peers_reachable",
    ):
        r = c.post("/v1/run", json=_payload())
    assert r.status_code == 200
    data = r.json()
    assert data.get("warning") or (data.get("plan") or {}).get("fit_status") == "tight"


def test_run_rpc_preflight_blocks_unreachable(client):
    c, agent, tmp = client
    _model_file(tmp)
    agent.STATE.members["w1"]["rpc_port"] = 1
    with patch.object(
        agent.native,
        "plan_split",
        return_value=_fake_plan(),
    ), patch.object(agent.native, "start_master") as sm:
        r = c.post("/v1/run", json=_payload())
    assert r.status_code == 503
    assert "unreachable" in r.text.lower()
    sm.assert_not_called()
    assert agent.STATE.process_pid is None


def test_remember_round_trip(client):
    c, agent, tmp = client
    _model_file(tmp)
    with patch.object(agent.native, "plan_split", return_value=_fake_plan()):
        r = c.post("/v1/plan", json=_payload(remember=True))
    assert r.status_code == 200
    r2 = c.get("/v1/model-settings/toy.gguf")
    assert r2.status_code == 200
    assert r2.json()["parameter_set"]["context"] == 4096

    with patch.object(agent.native, "plan_split", return_value=_fake_plan()):
        r3 = c.post("/v1/plan", json=_payload(remember=False, context=8192))
    assert r3.status_code == 200
    r4 = c.get("/v1/model-settings/toy.gguf")
    assert r4.json()["parameter_set"]["context"] == 4096


def test_remember_inference_overrides(client):
    c, agent, tmp = client
    _model_file(tmp)
    with patch.object(agent.native, "plan_split", return_value=_fake_plan()):
        r = c.post(
            "/v1/plan",
            json=_payload(
                remember=True,
                temperature=0.35,
                top_k=12,
                enable_thinking=False,
            ),
        )
    assert r.status_code == 200
    ps = c.get("/v1/model-settings/toy.gguf").json()["parameter_set"]
    assert ps["temperature"] == 0.35
    assert ps["top_k"] == 12
    assert ps["enable_thinking"] is False
    assert ps.get("top_p") is None


def test_cli_payload_mapping():
    from kllm.cli_payload import build_plan_run_payload

    payload = build_plan_run_payload(
        model="m.gguf",
        context=2048,
        parallel="auto",
        micro_batch="2",
        remember=False,
        extra_args=["--rope-scaling", "yarn"],
    )
    assert payload["model"] == "m.gguf"
    assert payload["parallel_slots"] == "auto"
    assert payload["micro_batch"] == "2"
    assert payload["remember"] is False
    assert payload["extra_args"] == ["--rope-scaling", "yarn"]


def test_cli_remember_flags_in_payload():
    from kllm.cli_payload import build_plan_run_payload

    on = build_plan_run_payload(model="m.gguf", remember=True)
    off = build_plan_run_payload(model="m.gguf", remember=False)
    assert on["remember"] is True
    assert off["remember"] is False


def test_cli_split_mode_in_payload():
    from kllm.cli_payload import build_plan_run_payload

    assert build_plan_run_payload(model="m.gguf", split_mode="tensor")["split_mode"] == "tensor"


def test_inference_stop_unloads_model_and_keeps_cluster(client, monkeypatch):
    from kllm import load_progress

    c, agent, _tmp = client
    stopped: list[tuple[int, bool]] = []

    def _stop(pid: int, force: bool = False) -> bool:
        stopped.append((pid, force))
        return True

    monkeypatch.setattr(agent.native, "stop_process", _stop)
    previous = (load_progress._active, load_progress._latest_terminal)
    agent.STATE.process_pid = 4242
    agent.STATE.process_role = "master"
    agent.STATE.model_name = "toy.gguf"
    agent.STATE.join_key = "secret"
    agent.STATE.last_plan = {"fit_status": "fits"}
    agent.STATE.members["master"] = {"node_id": "master", "role": "master", "host": "127.0.0.1"}
    try:
        load_progress._active = load_progress._latest_terminal = None
        load_progress.begin("toy.gguf", 4242, 1024)
        first = c.post("/v1/inference/stop")
        assert first.status_code == 200
        assert first.json()["role"] == "master"
        assert agent.STATE.role == "master"
        assert agent.STATE.process_pid is None
        assert agent.STATE.process_role is None
        assert agent.STATE.model_name is None
        assert agent.STATE.join_key == "secret"
        assert agent.STATE.last_plan == {"fit_status": "fits"}
        assert set(agent.STATE.members) == {"w1", "master"}
        assert stopped == [(4242, True)]
        progress = load_progress.records(agent.STATE.infer_port)
        assert progress["current"] is None
        assert progress["last_terminal"]["phase"] == "cancelled"

        again = c.post("/v1/inference/stop")
        assert again.status_code == 200
        assert agent.STATE.role == "master"
        assert agent.STATE.model_name is None
        assert stopped == [(4242, True)]

        agent.STATE.role = "worker"
        agent.STATE.model_name = "toy.gguf"
        denied = c.post("/v1/inference/stop")
        assert denied.status_code == 400
        assert agent.STATE.model_name == "toy.gguf"
    finally:
        load_progress._active, load_progress._latest_terminal = previous


def test_model_settings_file_location(client, tmp_path):
    c, agent, tmp = client
    _model_file(tmp)
    with patch.object(agent.native, "plan_split", return_value=_fake_plan()):
        c.post("/v1/plan", json=_payload(remember=True))
    store = Path(tmp_path / "kllm-home" / "model-settings.json")
    assert store.is_file()
    data = json.loads(store.read_text())
    assert "toy.gguf" in data.get("models", {})


@pytest.mark.parametrize("route", ["/v1/plan", "/v2/inference/plans"])
def test_priority_api_forwards_and_remembers_order(client, route):
    c, agent, tmp = client
    _model_file(tmp)
    with patch.object(agent.native, "plan_split", return_value=_fake_plan()) as planner:
        response = c.post(route, json=_payload(node_priority=["w1"], required_nodes=["w1"]))
    assert response.status_code == 200
    assert planner.call_args.kwargs["node_priority"] == ["w1"]
    assert planner.call_args.kwargs["required_nodes"] == ["w1"]
    assert c.get("/v1/model-settings/toy.gguf").json()["parameter_set"]["node_priority"] == ["w1"]
