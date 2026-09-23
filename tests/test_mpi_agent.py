from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


class _Reservation:
    address = "127.0.0.1:20042"

    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class _Supervisor:
    last_spec = None

    def __init__(self, binary: Path, *, startup_timeout: float) -> None:
        self.binary = binary
        self.stopped = False

    def start(self, spec):
        type(self).last_spec = spec
        return {
            "kind": "kllm-mpi-rank-ready",
            "rank": spec["rank"],
            "world_size": spec["world_size"],
            "manifest_identity": spec["manifest_identity"],
        }

    def status(self):
        return {"state": "stopped" if self.stopped else "ready", "rank": 0}

    def command(self, value, *, timeout):
        assert value["command"] in ("barrier", "collectives")
        return {"kind": f"kllm-mpi-rank-{value['command']}", "rank": 0, "status": "passed"}

    def stop(self):
        self.stopped = True

    def terminate(self):
        self.stopped = True


def _configure(agent, monkeypatch):
    from kllm import mpi_runtime

    agent.STATE.role = "worker"
    agent.STATE.join_key = "cluster-key"
    agent.STATE.rpc_bind = "127.0.0.1"
    agent.STATE.gpu_ids = [0]
    agent._mpi_pending = None
    agent._mpi_rank_supervisor = None
    agent._mpi_active = None
    monkeypatch.setattr(
        agent,
        "_resolve_gpu_selection",
        lambda _: {"gpus": [{"index": 0}], "gpu_ids": [0], "backend": "vulkan", "vram_bytes": 1},
    )
    monkeypatch.setattr(
        agent,
        "_local_mpi_capabilities",
        lambda _: {
            "provider": {
                "upstream_revision": "rev",
                "local_patch_digest": "digest",
                "protocol_version": 1,
                "target_triple": "target",
                "features": ["derive"],
                "binary_sha256": "sha256:provider",
                "self_test": "passed",
            },
            "devices": [{
                "physical_id": "pci:0000:01:00.0",
                "platform_handle": "card0",
                "vendor_id": "0x10de",
            }],
            "backends": {"vulkan": {"available": False}},
        },
    )
    monkeypatch.setattr(mpi_runtime, "reserve_private_port", lambda _: _Reservation())
    monkeypatch.setattr(mpi_runtime, "rank_binary", lambda _: Path("fixed-rank"))
    monkeypatch.setattr(mpi_runtime, "MpiRankSupervisor", _Supervisor)


def _prepare_body():
    return {
        "job_id": "01" * 16,
        "rank": 0,
        "world_size": 1,
        "plan_fingerprint": "sha256:plan",
        "backend": "vulkan",
        "physical_device_id": "pci:0000:01:00.0",
        "manifest_identity": "sha256:manifest",
        "purpose": "provider_validation",
    }


def test_worker_launch_endpoints_require_key_and_reject_raw_launch_fields(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "home"))
    import kllm.agent as agent

    _configure(agent, monkeypatch)
    with TestClient(agent.app) as client:
        assert client.post("/v1/mpi/prepare", json=_prepare_body()).status_code == 401
        injected = {**_prepare_body(), "executable": "cmd.exe /c whoami"}
        response = client.post(
            "/v1/mpi/prepare",
            json=injected,
            headers={"X-Kllm-Key": "cluster-key"},
        )
        assert response.status_code == 400
        assert "cmd.exe" not in response.text


def test_prepare_commit_status_and_abort_do_not_expose_job_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "home"))
    import kllm.agent as agent

    _configure(agent, monkeypatch)
    headers = {"X-Kllm-Key": "cluster-key"}
    with TestClient(agent.app) as client:
        prepared = client.post("/v1/mpi/prepare", json=_prepare_body(), headers=headers)
        assert prepared.status_code == 200, prepared.text
        allocation = prepared.json()
        secret = "ab" * 32
        committed = client.post(
            "/v1/mpi/commit",
            headers=headers,
            json={
                "job_id": _prepare_body()["job_id"],
                "prepare_token": allocation["prepare_token"],
                "job_secret": secret,
                "peers": [{
                    "rank": 0,
                    "address": allocation["address"],
                    "nonce": allocation["nonce"],
                }],
            },
        )
        assert committed.status_code == 200, committed.text
        assert secret not in committed.text
        assert _Supervisor.last_spec["job_secret"] == secret
        status = client.get("/v1/mpi/rank/status", headers=headers)
        assert status.status_code == 200
        assert secret not in json.dumps(status.json())
        barrier = client.post(
            "/v1/mpi/rank/barrier",
            headers=headers,
            json={"job_id": _prepare_body()["job_id"], "phase": "interop", "layer": -1},
        )
        assert barrier.status_code == 200
        assert barrier.json()["status"] == "passed"
        collectives = client.post(
            "/v1/mpi/rank/collectives",
            headers=headers,
            json={"job_id": _prepare_body()["job_id"], "phase": "interop"},
        )
        assert collectives.status_code == 200
        assert collectives.json()["status"] == "passed"
        failed = client.post(
            "/v1/mpi/rank/fail",
            headers=headers,
            json={"job_id": _prepare_body()["job_id"]},
        )
        assert failed.status_code == 200
        assert failed.json()["state"] == "terminated"
        stopped = client.post(
            "/v1/mpi/abort",
            headers=headers,
            json={"job_id": _prepare_body()["job_id"]},
        )
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "stopped"
