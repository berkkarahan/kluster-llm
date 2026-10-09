# Usage-monitor interfaces

Contract for the proposed agent additions. Keep existing `/v1/status`, register/heartbeat, `/v1/plan`, `/v1/run`, start/stop/join/leave and chat fields/availability compatible. Examples show only permitted measurement metadata; never include prompts, responses, join keys, command lines or unrelated processes. All times are ISO 8601 UTC (`Z`); memory and limits are bytes on the wire; utilization/CPU are percentages of their labeled scope. Unsupported/error values are `null` with a reason, not numeric zero.

## Private collection boundary

`POST /v1/usage/ingest`: worker→master only via existing private control client and `X-Kllm-Key`. Master rejects non-master role, unregistered `node_id`, mismatched lifetime/node identity, invalid clock skew (over 30 seconds in the future), observations outside the rolling 24-hour retention window, non-finite/out-of-range values, unknown scope, invalid PID/start time, oversize batch (>8 points or 64 KiB), and payloads with unexpected fields. Do not update member liveness or restart a workload when ingest fails. Master itself uses the same persistence validation for local samples, without an HTTP round trip.

```json
{
  "node_id": "a1b2c3d4",
  "readings": [{
    "lifetime_id": "a1b2c3d4:4812:2026-09-28T12:00:00.000Z",
    "pid": 4812,
    "process_started_at": "2026-09-28T12:00:00.000Z",
    "process_role": "worker",
    "observed_at": "2026-09-28T12:00:02.000Z",
    "process": {"cpu_percent_machine": 12.5, "rss_bytes": 2147483648},
    "host": {"cpu_percent": 16.0, "used_bytes": 4294967296, "total_bytes": 17179869184},
    "devices": [{"device_id": "0", "scope": "device", "utilization_percent": null,
      "used_bytes": null, "total_bytes": 8589934592, "temperature_c": null,
      "power_w": null, "unavailable_reason": "utilization/temperature/power unsupported"}],
    "availability": {"process_cpu": "measured", "process_rss": "measured",
      "device_utilization": "unsupported"}
  }]
}
```

Strict validation: accepted `process_role` is `master` or `worker`, samples reference an actual tracked process start identity and valid registered node; percentages are 0–100 inclusive, bytes and watts are nonnegative, temperature must be finite and within the documented sensor range. A process CPU first sample has `cpu_percent_machine: null` and availability `initializing`. Device-wide `used_bytes` is never process-exclusive. The server replies `{"accepted": 1, "duplicates": 0, "last_successful_save": "...Z"}` after durable commit; duplicates identified by `(lifetime_id, observed_at)` do not create another point; invalid requests receive HTTP 4xx with actionable detail; unavailable storage returns 503 with sanitized status. Unknown future sample fields require explicit versioned support, not silent persistence.

## Read-only same-origin endpoints

All browser reads are on the serving agent's same origin, under its **existing local/private-cluster boundary**. A worker agent proxies reads to the master server-side using its join key (as existing logs do), or reports master unavailable; it must not silently substitute incomplete worker-local history. No browser call gets a new join-key header. Where stronger operator authentication is introduced separately, apply the same existing access rule to usage reads; do not open a separate public listener.

| Endpoint | Required input / bound | Output |
| --- | --- | --- |
| `GET /v1/usage/live` | No input | `nodes` map keyed by stable ID; latest accepted process/host/device reading, `observed_at`, `received_at`, `age_seconds`, `fresh` or `stale` or `unavailable`, current process/lifetime (or explicit no managed process), plus cluster `recording` status (`recording`/`interrupted`/`failed`), error and `last_successful_save`. Nodes departed from live membership have `connected: false` while retained history exists. Dashboard disconnection comes from existing `viewState`, not a false healthy reading. |
| `GET /v1/usage/lifetimes?node_id=...` | Required stable node ID; list limited to retained 24-hour coverage | Node identity/host, connected state, process lifetimes (PID, role, actual start and observed end, model/workload associations with known time bounds), earliest retained time, storage truncation and recorder status. Also provide retained node IDs so a departed node remains selectable. Unknown node → 404; empty retained history → empty list with coverage `unrecorded`. |
| `GET /v1/usage/series?node_id=...&lifetime_id=...&window=15m` | Required node ID, lifetime ID and enum `15m`/`1h`/`24h`; maximum 720 plotted time buckets | Ordered, bounded time buckets with process CPU/RSS min/max, optional scoped host/device readings, bucket time span/count, and explicit empty buckets/gaps. Return coverage earliest/latest, window start/end, storage truncation, time zone UTC and recording status. A bucket of measured zero has count > 0; missing data has count 0. Do not connect a plot across a gap. |
| `GET /v1/usage/readings?node_id=...&lifetime_id=...&window=15m&limit=500&cursor=...` | Same filters; limit 1–1000 and opaque seek cursor | Exact timestamped readings in stable `(observed_at, id)` order, `next_cursor` or `null`, and coverage/status. Cursor is bound to filters/time window; reject mismatch/invalid cursor with 400, never scan/return unbounded history. Read failures return actionable 503 without hiding unaffected readable history. |

All reads have bounded inputs and errors; live staleness uses **observation time** (and separately shows receipt time when clocks drift). A missing/late worker sample cannot overwrite a later live observation. History explicitly labels periods before first recording as `unrecorded`; missing two-second intervals inside observed coverage are gaps, not zeros. Earliest retained time reflects 24-hour or storage-limit pruning. Server errors must distinguish unavailable master, failed recording and damaged history where possible, without echoing credentials.

## Workspace UI contract

- Node button exposes accessible name with host, role, identity and textual connection/measurement status. Known master–worker SVG edges are decorative to assistive technology; the same relationships appear in text. Duplicate hostnames are distinguished by node ID. Select a node without changing membership, inference or chat; selection and pan/zoom persist through refresh, and Fit restores bounds. A disconnected selected node remains in the selectable history/details list while retained.
- Details distinguish process CPU (% entire logical machine), process RSS, host CPU/RAM, device-specific utilization/memory/temperature/power, physical capacity and planner estimate. Each shows scope, unit, freshness/availability and observation time. Unified RAM is counted once; incomplete aggregates say `partial`. Assignment and cluster activity have separate labels from measured computation.
- History filters node → actual process lifetime → `15m` / `1h` / `24h`; bounded visual series plus keyboard-operable timestamped exact readings. Focus and selected point reveal values, units and explicit gaps in text without hover or color. Text contrasts meet WCAG AA; visible focus, reduced motion and 390px stacked/list navigation preserve the same information. Loading, idle, disconnected, no managed process, recording failed and pre-monitoring periods are distinct states.
- Start/Stop/Join/Leave/Update workers, Run model planning/launch and chat continue to use existing UI routes and disable rules; monitoring navigation does not mutate workloads or advertise unsupported split modes. Reference [../data-model.md](../data-model.md) for state/retention semantics and [../quickstart.md](../quickstart.md) for validation.
