# Logs interface contract

**Date**: 2026-09-29 | **Status**: Proposed additive contract

## Existing surface and compatibility

Extend `GET /v1/logs` on the existing private agent control service. Keep `n`, `follow`,
`node`, `source`, the `lines/source/warning` envelope, and old POST ingestion bodies.
GET currently has no separate auth dependency; do not describe same-origin as authentication.
Worker forwarding uses the existing cluster-key boundary. No new listener or deletion API.

Legacy callers without `after` retain recent-tail behavior. Keep `follow=true` CLI/SSE
behavior, with additional entry identity allowed but no new metadata `data:` frames.
The new dashboard uses only `follow=false` reads.

## Snapshot request

| Input | Contract |
|-------|----------|
| `n` | Existing default 200 and clamp 1–5,000 |
| `follow` | Existing boolean; dashboard uses false |
| `node`, `source` | Existing exact filters; dashboard filters nodes locally |
| `after` | New optional opaque checkpoint, at most 128 characters; `<canonical UUID>:<nonnegative integer>` |

Reject malformed `after` with 400 and an actionable message. Reject `after` together with
`follow=true` with 400; streaming resume is not added by this change. A position beyond the
current high-water in the same epoch is invalid (400). A well-formed foreign epoch yields
a reset snapshot, not a reinterpretation of its sequence as a local position.

Without `after`, return the most recent `n` retained matches. With a current cursor, select
records with later receipt sequences, then retain the most recent `n` matches. Set a gap
if retention or this batch cap omitted intervening matching history; do not silently page
past unseen entries and claim full coverage. This endpoint is a bounded tail, not an archive.

Capture selected records and the global high-water under one lock. The response checkpoint
may advance across nonmatching records; callers must not reuse it with a changed filter if
they expect historical backfill. The dashboard always requests the unfiltered feed.

## Snapshot response

```json
{
  "lines": [
    {
      "event_id": "57a75357-6338-4e17-a49e-08af5f5751c0",
      "ts": 1790632000.25,
      "node_id": "worker-02",
      "source": "rpc",
      "line": "worker ready"
    }
  ],
  "source": "cluster",
  "cursor": "f451a2e9-ac31-413a-a70e-e845caa12a4e:42",
  "reset": null,
  "gap": false,
  "gap_reason": null
}
```

An empty response still supplies the current checkpoint. A feed reset supplies a bounded
recent snapshot with `reset=origin_changed` (proxy/local or ownership change) or `restart`
when known, `gap=true`, and `gap_reason=feed_reset`. Do not infer a more specific cause than
the server knows. A trimmed batch sets `batch_limit`; expired retained coverage sets
`retention`, which takes precedence if both occurred. `warning` remains optional.

## Identity, journal, and relay

- Add optional `event_id` to each `POST /v1/logs` line. Validate supplied UUIDs, generate
  one for legacy inputs, and preserve supplied values. Existing auth and response stay intact.
- Assign IDs before a worker's local-ingest/relay split; relay retries carry the same ID.
  Distinct identical lines receive different IDs. UI identity deduplication does not imply
  exactly-once journal ingestion or alter legacy sender semantics.
- Preserve IDs in newly persisted records and on reload. Old four-field journal rows load
  with runtime IDs. A new hub epoch explicitly resets read continuity; no destructive migration.
- Load retained master history before publishing new master events/checkpoints. Merge any
  startup-local records once, with known-ID deduplication; never silently ignore the journal
  because the ring is already nonempty. Ownership changes reset the feed epoch.
- Proxy the whole snapshot unchanged. Local fallback uses local-node records and its own
  checkpoint, with `source=local` and the existing master-unreachable warning. Retry the
  master on every later read. Preserve bounded upstream timeouts and close responses.
- If an upstream old master lacks checkpoint/identity support, report an explicit unsupported
  incremental-log capability instead of inventing cursors or silently showing only local logs.
  Legacy unversioned reads remain available to old callers.

## UI behavior contract

1. Mount performs a latest-200 read. Subsequent reads use `n=5000` and the committed cursor.
2. Live has a 1,000 ms foreground cadence; other deadlines are 5,000/30,000/60,000 ms.
   Schedule from monotonic deadlines rather than adding request duration to every interval.
   Allow one request at a time; skip missed deadlines rather than accumulating concurrent work.
3. Switching mode replaces the schedule from selection time; the next periodic read occurs
   after the new interval. With a committed cursor, Clear invalidates the active request
   while preserving mode and the next deadline. When `cursor === null` (no successful
   snapshot yet), Clear is a no-op: retain the request, generation, loading/error state,
   and schedule, leave `hasCleared=false`, and consume the first successful snapshot
   normally. A message becoming available between that Clear and the first completed read
   must not be discarded because of Clear. Mount/foreground recovery are immediate reads.
4. Check generation and disposed state after fetching and decoding, immediately before
   committing state. Abort alone does not prevent a queued callback from committing old data.
5. Clear removes every buffered row, including filter-hidden rows. Keep seen IDs and cursor;
   new unseen records are eligible. Do not issue POST, DELETE, truncation, or hub reset.
6. Only after Clear with an already committed cursor (`hasCleared=true`), handle a feed
   reset by ignoring its snapshot rows, adopting its checkpoint, and showing a recovery-gap
   notice before resuming incremental reading. The omitted window may include unseen
   post-Clear events. Do not apply this recovery exception to a first load after a no-op
   Clear. Keep the committed cursor on read failures so absence of a new result cannot
   accidentally reclassify a previously loaded session as an initial load.
7. Deduplicate accepted IDs across the session, preserving different IDs with identical text.
   Display at most 2,000 rows and disclose trimming. A new visit releases session identities.
8. Node filtering is local. Render roles only when known. Use escaped text and explicit UTC;
   arrival order remains stable despite backward/tied timestamps.
9. Preserve scroll anchor while reading older entries. If its row is trimmed, show the
   oldest remaining row and an omission notice rather than unexpectedly jumping to the tail.
10. Keyboard access and labels cover node selector, four refresh radio choices, Clear, and
    the log viewport. Status is textual; incoming rows do not each force a live announcement.
11. Keep current rows after errors; show last success and retry state. A healthy empty poll
    is not offline. Hidden tabs suspend acquisition; unmount releases all resources.
12. Every snapshot request, including initial and foreground-recovery reads, has a fixed
    5,000 ms total deadline from dispatch through body consumption/decoding and validation
    before commit. Receiving headers or body chunks does not extend it. Check the elapsed
    deadline before committing as well as arming an abort timer; a delayed timer callback
    must not admit an expired result. On expiry abort, invalidate that request's generation,
    release its in-flight slot, retain rows/cursor/coverage/last-success time, and show
    "Log read timed out; retrying". Retry at the first future deadline of the selected
    cadence, not a catch-up burst. Clear the request timer on success, error, timeout,
    mode change, effective Clear, hide, or unmount. Cancellation for those user/lifecycle
    actions is not a timeout error. A late completion must not commit or release a newer
    request's slot. The pre-initial-load no-op Clear leaves the request and its timer alone.

## Live-latency measurement boundary

For SC-002, start at the first moment an event is readable from the currently serving
feed and end at its first rendered row in the browser. Correlate by `event_id`; source
`ts`, request start, and response receipt are not substitutes for these boundaries.
Initial retained history is outside the sample. Use All nodes and steady foreground Live
for the fixture measurement, without Clear, mode changes, or interruptions during that
sample. Count an eligible entry not rendered within two seconds as a miss, rather than
excluding it from the denominator. [quickstart.md](../quickstart.md) defines the shared-clock
fixture procedure and the evidence needed to compare timestamps from a real cluster.

## Required boundary checks

Atomic checkpoint/read, identical distinct messages, overlap/replay, worker ID preservation,
legacy journal load and startup merge, cursor validation/reset/retention, master/local transitions,
old CLI compatibility, Clear during fetch/decode, all 12 directed mode transitions, filter-hidden
Clear, no-op Clear before first success with a newly arriving event, stalled fetch/body deadline
and late completion after retry, page cleanup, timing without jitter/drift, same-clock feed-to-render
latency including missed entries, markup-like log text, mobile/keyboard layout.
