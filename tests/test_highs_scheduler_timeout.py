"""Tests for HiGHS MILP scheduler timeout, node budget, and capacity-proportional heuristic."""

import time

from kllm.modules.primacpp.interactors.highs_scheduler import (
    HighsMilpModel,
    _solve_capacity_proportional_heuristic,
    _solve_highs_milp,
)


def test_highs_pure_python_search_honors_node_budget(monkeypatch):
    """Simulates an 80-layer model across 8 devices with highspy = None; asserts completion in <= 2.2s."""
    # Force pure-Python branch-and-bound solver path
    monkeypatch.setattr(
        "kllm.modules.primacpp.interactors.highs_scheduler._solve_with_highspy",
        lambda model: None,
    )

    model = HighsMilpModel()
    num_devices = 8
    target_layers = 80
    var_pairs = []

    # 8 devices, each with GPU and CPU columns (16 integer variables)
    for dev_idx in range(num_devices):
        # Varying GPU capacity (20 to 50 layers) and varying costs
        gpu_cap = 20.0 + (dev_idx * 5)
        cpu_cap = 30.0
        gpu_col = model.add_var(lb=0.0, ub=gpu_cap, cost=2.0 + dev_idx * 0.1, is_integer=True)
        cpu_col = model.add_var(lb=0.0, ub=cpu_cap, cost=10.0 + dev_idx * 0.5, is_integer=True)
        var_pairs.append((gpu_col, cpu_col))

        # Each device must host at least 1 layer
        model.add_row(lb=1.0, ub=float(target_layers), cols=[gpu_col, cpu_col], vals=[1.0, 1.0])

    # Global layer coverage constraint: sum of all variables == target_layers (80)
    all_cols = []
    for g, c in var_pairs:
        all_cols.extend([g, c])
    model.add_row(
        lb=float(target_layers),
        ub=float(target_layers),
        cols=all_cols,
        vals=[1.0] * len(all_cols),
    )

    start = time.monotonic()
    sol = _solve_highs_milp(model)
    elapsed = time.monotonic() - start

    # Asserts solver bounded execution within budget (<= 2.2s + small test margin)
    assert elapsed <= 2.5, f"Search took too long: {elapsed:.3f}s"
    assert sol.feasible is True
    assert len(sol.col_values) == len(all_cols)

    # Validates valid integer layer partition satisfying global coverage
    total_assigned = sum(sol.col_values)
    assert abs(total_assigned - target_layers) < 1e-6
    for val in sol.col_values:
        assert val >= 0.0
        assert abs(val - round(val)) < 1e-6


def test_greedy_heuristic_satisfies_constraints():
    """Directly tests _solve_capacity_proportional_heuristic() against edge cases."""
    # Edge case 1: Asymmetric GPU memory
    # GPU 1 has 40 layers cap, GPU 2 has 20 layers cap.
    # Total layers = 30. Capacity-proportional allocation must be 20 and 10 (2:1 ratio).
    model1 = HighsMilpModel()
    col_gpu1 = model1.add_var(lb=0.0, ub=40.0, cost=2.0, is_integer=True)
    col_gpu2 = model1.add_var(lb=0.0, ub=20.0, cost=2.0, is_integer=True)
    model1.add_row(lb=30.0, ub=30.0, cols=[col_gpu1, col_gpu2], vals=[1.0, 1.0])

    sol1 = _solve_capacity_proportional_heuristic(model1)
    assert sol1.feasible is True
    assert sol1.col_values[col_gpu1] == 20.0
    assert sol1.col_values[col_gpu2] == 10.0
    assert sum(sol1.col_values) == 30.0

    # Edge case 2: Asymmetric GPU memory + Single CPU coordinator
    # Device 0: CPU Coordinator (no GPU, cap 10 layers, cost 10.0, required min 1 layer)
    # Device 1: GPU 1 (cap 40 layers, cost 2.0)
    # Device 2: GPU 2 (cap 20 layers, cost 2.0)
    # Total layers = 31.
    # Coordinator gets its required 1 layer on CPU.
    # Remaining 30 layers are distributed capacity-proportionally across GPU 1 and GPU 2 (20 and 10).
    model2 = HighsMilpModel()
    col_cpu0 = model2.add_var(lb=0.0, ub=10.0, cost=10.0, is_integer=True)
    col_gpu1 = model2.add_var(lb=0.0, ub=40.0, cost=2.0, is_integer=True)
    col_gpu2 = model2.add_var(lb=0.0, ub=20.0, cost=2.0, is_integer=True)

    # Local row: Coordinator must have at least 1 layer
    model2.add_row(lb=1.0, ub=10.0, cols=[col_cpu0], vals=[1.0])

    # Global row: Total layers == 31
    model2.add_row(
        lb=31.0,
        ub=31.0,
        cols=[col_cpu0, col_gpu1, col_gpu2],
        vals=[1.0, 1.0, 1.0],
    )

    sol2 = _solve_capacity_proportional_heuristic(model2)
    assert sol2.feasible is True
    assert sol2.col_values[col_cpu0] == 1.0
    assert sol2.col_values[col_gpu1] == 20.0
    assert sol2.col_values[col_gpu2] == 10.0
    assert sum(sol2.col_values) == 31.0
