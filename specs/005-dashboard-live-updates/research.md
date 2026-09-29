# Research: Dashboard Live Updates

**Created**: 2026-09-29
**Status**: Complete for planning
**Repo / scope**: kluster-llm — five dashboard update areas
**Baseline**: `a0a4f5259c7dfd5c204deb5ff0972f27b8e87c69`
**Specification**: [spec.md](spec.md)

## Research questions and disposition

| Question | Resolution |
|---|---|
| SSE or WebSocket for five viewers alongside chat? | Native WebSocket; retain chat's existing HTTP stream. |
| New dependencies or infrastructure? | None; FastAPI/Starlette and `uvicorn[standard]` already provide the server transport. |
| Sampling per connection or shared state? | One application-owned acquisition pipeline per source, with bounded viewer demand. |
| Ordering between pushed data and explicit reads? | One versioned GET/WS dashboard contract; serialize versionless legacy fallback. |
| Recoverable Logs and Clear? | Existing snapshot/checkpoint semantics, whole-batch acknowledgments, and subscription generations. |
| Worker scope and upstream acquisition? | Preserve existing topic scopes; share upstream usage/log reads and mirror logs in memory. |
| Identify completed operations? | Source-owned UUIDs and retained active/latest-terminal records for load and download. |
| Detect denial and silent failure? | HTTP-readable admission plus explicit socket errors; application heartbeat and bounded deadlines. |
| Existing tests and performance proof? | pytest, Node tests, existing real-browser CDP harness, and an added real-Uvicorn transport check. |

All planning unknowns are resolved below. Performance targets remain acceptance work,
not measurements established by this research.

## 1. Native WebSocket, not another long-lived HTTP response

**Decision:** One same-origin native WebSocket per visible dashboard tab, carrying all
requested topics. Keep HTTP commands, chat streaming, and timed log reads. Use one new
path, `/v1/dashboard/live`, for both WebSocket delivery and a versioned HTTP snapshot.

**Rationale:** `python/kllm/agent/__main__.py:main` uses a direct Uvicorn deployment
without enabling HTTP/2. Five dashboard SSE responses plus one chat response exhaust
Chromium's ordinary six-connection-per-origin pool; fetch-streamed SSE uses that same
pool. Chromium has a separate WebSocket connection pool. WebSocket also permits changing
Logs interests without closing/reopening the shared feed. The declared
`uvicorn[standard]` dependency already includes WebSocket support.

**Alternatives considered:** Native EventSource is simpler for a single read-only stream
but obscures HTTP denials and has the connection-budget issue. Fetch-streamed SSE exposes
status and cancellation but retains that issue. Browser-facing HTTP/2 or a cross-tab shared
SSE owner would add deployment or coordination requirements. Socket.IO, brokers, and custom
socket servers solve no current requirement.

**Sources:**
- [MDN: SSE connection limits](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events#receiving_events_from_the_server)
- [Chromium: separate HTTP and WebSocket socket pools](https://raw.githubusercontent.com/chromium/chromium/main/net/socket/client_socket_pool_manager.cc)
- [MDN: WebSocket API and lack of receive backpressure](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)
- [Uvicorn implementation settings](https://uvicorn.dev/settings/#implementation)

These are deployment/browser-specific findings, not a claim that SSE universally
supports only six clients. Five foreground views must be tested as separate visible
windows; ordinary background tabs do not exercise the foreground load target.

## 2. Shared acquisition rather than repeatedly calling expensive routes

**Decision:** Add a small `python/kllm/agent/live.py` runtime, owned by agent lifespan.
Reuse existing source functions through narrow snapshot helpers; share their results
across GET and WebSocket consumers. Use independent, single-in-flight source work so a
slow topic cannot block other topics or connection health. Full topic replacements are
sufficient; no general event bus or JSON patch protocol.

**Rationale:** `agent:status` currently performs liveness maintenance, capacity probing,
configuration reads, inference sampling, and software identity work. Capacity subprocesses
can take 15 seconds and software identification can take ten seconds. Moving this route
unchanged into a per-socket loop would multiply expensive work and block initial delivery.
`agent:_inference_activity` also runs for worker heartbeat responses; cached acquisition
must serve these callers as well as the dashboard.

The fast path reads detached state and previously completed capacity/software results.
Slow metadata is refreshed separately, retaining previous values or explicit unavailable
state. It does not run native probes or shell commands on the event loop. Usage reads
reuse `usage.UsageStore`; they never create additional `usage.Sampler` instances.

Target shared acquisition: status/activity/load/usage at most once per second while
needed; download and logs at most once per 500 ms while active/needed. A source timeout
produces topic unavailability without creating overlapping replacement work. Cancellation
of a thread wrapper does not kill its underlying function; keep its in-flight ownership
until it actually completes and reject results from obsolete role/process generations.

WebSocket interests and five-second HTTP demand leases keep viewer-specific acquisition
active. Expired leases stop scheduling work; underlying membership, usage recording, log
collection, and operation execution remain independent of viewers. New GET callers join
the same due/in-flight acquisition rather than spawning their own worker reads.

**Alternatives considered:** Per-viewer route polling fails work-sharing and isolation;
instrumenting every state mutation before shipping is unnecessarily invasive. Existing
collection cadence plus shared bounded observation is enough for this feature's delivery
targets. Measure source-to-render latency before tuning cadence.

## 3. A common HTTP/WS snapshot contract resolves ordering and fallback

**Decision:** The new path's GET returns protocol capability and the same versioned topic
records as the WebSocket. Use it for initial admission/state, explicit action refreshes,
recovery, periodic fallback, and selected timed log reads. Existing read endpoints retain
their CLI/third-party contracts and serve only compatibility fallback on older agents.

**Rationale:** `AppStore.refresh()` is called from `saveConfig`, `post`, and
`updateWorkers`, not just the polling timer. An unversioned delayed GET can overwrite a
newer pushed state. Per-topic revisions under a serving epoch let every accepted result
use one ordering rule. Complete topic payloads reuse current types and consumers.

An agent restart or serving role/master change establishes a new epoch. Transport batch
numbers, source revisions, log cursors, and process/operation identities are separate.
Equal revisions may update delivery health but do not replay domain effects. A live
recovery snapshot is staged before atomically invalidating the fallback generation.

Use one complete bounded initial frame, not multipart staging, so ACK never waits for
frames the server cannot send yet. A valid current-generation HTTP snapshot also admits
epoch replacement during HTTP-only recovery. Continue ordinary two-second retries while
unavailable; the 30-second live probe is a separate schedule. Non-log aggregate reads and
timed log reads have independent single-in-flight lanes so one cannot steal the other's
deadline. Area errors use the same revision/generation protections as successful data.

Versionless old-server reads are serialized per area and never drive the store concurrently
with a live/versioned owner. Five simultaneous chat streams leave little HTTP capacity:
the aggregate fallback GET is also preferable to four independent recurring state reads.

**Alternatives considered:** Adding revision metadata to all existing endpoints affects
more public contracts. Client arrival times cannot establish source ordering. Retrying
commands on reconnect is forbidden.

## 4. Preserve log checkpoints; do not adopt the existing follow stream

**Decision:** Reuse `cluster_log.LogHub.snapshot`, stable event IDs, and receiver-local
`UUID:sequence` cursors. Read an unfiltered recent trail and filter by node in the browser.
Send at most one unacknowledged bounded data batch per viewer. Its accepted highwater is
the next checkpoint; no per-entry position protocol or additional per-viewer log queue.

**Rationale:** `LogHub.iter_sse` currently takes history before subscribing, has a silent
queue-overflow path, and supplies neither checkpoints nor heartbeat/coverage metadata.
`agent:get_logs` rejects combining `after` with `follow`. The CLI depends on the existing
follow shape, so the new dashboard route leaves it intact.

Initial tail is 200 rows; the existing hub retains 8,000 rows. Live/new-GET batches cap at
500 rows and 256 KiB of UTF-8 log payload. If a snapshot exceeds a bound, retain the newest
suffix that fits, preserve its original highwater, and explicitly report `batch_limit`.
An individually oversized newest row may produce an empty suffix with an omission notice.
The browser still displays at most 2,000 rows. This bounds new delivery work; it does not
redesign the existing journal's storage or its input-line policy.

Clear keeps its accepted checkpoint and visit-wide seen IDs, invalidates pre-Clear work,
and changes only the display. On changed continuity after Clear, consume the recovery
snapshot as baseline only and disclose the omitted window. Keep the existing seen-ID set
for the visit: evicting IDs with rendered rows would allow old cleared events to reappear.
Its known long-session growth is not an unbounded pending-message queue.

**Alternatives considered:** Timestamp cutoffs fail with clock skew and repeated messages.
Per-entry delivery positions and durable replay are unnecessary given one batch in flight,
whole-batch generation checks, and explicit bounded-history gaps.

## 5. Worker coverage and a shared, nonpersistent log mirror

**Decision:** Preserve current scope: worker status is local plus heartbeat-mirrored
membership/activity; usage comes from the master; load/download/update progress remain
local; logs use master coverage with local-only fallback on transport failure. Do not
silently turn a worker dashboard into the master's complete status/progress view.

Share finite upstream reads through `http_clients.control_client()` and
`agent:_master_control_base`, retaining the private join-key header and existing relay
support. Preserve upstream 401/403 rather than converting usage denial to 503.

For new dashboard delivery only, use a separate worker `LogHub` as an in-memory master-log
mirror: `ingest(..., persist=False)` and `snapshot()` only. Never call its journal loading,
file-tailing, relay, or old subscription methods. Keep the upstream checkpoint internal;
GET and WS clients consistently use the mirror's own receiver-local cursor.

On upstream reset/gap, reset the mirror, ingest the available suffix, and publish a sticky
continuity descriptor in one short source-lock transaction. Network I/O stays outside
that lock. Source transitions between mirror and local HUB change continuity identity,
even when a viewer misses an entire coverage round trip. Never reset the real local HUB.
Denial or malformed upstream data is an error, not successful local fallback.

**Rationale:** This shares worker-to-master log reads across viewers while retaining the
same snapshot semantics for both new delivery modes. It avoids an unbounded upstream
follow stream and does not alter existing node-to-node collection or retained journals.

**Alternatives considered:** Proxying once per browser multiplies upstream reads. Reusing
master cursors as local mirror positions is incorrect. Persisting another log archive or
changing worker heartbeat payloads to mirror all progress expands scope unnecessarily.

## 6. Stable operation identity at the source

**Decision:** Add additive UUID identity and timestamps to download/load operation state
and accepted responses. Retain one active and one latest terminal record per kind, in
memory, independently of viewers. Success/failure/cancellation are latched transitions;
missing outcomes after restart or supersession are explicitly unknown to a recovering
viewer. This is not a durable operation-history feature.

**Rationale:** `models.download_job_snapshot` and `load_progress.sample` have no operation
ID. Downloads have an admission window before the executor sets progress, terminal state
currently blocks a subsequent start, and the cancelled path can reach `path.stat()` with
no path. Load readiness is sampled rather than latched, and automatic membership-driven
relaunches/stops bypass parts of its lifecycle. These source paths must be corrected once
so HTTP and live consumers observe the same truthful operation.

Allocate/reserve before acceptance, update only the matching identity, and acknowledge
cancellation only after the work has stopped. Include existing explicit and membership
spawn/stop callers in the load lifecycle. A later process exit is current runtime state,
not retroactive failure of a completed load. Do not introduce update-run history: worker
update progress remains the existing status field, with its existing scope.

**Alternatives considered:** Filename, model name, PID, or previous `running` state cannot
deduplicate repeated operations or recover a terminal transition missed by the viewer.
Persistent history and retaining every intermediate transition exceed the requirement.

Inventory reconciliation is distinct from auto-selection/notification: every newly seen
successful terminal ID reconciles inventory once, including a completion whose running
state was coalesced away. Selection/notification requires a running observation or the ID
from this visit's accepted command. Read latest-terminal even when a newer operation runs.

## 7. Membership publication must not wait for maintenance

**Decision:** Preserve the existing 20-second membership expiry and two-second sweep;
separate removal/publication from blocking shrink maintenance and sequential probes.
Publish status and cached usage connectivity immediately after `membership.sweep_liveness`,
before usage-store closure, persistence, or `on_membership_shrink`. Coalesce maintenance
behind one in-flight worker with a pending-removal set and role/membership generation checks.

**Rationale:** `agent:_sync_master_liveness` currently holds its lock through replanning;
`_membership_loop` waits for latency probes before its next sweep. Removing browser status
reads must not remove an incidental path to expiry or make the 25-second contract depend
on a slow planner, disk, or 16 sequential probes. A short sweep/publication path leaves
approximately three seconds for delivery after worst-case 22-second expiry observation.

**Alternatives considered:** A second sweep timer still blocks on the existing long-held
lock. Redesigning membership policy or parallelizing every network probe is unnecessary.

## 8. Admission, deadlines, and bounded browser consumption

**Decision:** Validate actual WebSocket Origin against its serving HTTP origin, plus the
two explicit loopback development origins on port 5173 for loopback connections. Reject
missing/null/foreign Origin before application data. The HTTP snapshot accepts a
`page_origin` diagnostic query and applies the same origin policy to it; the socket still
checks its actual Origin. Preserve existing route-specific access checks.

The existing dashboard reads are private-network, unauthenticated reads—not a session
authentication system. This feature adds no credential system and sends no credentials
in URLs or protocol controls. Existing authorized status payload fields retain their
semantics; neither payloads nor close/error diagnostics may be dumped into logs.

Application heartbeat: every two seconds; four-second foreground silence deadline;
three-second connect/subscribe deadline. Start permitted fallback immediately on failure;
HTTP reads have a five-second dispatch-through-decode deadline. Fallback uses two-second
deadlines with one non-log aggregate read and a separate log-read lane. Retry live availability every 30 seconds
with bounded ±20% jitter, at most one probe in flight. Selected timed Logs keep their
own deadlines. These budgets leave margin within ten-second/60-second requirements.

One application-data batch may be outstanding per viewer; acknowledge after validation
and synchronous store/trail commit. Heartbeats remain independent of this window. Bound
send and acknowledgment waits to five seconds; close a slow viewer and recover from its
last accepted checkpoint. Native WebSocket's `bufferedAmount` does not bound received data.

The complete initialization and HTTP aggregate are each bounded to 5 MiB, accommodating
four 1 MiB area records, a 256 KiB log result, and bounded outer metadata. This avoids a
multipart initialization/ACK state machine and an impossible smaller HTTP aggregate cap.
Cache residence is included in serialized usage age; bounded HTTP/heartbeat clock samples
conservatively account for transit instead of assuming synchronized browser/server clocks.

**Alternatives considered:** WebSocket failure code 1006 cannot distinguish 403 from a
network outage. An HTTP-readable snapshot distinguishes denial from transport failure.
An unexplained 404/HTML response is not proof of an old server: successful permitted old
reads can support labeled compatibility fallback without claiming a diagnosis. Uvicorn's
default protocol ping timing is too slow and not visible to browser code.

**Sources:**
- [WHATWG: WebSocket failure visibility](https://websockets.spec.whatwg.org/#feedback-from-the-protocol)
- [WHATWG: protocol ping/pong visibility](https://websockets.spec.whatwg.org/#ping-and-pong-frames)
- [Starlette: WebSocket denial responses](https://www.starlette.dev/websockets/#send-denial-response)
- [Vite: WebSocket proxy and Origin warning](https://vite.dev/config/server-options#server-proxy)

## 9. Existing regression harnesses are sufficient

**Decision:** Extend pytest and Node's built-in tests, including the existing Chrome/Edge
CDP harness in `dashboard/tests/usage-workspace.test.mjs`. Add a focused real-Uvicorn
GET/WebSocket boundary check using already-installed clients. Do not add a test framework.

**Rationale:** Controller tests alone do not prove upgrade proxying, cancellation,
five-window connection behavior, or cleanup. SSR tests do not execute mounted effects.
Actual in-app navigation is needed; full-document navigation conceals leaked subscriptions.

**Alternatives considered:** A new browser automation stack duplicates the current harness.
Real GPUs or public model downloads are unnecessary for deterministic protocol regression
tests. Physical multi-node performance remains a separate acceptance run with recorded
hardware/model/backend and before/after evidence.

## Constitution gate before research

Pass: scope is the existing Python agent and Svelte dashboard; existing dependencies and
tests are available; existing CLI/HTTP contracts and private RPC boundaries are retained;
the design must define cancellation, validation, and performance evidence. No exception
was requested. Post-design evaluation is recorded in [plan.md](plan.md).
