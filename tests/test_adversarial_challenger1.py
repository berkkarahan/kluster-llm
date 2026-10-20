"""Adversarial stress-test suite for Challenger 1.

Stress-tests:
1. Node identifier variations:
   - Hostname matching (exact, mixed case, fqdn)
   - Network host / IP matching (LAN, WireGuard)
   - Role alias matching ('master', 'master1', 'master-1', 'MASTER')
   - Mixed sets (role, hex, hostname, IP)
   - Truly unavailable nodes (confirm ValidationError is raised with exact missing node names)
   - Edge cases: duplicates, hex prefixes, case insensitivity, empty/whitespace IDs
2. Candidate subset generation and execution in planner.py:
   - Multiple candidate subsets omitting optional nodes while preserving required nodes
   - Exception handling: catching ValueError and HTTPException for candidate subsets
   - Re-raising exceptions on index 0 (full cluster)
   - Pruning required_nodes per candidate subset
   - Edge case analysis of IP matching in planner.candidates()
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from kllm.modules.inference.interactors.core_placement import (
    plan_devices,
    resolve_canonical_node_id,
)
from kllm.modules.inference.interactors.placement_models import (
    ModelLayout,
    PlacementDeviceInput,
    PlacementNodeInput,
    PlacementSettings,
)
from kllm.modules.inference.interactors.run_planning import cluster_nodes
from kllm.modules.primacpp.interactors.planner import candidates
from kllm.shared.exceptions import ValidationError

MIB = 1024**2
GIB = 1024**3


def _make_node(
    nid: str,
    *,
    is_master: bool = False,
    hostname: str | None = None,
    host: str | None = None,
    lan_host: str | None = None,
    gpu_bytes: int = 16 * GIB,
    ram_bytes: int = 32 * GIB,
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
    aliases: set[str] = set()
    if hostname:
        aliases.add(hostname)
    if host:
        aliases.add(host)
    if lan_host:
        aliases.add(lan_host)
    if is_master:
        aliases.add("master")
        aliases.add("master1")
        aliases.add("master-1")
    object.__setattr__(node, "aliases", tuple(aliases))
    object.__setattr__(node, "hostname", hostname)
    object.__setattr__(node, "host", host)
    object.__setattr__(node, "lan_host", lan_host)
    return node


def _make_layout(layers: int = 24, layer_bytes: int = 400 * MIB) -> ModelLayout:
    metadata = {
        "gpt-oss.embedding_length": 4096,
        "gpt-oss.attention.head_count": 32,
        "gpt-oss.attention.head_count_kv": 8,
        "gpt-oss.context_length": 8192,
        "gpt-oss.attention.sliding_window": 4096,
    }
    return ModelLayout(
        architecture="gpt-oss",
        metadata=metadata,
        layers=tuple([layer_bytes] * layers),
        output_bytes=100 * MIB,
        input_bytes=50 * MIB,
        tensors=(),
        confidence="metadata",
    )


class TestAdversarialNodeIdentifierVariations:
    """Stress-test node identifier variations against resolve_canonical_node_id and plan_devices."""

    @pytest.fixture
    def cluster_setup(self):
        master = _make_node(
            "049f6c77",
            is_master=True,
            hostname="master-srv",
            host="10.0.0.1",
            lan_host="192.168.1.10",
        )
        worker1 = _make_node(
            "a83ac5b6",
            is_master=False,
            hostname="worker-gpu-1",
            host="10.0.0.2",
            lan_host="192.168.1.20",
        )
        worker2 = _make_node(
            "b72df109",
            is_master=False,
            hostname="worker-gpu-2",
            host="10.0.0.3",
            lan_host="192.168.1.21",
        )
        nodes = [master, worker1, worker2]
        layout = _make_layout(layers=30)
        settings = PlacementSettings(prima_graphs=True, split_mode="layer", flash_attention="auto")
        return nodes, layout, settings

    def test_hostname_matching_exact_and_special_names(self, cluster_setup):
        nodes, layout, settings = cluster_setup
        assert resolve_canonical_node_id("master-srv", nodes) == "049f6c77"
        assert resolve_canonical_node_id("worker-gpu-1", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("worker-gpu-2", nodes) == "b72df109"

        plan = plan_devices(
            nodes,
            size=6 * GIB,
            layout=layout,
            settings=settings,
            context=2048,
            headroom=0.8,
            device="gpu",
            vram_weight=0.6,
            latency_weight=0.4,
            required_nodes=["worker-gpu-1", "worker-gpu-2"],
        )
        alloc_ids = {a.node_id for a in plan.device_allocations}
        assert "a83ac5b6" in alloc_ids
        assert "b72df109" in alloc_ids

    def test_network_host_ip_matching_lan_and_wireguard(self, cluster_setup):
        nodes, layout, settings = cluster_setup
        # LAN IPs
        assert resolve_canonical_node_id("192.168.1.10", nodes) == "049f6c77"
        assert resolve_canonical_node_id("192.168.1.20", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("192.168.1.21", nodes) == "b72df109"

        # WireGuard / Host IPs
        assert resolve_canonical_node_id("10.0.0.1", nodes) == "049f6c77"
        assert resolve_canonical_node_id("10.0.0.2", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("10.0.0.3", nodes) == "b72df109"

        plan = plan_devices(
            nodes,
            size=6 * GIB,
            layout=layout,
            settings=settings,
            context=2048,
            headroom=0.8,
            device="gpu",
            vram_weight=0.6,
            latency_weight=0.4,
            required_nodes=["192.168.1.20", "10.0.0.3"],
        )
        alloc_ids = {a.node_id for a in plan.device_allocations}
        assert "a83ac5b6" in alloc_ids
        assert "b72df109" in alloc_ids

    def test_role_alias_matching_variations(self, cluster_setup):
        nodes, layout, settings = cluster_setup
        for alias in ("master", "master1", "master-1", "MASTER", "Master", "Master1", "MASTER-1"):
            assert resolve_canonical_node_id(alias, nodes) == "049f6c77"

        plan = plan_devices(
            nodes,
            size=6 * GIB,
            layout=layout,
            settings=settings,
            context=2048,
            headroom=0.8,
            device="gpu",
            vram_weight=0.6,
            latency_weight=0.4,
            required_nodes=["master"],
        )
        alloc_ids = {a.node_id for a in plan.device_allocations}
        assert "049f6c77" in alloc_ids

    def test_mixed_identifier_set(self, cluster_setup):
        nodes, layout, settings = cluster_setup
        # Mixed: role 'master', hex ID 'a83ac5b6', hostname 'worker-gpu-2'
        plan = plan_devices(
            nodes,
            size=8 * GIB,
            layout=layout,
            settings=settings,
            context=2048,
            headroom=0.8,
            device="gpu",
            vram_weight=0.6,
            latency_weight=0.4,
            required_nodes=["master", "a83ac5b6", "worker-gpu-2"],
        )
        alloc_ids = {a.node_id for a in plan.device_allocations}
        assert alloc_ids == {"049f6c77", "a83ac5b6", "b72df109"}

    def test_truly_unavailable_nodes_raise_validation_error(self, cluster_setup):
        nodes, layout, settings = cluster_setup

        # Single unknown node
        with pytest.raises(ValidationError, match="required nodes are unavailable: offline_worker"):
            plan_devices(
                nodes,
                size=4 * GIB,
                layout=layout,
                settings=settings,
                context=2048,
                headroom=0.8,
                device="gpu",
                vram_weight=0.6,
                latency_weight=0.4,
                required_nodes=["offline_worker"],
            )

        # Multiple unknown nodes sorted in error
        with pytest.raises(
            ValidationError,
            match="required nodes are unavailable: deadbeef, offline_node_1, offline_node_2",
        ):
            plan_devices(
                nodes,
                size=4 * GIB,
                layout=layout,
                settings=settings,
                context=2048,
                headroom=0.8,
                device="gpu",
                vram_weight=0.6,
                latency_weight=0.4,
                required_nodes=["master", "offline_node_2", "deadbeef", "offline_node_1"],
            )

        # Truly unavailable in node_priority
        with pytest.raises(ValidationError, match="priority nodes are unavailable: phantom_node"):
            plan_devices(
                nodes,
                size=4 * GIB,
                layout=layout,
                settings=settings,
                context=2048,
                headroom=0.8,
                device="gpu",
                vram_weight=0.6,
                latency_weight=0.4,
                node_priority=["phantom_node"],
            )

    def test_hex_prefix_and_casing_variations(self, cluster_setup):
        nodes, layout, settings = cluster_setup
        # 6-char prefix matches
        assert resolve_canonical_node_id("049f6c", nodes) == "049f6c77"
        assert resolve_canonical_node_id("a83ac5", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("b72df1", nodes) == "b72df109"

        # Case insensitivity for hex IDs
        assert resolve_canonical_node_id("049F6C77", nodes) == "049f6c77"
        assert resolve_canonical_node_id("A83AC5B6", nodes) == "a83ac5b6"

    def test_duplicate_identifiers_resolution(self, cluster_setup):
        nodes, layout, settings = cluster_setup
        # Multiple aliases referring to the exact same master node
        plan = plan_devices(
            nodes,
            size=4 * GIB,
            layout=layout,
            settings=settings,
            context=2048,
            headroom=0.8,
            device="gpu",
            vram_weight=0.6,
            latency_weight=0.4,
            required_nodes=["master", "master-srv", "192.168.1.10", "049f6c77"],
        )
        assert plan.fit_status in ("fits", "tight")

        # Duplicate resolved node IDs in node_priority MUST fail
        with pytest.raises(
            ValidationError, match="node_priority must contain unique nonempty node IDs"
        ):
            plan_devices(
                nodes,
                size=4 * GIB,
                layout=layout,
                settings=settings,
                context=2048,
                headroom=0.8,
                device="gpu",
                vram_weight=0.6,
                latency_weight=0.4,
                node_priority=["master", "master1"],
            )


class TestAdversarialCandidateSubsetGeneration:
    """Stress-test candidate subset generation and execution in planner.py."""

    @pytest.fixture
    def four_node_cluster(self):
        members = {
            "049f6c77": {
                "node_id": "049f6c77",
                "role": "master",
                "hostname": "master1",
                "host": "192.168.1.10",
            },
            "w1": {
                "node_id": "w1",
                "role": "worker",
                "hostname": "worker-gpu-1",
                "host": "192.168.1.20",
            },
            "w2": {
                "node_id": "w2",
                "role": "worker",
                "hostname": "worker-gpu-2",
                "host": "192.168.1.21",
            },
            "w3": {
                "node_id": "w3",
                "role": "worker",
                "hostname": "worker-gpu-3",
                "host": "192.168.1.22",
            },
        }
        return members

    def test_candidates_prunes_optional_nodes_while_preserving_required(self, four_node_cluster):
        members = four_node_cluster

        class TestBody(BaseModel):
            required_nodes: list[str] = ["worker-gpu-1"]
            node_priority: list[str] = []
            network_id: str = ""

        body = TestBody()

        inv_history = []

        def mock_execute(b, inv):
            inv_history.append((set(inv.keys()), list(b.required_nodes)))
            return {
                "fit_status": "fits",
                "device_allocations": [{"node_id": nid} for nid in inv],
                "cpu_layers": 0,
                "ngl": 10,
                "tensor_split_csv": "",
                "tensor_overrides": [],
            }

        res = candidates(body, members, mock_execute)
        # Expected subsets:
        # Full: {049f6c77, w1, w2, w3}
        # Removing 1: {049f6c77, w1, w3}, {049f6c77, w1, w2}
        # Removing 2: {049f6c77, w1}
        # Total = 4 subsets, all preserving master and w1 (worker-gpu-1)
        assert len(res) == 4
        for inv, req in inv_history:
            assert "049f6c77" in inv, "Master must never be omitted"
            assert "w1" in inv, "Required worker w1 must never be omitted"
            assert "worker-gpu-1" in req, "Pruned required nodes must preserve worker-gpu-1"

    def test_candidates_handles_http_exception_and_value_error_in_subsets(self, four_node_cluster):
        members = four_node_cluster

        class TestBody(BaseModel):
            required_nodes: list[str] = ["worker-gpu-1"]
            node_priority: list[str] = []
            network_id: str = ""

        body = TestBody()

        call_count = 0

        def failing_subset_execute(b, inv):
            nonlocal call_count
            call_count += 1
            if len(inv) == 4:
                # Full cluster succeeds
                return {
                    "fit_status": "fits",
                    "device_allocations": [{"node_id": nid} for nid in inv],
                    "cpu_layers": 0,
                    "ngl": 10,
                    "tensor_split_csv": "",
                    "tensor_overrides": [],
                }
            elif len(inv) == 3:
                # 3-node subset raises HTTPException
                raise HTTPException(400, "Device memory overflow on subset")
            else:
                # 2-node subset raises ValueError
                raise ValueError("Insufficient layers on subset")

        res = candidates(body, members, failing_subset_execute)
        # Should gracefully catch the HTTPException and ValueError on candidate subsets
        # and return only the 1 feasible plan (the full cluster)
        assert len(res) == 1
        assert set(res[0][1].keys()) == {"049f6c77", "w1", "w2", "w3"}

    def test_candidates_reraises_exception_on_index_zero(self, four_node_cluster):
        members = four_node_cluster

        class TestBody(BaseModel):
            required_nodes: list[str] = ["offline_node"]
            node_priority: list[str] = []
            network_id: str = ""

        body = TestBody()

        def fail_index_zero(b, inv):
            raise HTTPException(400, "required nodes are unavailable: offline_node")

        # Must NOT swallow index 0 exception
        with pytest.raises(
            HTTPException, match="required nodes are unavailable: offline_node"
        ) as exc_info:
            candidates(body, members, fail_index_zero)
        assert exc_info.value.status_code == 400

    def test_candidates_no_feasible_plan_raises_value_error(self, four_node_cluster):
        members = four_node_cluster

        class TestBody(BaseModel):
            required_nodes: list[str] = []
            node_priority: list[str] = []
            network_id: str = ""

        body = TestBody()

        def all_overflow(b, inv):
            return {
                "fit_status": "overflow",
                "hint": "Prima retained graph visits exceed device capacity",
            }

        with pytest.raises(ValueError, match="No feasible Kluster capacity plan"):
            candidates(body, members, all_overflow)

    def test_candidates_ip_matching_gap_analysis(self, four_node_cluster):
        """Adversarial stress-test demonstrating the behavior when required_nodes contains an IP."""
        members = four_node_cluster

        class TestBody(BaseModel):
            # w1 has host: "192.168.1.20"
            required_nodes: list[str] = ["192.168.1.20"]
            node_priority: list[str] = []
            network_id: str = ""

        body = TestBody()
        inv_history = []

        def mock_execute(b, inv):
            inv_history.append((set(inv.keys()), list(b.required_nodes)))
            return {
                "fit_status": "fits",
                "device_allocations": [{"node_id": nid} for nid in inv],
                "cpu_layers": 0,
                "ngl": 10,
                "tensor_split_csv": "",
                "tensor_overrides": [],
            }

        _ = candidates(body, members, mock_execute)
        # Remediated by Worker 3: planner.py lines 49-65 now checks host and lan_host.
        # w1 is preserved in 100% of candidate subsets and never treated as optional.
        omitted_w1_subsets = [inv for inv, _ in inv_history if "w1" not in inv]
        # Empirical verification that IP-required worker is preserved in all candidate subsets
        assert len(omitted_w1_subsets) == 0, f"planner.candidates omitted IP-required worker in {len(omitted_w1_subsets)} subsets"

    def test_candidates_boundary_limits_and_network_id(self, four_node_cluster):
        members = four_node_cluster

        class TestBody(BaseModel):
            required_nodes: list[str] = []
            node_priority: list[str] = []
            network_id: str = "managed-net-uuid"

        body = TestBody()

        # Invalid limit <= 0 raises ValueError
        with pytest.raises(ValueError, match="limit must be positive"):
            candidates(body, members, lambda b, inv: {}, limit=0)

        with pytest.raises(ValueError, match="limit must be positive"):
            candidates(body, members, lambda b, inv: {}, limit=-5)

        # limit=1 returns only the full cluster (index 0)
        def dummy_exec(b, inv):
            return {
                "fit_status": "fits",
                "device_allocations": [{"node_id": nid} for nid in inv],
                "cpu_layers": 0,
                "ngl": 10,
                "tensor_split_csv": "",
                "tensor_overrides": [],
            }

        res_limit_1 = candidates(body, members, dummy_exec, limit=1)
        assert len(res_limit_1) == 1
        assert set(res_limit_1[0][1].keys()) == {"049f6c77", "w1", "w2", "w3"}

        # When network_id is set, it breaks before removing all native workers
        res_managed = candidates(body, members, dummy_exec, limit=32)
        for _, inv in res_managed:
            # At least one worker must remain
            workers = [m for m in inv.values() if m.get("role") == "worker"]
            assert len(workers) >= 1, "Managed network candidate must keep at least one native worker"


class TestAdversarialClusterNodesAndExecutePlan:
    """Stress-test cluster_nodes mapping and execute_plan resolution."""

    def test_cluster_nodes_handles_sparse_and_corrupt_members(self):
        members = {
            "m1": {
                "node_id": "049f6c77",
                "role": "master",
                # missing hostname, host, lan_host
            },
            "w1": {
                "node_id": "a83ac5b6",
                "role": "worker",
                "hostname": "gpu-box",
                # missing host
                "lan_host": "192.168.1.50",
            },
            "corrupt": {
                # missing node_id! cluster_nodes catches KeyError
            },
        }

        # Corrupt node without node_id raises KeyError during cluster_nodes extraction
        with pytest.raises(KeyError):
            cluster_nodes(members)

        # Valid sparse members
        valid_members = {k: v for k, v in members.items() if "node_id" in v}
        nodes = cluster_nodes(valid_members)
        assert len(nodes) == 2
        m_node = next(n for n in nodes if n.is_master)
        w_node = next(n for n in nodes if not n.is_master)

        assert "master" in getattr(m_node, "aliases", ())
        assert getattr(m_node, "hostname", None) is None

        assert getattr(w_node, "hostname") == "gpu-box"
        assert getattr(w_node, "lan_host") == "192.168.1.50"
        assert "192.168.1.50" in getattr(w_node, "aliases", ())


class TestAdversarialRound2DynamicNodeResolutionAndIPRetention:
    """Round 2 Adversarial Challenge Suite: Stress-testing dynamic node resolution and IP candidate preservation."""

    @pytest.mark.parametrize(
        "master_name",
        ["MasterAlpha", "host-01", "node-master", "kllm-srv-01"],
    )
    def test_arbitrary_master_hostnames_and_case_insensitive_resolution(self, master_name):
        """Stress-test arbitrary master hostnames and case-insensitivity without magic strings."""
        members = {
            "049f6c77": {
                "node_id": "049f6c77",
                "role": "master",
                "hostname": master_name,
                "host": "192.168.1.10",
                "lan_host": "10.0.0.1",
                "total_ram_bytes": 32 * GIB,
                "available_ram_bytes": 32 * GIB,
                "devices": [{"index": 0, "device": "Vulkan0", "backend": "vulkan", "vram_bytes": 16 * GIB}],
            },
            "a83ac5b6": {
                "node_id": "a83ac5b6",
                "role": "worker",
                "hostname": "WorkerGPU1",
                "host": "192.168.1.20",
                "lan_host": "10.0.0.5",
                "total_ram_bytes": 32 * GIB,
                "available_ram_bytes": 32 * GIB,
                "devices": [{"index": 0, "device": "Vulkan0", "backend": "vulkan", "vram_bytes": 24 * GIB}],
            },
        }
        nodes = cluster_nodes(members)

        # 1. Exact arbitrary hostname
        assert resolve_canonical_node_id(master_name, nodes) == "049f6c77"

        # 2. Lowercase and uppercase arbitrary hostname
        assert resolve_canonical_node_id(master_name.lower(), nodes) == "049f6c77"
        assert resolve_canonical_node_id(master_name.upper(), nodes) == "049f6c77"

        # 3. Dynamic role resolution ('master', 'MASTER', 'Master')
        assert resolve_canonical_node_id("master", nodes) == "049f6c77"
        assert resolve_canonical_node_id("MASTER", nodes) == "049f6c77"
        assert resolve_canonical_node_id("Master", nodes) == "049f6c77"

        # 4. Master network host and lan_host IPs
        assert resolve_canonical_node_id("192.168.1.10", nodes) == "049f6c77"
        assert resolve_canonical_node_id("10.0.0.1", nodes) == "049f6c77"

    def test_case_insensitive_hostname_and_alias_matching_worker(self):
        """Stress-test case-insensitive matching for worker hostnames, hex IDs, and IPs."""
        members = {
            "049f6c77": {
                "node_id": "049f6c77",
                "role": "master",
                "hostname": "MasterAlpha",
                "host": "192.168.1.10",
            },
            "a83ac5b6": {
                "node_id": "a83ac5b6",
                "role": "worker",
                "hostname": "WorkerGPU1",
                "host": "192.168.1.20",
                "lan_host": "10.0.0.5",
            },
        }
        nodes = cluster_nodes(members)

        # Worker hostname case variations
        assert resolve_canonical_node_id("WorkerGPU1", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("workergpu1", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("WORKERGPU1", nodes) == "a83ac5b6"

        # Worker IPs
        assert resolve_canonical_node_id("192.168.1.20", nodes) == "a83ac5b6"
        assert resolve_canonical_node_id("10.0.0.5", nodes) == "a83ac5b6"

        # Uppercase hex ID
        assert resolve_canonical_node_id("A83AC5B6", nodes) == "a83ac5b6"

    def test_genuinely_offline_nodes_strictly_raise_validation_error(self):
        """Verify that genuinely offline nodes strictly raise ValidationError in plan_devices."""
        members = {
            "049f6c77": {
                "node_id": "049f6c77",
                "role": "master",
                "hostname": "MasterAlpha",
                "host": "192.168.1.10",
                "total_ram_bytes": 32 * GIB,
                "available_ram_bytes": 32 * GIB,
                "devices": [{"index": 0, "device": "Vulkan0", "backend": "vulkan", "vram_bytes": 16 * GIB}],
            },
            "a83ac5b6": {
                "node_id": "a83ac5b6",
                "role": "worker",
                "hostname": "worker1",
                "host": "192.168.1.20",
                "total_ram_bytes": 32 * GIB,
                "available_ram_bytes": 32 * GIB,
                "devices": [{"index": 0, "device": "Vulkan0", "backend": "vulkan", "vram_bytes": 24 * GIB}],
            },
        }
        nodes = cluster_nodes(members)
        layout = _make_layout(layers=12, layer_bytes=200 * MIB)
        settings = PlacementSettings()

        # Offline hostname
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
                required_nodes=["MasterAlpha", "offline_node"],
            )

        # Offline IP
        with pytest.raises(ValidationError, match=r"required nodes are unavailable: 192\.168\.99\.99"):
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
                required_nodes=["192.168.99.99"],
            )

        # Offline priority node
        with pytest.raises(ValidationError, match="priority nodes are unavailable: phantom_node"):
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
                node_priority=["phantom_node"],
            )

    def test_stress_ip_candidate_preservation_multi_worker(self):
        """Stress-test candidates() in planner.py ensuring IP-required workers are preserved in 100% of subsets."""
        members = {
            "m": {"node_id": "049f6c77", "role": "master", "hostname": "MasterAlpha", "host": "192.168.1.10", "lan_host": "10.0.0.1"},
            "w1": {"node_id": "w1_id", "role": "worker", "hostname": "gpu-node-1", "host": "192.168.1.20", "lan_host": "10.0.0.2"},
            "w2": {"node_id": "w2_id", "role": "worker", "hostname": "gpu-node-2", "host": "192.168.1.21", "lan_host": "10.0.0.3"},
            "w3": {"node_id": "w3_id", "role": "worker", "hostname": "gpu-node-3", "host": "192.168.1.22", "lan_host": "10.0.0.4"},
            "w4": {"node_id": "w4_id", "role": "worker", "hostname": "gpu-node-4", "host": "192.168.1.23", "lan_host": "10.0.0.5"},
        }

        # Scenario 1: Require w1 by host IP '192.168.1.20'
        class BodyA(BaseModel):
            required_nodes: list[str] = ["192.168.1.20"]
            node_priority: list[str] = []
            network_id: str = ""

        inv_a = []
        candidates(BodyA(), members, lambda b, inv: inv_a.append((set(inv.keys()), list(b.required_nodes))) or {"fit_status": "fits"})
        assert len(inv_a) == 8  # 3 optional workers -> 2^3 subsets
        for inv, req in inv_a:
            assert "w1" in inv, f"w1 was omitted in candidate {inv}"
            assert "192.168.1.20" in req, f"192.168.1.20 was pruned in candidate {req}"

        # Scenario 2: Require w4 by lan_host IP '10.0.0.5'
        class BodyB(BaseModel):
            required_nodes: list[str] = ["10.0.0.5"]
            node_priority: list[str] = []
            network_id: str = ""

        inv_b = []
        candidates(BodyB(), members, lambda b, inv: inv_b.append((set(inv.keys()), list(b.required_nodes))) or {"fit_status": "fits"})
        assert len(inv_b) == 8
        for inv, req in inv_b:
            assert "w4" in inv, f"w4 was omitted in candidate {inv}"
            assert "10.0.0.5" in req, f"10.0.0.5 was pruned in candidate {req}"

        # Scenario 3: Require multiple workers by mixed IPs ('192.168.1.20' and '10.0.0.4')
        class BodyC(BaseModel):
            required_nodes: list[str] = ["192.168.1.20", "10.0.0.4"]
            node_priority: list[str] = []
            network_id: str = ""

        inv_c = []
        candidates(BodyC(), members, lambda b, inv: inv_c.append((set(inv.keys()), list(b.required_nodes))) or {"fit_status": "fits"})
        assert len(inv_c) == 4  # 2 optional workers -> 2^2 subsets
        for inv, req in inv_c:
            assert "w1" in inv, f"w1 omitted in {inv}"
            assert "w3" in inv, f"w3 omitted in {inv}"
            assert "192.168.1.20" in req
            assert "10.0.0.4" in req

        # Scenario 4: All workers required by IP -> no pruning possible
        class BodyD(BaseModel):
            required_nodes: list[str] = ["192.168.1.20", "192.168.1.21", "192.168.1.22", "10.0.0.5"]
            node_priority: list[str] = []
            network_id: str = ""

        inv_d = []
        candidates(BodyD(), members, lambda b, inv: inv_d.append((set(inv.keys()), list(b.required_nodes))) or {"fit_status": "fits"})
        assert len(inv_d) == 1
        assert "w1" in inv_d[0][0] and "w2" in inv_d[0][0] and "w3" in inv_d[0][0] and "w4" in inv_d[0][0]

    def test_arbitrary_master_deployment_planning_end_to_end(self):
        """End-to-end plan_devices execution with arbitrary master hostname and worker IP."""
        members = {
            "049f6c77": {
                "node_id": "049f6c77",
                "role": "master",
                "hostname": "MasterAlpha",
                "host": "192.168.1.10",
                "total_ram_bytes": 32 * GIB,
                "available_ram_bytes": 32 * GIB,
                "devices": [{"index": 0, "device": "Vulkan0", "backend": "vulkan", "vram_bytes": 16 * GIB}],
            },
            "a83ac5b6": {
                "node_id": "a83ac5b6",
                "role": "worker",
                "hostname": "WorkerGPU1",
                "host": "192.168.1.20",
                "lan_host": "10.0.0.5",
                "total_ram_bytes": 32 * GIB,
                "available_ram_bytes": 32 * GIB,
                "devices": [{"index": 0, "device": "Vulkan0", "backend": "vulkan", "vram_bytes": 24 * GIB}],
            },
        }
        nodes = cluster_nodes(members)
        layout = _make_layout(layers=16, layer_bytes=300 * MIB)
        settings = PlacementSettings()

        plan = plan_devices(
            nodes,
            size=4 * GIB,
            layout=layout,
            settings=settings,
            context=2048,
            headroom=0.8,
            device="gpu",
            vram_weight=0.6,
            latency_weight=0.4,
            required_nodes=["MasterAlpha", "10.0.0.5"],
        )
        assert plan.fit_status in ("fits", "tight")
        allocated_nodes = {a.node_id for a in plan.device_allocations}
        assert "049f6c77" in allocated_nodes
        assert "a83ac5b6" in allocated_nodes


