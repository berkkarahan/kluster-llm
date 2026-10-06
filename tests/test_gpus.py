from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer
from fastapi.testclient import TestClient

from kllm.agent.services.gpus import ResolvedGpuSelection
from kllm.core.gpu_selection import GpuDeviceInput, GpuSelection


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.agent as agent_mod
    import kllm.state as state_mod

    importlib.reload(state_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    test_client = TestClient(agent_mod.app)
    yield test_client, agent_mod
    test_client.close()


FAKE_GPUS = {
    "backend": "cuda",
    "gpus": [
        {"index": 0, "name": "RTX 4090", "vram_bytes": 24 * 1024**3},
        {"index": 1, "name": "RTX 3090", "vram_bytes": 24 * 1024**3},
    ],
}


def test_parse_gpu_ids():
    from kllm.cli import parse_gpu_ids

    assert parse_gpu_ids(None) is None
    assert parse_gpu_ids("0") == [0]
    assert parse_gpu_ids("0,1") == [0, 1]
    assert parse_gpu_ids(" 1, 0, 1 ") == [1, 0]
    with pytest.raises(typer.BadParameter):
        parse_gpu_ids("")
    with pytest.raises(typer.BadParameter):
        parse_gpu_ids("a")
    with pytest.raises(typer.BadParameter):
        parse_gpu_ids("-1")


def test_get_gpus(client, monkeypatch):
    c, agent = client
    monkeypatch.setattr(agent.native, "list_gpus", lambda: FAKE_GPUS)
    r = c.get("/v1/gpus")
    assert r.status_code == 200
    data = r.json()
    assert data["backend"] == "cuda"
    assert data["probed_backend"] == "cuda"
    from kllm.accel import selectable_accel_backends

    assert data["allowed_backends"] == selectable_accel_backends()
    assert len(data["gpus"]) == 2


def test_put_config_accel_backend(client, monkeypatch):
    c, agent = client

    def fake_list():
        return {
            "backend": "metal",
            "probed_backend": "metal",
            "gpus": [{"index": 0, "name": "Apple M5", "vram_bytes": 40 * 1024**3}],
        }

    monkeypatch.setattr(agent.native, "list_gpus", fake_list)
    r = c.put("/v1/config", json={"device": "gpu", "accel_backend": "vulkan"})
    assert r.status_code == 200
    assert r.json()["accel_backend"] == "vulkan"

    r = c.get("/v1/status")
    assert r.status_code == 200
    assert r.json()["accel_backend"] == "vulkan"

    r = c.put("/v1/config", json={"device": "gpu", "accel_backend": "cuda"})
    assert r.status_code == 200
    assert r.json()["accel_backend"] == "cuda"

    r = c.put("/v1/config", json={"device": "gpu", "accel_backend": None})
    assert r.status_code == 200
    assert r.json()["accel_backend"] is None


def test_start_defaults_all_gpus(client, monkeypatch):
    c, agent = client
    with (
        patch.object(agent.native, "list_gpus", return_value=FAKE_GPUS),
        patch.object(
            agent.native,
            "probe_capacity",
            return_value={
                "total_ram_bytes": 32 * 1024**3,
                "available_ram_bytes": 16 * 1024**3,
                "vram_bytes": 48 * 1024**3,
                "backend": "cuda",
            },
        ),
        patch.object(agent.netsec, "pick_rpc_bind_host", return_value="10.0.0.1"),
        patch.object(
            agent.netsec,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": False, "warning": None},
        ),
        patch.object(agent.discovery, "MasterAdvertiser") as Adv,
    ):
        Adv.return_value.start = lambda: None
        Adv.return_value.stop = lambda: None
        r = c.post("/v1/start", json={"overlay": "none"})
    assert r.status_code == 200
    assert r.json()["gpu_ids"] == [0, 1]
    assert agent.STATE.gpu_ids == [0, 1]
    mem = agent.STATE.members[agent.STATE.node_id]
    assert mem["gpu_ids"] == [0, 1]
    assert mem["vram_bytes"] == 48 * 1024**3


def test_start_uses_config_gpu_ids_when_body_omits(client, monkeypatch):
    c, agent = client
    import importlib

    import kllm.config as config_mod

    importlib.reload(config_mod)
    config_mod.save_config({"device": "gpu", "gpu_ids": [1]})

    with (
        patch.object(agent.native, "list_gpus", return_value=FAKE_GPUS),
        patch.object(
            agent.native,
            "probe_capacity",
            return_value={
                "total_ram_bytes": 32 * 1024**3,
                "available_ram_bytes": 16 * 1024**3,
                "vram_bytes": 48 * 1024**3,
                "backend": "cuda",
            },
        ),
        patch.object(agent.netsec, "pick_rpc_bind_host", return_value="10.0.0.1"),
        patch.object(
            agent.netsec,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": False, "warning": None},
        ),
        patch.object(agent.discovery, "MasterAdvertiser") as Adv,
    ):
        Adv.return_value.start = lambda: None
        Adv.return_value.stop = lambda: None
        r = c.post("/v1/start", json={"overlay": "none"})
    assert r.status_code == 200
    assert r.json()["gpu_ids"] == [1]


def test_start_selects_subset(client, monkeypatch):
    c, agent = client
    with (
        patch.object(agent.native, "list_gpus", return_value=FAKE_GPUS),
        patch.object(
            agent.native,
            "probe_capacity",
            return_value={
                "total_ram_bytes": 32 * 1024**3,
                "available_ram_bytes": 16 * 1024**3,
                "vram_bytes": 48 * 1024**3,
                "backend": "cuda",
            },
        ),
        patch.object(agent.netsec, "pick_rpc_bind_host", return_value="10.0.0.1"),
        patch.object(
            agent.netsec,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": False, "warning": None},
        ),
        patch.object(agent.discovery, "MasterAdvertiser") as Adv,
    ):
        Adv.return_value.start = lambda: None
        Adv.return_value.stop = lambda: None
        r = c.post("/v1/start", json={"overlay": "none", "gpu_ids": [1]})
    assert r.status_code == 200
    assert r.json()["gpu_ids"] == [1]
    mem = agent.STATE.members[agent.STATE.node_id]
    assert mem["vram_bytes"] == 24 * 1024**3


def test_start_rejects_unknown_gpu(client, monkeypatch):
    c, agent = client
    with patch.object(agent.native, "list_gpus", return_value=FAKE_GPUS):
        r = c.post("/v1/start", json={"overlay": "none", "gpu_ids": [9]})
    assert r.status_code == 400
    assert "unknown gpu_ids" in r.json()["detail"]


EMPTY_GPUS = {
    "backend": "cpu",
    "gpus": [],
    "error": "llama-server is not installed",
}


def test_start_ignores_config_gpu_ids_when_inventory_empty(client, monkeypatch):
    c, agent = client
    import importlib

    import kllm.config as config_mod

    importlib.reload(config_mod)
    config_mod.save_config({"device": "gpu", "gpu_ids": [0]})

    with (
        patch.object(agent.native, "list_gpus", return_value=EMPTY_GPUS),
        patch.object(
            agent.native,
            "probe_capacity",
            return_value={
                "total_ram_bytes": 32 * 1024**3,
                "available_ram_bytes": 16 * 1024**3,
                "vram_bytes": None,
                "backend": "metal",
            },
        ),
        patch.object(agent.netsec, "pick_rpc_bind_host", return_value="10.0.0.1"),
        patch.object(
            agent.netsec,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": False, "warning": None},
        ),
        patch.object(agent.discovery, "MasterAdvertiser") as Adv,
    ):
        Adv.return_value.start = lambda: None
        Adv.return_value.stop = lambda: None
        r = c.post("/v1/start", json={"overlay": "none"})
    assert r.status_code == 200
    assert r.json()["gpu_ids"] == []
    assert agent.STATE.gpu_ids == []


def test_install_recovery_command_matches_platform(monkeypatch):
    import kllm.native as native_mod

    monkeypatch.setattr(native_mod.sys, "platform", "win32")
    windows = native_mod.install_recovery_command()
    assert windows == "python scripts/install.py && python scripts/install.py --check"
    assert "metal" not in windows

    monkeypatch.setattr(native_mod.sys, "platform", "darwin")
    macos = native_mod.install_recovery_command()
    assert macos.startswith("python scripts/install.py --backend metal && ")


def test_list_gpus_reports_missing_native(monkeypatch):
    import kllm.native as native_mod

    monkeypatch.setattr(native_mod, "_NATIVE", None)
    monkeypatch.setattr(native_mod, "_IMPORT_ERROR", ImportError("cannot import name '_native'"))
    data = native_mod.list_gpus()
    assert data["gpus"] == []
    assert data["backend"] == "cpu"
    assert "kllm._native extension is not built" in data["error"]
    assert "cannot import name '_native'" in data["error"]


def test_start_rejects_explicit_gpu_ids_when_inventory_empty(client, monkeypatch):
    c, agent = client
    with patch.object(agent.native, "list_gpus", return_value=EMPTY_GPUS):
        r = c.post("/v1/start", json={"overlay": "none", "gpu_ids": [0]})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "no GPUs available" in detail
    assert "llama-server is not installed" in detail
    assert "install.py" in detail


def test_list_gpus_empty_metal_inventory_sets_error(monkeypatch, tmp_path):
    import kllm.native as native_mod

    binary = tmp_path / "llama-server"
    binary.write_text("stub", encoding="utf-8")

    class FakeDev:
        index = 0
        name = "Apple M1 Pro"
        vram_bytes = 16 * 1024**3

    class FakeNative:
        @staticmethod
        def list_gpus():
            return ([FakeDev()], "metal")

    monkeypatch.setattr(native_mod, "_NATIVE", FakeNative())
    monkeypatch.setattr(native_mod, "resolve_llama_server", lambda rr=None: str(binary))
    monkeypatch.setattr(
        native_mod,
        "binary_capabilities",
        lambda *a, **k: {
            "devices": [],
            "binary_version": "test",
            "metal_init_failed": True,
        },
    )
    monkeypatch.setattr("kllm.state.repo_root", lambda: str(tmp_path))
    monkeypatch.setenv("KLLM_ACCEL_BACKEND", "metal")

    data = native_mod.list_gpus()
    assert data["gpus"] == []
    assert "error" in data
    assert "Metal" in data["error"]


def test_list_gpus_backend_mismatch_fallback(monkeypatch, tmp_path):
    import kllm.native as native_mod

    binary = tmp_path / "llama-server"
    binary.write_text("stub", encoding="utf-8")

    class FakeDev:
        index = 0
        name = "Apple GPU"
        vram_bytes = 16 * 1024**3

    class FakeNative:
        @staticmethod
        def list_gpus():
            return ([FakeDev()], "metal")

    monkeypatch.setattr(native_mod, "_NATIVE", FakeNative())
    monkeypatch.setattr(native_mod, "resolve_llama_server", lambda rr=None: str(binary))
    monkeypatch.setattr(
        native_mod,
        "binary_capabilities",
        lambda *a, **k: {
            "devices": [
                {
                    "index": 0,
                    "device": "MTL0",
                    "name": "Apple GPU",
                    "backend": "metal",
                    "vram_bytes": 16 * 1024**3,
                    "free_bytes": 8 * 1024**3,
                }
            ],
            "binary_version": "test",
            "metal_init_failed": False,
        },
    )
    monkeypatch.setattr("kllm.state.repo_root", lambda: str(tmp_path))
    monkeypatch.setenv("KLLM_ACCEL_BACKEND", "vulkan")

    data = native_mod.list_gpus()
    assert len(data["gpus"]) == 1
    assert data["gpus"][0]["backend"] == "metal"
    assert data["actual_backend"] == "metal"
    assert data["requested_backend"] == "vulkan"
    assert data["backend_warning"]
    assert "using metal" in data["backend_warning"]


def test_gpu_visibility_env():
    from kllm.agent import _gpu_visibility_env

    assert _gpu_visibility_env("cuda", [0, 1]) == {"CUDA_VISIBLE_DEVICES": "0,1"}
    assert _gpu_visibility_env("rocm", [0]) == {
        "HIP_VISIBLE_DEVICES": "0",
        "ROCR_VISIBLE_DEVICES": "0",
    }
    assert _gpu_visibility_env("metal", [0]) == {}
    assert _gpu_visibility_env("cuda", []) == {}


@pytest.mark.unit
def test_join_uses_explicit_gpu_and_cpu_devices_without_renumbering(client, monkeypatch):
    c, agent = client
    monkeypatch.setattr(agent, "_ensure_master_control_route", lambda *_args: [])
    monkeypatch.setattr(agent, "_worker_post_heartbeat", lambda: None)
    from kllm.discovery import DiscoveredMaster, join_key_id

    kid = join_key_id("secret")
    captured: dict = {}

    def fake_browse(timeout: float = 2.0, key: str | None = None, port: int = 18765):
        return [DiscoveredMaster(name="kllm-m", host="192.168.10.1", port=18765, key_id=kid)]

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "members": 2}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return type("HealthResponse", (), {"status_code": 200, "raise_for_status": lambda self: None, "json": lambda self: {"control_protocol": 2}})()

        def post(self, url, *a, **k):
            captured["payload"] = k.get("json")
            return FakeResp()

    def fake_start_worker(**kwargs):
        captured["worker_kwargs"] = kwargs
        return {"pid": 42, "binary": "rpc", "argv": [], "role": "worker"}

    monkeypatch.setattr(agent.discovery, "browse_masters", fake_browse)
    with (
        patch.object(agent.netsec, "pick_rpc_bind_host", return_value="192.168.10.2"),
        patch.object(
            agent.netsec,
            "ensure_rpc_firewall",
            return_value={"ok": True, "applied": False, "warning": None},
        ),
        patch.object(agent.native, "list_gpus", return_value={**FAKE_GPUS, "gpus": [
            {**gpu, "device": f"CUDA{gpu['index']}"} for gpu in FAKE_GPUS["gpus"]
        ]}),
        patch.object(
            agent.native,
            "probe_capacity",
            return_value={
                "total_ram_bytes": 8,
                "available_ram_bytes": 4,
                "vram_bytes": 1,
                "backend": "cuda",
            },
        ),
        patch.object(agent.native, "start_worker", side_effect=fake_start_worker),
        patch("httpx.Client", FakeClient),
    ):
        tc = TestClient(agent.app)
        r = tc.post("/v1/join", json={"key": "secret", "overlay": "none", "gpu_ids": [0]})
    assert r.status_code == 200, r.text
    assert r.json()["gpu_ids"] == [0]
    assert captured["payload"]["gpu_ids"] == [0]
    assert captured["payload"]["vram_bytes"] == 24 * 1024**3
    assert "worker_kwargs" not in captured
    assert agent.STATE.process_pid is None
    assert agent.STATE.members[agent.STATE.node_id]["rpc_devices"] == []
    assert agent.STATE.members[agent.STATE.node_id]["rpc_capabilities"] == [
        {"index": 0, "device": "CUDA0", "backend": "cuda", "memory_kind": "gpu"},
        {"index": 1, "device": "CPU", "backend": "cpu", "memory_kind": "ram"},
    ]


LLAMA_VRAM = 17_095_983_104
REGISTRY_VRAM = LLAMA_VRAM + 536_870_912


def _snap():
    return SimpleNamespace(
        backend="vulkan", total_ram_bytes=32 * 1024**3, available_ram_bytes=8 * 1024**3,
        vram_bytes=REGISTRY_VRAM, hostname="box",
    )


def test_probe_capacity_vram_is_the_llama_device_total(monkeypatch):
    import kllm.native as native_mod

    monkeypatch.setattr(native_mod, "require_native", lambda: SimpleNamespace(probe_capacity=_snap))
    monkeypatch.setattr(native_mod, "list_gpus", lambda: {
        "backend": "vulkan", "gpus": [
            {"index": 0, "name": "AMD Radeon RX 9070 XT", "vram_bytes": LLAMA_VRAM},
        ],
    })
    assert native_mod.probe_capacity()["vram_bytes"] == LLAMA_VRAM


def test_probe_capacity_reuses_one_native_gpu_observation(monkeypatch):
    import kllm.native as native_mod

    calls = []

    class Native:
        def _probe_capacity_and_gpus(self):
            calls.append("bundle")
            return _snap(), [SimpleNamespace(index=0)], "vulkan"

        def list_gpus(self):
            raise AssertionError("bundle must avoid a second native GPU probe")

    monkeypatch.setattr(native_mod, "require_native", lambda: Native())
    monkeypatch.setattr(native_mod, "_list_gpus_from_probe", lambda devices, backend: {
        "backend": backend, "gpus": [{"vram_bytes": LLAMA_VRAM}],
    })
    result = native_mod.probe_capacity()
    assert calls == ["bundle"]
    assert result["vram_bytes"] == LLAMA_VRAM


def test_probe_capacity_without_a_gpu_is_unknown(monkeypatch):
    import kllm.native as native_mod

    monkeypatch.setattr(native_mod, "require_native", lambda: SimpleNamespace(probe_capacity=_snap))
    monkeypatch.setattr(native_mod, "list_gpus", lambda: {"backend": "cpu", "gpus": []})
    assert native_mod.probe_capacity()["vram_bytes"] is None


def test_probe_capacity_keeps_the_registry_sum_when_inventory_fails(monkeypatch):
    import kllm.native as native_mod

    monkeypatch.setattr(native_mod, "require_native", lambda: SimpleNamespace(probe_capacity=_snap))
    monkeypatch.setattr(native_mod, "list_gpus", lambda: {
        "backend": "cpu", "gpus": [], "error": "llama-server is not installed",
    })
    assert native_mod.probe_capacity()["vram_bytes"] == REGISTRY_VRAM


def test_refresh_local_capacity_updates_member_vram(client):
    c, agent = client
    agent.STATE.role = "master"
    agent.STATE.members[agent.STATE.node_id] = {
        "role": "master", "node_id": agent.STATE.node_id, "vram_bytes": None, "devices": [],
    }
    with (
        patch.object(agent.native, "probe_capacity", return_value={
            "total_ram_bytes": 32 * 1024**3, "available_ram_bytes": 8 * 1024**3,
            "requested_backend": None, "unified_memory": False, "binary_version": "v",
        }),
        patch.object(
            agent,
            "_resolve_gpu_selection",
            return_value=ResolvedGpuSelection(
                GpuSelection((0,), "vulkan", LLAMA_VRAM),
                (
                    GpuDeviceInput(
                        index=0, name="AMD Radeon RX 9070 XT", vram_bytes=LLAMA_VRAM
                    ),
                ),
            ),
        ) as resolve,
    ):
        agent._refresh_local_capacity()
    assert resolve.call_args.kwargs["listed"]["binary_version"] == "v"
    member = agent.STATE.members[agent.STATE.node_id]
    assert member["vram_bytes"] == LLAMA_VRAM
    assert member["devices"][0]["vram_bytes"] == LLAMA_VRAM
