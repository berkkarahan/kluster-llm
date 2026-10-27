"""Atomic reservations and real owned-child cleanup, with isolated model fixtures."""

from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import psutil
import pytest

from kllm.modules.primacpp.entities import ProfileRequest
from kllm.modules.primacpp.interactors.preparation import ProfileJobs
from kllm.shared.runtime_resources import NodeResources
import test_prima_port_runs as port_tests

prepared = port_tests.prepared


def wait(condition, seconds=5):
    until = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < until, "owned resource boundary did not settle"
        time.sleep(0.01)


def profile_service(tmp_path, resources, kill):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"controlled model bytes")
    state = SimpleNamespace(node_id="node", process_pid=None, members={"node": {"backend": "cpu"}})
    runtime = {"engine": "primacpp", "backend": "cpu", "capabilities": {"features": ["model_structure_v1"]}}
    return ProfileJobs(tmp_path / "jobs", lambda _: runtime, lambda _: model, lambda: state, kill,
                       resources=resources)


def request(mode="kernel"):
    return ProfileRequest(runtime_id="a" * 64, model="model.gguf", mode=mode, request_id=str(uuid4()))


def test_simultaneous_work_has_exactly_one_node_owner():
    resources = NodeResources()
    barrier = threading.Barrier(2)
    proceed = threading.Event()
    accepted, refused = [], []
    def start(name):
        barrier.wait(2)
        try:
            lease = resources.acquire(("primacpp", name, "authority", "manifest"), "run")
        except ValueError:
            refused.append(name)
            proceed.set()
            return
        accepted.append(name)
        assert proceed.wait(2)
        lease.release()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(start, "one"), pool.submit(start, "two")
        first.result(3)
        second.result(3)
    assert len(accepted) == len(refused) == 1 and not resources.busy


def test_exact_preparation_can_profile_but_foreign_or_overlapping_work_cannot_join():
    resources = NodeResources()
    scope = ("primacpp", "operation", "authority", "manifest")
    parent = resources.acquire(scope, "prepare")
    profile = resources.acquire(scope, "profile")
    for foreign in (scope[:-1] + ("other-manifest",), ("primacpp", "other", "authority", "manifest"),
                    ("llamacpp", "operation", "authority", "manifest")):
        with pytest.raises(ValueError, match="resources"):
            resources.acquire(foreign, "run")
    for kind in ("profile", "prepare", "link"):
        with pytest.raises(ValueError, match="resources"):
            resources.acquire(scope, kind)
    with pytest.raises(ValueError, match="child work"):
        parent.bind(("primacpp", "run", "authority", "new-manifest"))
    parent.release()
    assert resources.busy
    profile.release()
    assert not resources.busy
    later = resources.acquire(("primacpp", "run", "authority", "manifest"), "run")
    parent.release()  # An old callback cannot retire a later owner's token.
    assert resources.busy
    later.release()


def test_profile_cancellation_retains_reservation_until_queued_work_settles(tmp_path, monkeypatch):
    resources = NodeResources()
    service = profile_service(tmp_path, resources, lambda _: None)
    entered, proceed = threading.Event(), threading.Event()
    def held(*_):
        entered.set()
        assert proceed.wait(5)
    monkeypatch.setattr(service, "_run", held)
    try:
        job = service.start(request())
        assert entered.wait(2)
        assert service.cancel(job["job_id"])["phase"] == "cancelled"
        with pytest.raises(ValueError, match="resources"):
            resources.acquire(("llamacpp", "other-run"), "rpc_start")
        assert resources.busy
        proceed.set()
        wait(lambda: not resources.busy)
        assert not service.processes
    finally:
        proceed.set()
        service.close()


def test_generation_cleanup_failure_retains_real_child_and_reservation(tmp_path, monkeypatch):
    resources = NodeResources()
    children = []
    def cannot_stop(_):
        raise OSError("controlled owned cleanup failure")
    service = profile_service(tmp_path, resources, cannot_stop)
    def fail_generation(identity, *_):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        children.append(child)
        with service.lock:
            service.processes[identity] = child
            service.jobs[identity].update(phase="profiling", pid=child.pid,
                process_created_at=psutil.Process(child.pid).create_time())
            service._save()
        raise ValueError("controlled generation failure")
    monkeypatch.setattr(service, "_run_generation", fail_generation)
    def stop(child):
        child.terminate()
        child.wait(5)
    try:
        job = service.start(request("generation"))
        identity = job["job_id"]
        wait(lambda: service.futures[identity].done())
        assert service.job(identity)["phase"] == "cleanup_pending"
        assert children[0].poll() is None and service.processes[identity] is children[0]
        assert resources.busy and not service.cache.root.exists()
        with pytest.raises(ValueError, match="resources"):
            resources.acquire(("primacpp", "another-rank"), "rank_start")
        service.kill = stop
        assert service.cancel(identity)["phase"] == "cancelled"
        assert children[0].poll() is not None and not service.processes and not resources.busy
    finally:
        service.kill = stop
        service.close()
        for child in children:
            if child.poll() is None:
                stop(child)


def test_profile_persistence_failure_releases_unlaunched_reservation(tmp_path, monkeypatch):
    resources = NodeResources()
    service = profile_service(tmp_path, resources, lambda _: None)
    def failed():
        raise OSError("controlled persistence failure")
    monkeypatch.setattr(service, "_save", failed)
    try:
        with pytest.raises(OSError, match="persistence"):
            service.start(request())
        assert not service.jobs and not service.processes and not resources.busy
    finally:
        service.close()


def test_master_preparation_excludes_public_profile_and_cancel_does_not_release_early(tmp_path, monkeypatch):
    from kllm.modules.inference.entities import PlanBody
    from kllm.modules.managed_network.entities import MasterAuthority
    from kllm.modules.primacpp.interactors.control import PrimaControl
    model = tmp_path / "model.gguf"
    model.write_bytes(b"controlled model bytes")
    authority = MasterAuthority(cluster_binding_id=uuid4(), master_node_id="master", master_key_id="1" * 64,
        master_term=1, master_session_id=uuid4())
    state = SimpleNamespace(role="master", node_id="master", process_pid=None, active_config=None, members={"master": {}}, save=lambda: None)
    service = SimpleNamespace(state=lambda: state, channels=SimpleNamespace(master_authority=lambda: authority))
    registry = SimpleNamespace(root=tmp_path / "runtimes", lock=threading.RLock(), capture=lambda _: {"engine": "primacpp"},
        _kill=lambda _: None, running=lambda _: None)
    control = PrimaControl(service, registry, resolve_model=lambda _: model, execute_plan=lambda *_: {})
    entered, proceed = threading.Event(), threading.Event()
    def held(*_):
        entered.set()
        assert proceed.wait(5)
    monkeypatch.setattr(control, "_prepare", held)
    try:
        job = control.start_preparation("a" * 64, PlanBody(model=model.name), "b" * 64, str(uuid4()))
        assert entered.wait(2)
        with pytest.raises(ValueError, match="resources"):
            control.profiles.start(request())
        control.cancel(job["job_id"])
        assert control.resources.busy
        proceed.set()
        wait(lambda: not control.resources.busy)
    finally:
        proceed.set()
        control.close()


def test_rpc_start_refuses_reserved_node_before_firewall_or_spawn(monkeypatch, tmp_path):
    from kllm.modules.inference.interactors.worker_rpc import WorkerRpcService
    from kllm.modules.managed_network.interactors.identity import SignedRuntimeCommand
    from kllm.shared.platform.base import PlatformRefusal
    from kllm.shared.runtime_resources import node_resources
    registry = SimpleNamespace(lock=threading.RLock(), capture=lambda _: {"engine": "llamacpp", "runtime_id": "a" * 64})
    rpc = WorkerRpcService(lambda: None, lambda: None, registry=registry)
    calls = []
    monkeypatch.setattr(rpc, "_execute", lambda *_: calls.append("spawn") or {"status": "ready"})
    command = SignedRuntimeCommand(version=1, type="rpc_command", cluster_binding_id=uuid4(),
        master_node_id="master", master_key_id="a" * 64, master_term=1, master_session_id=uuid4(),
        channel_id=uuid4(), sender_node_id="master", target_node_id="worker", sequence=1,
        request_id=uuid4(), run_id=uuid4(), action="start", ttl_ms=5000, body={"runtime_id": "a" * 64})
    resources = node_resources(registry)
    lease = resources.acquire(("primacpp", "active-profile"), "profile")
    with pytest.raises(PlatformRefusal) as refusal:
        rpc.execute(command, time.monotonic() + 5)
    assert refusal.value.code == "RPC_BUSY" and not calls
    lease.release()
    assert rpc.execute(command, time.monotonic() + 5) == {"status": "ready"}
    assert calls == ["spawn"] and not resources.busy


def test_llama_master_run_refuses_reserved_node_and_releases_its_startup_claim(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from kllm.modules.inference.entities import RunBody
    from kllm.modules.inference.router_http import InferenceHandlers
    from kllm.modules.runtimes.interactors.selection import use_runtime
    resources = NodeResources()
    model = tmp_path / "model.gguf"
    model.touch()
    state = SimpleNamespace(role="master", members={})
    deps = SimpleNamespace(state=state, runtime_context=None, resolve_settings=None,
        resolve_model=lambda _: model, preflight=None, ensure_master_member=lambda: None,
        merge_extra=lambda _: None, validate_vision=lambda _: None, execute_plan=lambda *_: {"fit_status": "fit"},
        network_run=None, network_members=None, rpc_context=None, runtime_resources=lambda: resources)
    handler = InferenceHandlers(deps)
    calls = []
    monkeypatch.setattr(handler, "_guard_context", lambda _: None)
    monkeypatch.setattr(handler, "_launch", lambda *_: calls.append("spawn") or {"phase": "ready"})
    lease = resources.acquire(("primacpp", "active-preparation"), "prepare")
    with use_runtime({"engine": "llamacpp"}):
        with pytest.raises(HTTPException) as refusal:
            handler.run(RunBody(model=model.name))
        assert refusal.value.status_code == 409 and refusal.value.detail["code"] == "NODE_RESOURCE_RESERVED"
        assert not calls
        lease.release()
        assert handler.run(RunBody(model=model.name)) == {"phase": "ready"}
    assert calls == ["spawn"] and not resources.busy


def test_listener_reservation_is_retained_until_durable_cleanup_succeeds(prepared):
    from kllm.modules.primacpp.interactors.port_runs import PortSupervisor
    manifest, _, path = prepared
    resources = NodeResources()
    platform = SimpleNamespace(release_prima_ports=lambda *_: {"phase": "released"})
    owner = {"sender": "master", "authority_digest": "e" * 64}
    ports = PortSupervisor(path.parent / "resources-ports.json", platform=lambda: platform,
        rank=lambda: SimpleNamespace(owner=None), resources=resources)
    ports.admit(manifest, owner, lambda: {"phase": "admitted", "manifest_digest": manifest.digest}, lambda: None)
    assert resources.busy
    def lost(*_):
        raise OSError("controlled release failure")
    platform.release_prima_ports = lost
    with pytest.raises(OSError, match="release failure"):
        ports.release(manifest.network_id, manifest.run_id, owner)
    assert resources.busy and ports.owner["phase"] == "cleanup_pending"
    with pytest.raises(ValueError, match="resources"):
        resources.acquire(("primacpp", "other-profile"), "profile")
    platform.release_prima_ports = lambda *_: {"phase": "released"}
    ports.release(manifest.network_id, manifest.run_id, owner)
    assert not resources.busy and ports.owner is None


@pytest.mark.parametrize("persisted", [False, True])
def test_listener_initial_save_failure_reconciles_durable_owner(prepared, monkeypatch, persisted):
    from kllm.modules.primacpp.interactors.port_runs import PortSupervisor
    manifest, _, path = prepared
    resources = NodeResources()
    platform = SimpleNamespace(release_prima_ports=lambda *_: {"phase": "released"})
    owner = {"sender": "master", "authority_digest": "e" * 64}
    ports = PortSupervisor(path.parent / "initial-save-ports.json", platform=lambda: platform,
        rank=lambda: SimpleNamespace(owner=None), resources=resources)
    original_save = ports._save

    def failed():
        if persisted:
            original_save()
        raise OSError("controlled initial journal failure")

    monkeypatch.setattr(ports, "_save", failed)
    called = []
    with pytest.raises(OSError, match="initial journal failure"):
        ports.admit(manifest, owner, lambda: called.append("apply"), lambda: None)
    assert not called
    assert resources.busy is persisted
    assert (ports.owner is not None) is persisted
    if persisted:
        assert json.loads(ports.path.read_text())["phase"] == "applying"
        monkeypatch.setattr(ports, "_save", original_save)
        ports.expire()
        assert ports.owner is None and not resources.busy


def test_restarted_listener_owner_restores_node_reservation(prepared):
    from kllm.modules.primacpp.interactors.port_runs import PortSupervisor
    manifest, _, path = prepared
    platform = SimpleNamespace(release_prima_ports=lambda *_: {"phase": "released"})
    owner = {"sender": "master", "authority_digest": "e" * 64}
    journal = path.parent / "restart-resources-ports.json"
    initial = PortSupervisor(journal, platform=lambda: platform,
        rank=lambda: SimpleNamespace(owner=None), resources=NodeResources())
    initial.admit(manifest, owner, lambda: {"phase": "admitted", "manifest_digest": manifest.digest}, lambda: None)

    resources = NodeResources()
    restored = PortSupervisor(journal, platform=lambda: platform,
        rank=lambda: SimpleNamespace(owner=None), resources=resources)
    assert restored.owner["phase"] == "cleanup_pending" and resources.busy
    with pytest.raises(ValueError, match="resources"):
        resources.acquire(("llamacpp", "other-run"), "run")
    restored.expire()
    assert restored.owner is None and not resources.busy


def test_restarted_rank_owner_restores_node_reservation(tmp_path):
    from kllm.modules.primacpp.interactors.distributed_runs import PrimaRuns
    resources = NodeResources()
    rank = SimpleNamespace(owner={"run_id": str(uuid4()), "manifest_digest": "a" * 64,
                                  "command_owner": {"authority_digest": "e" * 64}})
    control = SimpleNamespace(root=tmp_path, rank=rank, ports=SimpleNamespace(owner=None),
                              resources=resources)
    runs = PrimaRuns(control)
    assert runs.busy and resources.busy
    with pytest.raises(ValueError, match="resources"):
        resources.acquire(("llamacpp", "other-run"), "run")
    rank.owner = None
    runs._release_if_idle()
    assert not runs.busy and not resources.busy


def test_local_run_retains_reservation_after_launch_until_cleanup(tmp_path, monkeypatch):
    from kllm.modules.primacpp.interactors import distributed_runs
    resources = NodeResources()
    control = SimpleNamespace(root=tmp_path, rank=SimpleNamespace(owner=None),
        ports=SimpleNamespace(owner=None), resources=resources, lock=threading.RLock(), jobs={},
        profiles=SimpleNamespace(lock=threading.RLock(), jobs={}),
        service=SimpleNamespace(channels=SimpleNamespace(master_authority=lambda: None)))
    runs = distributed_runs.PrimaRuns(control)
    monkeypatch.setattr(distributed_runs, "authority_owner", lambda _: {"authority_digest": "e" * 64})

    def launch(*_):
        runs.local.manifest = object()
        return {"phase": "serving"}

    monkeypatch.setattr(runs.local, "launch", launch)
    plan = {"plan_digest": "a" * 64, "prima_topology": {"ranks": [{}]}}
    assert runs.launch(None, None, plan, None) == {"phase": "serving"}
    assert resources.busy
    with pytest.raises(ValueError, match="resources"):
        resources.acquire(("llamacpp", "other-run"), "run")
    runs.local.manifest = None
    runs._release_if_idle()
    assert not resources.busy


def test_profile_reserves_node_before_resolving_model_and_releases_failed_resolution(tmp_path, monkeypatch):
    resources = NodeResources()
    service = profile_service(tmp_path, resources, lambda _: None)
    entered, proceed = threading.Event(), threading.Event()
    def resolve(_):
        entered.set()
        assert proceed.wait(5)
        raise ValueError("controlled model resolution failure")
    monkeypatch.setattr(service, "resolve_model", resolve)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service.start, request())
            assert entered.wait(2)
            with pytest.raises(ValueError, match="resources"):
                resources.acquire(("llamacpp", "other-startup"), "run")
            proceed.set()
            with pytest.raises(ValueError, match="resolution failure"):
                future.result(3)
        assert not resources.busy and not service.jobs
    finally:
        proceed.set()
        service.close()
