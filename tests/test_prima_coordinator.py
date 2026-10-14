"""Durable master lifecycle checks; physical cluster acceptance runs separately."""

import json
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.execution import Asset, ExecutionManifest, Segment
from kllm.modules.primacpp.interactors.lifecycle import PrimaCoordinator, RankSupervisor

# Features a current managed Prima runtime advertises (see test_prima_lifecycle.py).
BASELINE_FEATURES = [
    "execution_manifest_v1",
    "logical_context_sequences_v1",
    "embedding_role_v1",
    "decode_metadata_v2",
    "compact_swa_v1",
    "explicit_devices_v1",
]


@pytest.fixture
def execution():
    return ExecutionManifest(runtime_id="a" * 64, run_id=str(uuid4()), plan_digest="b" * 64,
        total_layers=8, ranks=[
            RankAllocation(node_id="master", role="master", rank=0, address="10.10.10.1",
                next_node_id="worker", layers=[], ngl=0),
            RankAllocation(node_id="worker", role="worker", rank=1, address="10.10.10.2",
                next_node_id="master", layers=[LayerRange(start=0, end=8)], ngl=0)],
        assets=[Asset(name="model.gguf", kind="model", sha256="c" * 64, size=1)],
        settings={"context": 512, "swa_full": False, "parallel_slots": 4,
                  "prima_kv_sequences": 5}, inventory_digest="d" * 64,
        segments=[Segment(start=0, end=8, node_id="worker")],
        backends={"master": "cpu", "worker": "cpu"})


def reply(manifest, node, phase):
    rank = next(r for r in manifest.ranks if r.node_id == node)
    return {"phase": phase, "run_id": manifest.run_id, "runtime_id": manifest.runtime_id,
        "manifest_digest": manifest.digest, "rank": rank.rank, "ngl": rank.ngl,
        "layers": sum(w.end - w.start for w in rank.layers), "backend": manifest.backends[node]}


def test_master_restart_retries_owned_cleanup_without_restarting_inference(tmp_path, execution):
    actions, released = [], []
    def command(node, action, manifest):
        actions.append((node, action))
        return reply(manifest, node, "idle" if action == "prima_stop" else "ready")
    original = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None,
        release=lambda: released.append(True))
    original.start(execution)
    actions.clear()
    restored = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None,
        release=lambda: released.append(True))
    assert restored.active is not None
    assert restored.monitor_once()["phase"] == "idle"
    assert actions == [("master", "prima_stop"), ("worker", "prima_stop")]
    assert released == [True]
    assert json.loads((tmp_path / "run.json").read_text())["phase"] == "idle"


def test_start_intent_survives_a_lost_worker_acknowledgement(tmp_path, execution):
    seen = []
    def command(node, action, manifest):
        if action == "prima_start":
            seen.append(json.loads((tmp_path / "run.json").read_text()))
            raise OSError("Start reply lost after child spawn")
        raise OSError("Worker disconnected")
    coordinator = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None,
        release=lambda: pytest.fail("Admission must remain owned"))
    with pytest.raises((OSError, ValueError)):
        coordinator.start(execution)
    assert seen[0]["pending_nodes"] == ["worker"]
    restored = PrimaCoordinator(tmp_path, command=lambda node, _, m: reply(m, node, "idle"),
        admit=lambda _: pytest.fail("Restart must not admit inference"), release=lambda: None)
    assert restored.active.run_id == execution.run_id
    assert restored.started == ["worker"]
    assert restored.monitor_once()["phase"] == "idle"


@pytest.mark.parametrize("change", [{"runtime_id": "f" * 64}, {"rank": 99},
    {"ngl": 8}, {"layers": 99}, {"run_id": str(uuid4())}, {"backend": "vulkan"},
    {"rank": True}])
def test_readiness_barrier_requires_each_ranks_exact_identity(tmp_path, execution, change):
    def command(node, action, manifest):
        if action == "prima_stop":
            return {"phase": "idle"}
        result = reply(manifest, node, "ready")
        if node == "worker":
            result.update(change)
        return result
    coordinator = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None,
        release=lambda: None)
    with pytest.raises(ValueError, match="identity|manifest|allocation"):
        coordinator.start(execution)
    assert coordinator.active is None


def test_stop_cannot_be_cleared_by_recovery_start(tmp_path, execution):
    actions, entered, resume = [], threading.Event(), threading.Event()
    failing = [False]
    def command(node, action, manifest):
        actions.append((node, action))
        return reply(manifest, node, "idle" if action == "prima_stop" else
            "failed" if failing[0] and action == "prima_renew" else "ready")
    admissions = [0]
    def admit(_):
        admissions[0] += 1
        if admissions[0] == 2:
            entered.set()
            assert resume.wait(5)
    coordinator = PrimaCoordinator(tmp_path, command=command, admit=admit, release=lambda: None)
    coordinator.start(execution)
    failing[0] = True
    coordinator.monitor_once()
    errors = []
    def recover():
        try:
            coordinator.monitor_once()
        except Exception as error:
            errors.append(error)
    monitor = threading.Thread(target=recover)
    monitor.start()
    assert entered.wait(5)
    stop = threading.Thread(target=coordinator.stop)
    stop.start()
    assert coordinator.cancelled.wait(5)
    resume.set()
    monitor.join(5)
    stop.join(5)
    assert not monitor.is_alive() and not stop.is_alive()
    assert coordinator.active is None
    assert actions.count(("worker", "prima_start")) == 1
    assert errors and "cancel" in str(errors[0]).lower()


def test_recovery_is_bounded_and_an_explicit_new_run_gets_a_new_budget(tmp_path, execution):
    starts = []
    def command(node, action, manifest):
        if action == "prima_start":
            starts.append(node)
        return reply(manifest, node, "idle" if action == "prima_stop" else
            "failed" if action == "prima_renew" else "ready")
    coordinator = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None, release=lambda: None)
    coordinator.start(execution)
    for _ in range(3):
        assert coordinator.monitor_once()["phase"] == "degraded"
        assert coordinator.monitor_once()["phase"] == "serving"
    assert coordinator.monitor_once()["phase"] == "degraded"
    assert coordinator.monitor_once()["phase"] == "failed"
    assert coordinator.monitor_once()["phase"] == "failed"
    assert starts.count("master") == starts.count("worker") == 4
    restored = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None, release=lambda: None)
    assert restored.active is None
    coordinator.start(execution.model_copy(update={"run_id": str(uuid4())}))
    assert coordinator.retries == 0
    coordinator.stop()


def test_failed_recovery_cleanup_retries_stops_without_starting_again(tmp_path, execution):
    starts, stops, releases = [], [], []
    def command(node, action, manifest):
        if action == "prima_start":
            starts.append(node)
        if action == "prima_stop":
            stops.append(node)
            if stops.count("worker") == 1 and node == "worker":
                raise OSError("Channel disappeared while stopping")
        return reply(manifest, node, "idle" if action == "prima_stop" else
            "failed" if action == "prima_renew" else "ready")
    coordinator = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None,
        release=lambda: releases.append(True))
    coordinator.start(execution)
    assert coordinator.monitor_once()["phase"] == "degraded"
    with pytest.raises(ValueError, match="cleanup pending"):
        coordinator.monitor_once()
    assert coordinator.cancelled.is_set() and coordinator.started == ["worker"]
    assert coordinator.monitor_once()["phase"] == "idle"
    assert starts == ["worker", "master"] and stops == ["master", "worker", "worker"]
    assert releases == [True]


@pytest.mark.parametrize("record", [
    {"phase": "unknown"}, {"schema_version": True, "phase": "idle"},
    {"phase": "starting", "pending_nodes": ["other"]},
    {"phase": "starting", "pending_nodes": ["worker", "worker"]},
    {"phase": "serving", "manifest_digest": "f" * 64},
    {"phase": "serving", "recovery_attempts": True},
    {"phase": "serving", "recovery_attempts": 4},
    {"phase": "serving", "command_owner": {"sender": "master"}},
    {"phase": "idle", "pending_nodes": ["worker"]},
    {"phase": "failed", "manifest": None, "pending_nodes": ["worker"]},
])
def test_ambiguous_durable_records_are_refused_without_process_commands(tmp_path, execution, record):
    (tmp_path / "run.json").write_text(json.dumps({"manifest": execution.model_dump(), **record}))
    with pytest.raises(ValueError):
        PrimaCoordinator(tmp_path, command=lambda *_: pytest.fail("Must not command ambiguous ownership"),
            admit=lambda _: None, release=lambda: None)


def test_legacy_record_conservatively_retains_all_ranks(tmp_path, execution):
    (tmp_path / "run.json").write_text(json.dumps({"manifest": execution.model_dump(), "phase": "admitting"}))
    stopped = []
    coordinator = PrimaCoordinator(tmp_path, command=lambda node, *_: stopped.append(node) or {"phase": "idle"},
        admit=lambda _: pytest.fail("Legacy restart cannot admit"), release=lambda: None)
    assert coordinator.monitor_once()["phase"] == "idle"
    assert stopped == ["master", "worker"]


def test_revoked_master_or_inventory_fence_stops_instead_of_recovering(tmp_path, execution):
    allowed, actions = [True], []
    def check():
        if not allowed[0]:
            raise ValueError("Master authority changed")
    def command(node, action, manifest):
        actions.append((node, action))
        return reply(manifest, node, "idle" if action == "prima_stop" else "ready")
    coordinator = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None,
        release=lambda: None, check=check)
    coordinator.start(execution)
    allowed[0] = False
    with pytest.raises(ValueError, match="authority changed"):
        coordinator.monitor_once()
    assert coordinator.active is None
    assert actions.count(("worker", "prima_start")) == 1


def test_unrecorded_cleanup_never_restarts_inference(monkeypatch, tmp_path, execution):
    from kllm.modules.primacpp.interactors import lifecycle

    starts = []
    def command(node, action, manifest):
        if action == "prima_start":
            starts.append(node)
        return reply(manifest, node, "idle" if action == "prima_stop" else
            "failed" if action == "prima_renew" else "ready")
    coordinator = PrimaCoordinator(tmp_path, command=command, admit=lambda _: None, release=lambda: None)
    coordinator.start(execution)
    assert coordinator.monitor_once()["phase"] == "degraded"
    original = lifecycle.atomic_json
    failed = [False]
    def persist(path, record):
        if record["phase"] == "idle" and not failed[0]:
            failed[0] = True
            raise OSError("Disk unavailable after release")
        return original(path, record)
    monkeypatch.setattr(lifecycle, "atomic_json", persist)
    with pytest.raises(OSError, match="Disk unavailable"):
        coordinator.monitor_once()
    assert coordinator.cancelled.is_set()
    assert coordinator.monitor_once()["phase"] == "idle"
    assert starts == ["worker", "master"]


def test_stop_during_readiness_publication_prevents_serving(monkeypatch, tmp_path, execution):
    from kllm.modules.primacpp.interactors import lifecycle

    publishing, resume, errors, results = threading.Event(), threading.Event(), [], []
    original = lifecycle.atomic_json
    def persist(path, record):
        if record["phase"] == "serving":
            publishing.set()
            assert resume.wait(5)
        return original(path, record)
    monkeypatch.setattr(lifecycle, "atomic_json", persist)
    coordinator = PrimaCoordinator(tmp_path,
        command=lambda node, action, m: reply(m, node, "idle" if action == "prima_stop" else "ready"),
        admit=lambda _: None, release=lambda: None)
    def start():
        try:
            results.append(coordinator.start(execution))
        except Exception as error:
            errors.append(error)
    launch = threading.Thread(target=start)
    launch.start()
    assert publishing.wait(5)
    stop = threading.Thread(target=coordinator.stop)
    stop.start()
    assert coordinator.cancelled.wait(5)
    resume.set()
    launch.join(5)
    stop.join(5)
    assert not launch.is_alive() and not stop.is_alive()
    assert not results and errors and "cancel" in str(errors[0]).lower()
    assert coordinator.active is None


@pytest.mark.integration
def test_durable_coordinator_and_rank_restart_stop_only_their_real_children(tmp_path, execution):
    import psutil

    children = []
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    supervisors = {}
    owner = {"sender": "master", "authority_digest": "e" * 64}
    def identity(pid):
        return str(psutil.Process(pid).create_time())
    def alive(pid):
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    def stop(pid):
        process = psutil.Process(pid)
        process.terminate()
        process.wait(5)
        return True
    def spawn(runtime, rank, model, manifest_path, log, settings):
        report = {**reply(execution, rank.node_id, "ready"), "schema_version": 1,
            "manifest_mode": True, "readiness_stage": "server_request_loop" if rank.rank == 0 else "worker_decode_loop"}
        code = "from pathlib import Path; import time; Path(" + repr(str(log)) + ").write_text(" + repr(
            "KLLM_PRIMA_RANK_READY=" + json.dumps(report) + "\n") + "); time.sleep(30)"
        child = subprocess.Popen([sys.executable, "-c", code])
        children.append(child)
        return {"pid": child.pid, "start_token": identity(child.pid)}
    def make_rank(node):
        return RankSupervisor(tmp_path / node, node, capture=lambda _: {
            "capabilities": {"features": list(BASELINE_FEATURES), "backends": ["cpu"]}},
            spawn=spawn, stop=stop, alive=alive, identity=identity)
    def command(node, action, manifest):
        rank = supervisors[node]
        if action == "prima_start":
            return rank.start(manifest, Path("unused-fixture.gguf"), lambda: None,
                assessment=True, command_owner=owner)
        if action == "prima_stop":
            return rank.stop(manifest.run_id, command_owner=owner)
        status = rank.status(manifest.run_id, command_owner=owner)
        if status["phase"] == "starting":
            report = rank.readiness_report(manifest.run_id, lambda: None, command_owner=owner)
            if report:
                return rank.ready(manifest.run_id, report, lambda: None)
        return status
    released = []
    try:
        supervisors.update({node: make_rank(node) for node in ("master", "worker")})
        first = PrimaCoordinator(tmp_path / "coordinator", command=command, admit=lambda _: None,
            release=lambda: released.append(True))
        assert first.start(execution, command_owner=owner)["phase"] == "serving"
        assert len(children) == 2 and all(child.poll() is None for child in children)
        # Recreate both durable owners without using the original in-memory run.
        supervisors.update({node: make_rank(node) for node in ("master", "worker")})
        restored = PrimaCoordinator(tmp_path / "coordinator", command=command,
            admit=lambda _: pytest.fail("Restart cannot relaunch inference"), release=lambda: released.append(True))
        assert restored.command_owner == owner
        assert restored.monitor_once()["phase"] == "idle"
        assert all(child.poll() is not None for child in children)
        assert unrelated.poll() is None and released == [True]
    finally:
        for child in [*children, unrelated]:
            if child.poll() is None:
                child.terminate()
            child.wait(5)
