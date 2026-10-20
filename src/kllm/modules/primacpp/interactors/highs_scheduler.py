"""Halda / HiGHS MILP layer, GPU offload, and ring cycle scheduler for Prima.cpp planning."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Any, Literal

from kllm.modules.inference.interactors.prima_graph_reserve import reserve_graph_visits
from kllm.modules.primacpp.entities import (
    DeviceProfile,
    HighsCycleCandidate,
    HighsNodeAllocation,
    HighsPrunedNode,
    HighsScheduleReport,
    LayerRange,
    MemoryPressureSet,
    PlacementStrategyMode,
    SchedulerMode,
)
from kllm.modules.primacpp.interactors.execution import Segment
from kllm.shared.prima_diagnostics import bound_emitter, emit, operation

SLOW_DISK_THRESHOLD_BPS = 500.0 * 1000.0 * 1000.0  # 500 MB/s per Prima.cpp highs_scheduler.cpp
MILP_TOLERANCE = 1e-9
MAX_HALDA_ITERATIONS = 30
MAX_BRANCH_AND_BOUND_NODES = 50_000
MAX_SOLVER_SECONDS = 2.2


@dataclass(frozen=True)
class HighsMilpSolution:
    feasible: bool
    objective: float
    col_values: list[float]
    solver_backend: Literal["highspy", "highs_exact_milp"] = "highs_exact_milp"


class HighsMilpModel:
    """Column-wise (CSC) MILP builder matching Prima.cpp highs_scheduler.cpp."""

    def __init__(self) -> None:
        self.col_cost: list[float] = []
        self.col_lower: list[float] = []
        self.col_upper: list[float] = []
        self.integrality: list[int] = []
        self.row_lower: list[float] = []
        self.row_upper: list[float] = []
        self._row_entries: list[ tuple[list[int], list[float]] ] = []

    @property
    def num_col(self) -> int:
        return len(self.col_cost)

    @property
    def num_row(self) -> int:
        return len(self.row_lower)

    def add_var(
        self,
        *,
        lb: float = 0.0,
        ub: float = float("inf"),
        cost: float = 0.0,
        is_integer: bool = True,
    ) -> int:
        idx = len(self.col_cost)
        self.col_cost.append(float(cost))
        self.col_lower.append(float(lb))
        self.col_upper.append(float(ub))
        self.integrality.append(1 if is_integer else 0)
        return idx

    def add_row(self, *, lb: float, ub: float, cols: list[int], vals: list[float]) -> int:
        if len(cols) != len(vals):
            raise ValueError("Row cols and vals must have equal length")
        row_idx = len(self.row_lower)
        self.row_lower.append(float(lb))
        self.row_upper.append(float(ub))
        merged: dict[int, float] = {}
        for col, val in zip(cols, vals):
            if abs(val) > 0.0:
                merged[int(col)] = merged.get(int(col), 0.0) + float(val)
        sorted_cols = sorted(merged.keys())
        self._row_entries.append((sorted_cols, [merged[c] for c in sorted_cols]))
        return row_idx

    def to_csc(self) -> dict[str, Any]:
        """Build the Compressed Sparse Column (a_format=1) representation."""
        col_rows: list[list[tuple[int, float]]] = [[] for _ in range(self.num_col)]
        for row_idx, (cols, vals) in enumerate(self._row_entries):
            for col_idx, val in zip(cols, vals):
                col_rows[col_idx].append((row_idx, val))
        a_start: list[int] = []
        a_index: list[int] = []
        a_value: list[float] = []
        for col_idx in range(self.num_col):
            a_start.append(len(a_index))
            for row_idx, val in col_rows[col_idx]:
                a_index.append(row_idx)
                a_value.append(val)
        return {
            "num_col": self.num_col,
            "num_row": self.num_row,
            "col_cost": list(self.col_cost),
            "col_lower": list(self.col_lower),
            "col_upper": list(self.col_upper),
            "row_lower": list(self.row_lower),
            "row_upper": list(self.row_upper),
            "a_format": 1,
            "a_start": a_start,
            "a_index": a_index,
            "a_value": a_value,
            "integrality": list(self.integrality),
        }

    def solve(self) -> HighsMilpSolution:
        return _solve_highs_milp(self)


def _solve_with_highspy(model: HighsMilpModel) -> HighsMilpSolution | None:
    try:
        import highspy  # type: ignore[import-not-found]
    except ImportError:
        emit("highs", "highspy unavailable; using exact MILP fallback")
        return None
    try:
        csc = model.to_csc()
        solver = highspy.Highs()
        solver.setOptionValue("output_flag", False)
        # A callback belongs to this solver instance; never redirect process-wide stdout.
        try:
            log = bound_emitter("highs")
            solver.cbLogging.subscribe(lambda event: log(event.message))
            solver.setOptionValue("log_to_console", False)
            solver.setOptionValue("output_flag", True)
        except Exception:
            solver.setOptionValue("output_flag", False)
            emit("highs", "native solver callback unavailable; retaining summary diagnostics")
        lp = highspy.HighsLp()
        lp.num_col_ = csc["num_col"]
        lp.num_row_ = csc["num_row"]
        lp.col_cost_ = csc["col_cost"]
        lp.col_lower_ = csc["col_lower"]
        lp.col_upper_ = csc["col_upper"]
        lp.row_lower_ = csc["row_lower"]
        lp.row_upper_ = csc["row_upper"]
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.num_col_ = csc["num_col"]
        lp.a_matrix_.num_row_ = csc["num_row"]
        lp.a_matrix_.start_ = [*csc["a_start"], len(csc["a_index"])]
        lp.a_matrix_.index_ = csc["a_index"]
        lp.a_matrix_.value_ = csc["a_value"]
        lp.integrality_ = [
            highspy.HighsVarType.kInteger if v else highspy.HighsVarType.kContinuous
            for v in csc["integrality"]
        ]
        solver.passModel(lp)
        solver.run()
        status = solver.getModelStatus()
        emit("highs", "native solve finished", status=str(status), rows=model.num_row, columns=model.num_col)
        if status != highspy.HighsModelStatus.kOptimal:
            return HighsMilpSolution(
                feasible=False,
                objective=float("inf"),
                col_values=[],
                solver_backend="highspy",
            )
        info = solver.getInfo()
        sol = solver.getSolution()
        return HighsMilpSolution(
            feasible=True,
            objective=float(info.objective_function_value),
            col_values=[float(v) for v in sol.col_value],
            solver_backend="highspy",
        )
    except Exception as error:
        emit("highs", f"highspy failed; using exact MILP fallback: {error}")
        return None


def _solve_capacity_proportional_heuristic(model: HighsMilpModel) -> HighsMilpSolution:
    """Deterministic capacity-proportional greedy heuristic solver for HighsMilpModel."""
    num_col = model.num_col
    if num_col == 0:
        return HighsMilpSolution(feasible=True, objective=0.0, col_values=[], solver_backend="highs_exact_milp")
    for c in range(num_col):
        if model.col_lower[c] > model.col_upper[c] + MILP_TOLERANCE:
            return HighsMilpSolution(feasible=False, objective=float("inf"), col_values=[], solver_backend="highs_exact_milp")

    assignment = [
        float(int(math.ceil(model.col_lower[c]))) if model.integrality[c] else float(model.col_lower[c])
        for c in range(num_col)
    ]

    def max_possible_add(col: int) -> float:
        cap = model.col_upper[col] - assignment[col]
        for r_idx, (r_cols, r_vals) in enumerate(model._row_entries):
            if col in r_cols:
                r_v = dict(zip(r_cols, r_vals))[col]
                if r_v > 0:
                    r_cur = sum(v * assignment[c] for c, v in zip(r_cols, r_vals))
                    r_rem = model.row_upper[r_idx] - r_cur
                    cap = min(cap, r_rem / r_v)
        if model.integrality[col]:
            return max(0.0, float(math.floor(cap + MILP_TOLERANCE)))
        return max(0.0, float(cap))

    # Pass 1: local rows (len(cols) < num_col)
    for r_idx, (cols, vals) in enumerate(model._row_entries):
        if len(cols) < num_col:
            cur_val = sum(v * assignment[c] for c, v in zip(cols, vals))
            if cur_val < model.row_lower[r_idx] - MILP_TOLERANCE:
                candidates = [c for c, v in zip(cols, vals) if v > 0 and max_possible_add(c) > MILP_TOLERANCE]
                candidates.sort(key=lambda c: model.col_cost[c])
                for c in candidates:
                    cur_val = sum(v * assignment[co] for co, v in zip(cols, vals))
                    if cur_val >= model.row_lower[r_idx] - MILP_TOLERANCE:
                        break
                    needed = (model.row_lower[r_idx] - cur_val) / dict(zip(cols, vals))[c]
                    add_amt = min(max_possible_add(c), needed)
                    if model.integrality[c]:
                        add_amt = float(math.ceil(add_amt - MILP_TOLERANCE))
                        add_amt = min(max_possible_add(c), add_amt)
                    assignment[c] += add_amt

    # Pass 2: remaining rows (including global coverage)
    for r_idx, (cols, vals) in enumerate(model._row_entries):
        cur_val = sum(v * assignment[c] for c, v in zip(cols, vals))
        while cur_val < model.row_lower[r_idx] - MILP_TOLERANCE:
            loop_start_val = cur_val
            deficit = model.row_lower[r_idx] - cur_val
            candidates = [c for c, v in zip(cols, vals) if v > 0 and max_possible_add(c) > MILP_TOLERANCE]
            if not candidates:
                break
            min_cost = min(model.col_cost[c] for c in candidates)
            tier_cost_threshold = min_cost + 2.0
            tier_candidates = [c for c in candidates if model.col_cost[c] <= tier_cost_threshold]
            if not tier_candidates:
                tier_candidates = candidates

            # Weight by variable column capacity
            caps = {c: max(1.0, model.col_upper[c] - model.col_lower[c]) for c in tier_candidates}
            total_tier_cap = sum(caps.values())
            for c in tier_candidates:
                c_cap = max_possible_add(c)
                prop = caps[c] / total_tier_cap
                if model.integrality[c]:
                    alloc = min(c_cap, float(math.floor(deficit * prop)))
                else:
                    alloc = min(c_cap, deficit * prop)
                assignment[c] += alloc

            cur_val = sum(v * assignment[c] for c, v in zip(cols, vals))
            rem_deficit = model.row_lower[r_idx] - cur_val
            if rem_deficit > MILP_TOLERANCE:
                tier_candidates.sort(key=lambda c: (-(model.col_upper[c] - assignment[c]), model.col_cost[c]))
                for c in tier_candidates:
                    if rem_deficit <= MILP_TOLERANCE:
                        break
                    c_add = max_possible_add(c)
                    if c_add > MILP_TOLERANCE:
                        step = 1.0 if model.integrality[c] else rem_deficit
                        add_amt = min(c_add, step if model.integrality[c] else rem_deficit)
                        assignment[c] += add_amt
                        rem_deficit -= add_amt
            cur_val = sum(v * assignment[c] for c, v in zip(cols, vals))
            if cur_val <= loop_start_val + MILP_TOLERANCE:
                break

    # Validate bounds
    for c in range(num_col):
        if assignment[c] < model.col_lower[c] - MILP_TOLERANCE or assignment[c] > model.col_upper[c] + MILP_TOLERANCE:
            return HighsMilpSolution(feasible=False, objective=float("inf"), col_values=[], solver_backend="highs_exact_milp")
    for r_idx, (cols, vals) in enumerate(model._row_entries):
        r_val = sum(v * assignment[c] for c, v in zip(cols, vals))
        if r_val < model.row_lower[r_idx] - MILP_TOLERANCE or r_val > model.row_upper[r_idx] + MILP_TOLERANCE:
            return HighsMilpSolution(feasible=False, objective=float("inf"), col_values=[], solver_backend="highs_exact_milp")

    obj = sum(model.col_cost[c] * assignment[c] for c in range(num_col))
    return HighsMilpSolution(
        feasible=True,
        objective=float(obj),
        col_values=[float(v) for v in assignment],
        solver_backend="highs_exact_milp",
    )


@operation("highs")
def _solve_highs_milp(model: HighsMilpModel) -> HighsMilpSolution:
    """Solve the CSC MILP model using highspy when present or the exact deterministic MILP solver."""
    emit("highs", "MILP dimensions", rows=model.num_row, columns=model.num_col)
    highspy_res = _solve_with_highspy(model)
    if highspy_res is not None:
        return highspy_res

    num_col = model.num_col
    if num_col == 0:
        return HighsMilpSolution(feasible=True, objective=0.0, col_values=[])

    for c in range(num_col):
        if model.col_lower[c] > model.col_upper[c] + MILP_TOLERANCE:
            return HighsMilpSolution(feasible=False, objective=float("inf"), col_values=[])

    # Check if this is a bounded integer program suitable for exact branch-and-bound / DP
    best_obj = float("inf")
    best_vals: list[float] | None = None
    assignment: list[float] = [0.0] * num_col
    visited_nodes = 0
    start_time = time.monotonic()
    deadline = start_time + MAX_SOLVER_SECONDS
    timed_out = False

    # Precompute row bounds and remaining min/max contribution per row for fast pruning
    row_cols_vals = model._row_entries
    num_row = model.num_row

    def check_rows_partial(col_idx: int) -> bool:
        for r_idx in range(num_row):
            cols, vals = row_cols_vals[r_idx]
            cur = 0.0
            min_rem = 0.0
            max_rem = 0.0
            for c, v in zip(cols, vals):
                if c < col_idx:
                    cur += v * assignment[c]
                else:
                    lb = model.col_lower[c]
                    ub = model.col_upper[c]
                    if v >= 0.0:
                        min_rem += v * lb
                        max_rem += v * ub
                    else:
                        min_rem += v * ub
                        max_rem += v * lb
            if cur + max_rem < model.row_lower[r_idx] - MILP_TOLERANCE:
                return False
            if cur + min_rem > model.row_upper[r_idx] + MILP_TOLERANCE:
                return False
        return True

    # Remaining minimal cost from col_idx..num_col-1 for objective bound pruning
    min_rem_cost = [0.0] * (num_col + 1)
    for c in range(num_col - 1, -1, -1):
        cost = model.col_cost[c]
        bound = model.col_lower[c] if cost >= 0.0 else model.col_upper[c]
        min_rem_cost[c] = min_rem_cost[c + 1] + (cost * bound if math.isfinite(bound) else 0.0)

    def search(col_idx: int, cur_obj: float) -> None:
        nonlocal best_obj, best_vals, visited_nodes, timed_out
        visited_nodes += 1
        if visited_nodes > MAX_BRANCH_AND_BOUND_NODES or time.monotonic() > deadline:
            timed_out = True
            return
        if cur_obj + min_rem_cost[col_idx] >= best_obj - MILP_TOLERANCE:
            return
        if not check_rows_partial(col_idx):
            return
        if col_idx == num_col:
            best_obj = cur_obj
            best_vals = list(assignment)
            return

        lb = model.col_lower[col_idx]
        ub = model.col_upper[col_idx]
        cost = model.col_cost[col_idx]

        # Tighten bounds of col_idx using any row where all other variables are already assigned
        for r_idx in range(num_row):
            cols, vals = row_cols_vals[r_idx]
            if col_idx not in cols:
                continue
            other_unassigned = any(c > col_idx for c in cols)
            if not other_unassigned:
                fixed_sum = sum(v * assignment[c] for c, v in zip(cols, vals) if c < col_idx)
                coeff = vals[cols.index(col_idx)]
                r_lb = model.row_lower[r_idx] - fixed_sum
                r_ub = model.row_upper[r_idx] - fixed_sum
                if coeff > 0.0:
                    lb = max(lb, r_lb / coeff - MILP_TOLERANCE)
                    ub = min(ub, r_ub / coeff + MILP_TOLERANCE)
                elif coeff < 0.0:
                    lb = max(lb, r_ub / coeff - MILP_TOLERANCE)
                    ub = min(ub, r_lb / coeff + MILP_TOLERANCE)

        if model.integrality[col_idx]:
            int_lb = int(math.ceil(lb - MILP_TOLERANCE))
            int_ub = int(math.floor(ub + MILP_TOLERANCE))
            if int_lb > int_ub:
                return
            candidates = (
                range(int_lb, int_ub + 1)
                if cost >= 0.0
                else range(int_ub, int_lb - 1, -1)
            )
            for val in candidates:
                assignment[col_idx] = float(val)
                search(col_idx + 1, cur_obj + cost * val)
                if timed_out:
                    return
        else:
            if lb > ub + MILP_TOLERANCE:
                return
            val = lb if cost >= 0.0 else ub
            assignment[col_idx] = float(val)
            search(col_idx + 1, cur_obj + cost * val)
            if timed_out:
                return

    search(0, 0.0)
    if timed_out or best_vals is None:
        emit(
            "highs",
            "exact MILP search exceeded budget or unfeasible; falling back to capacity-proportional heuristic",
            visited_nodes=visited_nodes,
            timed_out=timed_out,
        )
        fallback_sol = _solve_capacity_proportional_heuristic(model)
        if fallback_sol.feasible:
            return fallback_sol
        if best_vals is not None:
            return HighsMilpSolution(
                feasible=True,
                objective=float(best_obj),
                col_values=best_vals,
                solver_backend="highs_exact_milp",
            )
        return HighsMilpSolution(
            feasible=False,
            objective=float("inf"),
            col_values=[],
            solver_backend="highs_exact_milp",
        )

    return HighsMilpSolution(
        feasible=True,
        objective=float(best_obj),
        col_values=best_vals,
        solver_backend="highs_exact_milp",
    )


@dataclass(frozen=True)
class HaldaDeviceInput:
    node_id: str
    is_master: bool
    required: bool = False
    os_type: str = "Linux"
    has_gpu: bool = False
    unified_memory: bool = False
    is_integrated_gpu: bool = False
    avail_ram_bytes: int = 0
    avail_vram_bytes: int = 0
    total_vram_bytes: int = 0
    disk_seq_bw_bps: float = 1_000_000_000.0
    disk_rnd_bw_bps: float = 500_000_000.0
    t_cpu_layer_ms: float = 10.0
    t_gpu_layer_ms: float = 2.0
    t_kv_sync_ms: float = 0.1
    t_io_layer_ms: float = 0.0
    l_net_ms: float = 0.0
    b_net_bps: float = 1_250_000_000.0


@dataclass(frozen=True)
class HaldaProblemState:
    devices: list[HaldaDeviceInput]
    total_layers: int
    bytes_per_layer_weight: int
    bytes_per_layer_kv: int
    bytes_io_weight: int = 0
    hidden_state_bytes: int = 16384
    mmap_mode: str = "readahead"
    segmented_placement_enabled: bool = False
    just_enough_master: bool = False
    placement_strategy: PlacementStrategyMode = "auto"
    forced_m4_nodes: frozenset[str] = field(default_factory=frozenset)


def _effective_disk_bw_bps(dev: HaldaDeviceInput, mmap_mode: str) -> float:
    if mmap_mode == "none":
        return float("inf")
    seq = max(1.0, float(dev.disk_seq_bw_bps))
    rnd = max(1.0, float(dev.disk_rnd_bw_bps or dev.disk_seq_bw_bps))
    if dev.os_type.lower().startswith("win"):
        return seq if mmap_mode in ("prefetch", "readahead") else rnd
    if dev.os_type.lower().startswith("darwin"):
        return seq * 0.9 if mmap_mode == "readahead" else rnd
    return seq if mmap_mode in ("readahead", "prefetch") else rnd


def classify_device_set(
    state: HaldaProblemState,
    dev: HaldaDeviceInput,
    *,
    w_m: int,
    n_m: int,
    k: int,
    forced_m4: bool = False,
) -> tuple[MemoryPressureSet, int, float]:
    """Classify a device allocation (w_m, n_m, k) into Halda memory-pressure sets M1..M4."""
    total_w = k * w_m
    total_n = k * n_m
    total_c = k * (w_m - n_m)
    theta = state.bytes_per_layer_weight
    kappa = state.bytes_per_layer_kv
    io_bytes = state.bytes_io_weight if dev.is_master else 0

    if state.mmap_mode == "none" or forced_m4 or total_w == 0:
        return "M4", 0, 0.0

    disk_bw = _effective_disk_bw_bps(dev, state.mmap_mode)
    is_uma = dev.unified_memory or dev.is_integrated_gpu

    if is_uma:
        pool_budget = max(dev.avail_ram_bytes, dev.avail_vram_bytes)
        total_required = total_w * (theta + kappa) + io_bytes
        overload = max(0, total_required - pool_budget)
        if overload == 0:
            return "M4", 0, 0.0
        cpu_weight_bytes = total_c * theta
        all_weight_bytes = total_w * theta
        if overload > cpu_weight_bytes and total_n > 0:
            reload_bytes = min(all_weight_bytes, overload)
            reload_ms = (reload_bytes / disk_bw) * 1000.0
            return "M1", int(overload), float(reload_ms)
        if cpu_weight_bytes > 0 and overload >= int(0.8 * cpu_weight_bytes):
            reload_bytes = cpu_weight_bytes
            reload_ms = (reload_bytes / disk_bw) * 1000.0
            return "M2", int(overload), float(reload_ms)
        reload_ms = (overload / disk_bw) * 1000.0
        return "M3", int(overload), float(reload_ms)

    # Discrete GPU or CPU-only host
    vram_required = total_n * (theta + kappa)
    vram_spill = max(0, vram_required - dev.avail_vram_bytes) if dev.has_gpu else vram_required
    ram_required = total_c * (theta + kappa) + io_bytes + vram_spill
    overload = max(0, ram_required - dev.avail_ram_bytes)
    if overload == 0:
        return "M4", 0, 0.0

    cpu_weight_bytes = total_c * theta
    if cpu_weight_bytes > 0 and overload >= cpu_weight_bytes:
        reload_bytes = cpu_weight_bytes
        reload_ms = (reload_bytes / disk_bw) * 1000.0
        return "M2", int(overload), float(reload_ms)

    reload_ms = (overload / disk_bw) * 1000.0
    return "M3", int(overload), float(reload_ms)


def _solve_fixed_sets_milp(
    state: HaldaProblemState,
    k: int,
    device_sets: list[MemoryPressureSet],
) -> tuple[HighsMilpSolution, list[tuple[int, int]]]:
    """Build and solve the Halda MILP for fixed cycle count k and per-device sets M1..M4."""
    L = state.total_layers
    W = L // k
    M = len(state.devices)
    theta = float(state.bytes_per_layer_weight)
    kappa = float(state.bytes_per_layer_kv)
    layer_bytes = theta + kappa

    model = HighsMilpModel()
    var_pairs: list[tuple[int, int]] = []

    for m, dev in enumerate(state.devices):
        m_set = device_sets[m]
        forced_m4 = dev.node_id in state.forced_m4_nodes or state.mmap_mode == "none"
        disk_bw = _effective_disk_bw_bps(dev, state.mmap_mode)
        is_uma = dev.unified_memory or dev.is_integrated_gpu

        # Upper bound on GPU layers per cycle (n_m)
        if not dev.has_gpu or dev.avail_vram_bytes <= 0:
            max_n_m = 0
        elif is_uma and m_set == "M1" and not forced_m4:
            # In M1 on UMA, KV cache must still fit in pool
            max_total_n = int(max(dev.avail_ram_bytes, dev.avail_vram_bytes) // max(1.0, kappa))
            max_n_m = min(W, max_total_n // k)
        else:
            max_total_n = int(dev.avail_vram_bytes // max(1.0, layer_bytes))
            max_n_m = min(W, max_total_n // k)

        # Upper bound on CPU layers per cycle (c_m = w_m - n_m)
        io_bytes = float(state.bytes_io_weight if dev.is_master else 0)
        if m_set == "M4" or forced_m4:
            if is_uma:
                pool_avail = max(0.0, float(max(dev.avail_ram_bytes, dev.avail_vram_bytes)) - io_bytes)
                max_total_w = int(pool_avail // max(1.0, layer_bytes))
                max_c_m = min(W, max_total_w // k)
            else:
                ram_avail = max(0.0, float(dev.avail_ram_bytes) - io_bytes)
                max_total_c = int(ram_avail // max(1.0, layer_bytes))
                max_c_m = min(W, max_total_c // k)
        else:
            # Under mmap streaming (M1/M2/M3), KV cache + minimal working set must fit in RAM
            ram_pool = float(max(dev.avail_ram_bytes, dev.avail_vram_bytes) if is_uma else dev.avail_ram_bytes)
            kv_avail = max(0.0, ram_pool - io_bytes)
            max_total_c = int(kv_avail // max(1.0, kappa))
            max_c_m = min(W, max_total_c // k)

        # Objective coefficients for n_m (GPU layers/cycle) and c_m (CPU layers/cycle)
        gpu_cost_per_cycle_layer = float(k) * float(dev.t_gpu_layer_ms)
        cpu_cost_per_cycle_layer = float(k) * (float(dev.t_cpu_layer_ms) + float(dev.t_kv_sync_ms))

        if m_set == "M1" and not forced_m4:
            disk_per_layer_ms = (float(k) * theta / disk_bw) * 1000.0
            gpu_cost_per_cycle_layer += disk_per_layer_ms
            cpu_cost_per_cycle_layer += disk_per_layer_ms
        elif m_set == "M2" and not forced_m4:
            disk_per_layer_ms = (float(k) * theta / disk_bw) * 1000.0
            cpu_cost_per_cycle_layer += disk_per_layer_ms
        elif m_set == "M3" and not forced_m4:
            disk_per_layer_ms = (float(k) * layer_bytes / disk_bw) * 1000.0
            cpu_cost_per_cycle_layer += disk_per_layer_ms

        if state.just_enough_master and dev.is_master and M > 1:
            # Prefer placing layers on workers first when just_enough_master is active
            master_penalty = 50.0
            gpu_cost_per_cycle_layer += master_penalty
            cpu_cost_per_cycle_layer += master_penalty

        col_n = model.add_var(lb=0.0, ub=float(max(0, max_n_m)), cost=gpu_cost_per_cycle_layer, is_integer=True)
        col_c = model.add_var(lb=0.0, ub=float(max(0, max_c_m)), cost=cpu_cost_per_cycle_layer, is_integer=True)
        var_pairs.append((col_n, col_c))

        # Required nodes (and master rank 0) must host at least 1 layer per cycle
        min_w_m = 1 if (dev.is_master or dev.required) else 0
        if min_w_m > 0:
            model.add_row(lb=float(min_w_m), ub=float(W), cols=[col_n, col_c], vals=[1.0, 1.0])

        # Shared UMA pool capacity constraint in M4
        if is_uma and (m_set == "M4" or forced_m4):
            pool_avail = max(0.0, float(max(dev.avail_ram_bytes, dev.avail_vram_bytes)) - io_bytes)
            max_w_m = int(pool_avail // max(1.0, float(k) * layer_bytes))
            model.add_row(lb=float(min_w_m), ub=float(min(W, max_w_m)), cols=[col_n, col_c], vals=[1.0, 1.0])

    # Global layer coverage equality: sum_m (n_m + c_m) == W
    all_cols: list[int] = []
    all_vals: list[float] = []
    for col_n, col_c in var_pairs:
        all_cols.extend([col_n, col_c])
        all_vals.extend([1.0, 1.0])
    model.add_row(lb=float(W), ub=float(W), cols=all_cols, vals=all_vals)

    sol = model.solve()
    return sol, var_pairs


@dataclass(frozen=True)
class HaldaCycleResult:
    cycle_count_k: int
    window_size_w: int
    iterations: int
    solver_backend: Literal["highspy", "highs_exact_milp"]
    objective_step_ms: float
    compute_step_ms: float
    disk_reload_step_ms: float
    network_step_ms: float
    node_allocations: list[HighsNodeAllocation]


@dataclass(frozen=True)
class _VisitAllocation:
    node_id: str
    layer_start: int
    layer_end: int
    device: str = "CPU"
    memory_kind: str = "cpu"
    estimated_bytes: int = 0


def _compute_graph_counts_from_w(
    devices: list[HaldaDeviceInput],
    allocs: list[tuple[int, int]],
    *,
    total_layers: int,
    cycle_count_k: int,
) -> dict[str, int]:
    master_id = next((d.node_id for d in devices if d.is_master), devices[0].node_id if devices else "master")
    visits: list[_VisitAllocation] = []
    cursor = 0
    for _c in range(cycle_count_k):
        for dev, (w_m, n_m) in zip(devices, allocs):
            if w_m <= 0:
                continue
            visits.append(
                _VisitAllocation(
                    node_id=dev.node_id,
                    layer_start=cursor,
                    layer_end=cursor + w_m,
                    device="GPU0" if n_m > 0 else "CPU",
                    memory_kind="gpu" if n_m > 0 else "cpu",
                )
            )
            cursor += w_m
    if cursor != total_layers or not visits:
        return {d.node_id: 1 for d in devices}
    reserve = reserve_graph_visits(visits, 0, total_layers, master_id, 0)
    return {d.node_id: max(1, int(reserve.graph_counts.get(d.node_id, 1))) for d in devices}


def assign_layers_to_device(
    state: HaldaProblemState,
    k: int,
) -> HaldaCycleResult | None:
    """Solve for optimal (w_m, n_m) at fixed cycle count k | L with iterative M1..M4 convergence."""
    L = state.total_layers
    if k < 1 or L % k != 0:
        return None
    W = L // k
    M = len(state.devices)
    if M == 0 or W < sum(1 for d in state.devices if d.is_master or d.required):
        return None

    initial_sets: list[MemoryPressureSet] = ["M4" for _ in range(M)]
    current_sets: list[MemoryPressureSet] = list(initial_sets)
    seen_states: set[tuple[tuple[int, int, str], ...]] = set()

    last_sol: HighsMilpSolution | None = None
    last_allocs: list[tuple[int, int]] = []
    iterations = 0

    for iteration in range(1, MAX_HALDA_ITERATIONS + 1):
        iterations = iteration
        emit("highs", "Halda iteration", cycle=k, iteration=iteration, memory_sets=','.join(current_sets))
        sol, var_pairs = _solve_fixed_sets_milp(state, k, current_sets)
        if not sol.feasible:
            if state.mmap_mode == "none":
                return None
            relaxed = False
            for m, dev in enumerate(state.devices):
                if dev.node_id in state.forced_m4_nodes:
                    continue
                if current_sets[m] == "M4":
                    current_sets[m] = "M3"
                    relaxed = True
                elif current_sets[m] == "M3":
                    current_sets[m] = "M2"
                    relaxed = True
                elif current_sets[m] == "M2" and (dev.unified_memory or dev.is_integrated_gpu):
                    current_sets[m] = "M1"
                    relaxed = True
            if relaxed:
                continue
            return None

        allocs: list[tuple[int, int]] = []
        next_sets: list[MemoryPressureSet] = []
        for m, (col_n, col_c) in enumerate(var_pairs):
            n_m = int(round(sol.col_values[col_n]))
            c_m = int(round(sol.col_values[col_c]))
            w_m = n_m + c_m
            allocs.append((w_m, n_m))
            dev = state.devices[m]
            forced = dev.node_id in state.forced_m4_nodes or state.mmap_mode == "none"
            new_set, _, _ = classify_device_set(
                state, dev, w_m=w_m, n_m=n_m, k=k, forced_m4=forced
            )
            next_sets.append(new_set)

        last_sol = sol
        last_allocs = allocs
        signature = tuple((allocs[m][0], allocs[m][1], next_sets[m]) for m in range(M))
        if next_sets == current_sets or signature in seen_states:
            current_sets = next_sets
            break
        seen_states.add(signature)
        current_sets = next_sets

    if last_sol is None:
        return None

    # Build per-node allocation breakdown and exact step latency components
    active_count = sum(1 for w_m, _ in last_allocs if w_m > 0)
    node_allocations: list[HighsNodeAllocation] = []
    total_compute_ms = 0.0
    total_disk_ms = 0.0
    total_net_ms = 0.0

    theta = state.bytes_per_layer_weight
    kappa = state.bytes_per_layer_kv
    layer_bytes = theta + kappa
    graph_counts = _compute_graph_counts_from_w(
        state.devices, last_allocs, total_layers=L, cycle_count_k=k
    )

    for m, dev in enumerate(state.devices):
        w_m, n_m = last_allocs[m]
        c_m = w_m - n_m
        forced = dev.node_id in state.forced_m4_nodes or state.mmap_mode == "none"
        m_set, overload_bytes, disk_reload_ms = classify_device_set(
            state, dev, w_m=w_m, n_m=n_m, k=k, forced_m4=forced
        )
        total_w = k * w_m
        total_n = k * n_m
        total_c = k * c_m

        io_ms = float(dev.t_io_layer_ms) if dev.is_master and total_w > 0 else 0.0
        compute_ms = float(total_n) * float(dev.t_gpu_layer_ms) + float(total_c) * float(dev.t_cpu_layer_ms) + io_ms
        kv_sync_ms = float(total_c) * float(dev.t_kv_sync_ms) if dev.has_gpu and total_n > 0 else 0.0

        if active_count > 1 and total_w > 0:
            hop_wire_ms = float(dev.l_net_ms) + (float(state.hidden_state_bytes) / max(1.0, float(dev.b_net_bps))) * 1000.0
            network_ms = float(k) * hop_wire_ms
        else:
            network_ms = 0.0

        step_ms = compute_ms + kv_sync_ms + disk_reload_ms + network_ms
        total_compute_ms += compute_ms + kv_sync_ms
        total_disk_ms += disk_reload_ms
        total_net_ms += network_ms

        io_bytes = state.bytes_io_weight if dev.is_master else 0
        vram_used = total_n * layer_bytes if dev.has_gpu else 0
        ram_used = min(dev.avail_ram_bytes, total_c * layer_bytes + io_bytes)

        graphs = graph_counts.get(dev.node_id, 1)
        node_allocations.append(
            HighsNodeAllocation(
                node_id=dev.node_id,
                rank=m,
                w_m=w_m,
                n_m=n_m,
                cpu_layers_per_cycle=c_m,
                total_layers=total_w,
                total_gpu_layers=total_n,
                total_cpu_layers=total_c,
                memory_pressure_set=m_set,
                initial_memory_pressure_set=initial_sets[m],
                forced_m4=forced,
                overloaded_memory_bytes=overload_bytes,
                vram_used_bytes=int(vram_used),
                ram_used_bytes=int(ram_used),
                compute_ms=round(compute_ms, 6),
                disk_reload_ms=round(disk_reload_ms, 6),
                kv_and_sync_ms=round(kv_sync_ms, 6),
                network_ms=round(network_ms, 6),
                total_step_ms=round(step_ms, 6),
                prima_graphs=graphs,
            )
        )

    total_obj_ms = total_compute_ms + total_disk_ms + total_net_ms
    return HaldaCycleResult(
        cycle_count_k=k,
        window_size_w=W,
        iterations=iterations,
        solver_backend=last_sol.solver_backend,
        objective_step_ms=round(total_obj_ms, 6),
        compute_step_ms=round(total_compute_ms, 6),
        disk_reload_step_ms=round(total_disk_ms, 6),
        network_step_ms=round(total_net_ms, 6),
        node_allocations=node_allocations,
    )


def _evaluate_all_cycles(
    state: HaldaProblemState,
) -> tuple[HaldaCycleResult | None, list[HighsCycleCandidate]]:
    L = state.total_layers
    divisors = (
        [d for d in range(1, L + 1) if L % d == 0]
        if state.segmented_placement_enabled
        else [1]
    )
    evaluated: list[HighsCycleCandidate] = []
    best: HaldaCycleResult | None = None

    for k in divisors:
        emit("highs", "evaluating cycle", cycle=k, window=L // k, nodes=len(state.devices))
        res = assign_layers_to_device(state, k)
        emit("highs", "cycle result", cycle=k, feasible=res is not None,
             objective_step_ms=res.objective_step_ms if res else None)
        if res is None:
            evaluated.append(
                HighsCycleCandidate(
                    cycle_count_k=k,
                    window_size_w=L // k,
                    feasible=False,
                    objective_step_ms=None,
                )
            )
        else:
            evaluated.append(
                HighsCycleCandidate(
                    cycle_count_k=k,
                    window_size_w=L // k,
                    feasible=True,
                    objective_step_ms=res.objective_step_ms,
                )
            )
            if best is None or res.objective_step_ms < best.objective_step_ms - MILP_TOLERANCE:
                best = res
    return best, evaluated


def assign_layers_and_select_devices(state: HaldaProblemState) -> HighsScheduleReport:
    """Solve Halda MILP across cycle counts k | L, slow-disk M4 protection, and weak-worker pruning."""
    slow_disk_nodes = frozenset(
        dev.node_id
        for dev in state.devices
        if dev.disk_seq_bw_bps < SLOW_DISK_THRESHOLD_BPS and state.mmap_mode != "none"
    )
    active_devices = list(state.devices)
    pruned_nodes: list[HighsPrunedNode] = []
    forced_m4_rollback = False
    outer_iterations = 0

    while True:
        outer_iterations += 1
        cur_forced = slow_disk_nodes & frozenset(d.node_id for d in active_devices)
        cur_state = HaldaProblemState(
            devices=active_devices,
            total_layers=state.total_layers,
            bytes_per_layer_weight=state.bytes_per_layer_weight,
            bytes_per_layer_kv=state.bytes_per_layer_kv,
            bytes_io_weight=state.bytes_io_weight,
            hidden_state_bytes=state.hidden_state_bytes,
            mmap_mode=state.mmap_mode,
            segmented_placement_enabled=state.segmented_placement_enabled,
            just_enough_master=state.just_enough_master,
            placement_strategy=state.placement_strategy,
            forced_m4_nodes=cur_forced,
        )
        best, evaluated = _evaluate_all_cycles(cur_state)
        if best is None and cur_forced:
            emit("highs", "relaxing slow-disk M4 constraint after infeasible solve")
            # Roll back slow-disk M4 forcing when it makes the MILP infeasible (FR-003 / FR-005)
            forced_m4_rollback = True
            cur_state = HaldaProblemState(
                devices=active_devices,
                total_layers=state.total_layers,
                bytes_per_layer_weight=state.bytes_per_layer_weight,
                bytes_per_layer_kv=state.bytes_per_layer_kv,
                bytes_io_weight=state.bytes_io_weight,
                hidden_state_bytes=state.hidden_state_bytes,
                mmap_mode=state.mmap_mode,
                segmented_placement_enabled=state.segmented_placement_enabled,
                just_enough_master=state.just_enough_master,
                placement_strategy=state.placement_strategy,
                forced_m4_nodes=frozenset(),
            )
            best, evaluated = _evaluate_all_cycles(cur_state)

        if best is None:
            return HighsScheduleReport(
                scheduler_mode="capacity_first",
                requested_strategy=state.placement_strategy,
                solver_backend="heuristic_fallback",
                total_layers=state.total_layers,
                cycle_count_k=1,
                window_size_w=state.total_layers,
                segmented_placement_enabled=state.segmented_placement_enabled,
                mmap_mode=state.mmap_mode,
                just_enough_master=state.just_enough_master,
                fallback_reason="milp_infeasible_for_cluster_budget",
                forced_m4_rollback=forced_m4_rollback,
                iterations=outer_iterations,
                pruned_nodes=pruned_nodes,
                evaluated_cycles=evaluated,
            )

        # Check weak-device pruning condition (w_m <= 1 and n_m == 0, or w_m == 0) on non-master, non-required workers
        dev_by_id = {d.node_id: d for d in active_devices}
        newly_pruned: set[str] = set()
        for alloc in best.node_allocations:
            dev = dev_by_id[alloc.node_id]
            if dev.is_master or dev.required:
                continue
            if alloc.w_m == 0:
                emit("highs", "pruned worker", node=alloc.node_id, reason="zero_layers_assigned")
                newly_pruned.add(alloc.node_id)
                pruned_nodes.append(
                    HighsPrunedNode(
                        node_id=alloc.node_id,
                        reason="zero_layers_assigned",
                        iteration=outer_iterations,
                        last_w_m=alloc.w_m,
                        last_n_m=alloc.n_m,
                        detail="Worker received 0 layers per cycle in optimal MILP schedule",
                    )
                )
            elif alloc.w_m <= 1 and alloc.n_m == 0 and len(active_devices) > 1:
                emit("highs", "pruned worker", node=alloc.node_id, reason="weak_device_w_le_1_ngl_0")
                newly_pruned.add(alloc.node_id)
                pruned_nodes.append(
                    HighsPrunedNode(
                        node_id=alloc.node_id,
                        reason="weak_device_w_le_1_ngl_0",
                        iteration=outer_iterations,
                        last_w_m=alloc.w_m,
                        last_n_m=alloc.n_m,
                        detail="Weak worker assigned <= 1 CPU layer and 0 GPU layers per cycle",
                    )
                )

        if not newly_pruned or len(active_devices) - len(newly_pruned) < 1:
            # Re-index ranks contiguously for the final active node allocations
            final_allocs = [
                alloc.model_copy(update={"rank": idx})
                for idx, alloc in enumerate(best.node_allocations)
                if alloc.w_m > 0 or dev_by_id[alloc.node_id].is_master
            ]
            return HighsScheduleReport(
                scheduler_mode="highs_halda",
                requested_strategy=state.placement_strategy,
                solver_backend=best.solver_backend,
                total_layers=state.total_layers,
                cycle_count_k=best.cycle_count_k,
                window_size_w=best.window_size_w,
                segmented_placement_enabled=state.segmented_placement_enabled,
                mmap_mode=state.mmap_mode,
                just_enough_master=state.just_enough_master,
                objective_step_ms=best.objective_step_ms,
                compute_step_ms=best.compute_step_ms,
                disk_reload_step_ms=best.disk_reload_step_ms,
                network_step_ms=best.network_step_ms,
                fallback_reason=None,
                missing_profile_nodes=[],
                forced_m4_rollback=forced_m4_rollback,
                iterations=outer_iterations + best.iterations - 1,
                node_allocations=final_allocs,
                pruned_nodes=pruned_nodes,
                evaluated_cycles=evaluated,
            )

        active_devices = [d for d in active_devices if d.node_id not in newly_pruned]


def build_segments_for_schedule(
    node_allocations: list[HighsNodeAllocation],
    *,
    total_layers: int,
    cycle_count_k: int,
) -> tuple[list[Segment], dict[str, list[LayerRange]], dict[str, int]]:
    """Expand (w_m, n_m, k) into contiguous ring Segment blocks, per-node LayerRanges, and prima_graphs."""
    active = [a for a in node_allocations if a.w_m > 0]
    segments: list[Segment] = []
    per_node_ranges: dict[str, list[LayerRange]] = {a.node_id: [] for a in node_allocations}

    cursor = 0
    for _cycle in range(cycle_count_k):
        for alloc in active:
            start = cursor
            end = cursor + alloc.w_m
            cursor = end
            segments.append(
                Segment(
                    node_id=alloc.node_id,
                    start=start,
                    end=end,
                )
            )
            per_node_ranges[alloc.node_id].append(LayerRange(start=start, end=end))

    if cursor != total_layers:
        raise ValueError(f"Expanded segments cover {cursor} layers, expected {total_layers}")

    # Merge adjacent ranges on the same node when single-node (len(active) == 1)
    if len(active) == 1 and active[0].node_id in per_node_ranges:
        only_id = active[0].node_id
        per_node_ranges[only_id] = [LayerRange(start=0, end=total_layers)]
        segments = [
            Segment(
                node_id=only_id,
                start=0,
                end=total_layers,
            )
        ]

    n_by_node = {a.node_id: a.n_m for a in node_allocations}
    master_id = next((a.node_id for a in node_allocations if a.rank == 0), node_allocations[0].node_id if node_allocations else "master")
    visits = [
        _VisitAllocation(
            node_id=seg.node_id,
            layer_start=seg.start,
            layer_end=seg.end,
            device="GPU0" if n_by_node.get(seg.node_id, 0) > 0 else "CPU",
            memory_kind="gpu" if n_by_node.get(seg.node_id, 0) > 0 else "cpu",
        )
        for seg in segments
    ]
    reserve = reserve_graph_visits(visits, 0, total_layers, master_id, 0)
    per_node_graphs = {
        alloc.node_id: max(1, int(reserve.graph_counts.get(alloc.node_id, 1)))
        for alloc in node_allocations
    }

    return segments, per_node_ranges, per_node_graphs


def _normalize_mmap_mode(mmap_mlock: str | None) -> str:
    if mmap_mlock == "none":
        return "none"
    if mmap_mlock in ("mlock", "mmap+mlock"):
        return "prefetch"
    return "readahead"


def _fallback_allocations_from_plan(
    base_plan: dict[str, Any],
    members: dict[str, Any],
    *,
    total_layers: int,
    mmap_mode: str,
) -> list[HighsNodeAllocation]:
    allocs_by_node: dict[str, tuple[int, int]] = {}
    for item in base_plan.get("device_allocations") or []:
        node_id = str(item.get("node_id") or "")
        if not node_id:
            continue
        layers = int(item.get("layers", 0)) - (1 if item.get("output_layer") else 0)
        layers = max(0, layers)
        prev_w, prev_n = allocs_by_node.get(node_id, (0, 0))
        is_gpu = str(item.get("backend") or "cpu").lower() != "cpu"
        allocs_by_node[node_id] = (prev_w + layers, prev_n + (layers if is_gpu else 0))

    master_id = next(
        (nid for nid, m in members.items() if (m or {}).get("role") == "master"),
        next(iter(members.keys()), "master"),
    )
    cpu_layers = int(base_plan.get("cpu_layers") or 0)
    if cpu_layers > 0:
        prev_w, prev_n = allocs_by_node.get(master_id, (0, 0))
        allocs_by_node[master_id] = (prev_w + cpu_layers, prev_n)

    if not allocs_by_node:
        ngl = min(total_layers, int(base_plan.get("ngl") or 0))
        allocs_by_node[master_id] = (total_layers, ngl)

    ordered_ids = [master_id] + [nid for nid in allocs_by_node if nid != master_id]
    assigned_sum = sum(w for w, _ in allocs_by_node.values())
    if assigned_sum != total_layers and ordered_ids:
        first = ordered_ids[0]
        w0, n0 = allocs_by_node.get(first, (0, 0))
        new_w0 = max(0, w0 + (total_layers - assigned_sum))
        allocs_by_node[first] = (new_w0, min(n0, new_w0))

    result: list[HighsNodeAllocation] = []
    for rank_idx, node_id in enumerate(ordered_ids):
        if node_id not in allocs_by_node:
            continue
        w_m, n_m = allocs_by_node[node_id]
        n_m = min(w_m, max(0, n_m))
        result.append(
            HighsNodeAllocation(
                node_id=node_id,
                rank=rank_idx,
                w_m=w_m,
                n_m=n_m,
                cpu_layers_per_cycle=w_m - n_m,
                total_layers=w_m,
                total_gpu_layers=n_m,
                total_cpu_layers=w_m - n_m,
                memory_pressure_set="M4",
                initial_memory_pressure_set="M4",
                forced_m4=(mmap_mode == "none"),
                overloaded_memory_bytes=0,
                vram_used_bytes=int((base_plan.get("node_vram_alloc_bytes") or {}).get(node_id, 0)),
                ram_used_bytes=int((base_plan.get("node_ram_alloc_bytes") or {}).get(node_id, 0)),
                compute_ms=0.0,
                disk_reload_ms=0.0,
                kv_and_sync_ms=0.0,
                network_ms=0.0,
                total_step_ms=0.0,
                prima_graphs=1,
            )
        )
    return result


@operation("highs")
def solve_highs_halda_schedule(
    *,
    body: Any,
    runtime: dict[str, Any],
    members: dict[str, Any],
    base_plan: dict[str, Any],
    layout: dict[str, Any] | None = None,
    profiles: dict[str, DeviceProfile] | None = None,
    links: dict[tuple[str, str], Any] | None = None,
) -> HighsScheduleReport:
    """High-level Prima planning entry point for strategy selection, evidence gating, and Halda MILP."""
    raw_strategy = getattr(body, "placement_strategy", "auto") or "auto"
    if raw_strategy not in ("auto", "capacity_first", "priority"):
        raw_strategy = "auto"
    requested_strategy: PlacementStrategyMode = raw_strategy
    node_priority = list(getattr(body, "node_priority", []) or [])
    if requested_strategy == "auto" and node_priority:
        requested_strategy = "priority"

    total_layers = int(
        (layout or {}).get("n_layers")
        or len((layout or {}).get("layers") or [])
        or base_plan.get("estimated_layers")
        or 32
    )
    total_layers = max(1, total_layers)
    features = ((runtime.get("capabilities") or {}).get("features") or [])
    segmented_enabled = "segmented_placement" in features
    mmap_mode = _normalize_mmap_mode(getattr(body, "mmap_mlock", "auto"))
    just_enough = bool(getattr(body, "just_enough_master", False))

    if requested_strategy in ("capacity_first", "priority"):
        mode: SchedulerMode = "priority" if requested_strategy == "priority" else "capacity_first"
        reason = (
            "operator_selected_priority"
            if mode == "priority"
            else "operator_selected_capacity_first"
        )
        fallback_allocs = _fallback_allocations_from_plan(
            base_plan, members, total_layers=total_layers, mmap_mode=mmap_mode
        )
        return HighsScheduleReport(
            scheduler_mode=mode,
            requested_strategy=requested_strategy,
            solver_backend="heuristic_fallback",
            total_layers=total_layers,
            cycle_count_k=1,
            window_size_w=total_layers,
            segmented_placement_enabled=segmented_enabled,
            mmap_mode=mmap_mode,
            just_enough_master=just_enough,
            fallback_reason=reason,
            missing_profile_nodes=[],
            node_allocations=fallback_allocs,
            evaluated_cycles=[
                HighsCycleCandidate(
                    cycle_count_k=1,
                    window_size_w=total_layers,
                    feasible=True,
                    objective_step_ms=0.0,
                )
            ],
        )

    # Strategy is "auto": check DeviceProfile completeness for cluster members
    raw_profiles = profiles or {}
    normalized_profiles: dict[str, DeviceProfile] = {}
    for k_id, raw_prof in raw_profiles.items():
        if raw_prof is None:
            continue
        normalized_profiles[str(k_id)] = (
            raw_prof if isinstance(raw_prof, DeviceProfile) else DeviceProfile.model_validate(raw_prof)
        )
    missing_nodes = [
        str(node_id)
        for node_id in members
        if str(node_id) not in normalized_profiles
    ]
    if missing_nodes or not normalized_profiles:
        fallback_allocs = _fallback_allocations_from_plan(
            base_plan, members, total_layers=total_layers, mmap_mode=mmap_mode
        )
        return HighsScheduleReport(
            scheduler_mode="capacity_first",
            requested_strategy="auto",
            solver_backend="heuristic_fallback",
            total_layers=total_layers,
            cycle_count_k=1,
            window_size_w=total_layers,
            segmented_placement_enabled=segmented_enabled,
            mmap_mode=mmap_mode,
            just_enough_master=just_enough,
            fallback_reason="missing_device_profiles",
            missing_profile_nodes=sorted(missing_nodes),
            node_allocations=fallback_allocs,
            evaluated_cycles=[
                HighsCycleCandidate(
                    cycle_count_k=1,
                    window_size_w=total_layers,
                    feasible=True,
                    objective_step_ms=0.0,
                )
            ],
        )

    # Construct HaldaDeviceInput for each member in deterministic master-first order
    master_id = next(
        (str(nid) for nid, m in members.items() if (m or {}).get("role") == "master"),
        next(iter(members.keys())),
    )
    ordered_ids = [master_id] + [str(nid) for nid in members if str(nid) != master_id]
    required_set = set(getattr(body, "required_nodes", []) or [])

    layer_weights = list((layout or {}).get("layer_bytes") or [])
    if not layer_weights and (layout or {}).get("layers"):
        layer_weights = [
            int(item.get("bytes", 0))
            for item in ((layout or {}).get("layers") or [])
            if isinstance(item, dict) and item.get("bytes")
        ]
    if not layer_weights:
        sample_prof = next(iter(normalized_profiles.values()), None)
        if sample_prof and sample_prof.weights_bytes_by_index:
            layer_weights = list(sample_prof.weights_bytes_by_index)
    if layer_weights:
        bytes_per_layer_weight = max(1, int(sum(layer_weights) // len(layer_weights)))
    else:
        weights_est = int(base_plan.get("weights_estimate_bytes") or base_plan.get("model_size_bytes") or (total_layers * 64 * 1024**2))
        bytes_per_layer_weight = max(1, weights_est // total_layers)

    kv_est = int(base_plan.get("kv_estimate_bytes") or (total_layers * 4 * 1024**2))
    bytes_per_layer_kv = max(1, kv_est // total_layers)
    io_bytes = int((layout or {}).get("input_bytes", 0) or 0) + int((layout or {}).get("output_bytes", 0) or 0)
    metadata = (layout or {}).get("metadata") or {}
    arch = (layout or {}).get("architecture")
    embd_meta = metadata.get(f"{arch}.embedding_length") if arch else None
    hidden_bytes = max(
        4096,
        int((layout or {}).get("n_embd") or embd_meta or 4096) * 4,
    )

    gpu_budgets = base_plan.get("node_gpu_budget_bytes") or {}
    ram_budgets = base_plan.get("node_ram_budget_bytes") or {}

    halda_devices: list[HaldaDeviceInput] = []
    num_nodes = len(ordered_ids)
    for idx, node_id in enumerate(ordered_ids):
        member = members.get(node_id) or {}
        prof = normalized_profiles[node_id]
        next_id = ordered_ids[(idx + 1) % num_nodes]
        link = (links or {}).get((node_id, next_id))

        cpu_ms = (
            float(sum(prof.cpu_layer_ms_by_index) / len(prof.cpu_layer_ms_by_index))
            if prof.cpu_layer_ms_by_index
            else float(prof.cpu_layer_ms)
        )
        gpu_ms = (
            float(sum(prof.gpu_layer_ms_by_index) / len(prof.gpu_layer_ms_by_index))
            if prof.gpu_layer_ms_by_index
            else float(prof.gpu_layer_ms if prof.gpu_layer_ms is not None else cpu_ms)
        )
        has_gpu = prof.backend != "cpu" and (prof.gpu_layer_ms is not None or bool(prof.gpu_layer_ms_by_index) or (prof.decode_ms is not None and (prof.gpu_layers or 0) > 0))
        if has_gpu and prof.gpu_layer_ms is None and not prof.gpu_layer_ms_by_index and prof.decode_ms is not None:
            gpu_ms = max(0.01, float(prof.decode_ms) / max(1, total_layers))
        avail_vram = int(
            gpu_budgets.get(node_id)
            or member.get("vram_bytes")
            or (prof.memory_available_bytes if has_gpu else 0)
        )
        avail_ram = int(
            ram_budgets.get(node_id)
            or member.get("available_ram_bytes")
            or member.get("total_ram_bytes")
            or prof.memory_available_bytes
            or 8 * 1024**3
        )
        # Ensure the base plan's own capacity allocation is always considered feasible in M4
        planned_vram = int((base_plan.get("node_vram_alloc_bytes") or {}).get(node_id, 0))
        planned_ram = int((base_plan.get("node_ram_alloc_bytes") or {}).get(node_id, 0))
        avail_vram = max(avail_vram, planned_vram, total_layers * (bytes_per_layer_weight + bytes_per_layer_kv) if has_gpu and avail_vram == 0 else avail_vram)
        avail_ram = max(avail_ram, planned_ram, total_layers * (bytes_per_layer_weight + bytes_per_layer_kv) + io_bytes if avail_ram == 0 else avail_ram)

        l_net_ms = 0.0
        b_net_bps = 1_250_000_000.0
        if link is not None:
            l_net_ms = float(getattr(link, "latency_ms", None) or (link.get("latency_ms") if isinstance(link, dict) else 0.2) or 0.2)
            b_net_bps = float(
                getattr(link, "throughput_bytes_s", None)
                or getattr(link, "bandwidth_bytes_s", None)
                or ((link.get("throughput_bytes_s") or link.get("bandwidth_bytes_s")) if isinstance(link, dict) else 1_250_000_000.0)
                or 1_250_000_000.0
            )

        io_ms = float((prof.input_ms or 0.0) + (prof.output_gpu_ms or prof.output_cpu_ms or 0.0))
        halda_devices.append(
            HaldaDeviceInput(
                node_id=node_id,
                is_master=(node_id == master_id),
                required=(node_id in required_set),
                os_type=str(member.get("platform") or "Linux"),
                has_gpu=has_gpu,
                unified_memory=bool(member.get("unified_memory", False)),
                is_integrated_gpu=bool(member.get("is_integrated_gpu", False)),
                avail_ram_bytes=avail_ram,
                avail_vram_bytes=avail_vram if has_gpu else 0,
                total_vram_bytes=avail_vram if has_gpu else 0,
                disk_seq_bw_bps=float(prof.disk_read_bytes_s or 1_000_000_000.0),
                disk_rnd_bw_bps=float(prof.disk_random_read_bytes_s or prof.disk_read_bytes_s or 500_000_000.0),
                t_cpu_layer_ms=cpu_ms,
                t_gpu_layer_ms=gpu_ms,
                t_kv_sync_ms=float(prof.gpu_kv_copy_ms or 0.0),
                t_io_layer_ms=io_ms if node_id == master_id else 0.0,
                l_net_ms=l_net_ms,
                b_net_bps=b_net_bps,
            )
        )

    state = HaldaProblemState(
        devices=halda_devices,
        total_layers=total_layers,
        bytes_per_layer_weight=bytes_per_layer_weight,
        bytes_per_layer_kv=bytes_per_layer_kv,
        bytes_io_weight=io_bytes,
        hidden_state_bytes=hidden_bytes,
        mmap_mode=mmap_mode,
        segmented_placement_enabled=segmented_enabled,
        just_enough_master=just_enough,
        placement_strategy="auto",
    )
    return assign_layers_and_select_devices(state)
