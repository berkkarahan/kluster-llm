"""Unit tests for the Halda / HiGHS MILP layer, GPU, and cycle scheduler (019-highs-scheduler-planning)."""

from __future__ import annotations

import pytest

from kllm.modules.inference.entities import PlanBody
from kllm.modules.primacpp.entities import (
    DeviceProfile,
    HighsScheduleReport,
)
from kllm.modules.primacpp.interactors.highs_scheduler import (
    HaldaDeviceInput,
    HaldaProblemState,
    HighsMilpModel,
    assign_layers_and_select_devices,
    assign_layers_to_device,
    build_segments_for_schedule,
    classify_device_set,
    solve_highs_halda_schedule,
)


def _device(
    node_id: str,
    *,
    is_master: bool = False,
    required: bool = False,
    os_type: str = "Linux",
    has_gpu: bool = True,
    unified_memory: bool = False,
    is_integrated_gpu: bool = False,
    avail_ram_bytes: int = 16 * 1024**3,
    avail_vram_bytes: int = 8 * 1024**3,
    total_vram_bytes: int = 8 * 1024**3,
    disk_seq_bw_bps: float = 2_000_000_000.0,
    disk_rnd_bw_bps: float = 1_500_000_000.0,
    t_cpu_layer_ms: float = 12.0,
    t_gpu_layer_ms: float = 2.0,
    t_kv_sync_ms: float = 0.2,
    t_io_layer_ms: float = 1.0,
    l_net_ms: float = 0.4,
    b_net_bps: float = 1_250_000_000.0,
) -> HaldaDeviceInput:
    return HaldaDeviceInput(
        node_id=node_id,
        is_master=is_master,
        required=required,
        os_type=os_type,
        has_gpu=has_gpu,
        unified_memory=unified_memory,
        is_integrated_gpu=is_integrated_gpu,
        avail_ram_bytes=avail_ram_bytes,
        avail_vram_bytes=avail_vram_bytes if has_gpu else 0,
        total_vram_bytes=total_vram_bytes if has_gpu else 0,
        disk_seq_bw_bps=disk_seq_bw_bps,
        disk_rnd_bw_bps=disk_rnd_bw_bps,
        t_cpu_layer_ms=t_cpu_layer_ms,
        t_gpu_layer_ms=t_gpu_layer_ms if has_gpu else t_cpu_layer_ms,
        t_kv_sync_ms=t_kv_sync_ms if has_gpu else 0.0,
        t_io_layer_ms=t_io_layer_ms if is_master else 0.0,
        l_net_ms=l_net_ms,
        b_net_bps=b_net_bps,
    )


def _state(
    devices: list[HaldaDeviceInput],
    *,
    total_layers: int = 32,
    bytes_per_layer_weight: int = 256 * 1024**2,
    bytes_per_layer_kv: int = 16 * 1024**2,
    bytes_io_weight: int = 128 * 1024**2,
    hidden_state_bytes: int = 16 * 1024,
    mmap_mode: str = "readahead",
    segmented_placement_enabled: bool = True,
    just_enough_master: bool = False,
    placement_strategy: str = "auto",
) -> HaldaProblemState:
    return HaldaProblemState(
        devices=devices,
        total_layers=total_layers,
        bytes_per_layer_weight=bytes_per_layer_weight,
        bytes_per_layer_kv=bytes_per_layer_kv,
        bytes_io_weight=bytes_io_weight,
        hidden_state_bytes=hidden_state_bytes,
        mmap_mode=mmap_mode,
        segmented_placement_enabled=segmented_placement_enabled,
        just_enough_master=just_enough_master,
        placement_strategy=placement_strategy,
    )


def test_csc_milp_builder_and_exact_solver_invariants():
    """Verify HighsMilpModel CSC construction and exact integer optimization."""
    model = HighsMilpModel()
    # Minimize 2 * n0 + 10 * c0 subject to n0 + c0 == 16, n0 <= 10, c0 <= 16
    n0 = model.add_var(lb=0.0, ub=10.0, cost=2.0, is_integer=True)
    c0 = model.add_var(lb=0.0, ub=16.0, cost=10.0, is_integer=True)
    model.add_row(lb=16.0, ub=16.0, cols=[n0, c0], vals=[1.0, 1.0])
    csc = model.to_csc()
    assert csc["num_col"] == 2
    assert csc["num_row"] == 1
    assert len(csc["a_start"]) == 2
    sol = model.solve()
    assert sol.feasible
    assert round(sol.col_values[n0]) == 10
    assert round(sol.col_values[c0]) == 6
    assert sol.objective == pytest.approx(2.0 * 10 + 10.0 * 6)


@pytest.mark.parametrize("os_type", ["Windows", "Linux", "Darwin"])
def test_cross_platform_memory_pressure_sets_and_mmap_modes(os_type: str):
    """Verify M1..M4 classification across Windows, Linux, Darwin and mmap modes."""
    # 1. Resident M4 when everything fits in VRAM + RAM
    d_m4 = _device(
        "node-m4",
        is_master=True,
        os_type=os_type,
        avail_vram_bytes=8 * 1024**3,
        avail_ram_bytes=16 * 1024**3,
    )
    s_m4 = _state([d_m4], total_layers=16, bytes_per_layer_weight=256 * 1024**2)
    set_m4, overload_m4, reload_m4 = classify_device_set(s_m4, d_m4, w_m=16, n_m=16, k=1)
    assert set_m4 == "M4"
    assert overload_m4 == 0
    assert reload_m4 == 0.0

    # 2. Moderate pressure M3 when CPU layers slightly exceed available RAM under mmap="readahead"
    d_m3 = _device(
        "node-m3",
        is_master=True,
        os_type=os_type,
        avail_vram_bytes=2 * 1024**3,
        avail_ram_bytes=2 * 1024**3,
    )
    s_m3 = _state([d_m3], total_layers=16, bytes_per_layer_weight=256 * 1024**2, mmap_mode="readahead")
    set_m3, overload_m3, reload_m3 = classify_device_set(s_m3, d_m3, w_m=16, n_m=6, k=1)
    assert set_m3 in ("M3", "M2")
    assert overload_m3 > 0
    assert reload_m3 > 0.0

    # 3. Extreme UMA pressure M1 on unified memory when weights exceed unified pool
    d_uma = _device(
        "node-uma",
        is_master=True,
        os_type=os_type,
        unified_memory=True,
        avail_vram_bytes=2 * 1024**3,
        avail_ram_bytes=2 * 1024**3,
    )
    s_uma = _state([d_uma], total_layers=16, bytes_per_layer_weight=256 * 1024**2, mmap_mode="readahead")
    set_uma, overload_uma, reload_uma = classify_device_set(s_uma, d_uma, w_m=16, n_m=14, k=1)
    assert set_uma == "M1"
    assert overload_uma > 0
    assert reload_uma > 0.0

    # 4. mmap="none" forces M4 with zero disk reload on all active ranks
    s_no_mmap = _state(
        [d_m4],
        total_layers=16,
        bytes_per_layer_weight=256 * 1024**2,
        mmap_mode="none",
    )
    report = assign_layers_and_select_devices(s_no_mmap)
    assert report.scheduler_mode == "highs_halda"
    assert report.mmap_mode == "none"
    for alloc in report.node_allocations:
        assert alloc.memory_pressure_set == "M4"
        assert alloc.disk_reload_ms == 0.0


def test_multi_cycle_divisor_selection_and_segment_expansion():
    """Verify k | L cycle selection when segmented_placement is enabled vs k=1 when disabled."""
    master = _device(
        "master",
        is_master=True,
        avail_vram_bytes=4 * 1024**3,
        avail_ram_bytes=8 * 1024**3,
        t_gpu_layer_ms=1.5,
        t_cpu_layer_ms=8.0,
        l_net_ms=0.05,
        b_net_bps=10_000_000_000.0,
    )
    worker = _device(
        "worker-1",
        is_master=False,
        avail_vram_bytes=4 * 1024**3,
        avail_ram_bytes=8 * 1024**3,
        t_gpu_layer_ms=1.5,
        t_cpu_layer_ms=8.0,
        l_net_ms=0.05,
        b_net_bps=10_000_000_000.0,
    )
    # With segmented_placement_enabled=True, all divisors of L=32 are evaluated
    state_seg = _state([master, worker], total_layers=32, segmented_placement_enabled=True)
    report_seg = assign_layers_and_select_devices(state_seg)
    assert report_seg.segmented_placement_enabled is True
    assert [c.cycle_count_k for c in report_seg.evaluated_cycles] == [1, 2, 4, 8, 16, 32]
    assert sum(a.w_m for a in report_seg.node_allocations) == report_seg.window_size_w
    assert sum(a.total_layers for a in report_seg.node_allocations) == 32

    # Verify segment expansion for k=2 cycles
    res_k2 = assign_layers_to_device(state_seg, k=2)
    assert res_k2 is not None
    segments, per_node_ranges, per_node_graphs = build_segments_for_schedule(
        res_k2.node_allocations, total_layers=32, cycle_count_k=2
    )
    assert len(segments) == 2 * len(res_k2.node_allocations)
    covered = [(seg.start, seg.end) for seg in segments]
    assert covered[0][0] == 0
    for idx in range(1, len(covered)):
        assert covered[idx][0] == covered[idx - 1][1]
    assert covered[-1][1] == 32
    for alloc in res_k2.node_allocations:
        expected_graphs = 2 + (2 if alloc.rank == 0 else 0)
        assert per_node_graphs[alloc.node_id] == expected_graphs
        assert len(per_node_ranges[alloc.node_id]) == 2

    # With segmented_placement_enabled=False, only k=1 is evaluated
    state_no_seg = _state([master, worker], total_layers=32, segmented_placement_enabled=False)
    report_no_seg = assign_layers_and_select_devices(state_no_seg)
    assert report_no_seg.cycle_count_k == 1
    assert report_no_seg.window_size_w == 32
    assert [c.cycle_count_k for c in report_no_seg.evaluated_cycles] == [1]


def test_weak_worker_pruning_preserves_master_and_required_nodes():
    """Verify US2: weak non-required workers (w_m <= 1 and n_m == 0) are pruned while master & required stay."""
    master = _device(
        "master",
        is_master=True,
        avail_vram_bytes=4 * 1024**3,
        avail_ram_bytes=32 * 1024**3,
        t_gpu_layer_ms=1.0,
        t_cpu_layer_ms=15.0,
    )
    fast_worker = _device(
        "worker-fast",
        is_master=False,
        avail_vram_bytes=6 * 1024**3,
        avail_ram_bytes=32 * 1024**3,
        t_gpu_layer_ms=1.0,
        t_cpu_layer_ms=15.0,
    )
    weak_cpu_worker = _device(
        "worker-weak",
        is_master=False,
        has_gpu=False,
        avail_vram_bytes=0,
        avail_ram_bytes=4 * 1024**3,
        t_cpu_layer_ms=95.0,
        l_net_ms=15.0,
    )
    state = _state([master, fast_worker, weak_cpu_worker], total_layers=32)
    report = assign_layers_and_select_devices(state)
    active_ids = [a.node_id for a in report.node_allocations]
    pruned_ids = [p.node_id for p in report.pruned_nodes]
    assert "master" in active_ids
    assert "worker-fast" in active_ids
    assert "worker-weak" in pruned_ids
    assert "worker-weak" not in active_ids

    # Now mark worker-weak as required: it must NOT be pruned and must receive >= 1 layer
    weak_required = _device(
        "worker-weak",
        is_master=False,
        required=True,
        has_gpu=False,
        avail_vram_bytes=0,
        avail_ram_bytes=8 * 1024**3,
        t_cpu_layer_ms=95.0,
        l_net_ms=15.0,
    )
    state_req = _state([master, fast_worker, weak_required], total_layers=32)
    report_req = assign_layers_and_select_devices(state_req)
    alloc_by_node = {a.node_id: a for a in report_req.node_allocations}
    assert "worker-weak" in alloc_by_node
    assert alloc_by_node["worker-weak"].total_layers >= 1
    assert all(p.node_id != "worker-weak" for p in report_req.pruned_nodes)


def test_slow_disk_forced_m4_and_infeasibility_rollback():
    """Verify US2: slow disks (< 500 MB/s) are forced into M4 and rolled back if M4 is infeasible."""
    # Case A: Slow disk where M4 is feasible (plenty of RAM/VRAM, or offloading to fast peer)
    master = _device(
        "master",
        is_master=True,
        avail_vram_bytes=8 * 1024**3,
        avail_ram_bytes=16 * 1024**3,
        disk_seq_bw_bps=250_000_000.0,  # 250 MB/s < 500 MB/s
    )
    state_feasible = _state([master], total_layers=16, bytes_per_layer_weight=256 * 1024**2)
    report_ok = assign_layers_and_select_devices(state_feasible)
    assert report_ok.forced_m4_rollback is False
    assert report_ok.node_allocations[0].forced_m4 is True
    assert report_ok.node_allocations[0].memory_pressure_set == "M4"

    # Case B: Single node with slow disk (< 500 MB/s) and model larger than RAM+VRAM so M4 is impossible
    tight_master = _device(
        "master",
        is_master=True,
        avail_vram_bytes=2 * 1024**3,
        avail_ram_bytes=2 * 1024**3,
        disk_seq_bw_bps=200_000_000.0,  # 200 MB/s < 500 MB/s
    )
    state_rollback = _state(
        [tight_master],
        total_layers=24,
        bytes_per_layer_weight=256 * 1024**2,
        mmap_mode="readahead",
    )
    report_rb = assign_layers_and_select_devices(state_rollback)
    assert report_rb.forced_m4_rollback is True
    assert report_rb.node_allocations[0].memory_pressure_set in ("M1", "M2", "M3")
    assert report_rb.node_allocations[0].disk_reload_ms > 0.0


def test_just_enough_master_and_strategy_modes():
    """Verify just_enough_master preference and explicit capacity_first / priority / unprofiled fallback."""
    master = _device(
        "master",
        is_master=True,
        avail_vram_bytes=8 * 1024**3,
        avail_ram_bytes=16 * 1024**3,
        t_gpu_layer_ms=2.0,
        t_cpu_layer_ms=8.0,
    )
    worker = _device(
        "worker-1",
        is_master=False,
        avail_vram_bytes=8 * 1024**3,
        avail_ram_bytes=16 * 1024**3,
        t_gpu_layer_ms=2.0,
        t_cpu_layer_ms=8.0,
    )
    normal = assign_layers_and_select_devices(
        _state([master, worker], total_layers=24, just_enough_master=False, segmented_placement_enabled=False)
    )
    jem = assign_layers_and_select_devices(
        _state([master, worker], total_layers=24, just_enough_master=True, segmented_placement_enabled=False)
    )
    normal_master = next(a for a in normal.node_allocations if a.node_id == "master")
    jem_master = next(a for a in jem.node_allocations if a.node_id == "master")
    assert jem.just_enough_master is True
    assert jem_master.total_layers <= normal_master.total_layers

    # Verify solve_highs_halda_schedule fallback when profiles are missing
    body_auto = PlanBody(model="toy.gguf", placement_strategy="auto")
    fallback_report = solve_highs_halda_schedule(
        body=body_auto,
        runtime={"runtime_id": "a" * 64, "capabilities": {"features": ["segmented_placement"]}},
        members={
            "master": {"node_id": "master", "role": "master", "total_ram_bytes": 16 * 1024**3, "available_ram_bytes": 16 * 1024**3},
            "worker-1": {"node_id": "worker-1", "role": "worker", "total_ram_bytes": 16 * 1024**3, "available_ram_bytes": 16 * 1024**3},
        },
        base_plan={"estimated_layers": 32, "ngl": 16, "device_allocations": []},
        layout={"n_layers": 32, "layer_bytes": [1024 * 1024] * 32},
        profiles={},
    )
    assert isinstance(fallback_report, HighsScheduleReport)
    assert fallback_report.scheduler_mode == "capacity_first"
    assert fallback_report.requested_strategy == "auto"
    assert fallback_report.fallback_reason is not None
    assert "master" in fallback_report.missing_profile_nodes

    # Verify explicit capacity_first and priority bypass HiGHS MILP even when profiles exist
    profile_master = DeviceProfile(
        runtime_id="a" * 64,
        model_sha256="b" * 64,
        node_id="master",
        hardware_digest="c" * 64,
        settings_digest="d" * 64,
        backend="vulkan",
        measured_at=100.0,
        cpu_layer_ms=5.0,
        gpu_layer_ms=1.0,
        disk_read_bytes_s=2_000_000_000.0,
        memory_available_bytes=16 * 1024**3,
    )
    body_cap = PlanBody(model="toy.gguf", placement_strategy="capacity_first")
    cap_report = solve_highs_halda_schedule(
        body=body_cap,
        runtime={"runtime_id": "a" * 64, "capabilities": {"features": ["segmented_placement"]}},
        members={"master": {"node_id": "master", "role": "master", "total_ram_bytes": 16 * 1024**3, "available_ram_bytes": 16 * 1024**3}},
        base_plan={"estimated_layers": 32, "ngl": 16, "device_allocations": []},
        layout={"n_layers": 32, "layer_bytes": [1024 * 1024] * 32},
        profiles={"master": profile_master},
    )
    assert cap_report.scheduler_mode == "capacity_first"
    assert cap_report.requested_strategy == "capacity_first"
    assert cap_report.fallback_reason == "operator_selected_capacity_first"

    body_prio = PlanBody(model="toy.gguf", placement_strategy="priority", node_priority=["master"])
    prio_report = solve_highs_halda_schedule(
        body=body_prio,
        runtime={"runtime_id": "a" * 64, "capabilities": {"features": ["segmented_placement"]}},
        members={"master": {"node_id": "master", "role": "master", "total_ram_bytes": 16 * 1024**3, "available_ram_bytes": 16 * 1024**3}},
        base_plan={"estimated_layers": 32, "ngl": 16, "device_allocations": []},
        layout={"n_layers": 32, "layer_bytes": [1024 * 1024] * 32},
        profiles={"master": profile_master},
    )
    assert prio_report.scheduler_mode == "priority"
    assert prio_report.requested_strategy == "priority"
    assert prio_report.fallback_reason == "operator_selected_priority"
