# Data Model: Responsive Node Workspace

**Date:** 2026-09-29

This feature projects existing monitor data and adds ephemeral view state. The persisted
model remains [feature 002's model](../002-node-usage-monitor/data-model.md).

## Node and reading projection

Source: `dashboard/src/lib/types.ts:UsageNode`, `UsageReading`, `UsageDevice`, `UsageLive`.
Identity is `node_id`, not hostname, array position, or current selection.

| Presentation field | Existing source | Rules |
| --- | --- | --- |
| Name and identity | `host`, `node_id`, `role` | Preserve stable keyed identity and distinguish duplicate hostnames. |
| Connection and freshness | `connected`, `freshness`, `age_seconds`, observation time | Show independently; cached values retain stale/disconnected labels. |
| Host CPU | `reading.host.cpu_percent`, `availability.host_cpu` | Whole-node percent; zero is measured, null/invalid is unavailable. |
| Host RAM | `reading.host.used_bytes`, `total_bytes`, `availability.host_memory` | One-decimal GiB; percentage derived only from valid sample bytes and positive total. |
| Reported capacity | `total_ram_bytes`, per-device inventory capacity | Label as reported capacity when sample totals are unavailable; do not derive a live percentage from fallback metadata. |
| GPU identity | Inventory `devices[].index/name`, reading `devices[].device_id` | Union by `String(index)`/`device_id`; retain reading-only and inventory-only devices. |
| GPU usage | `utilization_percent`, `used_bytes`, `total_bytes`, `unavailable_reason` | Device-wide values; each field can be independently missing. |
| Shared-memory label | `unified_memory` | One physical pool; no summed host-plus-device usage. |
| Process diagnostics | `reading.process`, PID, lifetime/start identity, `process_state` | Accessible through Details with explicit process scope. |
| Assignment | `assignment`, existing plan/activity context | Not evidence of measured local work. |

Formatting uses `dashboard/src/lib/format-memory.ts:formatGib`. Percent values must be finite
and in 0-100; memory values must be finite and nonnegative. Compute RAM percentage from
unrounded bytes; an inconsistent used/total pair does not become a fabricated valid percentage.
Availability `initializing`, `unsupported`, and `error` is kept as explanatory text. A generic
device reason does not suppress independently valid fields. Decorative RAM fill exists only
for a valid percentage.

No inventory or readings means a qualified no-GPUs-reported/unavailable state, not proof that
the machine physically has no accelerator. No managed process can legitimately mean no host
or device sample. Do not substitute configuration budgets or local-agent GPU state.

## Topology view state

Owned locally by `NodeMap.svelte`; not persisted and not transmitted.

| State | Meaning |
| --- | --- |
| Node positions/card bounds | Deterministic grid derived from ordered IDs and untransformed card dimensions. |
| Graph viewport | Current nonzero width/height used to fit full node bounds with padding. |
| `scale`, `x`, `y` | Existing world transform for graph navigation. |
| Automatic-fit flag | Initially true; manual navigation disables it; explicit Fit enables it. |

Transitions:

- First nodes plus usable dimensions -> automatic fit, with single-node scale at most 1.
- Resize or geometry change -> refit only while automatic; manual transform remains intact.
- Routine readings with unchanged geometry -> update text only; no selection/focus reset.
- Pan/zoom -> manual mode; zoom-out always reduces scale, including after a small Fit scale.
- Graph hidden for narrow list -> ignore zero-sized measurements, preserve graph state.
- Return to graph -> fit if automatic; retain manual state otherwise. Fit stays reachable.
- Unmount -> disconnect resize observation and release any active pointer gesture.

## Inspection context

Source of truth: existing `appStore.selectedNodeId`, `selectedLifetimeId`, `usageWindow`,
`usageLifetimes`, `usageSeries`, `usagePage`, and `historyError`.

Local Home state represents a closed dialog or Details/History content, the activating
control for focus restoration, and which node owns the loaded/requested history. Chat keeps
its existing independent open/closed state and stream state.

- Select a card with History closed -> update selection only; no history request.
- Open Details -> render the selected node's existing scoped diagnostics.
- Open History for a new node -> `selectUsageNode`; clear old content before displaying it.
- Reopen History for the same node -> preserve valid lifetime/window; refresh the current
  history, or retry lifetime loading if it failed/has never completed.
- Change lifetime/window -> existing callbacks and response-sequence protection.
- Close dialog -> restore trigger focus, retain selection, keep collection/streaming running.
- Selected node disappears -> follow the store's existing valid-node fallback; render no
  history under a different node identity. Reinitialize open History for the new selection.

Only render historical content when its loaded node identity matches the current context.
The UI must not defeat `historySeq` ordering or use one node's cached lifetimes for another.

## Recording state

Reuse `UsageLive.recording`, `usageError`, and history coverage metadata. The compact workspace
status exposes interruption/failure and last successful save even with panels closed. A live
request failure qualifies cached recorder health as last known, not confirmed current health.
