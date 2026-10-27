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

from dataclasses import replace

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
from kllm.modules.primacpp.interactors.placement import topology_from_plan
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
    aliases = set()
    if hostname:
        aliases.add(hostname)
    if host:
        aliases.add(host)
    if is_master:
        aliases.add("master")
        aliases.add("master-1")
    return PlacementNodeInput(
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
        aliases=tuple(aliases),
        hostname=hostname,
        host=host,
        lan_host=host,
    )


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


class TestLiveThreeNodeClusterTopology:
    """Verifies live cluster topology behavior where worker's node_id is literally 'master1'."""

    @pytest.fixture
    def live_cluster(self):
        # 1. Master: 049f6c77 (Windows, Radeon RX 9070 XT, 16GB)
        # 2. Worker 1: master1 (macOS Darwin, M1 Pro, 32GB)
        # 3. Worker 2: a83ac5b6 (macOS Darwin, M5 Pro, 40GB)
        master = _build_placement_node(
            "049f6c77",
            is_master=True,
            gpu_bytes=16 * GIB,
            ram_bytes=32 * GIB,
            hostname="win-master-host",
            host="10.10.10.3",
            backend="vulkan",
        )
        worker_m1 = _build_placement_node(
            "master1",
            is_master=False,
            gpu_bytes=32 * GIB,
            ram_bytes=32 * GIB,
            hostname="Karahan-M1-Pro.local",
            host="10.10.10.2",
            backend="vulkan",
        )
        worker_m5 = _build_placement_node(
            "a83ac5b6",
            is_master=False,
            gpu_bytes=40 * GIB,
            ram_bytes=48 * GIB,
            hostname="LMUK-CJ7G5VHVFC",
            host="10.10.10.1",
            backend="vulkan",
        )
        nodes = [master, worker_m1, worker_m5]
        members = {
            "049f6c77": {
                "node_id": "049f6c77",
                "role": "master",
                "hostname": "win-master-host",
                "host": "10.10.10.3",
                "lan_host": "10.10.10.3",
                "backend": "vulkan",
                "actual_backend": "vulkan",
                "platform": "Windows",
                "devices": [{"device": "GPU0", "backend": "vulkan", "vram_bytes": 16 * GIB}],
            },
            "master1": {
                "node_id": "master1",
                "role": "worker",
                "hostname": "Karahan-M1-Pro.local",
                "host": "10.10.10.2",
                "lan_host": "10.10.10.2",
                "backend": "vulkan",
                "actual_backend": "vulkan",
                "platform": "Darwin",
                "devices": [{"device": "GPU0", "backend": "vulkan", "vram_bytes": 32 * GIB}],
                "rpc_devices": [{"device": "GPU0", "index": 0, "memory_kind": "gpu"}],
            },
            "a83ac5b6": {
                "node_id": "a83ac5b6",
                "role": "worker",
                "hostname": "LMUK-CJ7G5VHVFC",
                "host": "10.10.10.1",
                "lan_host": "10.10.10.1",
                "backend": "vulkan",
                "actual_backend": "vulkan",
                "platform": "Darwin",
                "devices": [{"device": "GPU0", "backend": "vulkan", "vram_bytes": 40 * GIB}],
                "rpc_devices": [{"device": "GPU0", "index": 0, "memory_kind": "gpu"}],
            },
        }
        return nodes, members

    def test_dataclasses_replace_preserves_node_identifiers(self, live_cluster):
        nodes, _ = live_cluster
        worker_m1 = nodes[1]
        assert worker_m1.node_id == "master1"
        assert worker_m1.hostname == "Karahan-M1-Pro.local"
        assert "Karahan-M1-Pro.local" in worker_m1.aliases
        assert worker_m1.host == "10.10.10.2"

        # Calling dataclasses.replace must preserve first-class dataclass fields
        modified = replace(worker_m1, ram_fraction=0.75, vram_fraction=0.85)
        assert modified.node_id == "master1"
        assert modified.hostname == "Karahan-M1-Pro.local"
        assert modified.aliases == worker_m1.aliases
        assert modified.host == "10.10.10.2"
        assert modified.lan_host == "10.10.10.2"
        assert modified.ram_fraction == 0.75
        assert modified.vram_fraction == 0.85

    def test_canonical_resolution_worker_master1_vs_master_role(self, live_cluster):
        nodes, _ = live_cluster

        # 'master1' must resolve to the worker node_id 'master1', NOT the master 049f6c77
        assert resolve_canonical_node_id("master1", nodes) == "master1"

        # 'master' role identifier must resolve to the actual master node
        assert resolve_canonical_node_id("master", nodes) == "049f6c77"
        assert resolve_canonical_node_id("MASTER", nodes) == "049f6c77"

        # Hostname resolution
        assert resolve_canonical_node_id("Karahan-M1-Pro.local", nodes) == "master1"
        assert resolve_canonical_node_id("karahan-m1-pro.local", nodes) == "master1"
        assert resolve_canonical_node_id("LMUK-CJ7G5VHVFC", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("lmuk-cj7g5vhvfc", nodes) == "a83ac5b6"

        # IP address resolution
        assert resolve_canonical_node_id("10.10.10.2", nodes) == "master1"
        assert resolve_canonical_node_id("10.10.10.1", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("10.10.10.3", nodes) == "049f6c77"

        # Prefix matching (>= 6 chars)
        assert resolve_canonical_node_id("049f6c", nodes) == "049f6c77"
        assert resolve_canonical_node_id("049F6C", nodes) == "049f6c77"
        assert resolve_canonical_node_id("a83ac5", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("A83AC5", nodes) == "a83ac5b6"

    def test_threenode_deployment_planning_and_terminal_output_pinning(self, live_cluster):
        nodes, members = live_cluster
        layout = _build_model_layout(layers_count=24, layer_bytes=800 * MIB)
        settings = PlacementSettings(prima_graphs=True, split_mode="layer", flash_attention="auto")

        plan = plan_devices(
            nodes,
            size=20 * GIB,
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
        # All required nodes plus master must be allocated
        assert "049f6c77" in allocated_nodes
        assert "master1" in allocated_nodes
        assert "a83ac5b6" in allocated_nodes

        # Terminal output stage must be pinned to master
        terminal = next(a for a in plan.device_allocations if a.layer_end == 25)
        assert terminal.node_id == "049f6c77"
        assert terminal.layer_start == 24
        assert terminal.layer_end == 25
        assert terminal.layers == 1
        assert terminal.output_layer is True

        # topology_from_plan must succeed without ValueError
        plan_dict = {
            "estimated_layers": plan.estimated_layers,
            "split_mode": plan.split_mode,
            "fit_status": plan.fit_status,
            "cpu_layers": plan.cpu_layers,
            "device_allocations": [
                {
                    "node_id": a.node_id,
                    "layer_start": a.layer_start,
                    "layer_end": a.layer_end,
                    "layers": a.layers,
                    "device": a.device,
                    "rpc_index": a.rpc_index,
                    "local": a.local,
                    "memory_kind": a.memory_kind,
                }
                for a in plan.device_allocations
            ],
        }
        topology = topology_from_plan(plan_dict, members, "a" * 64)
        assert len(topology.ranks) == 3
        assert topology.ranks[0].node_id == "049f6c77"
        assert topology.ranks[0].role == "master"
        assert topology.ranks[0].output_gpu is True

    def test_candidates_generation_with_worker_master1_and_prefixes(self, live_cluster):
        _, members = live_cluster
        body = SimpleNamespace(
            required_nodes=["master1", "a83ac5b6"],
            node_priority=[],
            network_id="",
            model="test.gguf",
        )

        def mock_execute_plan(b, inv):
            # Verify required nodes are not pruned from inventory
            assert "master1" in inv
            assert "a83ac5b6" in inv
            return {
                "fit_status": "fits",
                "device_allocations": [{"node_id": nid} for nid in inv],
                "cpu_layers": 0,
                "ngl": 24,
                "tensor_split_csv": "",
                "tensor_overrides": [],
            }

        cand_results = candidates(body, members, mock_execute_plan)
        assert len(cand_results) >= 1
        # In the first candidate (all members), all three nodes are present
        assert "master1" in cand_results[0][1]
        assert "a83ac5b6" in cand_results[0][1]
        assert "049f6c77" in cand_results[0][1]

    @pytest.mark.asyncio
    async def test_link_profiling_unmanaged_lan_does_not_grab_foreign_diagnostics(self):
        op_id = str(uuid4())
        src_id = "049f6c77"
        tgt_id = "master1"
        binding_id = uuid4()
        raw = bytes(32)
        key_b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        key_id = hashlib.sha256(raw).hexdigest()
        auth = MasterAuthority(
            cluster_binding_id=binding_id,
            master_node_id=src_id,
            master_key_id=key_id,
            master_term=1,
            master_session_id=uuid4(),
        )
        src_node = NodeIdentity(
            node_id=src_id,
            key_id=key_id,
            public_key=key_b64,
            cluster_binding_id=binding_id,
        )
        tgt_node = NodeIdentity(
            node_id=tgt_id,
            key_id=key_id,
            public_key=key_b64,
            cluster_binding_id=binding_id,
        )
        ranks = [
            RankAllocation(
                node_id=src_id,
                rank=0,
                address="10.10.10.3",
                role="master",
                next_node_id=tgt_id,
                ngl=0,
                layers=[LayerRange(start=0, end=10)],
                devices=[],
            ),
            RankAllocation(
                node_id=tgt_id,
                rank=1,
                address="10.10.10.2",
                role="worker",
                next_node_id=src_id,
                ngl=0,
                layers=[LayerRange(start=10, end=20)],
                devices=[],
            ),
        ]
        unmanaged_net_id = uuid4()
        foreign_net_id = uuid4()
        op_uuid = UUID(op_id)
        manifest = ExecutionManifest(
            runtime_id="a" * 64,
            run_id=str(uuid4()),
            plan_digest="b" * 64,
            total_layers=20,
            ranks=ranks,
            settings={},
            network_id="",
            inventory_digest="c" * 64,
            assets=[Asset(name="m.gguf", kind="model", sha256="d" * 64, size=1)],
            segments=[
                Segment(start=w.start, end=w.end, node_id=r.node_id)
                for r in ranks
                for w in r.layers
            ],
            backends={src_id: "cpu", tgt_id: "cpu"},
            transfer_edges=[
                {"source": src_id, "target": tgt_id},
                {"source": tgt_id, "target": src_id},
            ],
        )
        scope = StreamScope(
            probe_id=uuid4(),
            operation_id=op_uuid,
            contract_digest=manifest.digest,
            network_id=unmanaged_net_id,
            authority=auth,
            source=src_node,
            target=tgt_node,
            source_revision=1,
            target_revision=1,
        )

        foreign_diag_called = False

        async def foreign_grant_stream(*args, **kwargs):
            nonlocal foreign_diag_called
            foreign_diag_called = True
            return "foreign_grant"

        foreign_diag = SimpleNamespace(grant_stream=foreign_grant_stream)

        service = SimpleNamespace(
            identity=src_node,
            state=lambda: SimpleNamespace(
                node_id=src_id,
                members={
                    src_id: {"node_id": src_id, "role": "master", "host": "10.10.10.3", "latency_ms": 0.5},
                    tgt_id: {"node_id": tgt_id, "role": "worker", "host": "10.10.10.2", "latency_ms": 1.5},
                },
            ),
            executor=SimpleNamespace(
                diagnostics={str(foreign_net_id): foreign_diag},
                preparation={},
            ),
            store=SimpleNamespace(
                check_authority=lambda a: None,
            ),
            prima_admission=SimpleNamespace(validate_worker=lambda m: None),
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
                }
            },
            resources=SimpleNamespace(acquire=lambda scope, kind: SimpleNamespace(release=lambda: None)),
        )
        owner = {**authority_owner(auth), "sender": src_id}

        val_send = {
            "preparation_job_id": op_id,
            "scope": scope.model_dump(mode="json"),
            "mode": "send",
        }
        res_send = await execute_link(control, val_send, op_id, owner, lambda: True)

        # Foreign diagnostic from unrelated network must NEVER have been called
        assert foreign_diag_called is False
        assert res_send["phase"] == "measured"
        assert res_send["measurement"]["source"] == "10.10.10.3"
        assert res_send["measurement"]["peer"] == "10.10.10.2"
        assert res_send["measurement"]["source_node_id"] == src_id
        assert res_send["measurement"]["target_node_id"] == tgt_id
