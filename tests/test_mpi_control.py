from __future__ import annotations

import json

from kllm import mpi_control


class _Response:
    def __init__(self, value, status_code=200, text=""):
        self._value = value
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._value


def test_provider_orchestrator_uses_structured_control_and_redacts_secrets(monkeypatch):
    calls = []
    allocations = {}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json, headers):
            calls.append((url, dict(json)))
            host = url.split("/")[2]
            if url.endswith("/prepare"):
                rank = json["rank"]
                value = {
                    "address": f"127.0.0.{rank + 1}:{20000 + rank}",
                    "nonce": f"{rank + 1:064x}",
                    "prepare_token": f"{rank + 10:064x}",
                    "provider": {
                        "upstream_revision": "rev",
                        "local_patch_digest": "digest",
                        "protocol_version": 1,
                        "features": ["derive"],
                        "binary_sha256": f"sha256:{host}",
                        "target_triple": host,
                    },
                }
                allocations[host] = value
                return _Response(value)
            if url.endswith("/commit"):
                return _Response({"ready": {"warmup": "passed"}})
            if url.endswith("/collectives"):
                return _Response({
                    "allreduce": [3.0, 2.0],
                    "allgatherv_counts": [1, 2],
                    "broadcast_sha256": "sha256:b",
                    "allgatherv_sha256": "sha256:g",
                    "status": "passed",
                })
            return _Response({"state": "stopped"})

    monkeypatch.setattr(mpi_control.httpx, "Client", Client)
    targets = [
        mpi_control.ProviderValidationTarget("a", "http://127.0.0.1:18765", "vulkan", "gpu-a"),
        mpi_control.ProviderValidationTarget("b", "http://127.0.0.2:18765", "vulkan", "gpu-b"),
    ]
    result = mpi_control.validate_provider_world(
        targets, join_key="key", plan_fingerprint="sha256:plan"
    )
    assert result["status"] == "passed"
    rendered = json.dumps(result)
    commit_secret = next(body["job_secret"] for url, body in calls if url.endswith("/commit"))
    assert commit_secret not in rendered
    assert "prepare_token" not in rendered
    assert all("executable" not in body and "environment" not in body for _, body in calls)


def test_provider_target_rejects_public_control_host():
    target = mpi_control.ProviderValidationTarget("x", "http://8.8.8.8:18765", "vulkan", "gpu")
    try:
        target.validate()
    except mpi_control.MpiControlError as exc:
        assert "not private" in str(exc)
    else:
        raise AssertionError("public control host was accepted")


def test_empty_cleanup_target_set_is_a_noop():
    assert mpi_control._parallel([], lambda _target: {}) == {}


def test_provider_recovery_forces_loss_then_starts_a_second_clean_world(monkeypatch):
    calls = []
    loss_started = False

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json, headers):
            nonlocal loss_started
            calls.append((url, dict(json)))
            if url.endswith("/prepare"):
                rank = json["rank"]
                return _Response({
                    "address": f"127.0.0.{rank + 1}:{20000 + rank}",
                    "nonce": f"{rank + 1:064x}",
                    "prepare_token": f"{rank + 10:064x}",
                    "provider": {
                        "upstream_revision": "rev",
                        "local_patch_digest": "digest",
                        "protocol_version": 1,
                        "features": ["derive"],
                        "binary_sha256": f"sha256:{rank}",
                        "target_triple": f"target-{rank}",
                    },
                })
            if url.endswith("/commit"):
                return _Response({"ready": {"warmup": "passed"}})
            if url.endswith("/collectives"):
                return _Response({
                    "allreduce": [3.0, 2.0],
                    "allgatherv_counts": [1, 2],
                    "broadcast_sha256": "sha256:b",
                    "allgatherv_sha256": "sha256:g",
                    "status": "passed",
                })
            if url.endswith("/rank/fail"):
                loss_started = True
                return _Response({"state": "terminated"})
            if url.endswith("/rank/barrier") and loss_started:
                return _Response({}, status_code=503, text="communicator failed")
            return _Response({"state": "stopped"})

    monkeypatch.setattr(mpi_control.httpx, "Client", Client)
    targets = [
        mpi_control.ProviderValidationTarget("a", "http://127.0.0.1:18765", "vulkan", "gpu-a"),
        mpi_control.ProviderValidationTarget("b", "http://127.0.0.2:18765", "vulkan", "gpu-b"),
    ]

    result = mpi_control.validate_provider_recovery(
        targets,
        join_key="key",
        plan_fingerprint="sha256:plan",
        forced_loss_rank=1,
    )

    assert result["status"] == "passed"
    assert result["forced_loss_run"]["forced_rank_loss"]["status"] == "propagated"
    assert result["second_clean_run"]["forced_rank_loss"] is None
    assert sum(url.endswith("/prepare") for url, _ in calls) == 4
