# Quickstart & Verification Guide: HiGHS Scheduler Planning (`019-highs-scheduler-planning`)

**Feature**: `019-highs-scheduler-planning`  
**Date**: 2026-10-14

---

## 1. Run Backend HiGHS Scheduler & Prima Planning Tests

Run the dedicated HiGHS scheduler unit and integration tests alongside the existing Prima planning suite:

```powershell
uv run pytest tests/test_highs_scheduler.py tests/test_prima_windows.py tests/test_prima_lifecycle.py -q
```

### What This Verifies
- **Halda / HiGHS MILP Formulation (`FR-001`, `FR-002`)**:
  - Exact layer conservation ($\sum_m w_m = W = L / k$ and $\sum_m k \cdot w_m = L$) and per-rank GPU/CPU bounds ($0 \le n_m \le w_m$) across single-node and heterogeneous multi-node topologies.
- **Memory-Pressure Sets ($M_1 \dots M_4$) & Unrestricted `mmap` (`FR-003`)**:
  - Unrestricted disk-streaming vs. resident tradeoff when `mmap` is enabled (`"readahead"` / `"prefetch"`), and forced residence ($M_4$) when `mmap == "none"` or sequential disk speed is below threshold (`< 500 MB/s`) with automatic rollback if forcing $M_4$ is infeasible.
- **Iterative Weak-Worker Pruning (`FR-006`, `FR-007`)**:
  - Pruning of optional workers with $w_m \le 1, n_m = 0$ and preservation of master rank 0 and `required_nodes`.
- **Multi-Cycle ($k > 1$) Segmented Placement & Graph Reservation (`FR-004`, `FR-005`)**:
  - Evaluation of divisors $k \mid L$ when `"segmented_placement"` is present in runtime capabilities and accurate `prima_graphs` / `prima_graph_counts` generation via `reserve_graph_visits`.
- **Strategy Selection & Fallback (`FR-008`, `FR-011`)**:
  - `"auto"` selects `"highs_halda"` when complete `DeviceProfile` measurements exist, falls back cleanly to `"capacity_first"` with `fallback_reason` when profile data is missing, and respects explicit `"capacity_first"` or `"priority"` overrides.

---

## 2. Run Dashboard Type Checks & Unit Tests

Verify the Svelte 5 dashboard Run and Runtimes modules:

```powershell
npm --prefix dashboard run check
npm --prefix dashboard test
```

### What This Verifies
- Type safety for `HighsScheduleReport`, `HighsNodeAllocation`, `HighsPrunedNode`, and `PlacementStrategyMode` in [`dashboard/src/lib/modules/run/entities.ts`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/entities.ts) and [`dashboard/src/lib/modules/runtimes/entities.ts`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/runtimes/entities.ts).
- Rendering of the HiGHS schedule breakdown ($k, W, w_m, n_m$, $M_1 \dots M_4$ badges, latency components, pruned weak workers, and fallback notices) in [`DeploymentPlan.svelte`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/view/DeploymentPlan.svelte) and [`PrimaPreparation.svelte`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/runtimes/view/PrimaPreparation.svelte).
- Placement strategy selector (`Automatic (HiGHS / Capacity fallback)`, `Capacity-first`, `Priority`) in [`RunPage.svelte`](file:///D:/dev/kluster-llm/dashboard/src/lib/modules/run/view/RunPage.svelte).
