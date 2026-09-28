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
    assert [call.kwargs["launch_settings"]["split_mode"] for call in planner.call_args_list] == ["layer", "layer"]
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


def test_model_settings_file_location(client, tmp_path):
    c, agent, tmp = client
    _model_file(tmp)
    with patch.object(agent.native, "plan_split", return_value=_fake_plan()):
        c.post("/v1/plan", json=_payload(remember=True))
    store = Path(tmp_path / "kllm-home" / "model-settings.json")
    assert store.is_file()
    data = json.loads(store.read_text())
    assert "toy.gguf" in data.get("models", {})
