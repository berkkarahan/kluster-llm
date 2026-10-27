"""Prima admission persistence and signed diagnostic boundaries."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.managed_network.entities import ManagedNetwork, NodeAssignment, NetworkOperation, utc_now
from kllm.modules.managed_network.interactors.allocation import OperationRequest
from kllm.modules.managed_network.interactors.service import ManagedNetworkService
from kllm.modules.primacpp.entities import LayerRange, PrimaTopology, RankAllocation
from kllm.modules.primacpp.interactors.execution import Asset, ExecutionManifest, Segment, inventory_identity
from kllm.modules.primacpp.interactors.placement import network_edges
from kllm.shared.platform.base import PlatformRefusal


def execution(service, network_id):
    members = service.prima_admission.inventory(network_id)[2]
    names = [service.identity.node_id, *sorted(n for n in members if n != service.identity.node_id)]
    ranks = [RankAllocation(node_id=node, role="master" if i == 0 else "worker", rank=i,
        address=members[node]["rpc_bind"], next_node_id=names[(i + 1) % len(names)],
        layers=[] if i == 0 else [LayerRange(start=(i - 1) * 4, end=i * 4)], ngl=0)
        for i, node in enumerate(names)]
    topology = PrimaTopology(runtime_id="a" * 64, master_node_id=names[0],
        total_layers=(len(names) - 1) * 4, ranks=ranks)
    return ExecutionManifest(runtime_id=topology.runtime_id, run_id=str(uuid4()), plan_digest="b" * 64,
        total_layers=topology.total_layers, ranks=ranks, settings={"context": 512},
        network_id=str(network_id), inventory_digest=inventory_identity(members, str(network_id)),
        assets=[Asset(name="model.gguf", kind="model", sha256="c" * 64, size=1)],
        segments=[Segment(start=w.start, end=w.end, node_id=r.node_id) for r in ranks for w in r.layers],
        backends={n: "cpu" for n in names}, transfer_edges=network_edges(topology))


@pytest.fixture
def prepared(tmp_path):
    state = SimpleNamespace(role="master", node_id="master", process_pid=None,
        members={"master": {"host": "192.168.68.1", "role": "master"},
                 "worker": {"host": "192.168.68.2", "role": "worker"}})
    service = ManagedNetworkService(tmp_path, lambda: state, lambda: None)
    authority = service._authority()
    network = ManagedNetwork(network_id=uuid4(), cluster_binding_id=authority.cluster_binding_id,
        master_node_id="master", master_key_id=service.identity.key_id, subnet="192.168.234.0/24",
        current_operation_id=uuid4())
    service.store.put_network(network)
    for index, node in enumerate(state.members):
        service.store.put_assignment(NodeAssignment(network_id=network.network_id, node_id=node,
            adapter_id="adapter", os_interface="test", identity_fingerprint=str(index + 1) * 64,
            address=f"192.168.234.{index + 1}", prefix_length=24))
    for stage in ("applying", "verifying", "ready"):
        service._publish(network, stage)
    service.store.put_operation(NetworkOperation(operation_id=network.current_operation_id,
        request_id=uuid4(), network_id=network.network_id, cluster_binding_id=authority.cluster_binding_id,
        action="start", master_term=authority.master_term, master_session_id=authority.master_session_id,
        expected_network_revision=0, operation_revision=1, participants=list(state.members),
        proposal_digest="d" * 64, phase="complete", deadline=utc_now() + timedelta(seconds=60)))
    yield service, network
    service.store.close()


@pytest.mark.asyncio
async def test_prima_reservation_freezes_exact_private_edges_without_rpc(prepared, monkeypatch):
    service, network = prepared
    original = {node: dict(member) for node, member in service.state().members.items()}
    value = execution(service, network.network_id)
    observed = []
    async def health(n, _, assignments):
        observed.append((n.network_id, [a.address for a in assignments]))
    monkeypatch.setattr(service, "_health", health)
    async def forbidden(*_):
        pytest.fail("Prima admission cannot launch or probe RPC")
    monkeypatch.setattr(service, "prepare_rpc", forbidden)
    monkeypatch.setattr(service, "verify_idle", forbidden)
    reservation = await service.prima_admission.reserve(value)
    assert observed == [(network.network_id, ["192.168.234.1", "192.168.234.2"])]
    assert reservation.engine == "primacpp" and not reservation.relay_instance_ids
    assert reservation.execution_manifest_digest == value.digest
    assert reservation.authority == service.channels.master_authority()
    assert all("127.0.0.1" not in e and "50052" not in e for e in reservation.launch_endpoints)
    assert service.state().members == original
    assert (await service.prima_admission.validate(value)).run_id == reservation.run_id
    with pytest.raises(PlatformRefusal, match="managed run"):
        await service.prima_admission.reserve(value)
    with pytest.raises(PlatformRefusal, match="all owned Prima ranks"):
        await service.admission.release(reservation)
    with pytest.raises(PlatformRefusal, match="INFERENCE_ACTIVE|depends|drain|active"):
        await service.begin_operation(network.network_id,
            OperationRequest(request_id=uuid4(), expected_revision=network.revision, action="stop"))
    await service.prima_admission.serving(value, {"pid": 123, "start_token": "owned"})
    assert service.store.runs(network.network_id)[0].state == "active"
    await service.prima_admission.release(value)
    assert service.store.runs(network.network_id)[0].state == "released"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["address", "edges", "inventory", "authority"])
async def test_stale_prima_manifest_is_refused_before_reservation(prepared, monkeypatch, changed):
    service, network = prepared
    value = execution(service, network.network_id)
    if changed == "address":
        value.ranks[0].address = "192.168.234.99"
    elif changed == "edges":
        value.transfer_edges[0]["port"] += 1
    elif changed == "inventory":
        value.inventory_digest = "f" * 64
    else:
        authority = service._authority().model_copy(update={"master_key_id": "f" * 64})
        monkeypatch.setattr(service, "_authority", lambda: authority)
    async def health(*_):
        pytest.fail("Invalid contract must be refused before live diagnostics")
    monkeypatch.setattr(service, "_health", health)
    with pytest.raises((ValueError, PlatformRefusal)):
        await service.prima_admission.reserve(value)
    assert service.store.runs(network.network_id) == []


@pytest.mark.asyncio
async def test_health_failure_preserves_other_runs_and_writes_no_admission(prepared, monkeypatch):
    service, network = prepared
    async def health(*_):
        raise PlatformRefusal("TIMEOUT", "Private route proof failed")
    monkeypatch.setattr(service, "_health", health)
    with pytest.raises(PlatformRefusal, match="route proof"):
        await service.prima_admission.reserve(execution(service, network.network_id))
    assert service.store.runs(network.network_id) == []
    assert not service.lock.locked()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_prima_admission_uses_real_signed_three_agent_diagnostics(tmp_path, monkeypatch):
    from managed_network_lab import NativeAgentLab

    lab = NativeAgentLab(tmp_path, monkeypatch)
    try:
        await lab.start()
        service = lab.nodes[0].service
        started = await lab.setup()
        await service.wait_operation(started["operation_id"])
        assert service.network_view(started["network_id"])["observed_state"] == "ready"
        value = execution(service, started["network_id"])
        async def forbidden(*_):
            pytest.fail("Prima cannot start RPC as part of admission")
        monkeypatch.setattr(service, "prepare_rpc", forbidden)
        monkeypatch.setattr(service, "verify_idle", forbidden)
        before = len(lab.configurations)
        reservation = await service.prima_admission.reserve(value)
        assert reservation.engine == "primacpp" and len(reservation.assignment_revisions) == 3
        assert not reservation.relay_instance_ids and len(lab.configurations) == before
        for node in lab.nodes[1:]:
            assert node.service.prima_admission.validate_worker(value).node_id == node.name
        await service.prima_admission.release(value)
        assert service.store.runs(started["network_id"])[-1].state == "released"
    finally:
        await lab.close()


@pytest.mark.asyncio
async def test_native_port_intent_survives_lost_ack_and_cleanup_narrows_only_successes(prepared, monkeypatch):
    service, network = prepared
    value = execution(service, network.network_id)
    async def health(*_):
        return None
    monkeypatch.setattr(service, "_health", health)
    await service.prima_admission.reserve(value, native_ports=True)
    applied = []
    def command(node, action, manifest, body):
        applied.append(node)
        if node == "worker":
            raise OSError("Acknowledgement lost after mutation")
        return {"phase": "admitted", "manifest_digest": manifest.digest}
    with pytest.raises(OSError, match="Acknowledgement lost"):
        await service.prima_admission.admit_ports(value, command, lambda: None)
    reservation = service.store.runs(network.network_id)[-1]
    assert reservation.prima_port_nodes == ["master", "worker"]
    assert reservation.prima_admitted_nodes == ["master"]
    with pytest.raises(PlatformRefusal, match="every rank"):
        await service.prima_admission.validate(value)
    stopped = []
    def release(node, *_):
        stopped.append(node)
        if node == "master":
            raise OSError("Master native cleanup unavailable")
        return {"phase": "released"}
    with pytest.raises(PlatformRefusal, match="pending on master"):
        await service.prima_admission.release(value, release)
    assert stopped == ["master", "worker"]
    reservation = service.store.runs(network.network_id)[-1]
    assert reservation.state == "releasing" and reservation.prima_port_nodes == ["master"]
    assert reservation.prima_admitted_nodes == ["master"]
    stopped.clear()
    await service.prima_admission.release(value, lambda node, *_: stopped.append(node) or {"phase": "released"})
    assert stopped == ["master"] and service.store.runs(network.network_id)[-1].state == "released"


@pytest.mark.asyncio
async def test_native_port_reservation_rejects_corrupt_or_widened_cleanup(prepared, monkeypatch):
    from kllm.modules.managed_network.gateways.store import StoreConflict
    service, network = prepared
    async def health(*_):
        return None
    monkeypatch.setattr(service, "_health", health)
    value = execution(service, network.network_id)
    reservation = await service.prima_admission.reserve(value, native_ports=True)
    reservation.prima_port_nodes.extend(["master", "master"])
    with pytest.raises(ValueError, match="ownership"):
        service.store.reservation(reservation)
    reservation = service.store.runs(network.network_id)[-1]
    reservation.state = "releasing"
    service.store.reservation(reservation)
    reservation.prima_port_nodes.append("worker")
    with pytest.raises(StoreConflict, match="acquire new"):
        service.store.reservation(reservation)


@pytest.mark.asyncio
async def test_cancelled_admission_waits_for_outstanding_native_call_before_cleanup(prepared, monkeypatch):
    import asyncio
    import threading
    service, network = prepared
    async def health(*_):
        return None
    monkeypatch.setattr(service, "_health", health)
    value = execution(service, network.network_id)
    await service.prima_admission.reserve(value, native_ports=True)
    started, finish, completed = threading.Event(), threading.Event(), threading.Event()
    def command(*_):
        started.set()
        assert finish.wait(5)
        completed.set()
        return {"phase": "admitted", "manifest_digest": value.digest}
    task = asyncio.create_task(service.prima_admission.admit_ports(value, command, lambda: None))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), "Cleanup must wait until the outstanding native call settles"
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), "Repeated cancellation must not orphan the native call"
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        def release(*_):
            assert completed.is_set()
            return {"phase": "released"}
        await service.prima_admission.release(value, release)
        assert service.store.runs(network.network_id)[-1].state == "released"
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
