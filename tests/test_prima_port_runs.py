"""Durable node admission leases and real owned-child cleanup, without OS rules."""

import json
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import psutil
import pytest

from kllm.modules.primacpp.interactors.lifecycle import RankSupervisor
from kllm.modules.primacpp.interactors.port_runs import ADMISSION_LEASE_SECONDS, PortSupervisor
import test_prima_ports as port_contract_tests

prepared = port_contract_tests.prepared

# Features a current managed Prima runtime advertises (see test_prima_lifecycle.py).
BASELINE_FEATURES = [
    "execution_manifest_v1",
    "logical_context_sequences_v1",
    "embedding_role_v1",
    "decode_metadata_v2",
    "compact_swa_v1",
    "explicit_devices_v1",
]

OWNER = {"sender": "master", "authority_digest": "e" * 64}


def supervisor(prepared):
    manifest, _, path = prepared
    clock = [100.0]
    events = []
    rank = SimpleNamespace(owner=None)
    platform = SimpleNamespace(
        prima_ports_available=lambda: True,
        prima_ports_status=lambda *_: {"phase": "admitted", "manifest_digest": manifest.digest},
        release_prima_ports=lambda *_: events.append("release") or {"phase": "released"},
    )
    ports = PortSupervisor(
        path.parent / "ports.json",
        platform=lambda: platform,
        rank=lambda: rank,
        clock=lambda: clock[0],
    )

    def apply():
        record = json.loads(ports.path.read_text())
        assert record["phase"] == "applying" and record["manifest_digest"] == manifest.digest
        events.append("apply")
        return {"phase": "admitted", "manifest_digest": manifest.digest}

    ports.admit(manifest, OWNER, apply, lambda: None)
    return ports, platform, rank, clock, events


def test_initial_admission_is_bounded_and_running_renewal_checks_exact_receipt(prepared):
    manifest = prepared[0]
    ports, platform, _, clock, events = supervisor(prepared)
    assert ports.deadline == 100 + ADMISSION_LEASE_SECONDS
    ports.renew(manifest, OWNER)
    assert ports.deadline == 160
    platform.prima_ports_status = lambda *_: {"phase": "admitted", "manifest_digest": "f" * 64}
    clock[0] = 159
    with pytest.raises(ValueError, match="ownership changed"):
        ports.renew(manifest, OWNER)
    assert ports.deadline == 160
    assert events == ["apply"]


def test_lost_ack_is_recorded_and_restart_cannot_resume_admission(prepared):
    manifest, _, path = prepared
    rank = SimpleNamespace(owner=None)
    platform = SimpleNamespace(release_prima_ports=lambda *_: {"phase": "released"})
    ports = PortSupervisor(path.parent / "ports.json", platform=lambda: platform, rank=lambda: rank)

    def lost():
        assert json.loads(ports.path.read_text())["phase"] == "applying"
        raise OSError("Reply lost after native mutation")

    with pytest.raises(OSError, match="Reply lost"):
        ports.admit(manifest, OWNER, lost, lambda: None)
    restored = PortSupervisor(ports.path, platform=lambda: platform, rank=lambda: rank)
    assert restored.owner["phase"] == "cleanup_pending"
    with pytest.raises(ValueError, match="requires cleanup"):
        restored.validate(manifest, OWNER)
    restored.expire()
    assert restored.owner is None and json.loads(ports.path.read_text()) is None


def test_cleanup_failure_preserves_record_and_rejects_foreign_authority(prepared):
    manifest = prepared[0]
    ports, platform, _, _, _ = supervisor(prepared)
    with pytest.raises(ValueError, match="authority differs"):
        ports.release(manifest.network_id, manifest.run_id, {**OWNER, "sender": "other"})

    def changed(*_):
        raise OSError("Native ownership changed")

    platform.release_prima_ports = changed
    with pytest.raises(OSError, match="ownership changed"):
        ports.release(manifest.network_id, manifest.run_id, OWNER)
    assert ports.owner["phase"] == "cleanup_pending"
    assert json.loads(ports.path.read_text())["manifest_digest"] == manifest.digest


def test_local_recovery_requires_stopped_rank_and_exact_native_ack(prepared):
    manifest = prepared[0]
    ports, platform, rank, _, events = supervisor(prepared)
    ports.owner["phase"] = "cleanup_pending"
    rank.owner = {"run_id": manifest.run_id}
    platform.resolve_prima_ports = lambda *args: events.append(args) or {
        "phase": "released", "decision": "keep_current"
    }
    with pytest.raises(ValueError, match="Stop the owned"):
        ports.resolve(manifest.network_id, manifest.run_id, "keep_current", "a" * 64)
    assert events == ["apply"] and ports.owner
    rank.owner = None
    platform.resolve_prima_ports = lambda *_: {"phase": "released"}
    with pytest.raises(ValueError, match="acknowledge"):
        ports.resolve(manifest.network_id, manifest.run_id, "keep_current", "a" * 64)
    assert ports.owner and json.loads(ports.path.read_text())
    platform.resolve_prima_ports = lambda *args: events.append(args) or {
        "phase": "released", "decision": "keep_current"
    }
    assert ports.resolve(manifest.network_id, manifest.run_id, "keep_current", "a" * 64) == {
        "phase": "released", "decision": "keep_current"
    }
    assert ports.owner is None and json.loads(ports.path.read_text()) is None
    assert events[-1] == (manifest.network_id, manifest.run_id, "keep_current", "a" * 64)


def test_local_recovery_cannot_discard_live_or_foreign_admission(prepared):
    manifest = prepared[0]
    ports, platform, _, clock, _ = supervisor(prepared)
    platform.resolve_prima_ports = lambda *_: pytest.fail("Live/foreign recovery must not call helper")
    with pytest.raises(ValueError, match="requires cleanup"):
        ports.resolve(manifest.network_id, manifest.run_id, "keep_current", "a" * 64)
    clock[0] = ports.deadline
    with pytest.raises(ValueError, match="does not own"):
        ports.resolve(str(uuid4()), manifest.run_id, "keep_current", "a" * 64)


def test_local_persistence_failure_after_helper_resolution_retains_retryable_owner(prepared, monkeypatch):
    manifest = prepared[0]
    ports, platform, _, _, _ = supervisor(prepared)
    ports.owner["phase"] = "cleanup_pending"
    platform.resolve_prima_ports = lambda *_: {"phase": "released", "decision": "keep_current"}
    original_save = ports._save

    def fail_forgetting():
        if ports.owner is None:
            raise OSError("Node journal write failed")
        original_save()

    monkeypatch.setattr(ports, "_save", fail_forgetting)
    with pytest.raises(OSError, match="journal write failed"):
        ports.resolve(manifest.network_id, manifest.run_id, "keep_current", "a" * 64)
    assert ports.owner and ports.owner["phase"] == "cleanup_pending"
    monkeypatch.setattr(ports, "_save", original_save)
    ports.release(manifest.network_id, manifest.run_id)
    assert ports.owner is None


def test_spawn_fenced_by_expired_port_lease(prepared):
    manifest = prepared[0]
    ports, _, _, clock, events = supervisor(prepared)
    clock[0] = ports.deadline

    def forbidden():
        pytest.fail("Expired listener admission must refuse before spawn")

    with pytest.raises(ValueError, match="expired"):
        ports.launch(manifest, OWNER, forbidden, lambda: None)
    assert events == ["apply"]
    ports.expire()
    assert events == ["apply", "release"] and ports.owner is None


def test_post_spawn_revocation_stops_rank_before_native_rules(prepared):
    manifest = prepared[0]
    ports, _, rank, _, events = supervisor(prepared)

    def start():
        rank.owner = {"run_id": manifest.run_id}
        events.append("spawn")
        return {"phase": "starting"}

    def stop(run, **_):
        assert run == manifest.run_id
        rank.owner = None
        events.append("stop")

    rank.stop = stop

    def revoked():
        raise ValueError("Authority changed during spawn")

    with pytest.raises(ValueError, match="during spawn"):
        ports.launch(manifest, OWNER, start, revoked)
    assert events == ["apply", "spawn", "stop", "release"] and ports.owner is None


def test_port_only_owner_blocks_dispatch_and_is_drained_by_stop(prepared):
    import threading
    from kllm.modules.primacpp.interactors.distributed_runs import PrimaRuns

    ports, _, _, _, events = supervisor(prepared)
    runs = object.__new__(PrimaRuns)
    runs.control = SimpleNamespace(ports=ports, rank=SimpleNamespace(owner=None))
    runs.resource_reservation = None
    runs.local = SimpleNamespace(starting=False, manifest=None, stop=lambda: {"phase": "idle"})
    runs.distributed = SimpleNamespace(starting=False, stop=lambda: None,
        coordinator=SimpleNamespace(active=None, cancelled=threading.Event()))
    runs.lock = threading.RLock()
    runs.cancelled = threading.Event()
    runs.generation = 0
    runs._starting = False
    assert runs.busy
    with pytest.raises(ValueError, match="Reconcile the previous Prima rank, run or listener"):
        runs.launch(None, None, {}, {})
    assert runs.stop() == {"phase": "idle"}
    assert ports.owner is None and events == ["apply", "release"] and not runs.busy


@pytest.mark.parametrize("cause", ["restart", "lease", "failed_stop"])
def test_expiry_or_restart_drains_real_owned_child_and_preserves_an_unrelated_child(
    prepared, cause
):
    manifest, _, path = prepared
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    events = []
    clock = [100.0]
    can_stop = [cause != "failed_stop"]
    manifest = manifest.model_copy(update={"settings": {**manifest.settings,
        "swa_full": False, "parallel_slots": 2, "prima_kv_sequences": 3}})
    runtime = {"capabilities": {"features": list(BASELINE_FEATURES), "backends": ["cpu"]}}

    def stop(pid):
        assert pid == child.pid
        if not can_stop[0]:
            return False
        child.terminate()
        child.wait(5)
        events.append("stop")
        return True

    def make_rank():
        return RankSupervisor(
            path.parent / "rank",
            "worker1",
            capture=lambda _: runtime,
            spawn=lambda *_: {
                "pid": child.pid,
                "start_token": str(psutil.Process(child.pid).create_time()),
            },
            stop=stop,
            alive=lambda pid: psutil.pid_exists(pid),
            identity=lambda pid: str(psutil.Process(pid).create_time()),
            clock=lambda: clock[0],
        )

    rank = make_rank()

    def release(*_):
        assert child.poll() is not None and unrelated.poll() is None
        events.append("release")
        return {"phase": "released"}

    platform = SimpleNamespace(
        prima_ports_status=lambda *_: {"phase": "admitted", "manifest_digest": manifest.digest},
        release_prima_ports=release,
    )
    try:
        ports = PortSupervisor(
            path.parent / "ports.json",
            platform=lambda: platform,
            rank=lambda: rank,
            clock=lambda: clock[0],
        )
        ports.admit(
            manifest,
            OWNER,
            lambda: {"phase": "admitted", "manifest_digest": manifest.digest},
            lambda: None,
        )
        ports.launch(
            manifest,
            OWNER,
            lambda: rank.start(manifest, path, lambda: None, assessment=True, command_owner=OWNER),
            lambda: None,
        )
        if cause == "restart":
            rank = make_rank()
            restored = PortSupervisor(
                ports.path, platform=lambda: platform, rank=lambda: rank, clock=lambda: clock[0]
            )
        else:
            restored = ports
            clock[0] = 161.0
        if cause == "failed_stop":
            with pytest.raises(ValueError, match="has not stopped"):
                restored.expire()
            assert restored.owner["phase"] == "cleanup_pending" and rank.owner
            assert not events and child.poll() is unrelated.poll() is None
            can_stop[0] = True
        restored.expire()
        assert restored.owner is None and rank.owner is None and events == ["stop", "release"]
        assert unrelated.poll() is None
    finally:
        for process in (child, unrelated):
            if process.poll() is None:
                process.terminate()
                process.wait(5)
