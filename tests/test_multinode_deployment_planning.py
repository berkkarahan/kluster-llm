"""Automated regression tests for multi-node deployment planning and networking.

Verifies:
1. Canonical node ID resolution mapping hostname ('master1', 'master', host) to canonical node_id.
2. Deployment planning with required_nodes=['master1', 'a83ac5b6'] succeeds without 409 or ValidationError.
3. 120B model capacity planning with 100k context succeeds with bounded graph visit budgeting and auto flash attention.
4. Unified cluster networking and link profiling over LAN / WireGuard.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import statistics
import threading
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from starlette.exceptions import HTTPException

from kllm.modules.inference.interactors.core_placement import plan_devices, resolve_canonical_node_id
from kllm.modules.inference.interactors.placement_models import (
    ModelLayout,
    PlacementDeviceInput,
    PlacementNodeInput,
    PlacementSettings,
)
from kllm.modules.inference.interactors.run_planning import cluster_nodes
from kllm.modules.inference.interactors.prima_graph_reserve import reserve_graph_visits
from kllm.modules.inference.interactors.memory_estimation import memory_costs
from kllm.modules.managed_network.entities import MasterAuthority, NodeAssignment, NodeIdentity
from kllm.modules.managed_network.interactors.verification import StreamScope
from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.planner import candidates
from kllm.modules.primacpp.interactors.execution import (
    Asset,
    ExecutionManifest,
    Segment,
    authority_owner,
)
from kllm.modules.primacpp.interactors.link_profile import (
    collect_links,
    execute_link,
    _parse_network_uuid,
)
from kllm.shared.exceptions import ValidationError

MIB = 1024**2
GIB = 1024**3


def _build_placement_node(
    nid: str,
    *,
    is_master: bool = False,
    gpu_bytes: int = 24 * GIB,
    ram_bytes: int = 32 * GIB,
    hostname: str | None = None,
    host: str | None = None,
    backend: str = "vulkan",
) -> PlacementNodeInput:
    devices = (
        (
            PlacementDeviceInput(
                device="GPU0",
                backend=backend,
                vram_bytes=gpu_bytes,
                free_bytes=gpu_bytes,
                reclaimable_bytes=0,
                index=0,
            ),
        )
        if gpu_bytes
        else ()
    )
    node = PlacementNodeInput(
        node_id=nid,
        is_master=is_master,
        backend=backend if gpu_bytes else "cpu",
        total_ram_bytes=ram_bytes,
        available_ram_bytes=ram_bytes,
        reclaimable_ram_bytes=0,
        unified_memory=False,
        rpc_endpoint=None if is_master else f"{host or nid}:50052",
        latency_ms=0.5,
        devices=devices,
    )
    aliases = set()
    if hostname:
        aliases.add(hostname)
    if host:
        aliases.add(host)
    if is_master:
        aliases.add("master")
        aliases.add("master1")
        aliases.add("master-1")
    object.__setattr__(node, "aliases", tuple(aliases))
    object.__setattr__(node, "hostname", hostname)
    object.__setattr__(node, "host", host)
    return node


def _build_model_layout(layers_count: int = 80, layer_bytes: int = 700 * MIB, arch: str = "gpt-oss") -> ModelLayout:
    metadata = {
        f"{arch}.embedding_length": 8192,
        f"{arch}.attention.head_count": 64,
        f"{arch}.attention.head_count_kv": 8,
        f"{arch}.context_length": 100000,
    }
    if arch in ("gpt-oss", "gemma2", "gemma3", "granite_swa", "olmo2"):
        metadata[f"{arch}.attention.sliding_window"] = 4096
    return ModelLayout(
        architecture=arch,
        metadata=metadata,
        layers=tuple([layer_bytes] * layers_count),
        output_bytes=200 * MIB,
        input_bytes=100 * MIB,
        tensors=(),
        confidence="metadata",
    )


class TestCanonicalNodeResolution:
    def test_canonical_node_resolution_maps_hostname_and_aliases(self):
        master = _build_placement_node("049f6c77", is_master=True, hostname="master1", host="192.168.1.10")
        worker = _build_placement_node("a83ac5b6", is_master=False, hostname="worker1", host="192.168.1.20")
        nodes = [master, worker]

        # Hostname resolution
        assert resolve_canonical_node_id("master1", nodes) == "049f6c77"
        assert resolve_canonical_node_id("worker1", nodes) == "a83ac5b6"

        # Role resolution
        assert resolve_canonical_node_id("master", nodes) == "049f6c77"

        # IP address resolution
        assert resolve_canonical_node_id("192.168.1.10", nodes) == "049f6c77"
        assert resolve_canonical_node_id("192.168.1.20", nodes) == "a83ac5b6"

        # Direct hex ID resolution
        assert resolve_canonical_node_id("049f6c77", nodes) == "049f6c77"
        assert resolve_canonical_node_id("a83ac5b6", nodes) == "a83ac5b6"

        # Unknown node stays as-is for downstream validation error
        assert resolve_canonical_node_id("unknown_node", nodes) == "unknown_node"

    def test_cluster_nodes_attaches_aliases_from_member_records(self):
        members = {
            "049f6c77": {
                "node_id": "049f6c77",
                "role": "master",
                "hostname": "master1",
                "host": "10.0.0.1",
                "lan_host": "192.168.1.10",
                "total_ram_bytes": 32 * GIB,
                "available_ram_bytes": 32 * GIB,
                "devices": [{"index": 0, "device": "Vulkan0", "backend": "vulkan", "vram_bytes": 16 * GIB}],
            },
            "a83ac5b6": {
                "node_id": "a83ac5b6",
                "role": "worker",
                "hostname": "worker1",
                "host": "10.0.0.2",
                "lan_host": "192.168.1.20",
                "total_ram_bytes": 32 * GIB,
                "available_ram_bytes": 32 * GIB,
                "devices": [{"index": 0, "device": "Vulkan0", "backend": "vulkan", "vram_bytes": 24 * GIB}],
            },
        }
        nodes = cluster_nodes(members)
        assert len(nodes) == 2
        m_node = next(n for n in nodes if n.is_master)
        w_node = next(n for n in nodes if not n.is_master)

        assert "master1" in getattr(m_node, "aliases", ())
        assert "master" in getattr(m_node, "aliases", ())
        assert "192.168.1.10" in getattr(m_node, "aliases", ())

        assert "worker1" in getattr(w_node, "aliases", ())
        assert "192.168.1.20" in getattr(w_node, "aliases", ())


class TestRequiredNodesDeploymentPlanning:
    def test_planning_with_hostname_and_hex_required_nodes_succeeds(self):
        master = _build_placement_node("049f6c77", is_master=True, hostname="master1", gpu_bytes=16 * GIB)
        worker = _build_placement_node("a83ac5b6", is_master=False, hostname="worker1", gpu_bytes=24 * GIB)
        nodes = [master, worker]
        layout = _build_model_layout(layers_count=20, layer_bytes=500 * MIB)
        settings = PlacementSettings(prima_graphs=True, split_mode="layer", flash_attention="auto")

        # Plan requiring 'master1' (hostname) and 'a83ac5b6' (hex ID)
        plan = plan_devices(
            nodes,
            size=10 * GIB,
            layout=layout,
            settings=settings,
            context=4096,
            headroom=0.8,
            device="gpu",
            vram_weight=0.6,
            latency_weight=0.4,
            required_nodes=["master1", "a83ac5b6"],
        )
        assert plan.fit_status in ("fits", "tight")
        allocated_nodes = {a.node_id for a in plan.device_allocations}
        assert "049f6c77" in allocated_nodes
        assert "a83ac5b6" in allocated_nodes

    def test_planning_with_truly_unavailable_required_node_fails_validation(self):
        master = _build_placement_node("049f6c77", is_master=True, hostname="master1")
        worker = _build_placement_node("a83ac5b6", is_master=False, hostname="worker1")
        nodes = [master, worker]
        layout = _build_model_layout(layers_count=10, layer_bytes=200 * MIB)
        settings = PlacementSettings()

        with pytest.raises(ValidationError, match="required nodes are unavailable: offline_node"):
            plan_devices(
                nodes,
                size=2 * GIB,
                layout=layout,
                settings=settings,
                context=2048,
                headroom=0.8,
                device="gpu",
                vram_weight=0.6,
                latency_weight=0.4,
                required_nodes=["master1", "offline_node"],
            )


class TestCandidateOptionPruningAndErrorHandling:
    def test_candidates_prunes_required_nodes_for_subsets_and_catches_http_exception(self):
        members = {
            "049f6c77": {"node_id": "049f6c77", "role": "master", "hostname": "master1"},
            "w1": {"node_id": "w1", "role": "worker", "hostname": "worker1"},
            "w2": {"node_id": "w2", "role": "worker", "hostname": "worker2"},
        }
        body = SimpleNamespace(
            required_nodes=["master1", "w1"],
            node_priority=[],
            network_id="",
            model="toy.gguf",
        )

        def mock_execute_plan(b, inv):
            # If inv omits required node w1 without pruning, this would throw 400
            if "w1" not in inv and "w1" in b.required_nodes:
                raise HTTPException(400, "required nodes are unavailable: w1")
            return {
                "fit_status": "fits",
                "device_allocations": [{"node_id": nid} for nid in inv],
                "cpu_layers": 0,
                "ngl": 10,
                "tensor_split_csv": "",
                "tensor_overrides": [],
            }

        result = candidates(body, members, mock_execute_plan)
        assert len(result) >= 1
        # Main candidate (index 0) includes all members
        assert set(result[0][1].keys()) == {"049f6c77", "w1", "w2"}

    def test_candidates_preserves_workers_required_by_ip_address(self):
        members = {
            "049f6c77": {"node_id": "049f6c77", "role": "master", "hostname": "master1", "host": "192.168.1.10"},
            "w1": {"node_id": "w1", "role": "worker", "hostname": "worker1", "host": "192.168.1.20"},
            "w2": {"node_id": "w2", "role": "worker", "hostname": "worker2", "host": "192.168.1.21"},
        }
        body = SimpleNamespace(
            required_nodes=["192.168.1.20"],
            node_priority=[],
            network_id="",
            model="toy.gguf",
        )
        inv_history = []

        def mock_execute_plan(b, inv):
            inv_history.append(set(inv.keys()))
            return {
                "fit_status": "fits",
                "device_allocations": [{"node_id": nid} for nid in inv],
                "cpu_layers": 0,
                "ngl": 10,
                "tensor_split_csv": "",
                "tensor_overrides": [],
            }

        result = candidates(body, members, mock_execute_plan)
        assert len(result) >= 1
        # w1 must be preserved in all generated candidate inventories because it was required by IP
        for inv in inv_history:
            assert "w1" in inv, f"w1 (required by IP 192.168.1.20) was omitted from candidate {inv}"


class TestLargeModel100kContextCapacityPlanning:
    def test_120b_model_with_100k_context_avoids_graph_overflow(self):
        # Master has 32 GB RAM, 24 GB GPU. Worker has 32 GB RAM, 24 GB GPU.
        # Combined: 48 GB VRAM + 64 GB RAM.
        master = _build_placement_node("049f6c77", is_master=True, hostname="master1", gpu_bytes=24 * GIB, ram_bytes=32 * GIB)
        worker = _build_placement_node("a83ac5b6", is_master=False, hostname="worker1", gpu_bytes=24 * GIB, ram_bytes=32 * GIB)
        nodes = [master, worker]

        layout = _build_model_layout(layers_count=80, layer_bytes=300 * MIB, arch="gpt-oss")
        settings = PlacementSettings(
            prima_graphs=True,
            split_mode="layer",
            flash_attention="auto",
            context=100000,
        )

        # 1. Verify memory_costs enables flash attention for gpt-oss under auto
        costs = memory_costs(layout, 24 * GIB, 100000, settings)
        # Bounded overhead: should not explode to 13+ GB
        assert costs.overhead < 2 * GIB

        # 2. Verify graph reserve calculation has no redundant master RAM charges or pure GPU worker charges
        allocations = (
            SimpleNamespace(node_id="049f6c77", device="GPU0", memory_kind="gpu", layer_start=0, layer_end=40, estimated_bytes=15 * GIB, budget_bytes=20 * GIB),
            SimpleNamespace(node_id="a83ac5b6", device="GPU0", memory_kind="gpu", layer_start=40, layer_end=80, estimated_bytes=15 * GIB, budget_bytes=20 * GIB),
        )
        reserve = reserve_graph_visits(allocations, cpu_layers=0, total_layers=80, master="049f6c77", overhead=costs.overhead)
        # Master host units must be 0 (base overhead already in cpu_base)
        assert reserve.host_extra["049f6c77"] == 0
        # Worker has pure GPU layers; host extra must be 0
        assert reserve.host_extra["a83ac5b6"] == 0

        # 3. Plan devices with flash_attention="auto" produces a feasible plan
        plan = plan_devices(
            nodes,
            size=24 * GIB,
            layout=layout,
            settings=settings,
            context=100000,
            headroom=0.85,
            device="gpu",
            vram_weight=0.6,
            latency_weight=0.4,
            required_nodes=["master1", "a83ac5b6"],
        )
        assert plan.fit_status in ("fits", "tight")
        assert "Prima retained graph visits exceed" not in (plan.hint or "")


class TestUnifiedNetworkingAndProfiling:
    def test_prima_control_members_fallback_to_cluster_state(self):
        state_members = {
            "049f6c77": {"node_id": "049f6c77", "role": "master", "host": "192.168.1.10"},
            "a83ac5b6": {"node_id": "a83ac5b6", "role": "worker", "host": "192.168.1.20"},
        }
        service = SimpleNamespace(
            state=lambda: SimpleNamespace(members=state_members),
            prima_admission=SimpleNamespace(members=lambda net_id: {}),  # Admission returns empty
        )
        control = SimpleNamespace(service=service)

        from kllm.modules.primacpp.interactors.control import PrimaControl
        members_fn = PrimaControl.members.__get__(control)

        # When network_id is empty, returns cluster state members
        body_empty = SimpleNamespace(network_id="")
        assert set(members_fn(body_empty).keys()) == {"049f6c77", "a83ac5b6"}

        # When network_id yields empty admission members, falls back to cluster state members
        body_net = SimpleNamespace(network_id="non-enrolled-net")
        assert set(members_fn(body_net).keys()) == {"049f6c77", "a83ac5b6"}

    def test_collect_links_safely_handles_empty_network_id(self):
        binding_id = uuid4()
        raw = bytes(32)
        key_b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        key_id = hashlib.sha256(raw).hexdigest()
        authority = MasterAuthority(
            cluster_binding_id=binding_id,
            master_node_id="049f6c77",
            master_key_id=key_id,
            master_term=1,
            master_session_id=uuid4(),
        )
        src_node = NodeIdentity(
            node_id="049f6c77",
            key_id=key_id,
            public_key=key_b64,
            cluster_binding_id=binding_id,
        )
        object.__setattr__(src_node, "public", lambda b: src_node)
        tgt_node = NodeIdentity(
            node_id="a83ac5b6",
            key_id=key_id,
            public_key=key_b64,
            cluster_binding_id=binding_id,
        )
        ranks = [
            RankAllocation(
                node_id="049f6c77",
                rank=0,
                address="192.168.1.10",
                role="master",
                next_node_id="a83ac5b6",
                ngl=0,
                layers=[LayerRange(start=0, end=10)],
                devices=[],
            ),
            RankAllocation(
                node_id="a83ac5b6",
                rank=1,
                address="192.168.1.20",
                role="worker",
                next_node_id="049f6c77",
                ngl=0,
                layers=[LayerRange(start=10, end=20)],
                devices=[],
            ),
        ]
        manifest = ExecutionManifest(
            runtime_id="a" * 64,
            run_id=str(uuid4()),
            plan_digest="b" * 64,
            total_layers=20,
            ranks=ranks,
            settings={},
            network_id=None,
            inventory_digest="c" * 64,
            assets=[Asset(name="m.gguf", kind="model", sha256="d" * 64, size=1)],
            segments=[
                Segment(start=w.start, end=w.end, node_id=r.node_id)
                for r in ranks
                for w in r.layers
            ],
            backends={"049f6c77": "cpu", "a83ac5b6": "cpu"},
            transfer_edges=[{"source": "049f6c77", "target": "a83ac5b6"}],
        )
        cluster_members = {
            "049f6c77": {"node_id": "049f6c77", "role": "master", "host": "192.168.1.10", "latency_ms": 0.5},
            "a83ac5b6": {"node_id": "a83ac5b6", "role": "worker", "host": "192.168.1.20", "latency_ms": 0.5},
        }
        service = SimpleNamespace(
            identity=src_node,
            state=lambda: SimpleNamespace(node_id="049f6c77", members=cluster_members),
            channels=SimpleNamespace(master_authority=lambda: authority),
            store=SimpleNamespace(
                assignments=lambda net_id: [],
                peer=lambda nid: tgt_node if nid == "a83ac5b6" else src_node,
                check_authority=lambda auth: None,
            ),
            executor=SimpleNamespace(diagnostics={}),
            admission=SimpleNamespace(call=lambda coro: asyncio.run(coro)),
            prima_admission=SimpleNamespace(validate_worker=lambda m: None),
            resources=SimpleNamespace(acquire=lambda scope, kind: SimpleNamespace(release=lambda: None)),
        )
        op_id = str(uuid4())
        control = SimpleNamespace(
            lock=threading.Lock(),
            _check=lambda jid: None,
            service=service,
            jobs={
                op_id: {
                    "job_id": op_id,
                    "manifest_digest": manifest.digest,
                    "phase": "prepared",
                    "manifest": manifest.model_dump(),
                    "nodes": {"049f6c77": {"job_id": op_id}, "a83ac5b6": {"job_id": "job_worker"}},
                }
            },
            _call=lambda node, op, action, value: {
                "phase": "ready",
                "grant_id": StreamScope.model_validate(value["scope"]).digest,
            },
        )

        assert _parse_network_uuid("") is None
        assert _parse_network_uuid(str(uuid4())) is not None

        result = collect_links(control, manifest, op_id, lambda: None, lambda: None)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["method"] == "signed_private_tcp_application_v1"
        assert result[0]["bandwidth_bytes_s"] == 1048576 / 0.001
        assert result[0]["source"] == "192.168.1.10"
        assert result[0]["peer"] == "192.168.1.20"


@pytest.mark.asyncio
async def test_execute_link_unmanaged_lan_returns_conformant_receipt():
    binding_id = uuid4()
    op_id = str(uuid4())
    raw = bytes(32)
    key_b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    key_id = hashlib.sha256(raw).hexdigest()
    authority = MasterAuthority(
        cluster_binding_id=binding_id,
        master_node_id="049f6c77",
        master_key_id=key_id,
        master_term=1,
        master_session_id=uuid4(),
    )
    src_node = NodeIdentity(
        node_id="049f6c77",
        key_id=key_id,
        public_key=key_b64,
        cluster_binding_id=binding_id,
    )
    tgt_node = NodeIdentity(
        node_id="a83ac5b6",
        key_id=key_id,
        public_key=key_b64,
        cluster_binding_id=binding_id,
    )
    ranks = [
        RankAllocation(
            node_id="049f6c77",
            rank=0,
            address="192.168.1.10",
            role="master",
            next_node_id="a83ac5b6",
            ngl=0,
            layers=[LayerRange(start=0, end=10)],
            devices=[],
        ),
        RankAllocation(
            node_id="a83ac5b6",
            rank=1,
            address="192.168.1.20",
            role="worker",
            next_node_id="049f6c77",
            ngl=0,
            layers=[LayerRange(start=10, end=20)],
            devices=[],
        ),
    ]
    manifest = ExecutionManifest(
        runtime_id="a" * 64,
        run_id=str(uuid4()),
        plan_digest="b" * 64,
        total_layers=20,
        ranks=ranks,
        settings={},
        network_id=None,
        inventory_digest="c" * 64,
        assets=[Asset(name="m.gguf", kind="model", sha256="d" * 64, size=1)],
        segments=[
            Segment(start=w.start, end=w.end, node_id=r.node_id)
            for r in ranks
            for w in r.layers
        ],
        backends={"049f6c77": "cpu", "a83ac5b6": "cpu"},
        transfer_edges=[
            {"source": "049f6c77", "target": "a83ac5b6"},
            {"source": "a83ac5b6", "target": "049f6c77"},
        ],
    )
    scope = StreamScope(
        probe_id=uuid4(),
        operation_id=UUID(op_id),
        contract_digest=manifest.digest,
        network_id=uuid4(),
        authority=authority,
        source=src_node,
        target=tgt_node,
        source_revision=1,
        target_revision=1,
    )
    cluster_members = {
        "049f6c77": {"host": "192.168.1.10", "role": "master"},
        "a83ac5b6": {"host": "192.168.1.20", "role": "worker", "latency_ms": 2.0},
    }
    service = SimpleNamespace(
        identity=src_node,
        state=lambda: SimpleNamespace(node_id="049f6c77", members=cluster_members),
        executor=SimpleNamespace(diagnostics={}),
        prima_admission=SimpleNamespace(validate_worker=lambda m: SimpleNamespace(node_id="049f6c77")),
        store=SimpleNamespace(check_authority=lambda auth: None),
    )
    control = SimpleNamespace(
        lock=threading.Lock(),
        _check=lambda jid: None,
        service=service,
        jobs={
            op_id: {
                "job_id": op_id,
                "manifest_digest": manifest.digest,
                "phase": "prepared",
                "manifest": manifest.model_dump(),
                "nodes": {},
            }
        },
        cached_model=lambda d: SimpleNamespace(read_text=lambda: "{}"),
    )
    owner = {**authority_owner(authority), "sender": "049f6c77"}

    # Receive mode targets 049f6c77 when local is target
    scope_recv = scope.model_copy(update={"target": src_node, "source": tgt_node})
    val_recv = {"preparation_job_id": op_id, "scope": scope_recv.model_dump(mode="json"), "mode": "receive"}
    res_recv = await execute_link(control, val_recv, op_id, owner, lambda: True)
    assert res_recv == {"phase": "ready", "grant_id": scope_recv.digest}

    # Send mode on 049f6c77 (local is source)
    val_send = {"preparation_job_id": op_id, "scope": scope.model_dump(mode="json"), "mode": "send"}
    res_send = await execute_link(control, val_send, op_id, owner, lambda: True)
    assert res_send["phase"] == "measured"
    assert res_send["grant_id"] == scope.digest
    meas = res_send["measurement"]
    assert meas["method"] == "signed_private_tcp_application_v1"
    assert meas["source_node_id"] == "049f6c77"
    assert meas["target_node_id"] == "a83ac5b6"
    assert meas["source"] == "192.168.1.10"
    assert meas["peer"] == "192.168.1.20"
    assert meas["samples"] == 3
    assert len(meas["sample_seconds"]) == 3
    assert meas["bandwidth_bytes_s"] == 1048576 / statistics.median(meas["sample_seconds"])
    assert isinstance(meas["measured_at"], float)


@pytest.mark.asyncio
async def test_execute_link_managed_network_delegates_to_diagnostic():
    binding_id = uuid4()
    op_id = str(uuid4())
    net_id = uuid4()
    raw = bytes(32)
    key_b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    key_id = hashlib.sha256(raw).hexdigest()
    authority = MasterAuthority(
        cluster_binding_id=binding_id,
        master_node_id="049f6c77",
        master_key_id=key_id,
        master_term=1,
        master_session_id=uuid4(),
    )
    src_node = NodeIdentity(
        node_id="049f6c77",
        key_id=key_id,
        public_key=key_b64,
        cluster_binding_id=binding_id,
    )
    tgt_node = NodeIdentity(
        node_id="a83ac5b6",
        key_id=key_id,
        public_key=key_b64,
        cluster_binding_id=binding_id,
    )
    ranks = [
        RankAllocation(
            node_id="049f6c77",
            rank=0,
            address="192.168.1.10",
            role="master",
            next_node_id="a83ac5b6",
            ngl=0,
            layers=[LayerRange(start=0, end=10)],
            devices=[],
        ),
        RankAllocation(
            node_id="a83ac5b6",
            rank=1,
            address="192.168.1.20",
            role="worker",
            next_node_id="049f6c77",
            ngl=0,
            layers=[LayerRange(start=10, end=20)],
            devices=[],
        ),
    ]
    manifest = ExecutionManifest(
        runtime_id="a" * 64,
        run_id=str(uuid4()),
        plan_digest="b" * 64,
        total_layers=20,
        ranks=ranks,
        settings={},
        network_id=str(net_id),
        inventory_digest="c" * 64,
        assets=[Asset(name="m.gguf", kind="model", sha256="d" * 64, size=1)],
        segments=[
            Segment(start=w.start, end=w.end, node_id=r.node_id)
            for r in ranks
            for w in r.layers
        ],
        backends={"049f6c77": "cpu", "a83ac5b6": "cpu"},
        transfer_edges=[
            {"source": "049f6c77", "target": "a83ac5b6"},
            {"source": "a83ac5b6", "target": "049f6c77"},
        ],
    )
    scope = StreamScope(
        probe_id=uuid4(),
        operation_id=UUID(op_id),
        contract_digest=manifest.digest,
        network_id=net_id,
        authority=authority,
        source=src_node,
        target=tgt_node,
        source_revision=1,
        target_revision=1,
    )

    async def mock_grant_stream(s, c, release):
        return "grant_123"

    mock_diag = SimpleNamespace(grant_stream=mock_grant_stream)
    a1 = NodeAssignment(
        network_id=net_id,
        node_id="049f6c77",
        address="192.168.1.10",
        prefix_length=24,
        assignment_revision=1,
        identity_fingerprint="0" * 64,
        adapter_id="lan",
        os_interface="lan",
    )
    a2 = NodeAssignment(
        network_id=net_id,
        node_id="a83ac5b6",
        address="192.168.1.20",
        prefix_length=24,
        assignment_revision=1,
        identity_fingerprint="0" * 64,
        adapter_id="lan",
        os_interface="lan",
    )
    service = SimpleNamespace(
        identity=src_node,
        state=lambda: SimpleNamespace(node_id="049f6c77", members={}),
        executor=SimpleNamespace(
            diagnostics={str(net_id): mock_diag},
            preparation={str(net_id): SimpleNamespace(diagnostic_port=50055, participants=[a1, a2])},
            prepared={str(net_id): SimpleNamespace()},
            platform=SimpleNamespace(
                observe=lambda p: SimpleNamespace(
                    state=SimpleNamespace(
                        addresses=[ipaddress.IPv4Interface("192.168.1.10/24")]
                    )
                ),
                eligible=lambda s: None,
                check_source_route=lambda s, src, exp: None,
            ),
        ),
        prima_admission=SimpleNamespace(validate_worker=lambda m: a1),
        store=SimpleNamespace(
            check_authority=lambda auth: None,
            peer=lambda nid: tgt_node if nid == "a83ac5b6" else src_node,
        ),
    )
    control = SimpleNamespace(
        lock=threading.Lock(),
        _check=lambda jid: None,
        service=service,
        jobs={
            op_id: {
                "job_id": op_id,
                "manifest_digest": manifest.digest,
                "phase": "prepared",
                "manifest": manifest.model_dump(),
                "nodes": {},
            }
        },
        resources=SimpleNamespace(acquire=lambda scope, kind: SimpleNamespace(release=lambda: None)),
    )
    owner = {**authority_owner(authority), "sender": "049f6c77"}
    scope_recv = scope.model_copy(update={"target": src_node, "source": tgt_node})
    val_recv = {"preparation_job_id": op_id, "scope": scope_recv.model_dump(mode="json"), "mode": "receive"}
    res_recv = await execute_link(control, val_recv, op_id, owner, lambda: True)
    assert res_recv == {"phase": "ready", "grant_id": "grant_123"}
