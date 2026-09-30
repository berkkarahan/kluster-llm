# Native Resource Probe Contract

**Version:** 1 (planned) | **Consumer:** existing local agent / trusted Python callers

## Public Python surface

Export from existing `python/kllm/native.py`:

```python
probe_resources(*, node_id: str, managed_processes: list[dict] | None = None) -> dict
close_resource_probes() -> None
```

`None` or `[]` requests host/device readings and no process readings. The agent passes its
registered child selection explicitly. Nonempty selections follow
[ProcessSelection](../data-model.md#processselection-and-processreading) and must match the
managed-child registry (PID, role and start identity), not merely a live process number.
Bounds: eight unique processes, 32 devices, 64 KiB per complete serialized request/response,
including its envelope and framing. Node identity
is <=128 characters; an agent uses its configured node ID. Invalid input raises `ValueError`
before scheduling work, with actionable sanitized detail. Unsupported counters and runtime
collection failures are structured unavailable observations, not request exceptions.

The return is [ResourceSnapshot](../data-model.md#resourcesnapshot). Every expected metric
for a known resource is present even if null. An empty device list means no GPUs only when
discovery status is `measured`. Failed first-probe discovery returns `devices=[]`, discovery
status `error` or `unsupported`, and a reason while preserving valid host/process readings.
Process selection never controls GPU discovery.

The wrapper has at most two helper slots per parent process and one active node identity.
Reject conflicting concurrent node identities; all callers share slots/baselines. Agent
shutdown calls `close_resource_probes`; subsequent use can reopen only after old children
are reaped. Close is idempotent and bounded; an unreaped child keeps its slot quarantined.

**Deadline:** Start monotonic budget at entry, validate/schedule both sources within it,
stop waiting by 1.8 seconds and return by two seconds under runnable-host conditions.
Completed source values survive another source's failure. Source queue contention produces
`source_busy` or `timeout`; it does not append unbounded work. Equivalent concurrent requests
can share one observation and retain its timestamp. Different selections cannot receive a
substituted process set. Freshness is based on metric time, not call completion.

## Private Rust/PyO3 boundary

Add `_UsageCollector` in `crates/kllm-native/src/lib.rs`, implemented by concrete Rust
types in `usage.rs` and platform siblings. Private helper-only API:

```text
_UsageCollector(source: "host" | "gpu")
collector.collect_json(request_json: str) -> str
```

This low-level synchronous call is not the deadline-bearing public API. It validates
bounded input, releases the GIL during native calls, and returns bounded serialized Rust
readings. It performs no subprocess execution, inference selection, registry-wide process
inventory, Python callback, or network request. GIL release also lets the macOS helper's
parent-death control watcher terminate while a driver call is blocked.

Host collector owns CPU baselines and PID-targeted handles. GPU collector owns DXGI/PDH or
Metal handles. One request at a time per collector. Clear/reset baselines on invalid
intervals and compare actual start identity around multi-call process reads. The helper
exits after a native panic/protocol violation; the parent returns source_error and reaps it.

Existing `probe_capacity`, `list_gpus`, `CapacitySnapshot`, and planner signatures/semantics
are unchanged. New dependencies are target-gated. Linux need not implement `_UsageCollector`;
the public wrapper returns explicit unsupported status there and the existing Linux Sampler
continues its current path.

## Private helper wire protocol and cleanup

Launch the environment's Python interpreter with `-m kllm.usage_probe --source host` or
`--source gpu`, no shell and no user-supplied executable/arguments. On Windows use the base
interpreter with `__PYVENV_LAUNCHER__` pointing to the environment executable: the venv
executable is a redirector whose PID is not the actual interpreter. This keeps the two
owned helper PIDs, Job Objects, timing and memory accounting attached to the real processes.
Other platforms use `sys.executable` directly. This module is packaged with the existing wheel. Pipes
are parent-only, inherited descriptors/handles minimized, unrelated credentials stripped from
unneeded environment entries, and stdout reserved for protocol (diagnostics sanitized/bounded).

Frames are UTF-8 JSON followed by one newline, <=64 KiB including envelope and newline.
Version 1 request contains `version`, `generation`, `request_id`, `node_id`,
`managed_processes`, and `reply_budget_bytes`; GPU requests carry an empty process list.
The reply budget is a positive integer <=65536 assigned by the supervisor and includes the
complete helper reply frame. Replies echo correlation fields and one source result.
Unknown keys, duplicate JSON keys, non-finite values, oversized/unterminated frames, unexpected
generation/identity and invalid UTF-8 are protocol errors. No executable content is accepted.
Before dispatch, reserve bytes for snapshot metadata, totals, mandatory failure shapes and
the resource-only ingest envelope for this node. Allocate the remaining bytes between the
two source replies so that merging them, including serialization overhead, cannot exceed the
complete-message limit. Individual <=64 KiB replies alone do not prove the merged result fits.
Use the same UTF-8 serialization and escaping rules for sizing and transmission.

If a result exceeds its assigned budget, first omit optional diagnostic `detail` text.
If it still exceeds the budget, replace only that source's result with a compact
`source_error` identifying the payload limit; reserve room for that failure result and any
required parent-side expansion when allocating budgets. Preserve the other source's readings.
Recheck complete merged size before returning. Never silently truncate measurements or label
an incomplete result complete; GPU-source loss makes discovery erroneous and its totals
partial/unavailable. Enforce receive limits before allocating an unbounded frame.

Allocate one fixed reader/control path per slot with bounded queues. Pipe writes and reads
must not block the public caller; no thread-per-request pattern. EOF/source crash becomes
source_error. Deadline triggers kill, closes response acceptance for that generation, and
returns unaffected metrics without waiting for termination. Reap outside the response path;
new generation only after confirmed exit. Retry on the next scheduled sample, not a tight loop.

Normal shutdown requests exit, then terminate/kill with a bounded wait, closes pipes and joins
completed readers. Windows Job Object `KILL_ON_JOB_CLOSE` covers parent death. On macOS the
helper's main control reader detects stdin EOF and exits the process even while its one native
sampling thread blocks; no forked descendants or inherited writers may keep that pipe alive.
These helpers must never enter the inference-child registry or be confused with managed models.

## Process registration and compatibility time

Capture exact start identity as part of successful launch, using the existing retained Child
handle on Windows and checked PID/start metadata before reaping on macOS. Add optional
`process_start_token`/canonical start metadata to launch results and existing runtime/member
records; preserve all existing required fields and accept old saved records.

`_tracked_process_start` stays on `kllm.agent` and returns the canonical captured UTC value.
Sampler, heartbeat/register and legacy history share that same string rather than separately
rounding an OS timestamp. Exact tokens distinguish intervals even if compatibility timestamps
would collide. New native bindings verify both selection and OS identity on every collection.

When restoring a legacy child record, compare the saved member start timestamp to a current
checked OS observation before binding the exact token. With no trustworthy saved start,
return identity_unverified and host/device readings; require managed relaunch to restore
process attribution. Do not adopt an unrelated process with the same PID.

## Missing/older extension

Check for the specific `_UsageCollector` export, not just `native_available()`.
Direct public probes return native_unavailable observations and a rebuild hint rather than
invented values. The existing agent may retain its legacy Sampler path while surfacing
`collector=legacy` and a native-unavailable notice; it cannot claim feature-010 native or
deadline certification. No fallback silently presents legacy capacity as new GPU usage.

## Observable compatibility/validation cases

- First host/process CPU is initializing while valid memory returns immediately.
- After two seconds, one busy logical core on eight yields approximately 12.5% process CPU.
- A reused PID returns mismatch for the requested old lifetime, not the replacement's RSS.
- Host-only call returns `processes=[]`, independent of workload role.
- First-probe GPU enumeration failure returns `devices=[]` with discovery `error`/`unsupported`
  and valid host readings; it is not reported as successful empty discovery.
- Windows shared RAM and Apple unified memory reference one host pool; GPU budgets never
  appear as physical capacity or device usage.
- A blocked GPU helper returns timeout within two seconds and preserves host results;
  the converse source case is also checked. Four concurrent calls do not create extra helpers.
- Kill/reap, close, restart, parent death and malformed helper-output checks cross the real
  subprocess and rebuilt PyO3 boundaries rather than only mocking collector methods.
- Individually valid source replies whose unbudgeted merge would exceed 64 KiB exercise the
  assigned-budget fallback and preserve the unaffected source without silent truncation.
