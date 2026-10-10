"""Critical refusal/replay and recovery checks; no host configuration is changed."""

from datetime import timedelta
from uuid import uuid4

import pytest

from kllm.modules.managed_network.entities import ManagedNetwork, PeerIdentity
from kllm.modules.managed_network.gateways.store import NetworkStore, StoreConflict, digest


@pytest.mark.parametrize("platform_name", ["macos", "linux"])
def test_inventory_skips_uninspectable_interfaces(platform_name, monkeypatch):
    from importlib import import_module
    from kllm.shared.platform.inspection import NetworkError

    module = import_module(f"kllm.shared.platform.{platform_name}")
    platform = module.MacOSAdapter() if platform_name == "macos" else module.LinuxAdapter()
    monkeypatch.setattr(
        module.socket, "if_nameindex", lambda: [(1, "lo0"), (2, "ethernet"), (3, "utun0")]
    )
    snapshot = object()

    def inspect(name):
        if name == "ethernet":
            return snapshot
        raise NetworkError(f"no network service maps to interface {name!r}")

    monkeypatch.setattr(platform, "inspect", inspect)
    assert platform.inventory() == [snapshot]


def test_signed_authority_replay_and_changed_identity(tmp_path):
    from kllm.modules.managed_network.interactors.identity import ChannelFence, IdentityError, LocalIdentity, SignedCommand

    master_store = NetworkStore(tmp_path / "master")
    worker_store = NetworkStore(tmp_path / "worker")
    master = LocalIdentity(tmp_path / "master", "master", master_store)
    worker = LocalIdentity(tmp_path / "worker", "worker", worker_store)
    authority = master_store.startup_authority(master.node_id, master.key_id)
    worker_store.pair(
        PeerIdentity(
            node_id="master",
            key_id=master.key_id,
            public_key=master.public_key,
            cluster_binding_id=authority.cluster_binding_id,
            expected_role="master",
        )
    )
    channel = uuid4()
    authority.accepted_channel_id = channel
    worker_store.accept_authority(authority)
    fence = ChannelFence(worker_store, worker_store.peer("master"), authority, "master", "worker")
    command = SignedCommand(
        version=1,
        type="command",
        **authority.model_dump(exclude={"accepted_channel_id"}),
        channel_id=channel,
        sender_node_id="master",
        target_node_id="worker",
        sequence=1,
        network_id=uuid4(),
        operation_id=uuid4(),
        operation_revision=1,
        phase_sequence=1,
        phase="inspect",
        assignment_revision=1,
        expected_local_revision=0,
        ttl_ms=5000,
        body={},
    )
    wire = master.sign(command.model_dump(mode="json"))
    assert fence.receive(wire).sequence == 1
    with pytest.raises(IdentityError):
        fence.receive(wire)
    with pytest.raises(IdentityError):
        worker.verify(wire, worker.public_key)
    with pytest.raises(IdentityError):
        master.verify('{"payload":"{}","payload":"{}","signature":"a"}', master.public_key)
    with pytest.raises(StoreConflict):
        worker_store.accept_authority(authority.model_copy(update={"master_session_id": uuid4()}))
    newer = authority.model_copy(
        update={
            "master_term": authority.master_term + 1,
            "master_session_id": uuid4(),
            "accepted_channel_id": uuid4(),
        }
    )
    worker_store.accept_authority(newer)
    with pytest.raises(StoreConflict):
        fence.check_live()
    master.path.unlink()
    with pytest.raises(IdentityError):
        LocalIdentity(tmp_path / "master", "master", master_store)
    master_store.close()
    worker_store.close()


def test_rebuilt_node_label_keeps_the_pinned_key_and_drops_stale_fences(tmp_path):
    from kllm.modules.managed_network.interactors.identity import LocalIdentity

    store = NetworkStore(tmp_path)
    try:
        original = LocalIdentity(tmp_path, "old-node", store)
        authority = store.startup_authority("old-node", original.key_id)
        store.accept_authority(
            authority.model_copy(update={"accepted_channel_id": uuid4()}), "old-node"
        )
        rotated = LocalIdentity(tmp_path, "new-node", store)
        assert (rotated.node_id, rotated.key_id) == ("new-node", original.key_id)
        assert store.metadata("local_identity") == f"new-node:{original.key_id}"
        with pytest.raises(StoreConflict):
            store.check_authority(authority)
    finally:
        store.close()


def test_changed_private_key_still_requires_explicit_recovery(tmp_path):
    import secrets
    from kllm.modules.managed_network.interactors.identity import LocalIdentity

    store = NetworkStore(tmp_path)
    try:
        LocalIdentity(tmp_path, "node", store)
        (tmp_path / "network-identity/ed25519.key").write_bytes(secrets.token_bytes(32))
        with pytest.raises(StoreConflict, match="key changed"):
            LocalIdentity(tmp_path, "node", store)
    finally:
        store.close()


def test_durable_idempotency_and_restart_no_stale_ready(tmp_path):
    from kllm.modules.managed_network.entities import LocalMutationJournal, NetworkOperation, RunNetworkReservation, utc_now

    store = NetworkStore(tmp_path)
    binding = store.cluster_binding()
    authority = store.startup_authority("master", "a" * 64)
    network = ManagedNetwork(
        network_id=uuid4(),
        cluster_binding_id=binding,
        master_node_id="master",
        master_key_id="a" * 64,
        subnet="192.168.234.0/24",
    )
    store.put_network(network)
    operation = NetworkOperation(
        operation_id=uuid4(),
        request_id=uuid4(),
        network_id=network.network_id,
        cluster_binding_id=binding,
        action="start",
        master_term=authority.master_term,
        master_session_id=authority.master_session_id,
        expected_network_revision=0,
        operation_revision=1,
        participants=["master", "worker"],
        proposal_digest=digest({"start": 1}),
        phase="preparing",
        deadline=utc_now() + timedelta(seconds=60),
    )
    store.put_operation(operation)
    assert store.begin_command("apply-once", "b" * 64) == (True, None)
    with pytest.raises(StoreConflict):
        store.begin_command("apply-once", "b" * 64)
    store.finish_command("apply-once", {"status": "configured"})
    assert store.begin_command("apply-once", "b" * 64) == (False, {"status": "configured"})
    with pytest.raises(StoreConflict):
        store.begin_command("apply-once", "c" * 64)
    journal = LocalMutationJournal(
        journal_id=uuid4(),
        network_id=network.network_id,
        operation_id=operation.operation_id,
        node_id="master",
        adapter_fingerprint="d" * 64,
        exact_address_prefix="192.168.234.1/24",
        boot_id="boot",
        observation_epoch=uuid4(),
        event_high_water=1,
        observer_health="continuous",
        pre_snapshot={},
        intended_delta={"add": "192.168.234.1/24"},
        phase="applied_observed",
        authority_fence=authority,
    )
    store.journal(journal)
    for state in ("applying", "verifying", "ready"):
        before = network.revision
        network.observed_state = state
        network.revision += 1
        store.put_network(network, expected_revision=before)
    run = RunNetworkReservation(
        run_id=uuid4(),
        network_id=network.network_id,
        assignment_revisions={"worker": 1},
        logical_endpoints=["192.168.234.2:50052"],
        launch_endpoints=["127.0.0.1:34567"],
        relay_instance_ids=[uuid4()],
        state="reserved",
    )
    store.reservation(run)
    store.close()
    store = NetworkStore(tmp_path)
    store.recover()
    assert store.network(network.network_id).observed_state == "degraded"
    assert store.journals()[0].phase == "ownership_conflict"
    assert store.runs(network.network_id)[0].state == "uncertain"
    store.close()


@pytest.mark.asyncio
async def test_all_node_preparation_refusal_makes_no_host_changes(tmp_path, monkeypatch):
    """Hardware/privilege facts are controlled; refused preparation never adds."""
    from types import SimpleNamespace
    from kllm.modules.managed_network.interactors.service import ManagedNetworkService
    from kllm.modules.managed_network.interactors.allocation import StartRequest
    from kllm.shared.platform.base import AdapterSnapshot, PlatformAdapter
    from kllm.shared.platform.inspection import InterfaceState
    from kllm.shared.platform import base
    from kllm.shared import native

    mutations = []

    class RefusedPlatform(PlatformAdapter):
        def inventory(self):
            return [self.inspect("eth-test")]

        def inspect(self, adapter_id):
            return AdapterSnapshot(
                adapter_id,
                adapter_id,
                1,
                "d" * 64,
                "02:00:00:00:00:01",
                InterfaceState(adapter_id, True, 10000, True, (), ()),
            )

        def add_exact(self, *args):
            mutations.append("add")

        def delete_exact(self, *args):
            mutations.append("delete")

        def check_source_route(self, *args):
            raise AssertionError("refused interface reached route verification")

    monkeypatch.setattr(base, "is_admin", lambda: True)
    monkeypatch.setattr(native, "managed_network_available", lambda: True)
    platform = RefusedPlatform()
    state = SimpleNamespace(
        role="master",
        node_id="master",
        members={"master": {}, "worker": {}},
        master_host=None,
        process_pid=None,
    )
    service = ManagedNetworkService(tmp_path, lambda: state, lambda: None, platform=platform)
    # Refusal of the local master must prevent dispatching any apply, including
    # to unreachable/unpaired workers. No host mutation may precede all prepares.
    inventory = platform.inspect("eth-test").public()
    request = StartRequest(
        request_id=uuid4(),
        subnet="192.168.234.0/24",
        participants=[
            {
                "node_id": node,
                "adapter_id": "eth-test",
                "inventory_revision": inventory["inventory_revision"],
            }
            for node in ("master", "worker")
        ],
    )
    operation = await service.begin_start(request)
    await service.wait_operation(operation["operation_id"])
    assert service.operation_view(operation["operation_id"])["phase"] == "failed"
    assert mutations == []
    await service.close()


@pytest.mark.parametrize(
    ("speed", "expected_code"),
    [
        (None, "UNKNOWN_LINK_SPEED"),
        (0, "UNKNOWN_LINK_SPEED"),
        (-1, "UNKNOWN_LINK_SPEED"),
        (999, "LINK_TOO_SLOW"),
        (1000, None),
    ],
)
def test_platform_eligibility_uses_one_gigabit_floor(speed, expected_code, monkeypatch):
    from kllm.shared import native
    from kllm.modules.managed_network.interactors.helper_platform import HelperPlatform
    from kllm.shared.platform.base import AdapterSnapshot, PlatformAdapter, PlatformRefusal
    from kllm.shared.platform.inspection import InterfaceState

    class Candidate(PlatformAdapter):
        def inventory(self):
            return []

        def inspect(self, adapter_id):
            raise NotImplementedError

        def add_exact(self, snapshot, assignment):
            raise NotImplementedError

        def delete_exact(self, snapshot, assignment):
            raise NotImplementedError

        def check_source_route(self, snapshot, source, peer):
            raise NotImplementedError

    monkeypatch.setattr("kllm.shared.platform.base.is_admin", lambda: True)
    monkeypatch.setattr(native, "managed_network_available", lambda: True)
    snapshot = AdapterSnapshot(
        "eth-test",
        "eth-test",
        1,
        "a" * 64,
        "02:00:00:00:00:01",
        InterfaceState("eth-test", True, speed, False, (), ()),
        permission=True,
    )
    for candidate in (Candidate(), object.__new__(HelperPlatform)):
        if expected_code:
            with pytest.raises(PlatformRefusal) as error:
                candidate.eligible(snapshot)
            assert error.value.code == expected_code
        else:
            candidate.eligible(snapshot)


@pytest.mark.asyncio
async def test_run_reservation_freezes_inventory_and_blocks_stop(tmp_path, monkeypatch):
    """Admission orchestration with controlled bridge/workload boundaries.

    Real socket source/bytes/drain are checked by the two native cases; this
    focused case checks durable sequencing without configuring host adapters.
    """
    import time
    from types import SimpleNamespace
    from kllm.shared import native
    from kllm.modules.managed_network.interactors.service import ManagedNetworkService
    from kllm.modules.managed_network.interactors.allocation import OperationRequest
    from kllm.modules.managed_network.entities import NodeAssignment, NetworkOperation, RPCChildIdentity, utc_now
    from kllm.shared.platform.base import PlatformRefusal

    state = SimpleNamespace(
        role="master",
        node_id="master",
        members={
            "master": {"host": "192.168.68.1"},
            "worker": {"host": "192.168.68.2", "rpc_port": 50052},
        },
        process_pid=None,
    )
    service = ManagedNetworkService(tmp_path, lambda: state, lambda: None)
    authority = service._authority()
    operation_id = uuid4()
    network = ManagedNetwork(
        network_id=uuid4(),
        cluster_binding_id=authority.cluster_binding_id,
        master_node_id="master",
        master_key_id=service.identity.key_id,
        subnet="192.168.234.0/24",
        current_operation_id=operation_id,
    )
    service.store.put_network(network)
    child = RPCChildIdentity(
        pid=111,
        start_token="child-1",
        bind="192.168.68.2",
        port=50052,
        llama_tag="v0.5.0",
        tcp_only=True,
        compatibility_digest="a" * 64,
    )
    master = NodeAssignment(
        network_id=network.network_id,
        node_id="master",
        adapter_id="adapter",
        os_interface="test",
        identity_fingerprint="b" * 64,
        address="192.168.234.1",
        prefix_length=24,
    )
    worker = NodeAssignment(
        network_id=network.network_id,
        node_id="worker",
        adapter_id="adapter",
        os_interface="test",
        identity_fingerprint="c" * 64,
        address="192.168.234.2",
        prefix_length=24,
        rpc_child_identity=child,
        logical_rpc_endpoint="192.168.234.2:50052",
        launch_rpc_endpoint="127.0.0.1:34567",
    )
    for assignment in (master, worker):
        service.store.put_assignment(assignment)
    for stage in ("applying", "verifying", "ready"):
        service._publish(network, stage)
    operation = NetworkOperation(
        operation_id=operation_id,
        request_id=uuid4(),
        network_id=network.network_id,
        cluster_binding_id=authority.cluster_binding_id,
        action="start",
        master_term=authority.master_term,
        master_session_id=authority.master_session_id,
        expected_network_revision=0,
        operation_revision=1,
        participants=["master", "worker"],
        proposal_digest="d" * 64,
        phase="complete",
        deadline=utc_now() + timedelta(seconds=60),
        terminal_result={"state": "ready"},
    )
    service.store.put_operation(operation)

    class Bridge:
        generation = 0
        instance_id = str(uuid4())

        def grant(self, generation, ttl, renewal):
            self.generation = generation
            assert ttl == 15000

        def release(self, generation):
            return True

    bridge = Bridge()
    monkeypatch.setattr(
        native,
        "network_bridge_status",
        lambda handle: {
            "instance_id": handle.instance_id,
            "listener": "127.0.0.1:34567",
            "generation": handle.generation,
            "closed": False,
            "last_error": None,
        },
    )
    monkeypatch.setattr(
        native,
        "_managed_native",
        lambda: SimpleNamespace(_process_identity=lambda pid: ("master-child", "now")),
    )
    monkeypatch.setattr(native, "process_alive", lambda pid: False)
    service.master_bridges[(str(network.network_id), "worker")] = bridge
    service.verified_results[(str(network.network_id), "worker")] = {
        "bridge": {"instance_id": str(uuid4())}
    }

    async def health(*args):
        pass

    async def probe(*args):
        assert service.store.runs(network.network_id)[0].state == "reserved"
        service.freshness.publish(
            network.network_id,
            "worker",
            {
                "idle_probe_at": time.monotonic(),
                "rpc_released": True,
                "child": child.model_dump(mode="json"),
            },
        )
        return True

    async def phase(op, assignments, stage, bodies, budget):
        return {
            a.node_id: {"grant_ack": True, "drained": True, "status": "configured"}
            for a in assignments
        }

    monkeypatch.setattr(service, "_health", health)

    async def prepare_rpc(*args):
        pass

    monkeypatch.setattr(service, "prepare_rpc", prepare_rpc)
    monkeypatch.setattr(native, "stop_network_bridge", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "verify_idle", probe)
    monkeypatch.setattr(service, "_phase", phase)
    reservation, copied = await service.admission.reserve(str(network.network_id))
    assert copied["worker"]["host"] == "127.0.0.1"
    assert state.members["worker"]["host"] == "192.168.68.2"
    state.process_pid = 222
    await service.admission.finish(reservation, True, None)
    with pytest.raises(PlatformRefusal, match="blocks network changes"):
        await service.begin_operation(
            network.network_id,
            OperationRequest(request_id=uuid4(), expected_revision=network.revision, action="stop"),
        )
    state.members["worker"]["host"] = "192.168.68.99"
    assert service.store.runs(network.network_id)[0].launch_endpoints == ["127.0.0.1:34567"]
    await service.admission.release_stopped()
    assert service.store.runs(network.network_id)[0].state == "released"
    from kllm.modules.inference.entities import RunBody

    body = RunBody(model="model.gguf", network_id=str(network.network_id))
    assert "network_id" not in body.snapshot_for_remember()
    with pytest.raises(PlatformRefusal):
        await service.admission.preview(uuid4())
    service.master_bridges.clear()
    await service.close()


@pytest.mark.asyncio
async def test_local_review_preserves_ambiguous_address_and_requires_current_snapshot(tmp_path):
    from types import SimpleNamespace
    import asyncio
    import ipaddress
    from kllm.modules.managed_network.interactors.identity import LocalIdentity
    from kllm.modules.managed_network.entities import LocalMutationJournal, NodeAssignment
    from kllm.shared.platform.base import AdapterSnapshot
    from kllm.shared.platform.inspection import InterfaceState
    from kllm.modules.managed_network.interactors.recovery import CleanupDecision, resolve, review

    store = NetworkStore(tmp_path)
    identity = LocalIdentity(tmp_path, "local", store)
    authority = store.startup_authority("local", identity.key_id)
    network = ManagedNetwork(
        network_id=uuid4(),
        cluster_binding_id=authority.cluster_binding_id,
        master_node_id="local",
        master_key_id=identity.key_id,
        subnet="192.168.234.0/24",
    )
    store.put_network(network)
    assignment = NodeAssignment(
        network_id=network.network_id,
        node_id="local",
        adapter_id="test",
        os_interface="test",
        identity_fingerprint="a" * 64,
        address="192.168.234.1",
        prefix_length=24,
    )
    store.put_assignment(assignment)
    exact = ipaddress.IPv4Interface("192.168.234.1/24")
    addresses = [exact]
    removed = []

    class Platform:
        def inspect(self, adapter):
            return AdapterSnapshot(
                "test",
                "test",
                1,
                "a" * 64,
                "02:00:00:00:00:01",
                InterfaceState("test", True, 10000, False, tuple(addresses), ()),
            )

        def delete_exact(self, snapshot, selected):
            removed.append(f"{selected.address}/{selected.prefix_length}")
            addresses.remove(exact)

    platform = Platform()
    service = SimpleNamespace(
        store=store,
        identity=identity,
        lock=asyncio.Lock(),
        executor=SimpleNamespace(lock=asyncio.Lock(), platform=platform),
    )
    journal = LocalMutationJournal(
        journal_id=uuid4(),
        network_id=network.network_id,
        operation_id=uuid4(),
        node_id="local",
        adapter_fingerprint="a" * 64,
        exact_address_prefix=str(exact),
        boot_id="old-boot",
        observation_epoch=uuid4(),
        event_high_water=1,
        observer_health="gap",
        pre_snapshot={},
        intended_delta={"add": str(exact)},
        phase="ownership_conflict",
        authority_fence=authority,
    )
    from kllm.modules.managed_network.entities import NetworkOperation, utc_now

    store.put_operation(
        NetworkOperation(
            operation_id=journal.operation_id,
            request_id=uuid4(),
            network_id=network.network_id,
            cluster_binding_id=authority.cluster_binding_id,
            action="start",
            master_term=authority.master_term,
            master_session_id=authority.master_session_id,
            expected_network_revision=0,
            operation_revision=1,
            participants=["local", "worker"],
            proposal_digest="c" * 64,
            phase="failed",
            deadline=utc_now() + timedelta(seconds=60),
        )
    )
    store.journal(journal)
    inspected = await review(service, journal.journal_id)
    with pytest.raises(StoreConflict):
        await resolve(
            service,
            journal.journal_id,
            CleanupDecision(decision="confirm_owned", snapshot_digest="b" * 64),
        )
    assert addresses == [exact] and removed == []
    await resolve(
        service,
        journal.journal_id,
        CleanupDecision(decision="release_ownership", snapshot_digest=inspected["snapshot_digest"]),
    )
    assert addresses == [exact] and removed == []
    assert store.assignments(network.network_id)[0].ownership == "user_owned"
    assert store.journals()[0].phase == "removed_observed"
    # A separate ambiguous delta can be removed only after an explicit local
    # review; its exact deletion preserves every other interface address.
    unrelated = ipaddress.IPv4Interface("192.168.234.99/24")
    addresses.append(unrelated)
    second = journal.model_copy(update={"journal_id": uuid4()})
    store.journal(second)
    inspected = await review(service, second.journal_id)
    await resolve(
        service,
        second.journal_id,
        CleanupDecision(decision="confirm_owned", snapshot_digest=inspected["snapshot_digest"]),
    )
    assert removed == [str(exact)]
    assert addresses == [unrelated]
    from kllm.modules.managed_network.interactors.allocation import ParticipantSelection, assign

    local_inventory = platform.inspect("test").public()
    worker_inventory = AdapterSnapshot(
        "worker-test",
        "worker-test",
        2,
        "d" * 64,
        "02:00:00:00:00:02",
        InterfaceState(
            "worker-test", True, 10000, False, (ipaddress.IPv4Interface("192.168.234.20/24"),), ()
        ),
    ).public()
    inventories = {"local": local_inventory, "worker": worker_inventory}
    selected = [
        ParticipantSelection(
            node_id=node,
            adapter_id=value["adapter_id"],
            inventory_revision=value["inventory_revision"],
        )
        for node, value in inventories.items()
    ]
    proposed = assign(uuid4(), network.subnet, "local", selected, inventories)
    assert {a.address for a in proposed} == {"192.168.234.99", "192.168.234.20"}
    store.close()
