# Validation Guide: Dashboard Live Updates

**Created**: 2026-09-29
**Status**: Planned validation; the feature is not implemented by this planning command.
**References**: [spec.md](spec.md), [data-model.md](data-model.md),
[dashboard contract](contracts/dashboard-live.md)

All commands below run from the repository root. Commands naming new test files or the
new endpoint are intended to run after implementation; their absence today is not a pass.
Use existing runners and record actual failures/skips.

## Prerequisites and setup

1. Follow [the agent install guide](../../docs/agent-install.md), including its recovery
   tree. If setup is missing, the primary command is:

   ```text
   python scripts/install.py
   ```

2. Use the uv-managed Python environment, Node 22 or newer with type stripping support,
   installed dashboard dependencies, and Chrome/Edge for the existing browser harness.
   Set `CHROME_BIN` if the browser is not discovered automatically. Missing browser runs
   are skips, not successful integration validation.
3. Deterministic tests must use temporary state and synthetic source data; no credentials,
   public downloads, GPU, or active personal cluster is needed. Manual node tests require
   a disposable cluster, existing local model, and the committed llama tag on every node.
4. Record baseline commit `a0a4f5259c7dfd5c204deb5ff0972f27b8e87c69`, browser/OS/hardware,
   node/viewer count, and model/backend when inference is active. Capture the idle request
   baseline before replacing polling.

## Automated checks

Run check, Node tests, and build sequentially; they share generated SvelteKit state.

```text
uv run pytest -q tests/test_dashboard_live.py tests/test_cluster_log.py tests/test_dashboard_api.py tests/test_model_management_api.py tests/test_membership.py tests/test_cp_proxy.py
npm --prefix dashboard run check
node --experimental-strip-types --test --test-concurrency=1 dashboard/tests/*.test.mjs
npm --prefix dashboard run build
uv run pytest -q
```

`test_dashboard_live.py` and `dashboard/tests/live-updates.test.mjs` are planned additions;
the other named files/runners already exist. The Python file must include a finite,
real-Uvicorn HTTP/WebSocket test with an ephemeral local port and temporary state. A
TestClient-only test does not establish upgrade, heartbeat, close, and resource cleanup.
The Node browser fixture must support actual WebSocket push, in-app navigation, independent
source times, and closing long-lived fixture connections in teardown. Do not let it pass
only through fallback when live transport is the behavior being tested.

### Required deterministic scenarios

| Scenario | Observable pass condition | Requirements |
|---|---|---|
| Shared source acquisition with one/five clients | Source/probe/upstream counts follow cadence, not viewer count; no second usage sampler. | FR-001–003, FR-012 |
| GET/WS initial, action, and recovery races | Delayed older results never overwrite newer state; equal revisions do not repeat effects. | FR-002, FR-004, FR-010, FR-013 |
| Complete initialization / obsolete errors | One bounded initial frame covers every requested result; timeout/incomplete data fails safely; old-generation errors and old-revision denials cannot overwrite current state. | FR-002, FR-008, FR-010, FR-013 |
| Real operation paths | Consecutive same-file operations get distinct IDs; accepted→executor→terminal works for completion, failure and cancellation; late cancellation cannot overwrite another outcome. | FR-005, FR-015 |
| Operation while disconnected | Retained completion is handled once; missing superseded/restarted outcomes show unknown, not success or perpetual running. | FR-005, FR-010 |
| Completion between samples | New terminal ID reconciles inventory even without a running frame; local accepted ID permits once-only selection; a newer active job does not conceal last-terminal. | FR-005, FR-015 |
| All load lifecycle callers | Explicit run, automatic membership replacement, stop and process death use correct identities; completed load is not retroactively failed. | FR-005, FR-013 |
| Log transport equivalence | Identical event batches via new GET/WS preserve IDs, order, filtering, cursor and gap semantics. | FR-006–007 |
| Clear and timeout races | Clear before first success is a no-op; effective Clear cannot be undone by deferred decode, old generations, mode changes or recovery resets; late results cannot release a newer request. | FR-006–007, FR-010 |
| Worker mirror/coverage round trip | Original IDs retained; mirror cursors remain local to mirror; reset+suffix is atomic; cluster→local→cluster changes continuity even if a viewer missed it. | FR-007, FR-009, FR-014 |
| Read and socket denial | Invalid/foreign/null Origin, 401/403, malformed capability, old server and blocked upgrade are correctly distinguished; denied areas never become successful fallback. | FR-008, FR-011, FR-014 |
| HTTP-only restart and legacy fallback | New GET epoch recovers while upgrade stays blocked; ordinary reads retry within the ten-second budget; old payloads adapt conservatively; unsupported log identity/cursors produce an explicit error. | FR-005, FR-007, FR-010–011 |
| Silent connection and unchanged sources | Failure visible within ten seconds; healthy unchanged connection stays live; a ten-second-old usage observation becomes stale. | FR-008–009 |
| Cached first read / clock skew | Serialization includes cache residence; bounded transit estimate prevents old readings appearing fresh; duplicate replay never delays the aging anchor. | FR-009 |
| Blocked liveness maintenance | A blocked replan, usage persistence or latency probe cannot delay the next expiry; status and usage connectivity show loss within 25 seconds. | FR-009 |
| Single owner and cleanup | Across 20 navigation/mode/hide/show/reconnect cycles, one socket and bounded timers/requests; no viewer update work after ten seconds closed. | FR-010–012 |
| Slow viewer and oversized data | Hold ACK/send; other viewers advance; bounded buffers; log omissions explicit, oversized state rejected without partial commit. | FR-007, FR-013 |
| Near-limit initial snapshot | Four maximum-sized permitted area records plus Logs fit the 5 MiB aggregate/frame limit and complete without a multipart ACK deadlock. | FR-002, FR-011, FR-013 |
| Existing workflows/accessibility | Commands sent once; valid plan, drafts, selection, map position and keyboard focus survive; status is textual without per-row announcements. | FR-004, FR-015–016 |

Assert secret-free diagnostics using sentinel credentials in synthetic payloads. Test
control validation, maximum payloads, future cursors/ACKs, unsupported versions, cancellation,
and repeated lifespan startup/shutdown at the actual HTTP/WS boundary.

## Local endpoint and development proxy smoke

Use a disposable `KLLM_HOME` and an unused control port on the test machine. For the
standard Vite proxy, use port 18765; do not start a second agent on an occupied port.
For example, in PowerShell:

```powershell
$env:KLLM_HOME = Join-Path $env:TEMP ('kllm-live-check-' + [guid]::NewGuid())
uv run python -m kllm.agent --host 127.0.0.1 --port 18765
```

In another terminal, run the production dashboard build as above or start development:

```text
npm --prefix dashboard run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173/` for the Vite upgrade check, and
`http://127.0.0.1:18765/` for the built static dashboard check. Both must connect through
their own serving origin. Vite must forward `/v1` upgrades with Origin preserved.

Inspect the HTTP diagnostic/snapshot from PowerShell after implementation:

```powershell
curl.exe --get "http://127.0.0.1:18765/v1/dashboard/live" --data-urlencode "topics=status,usage,load,download" --data-urlencode "page_origin=http://127.0.0.1:18765"
```

On Linux/macOS, use `curl` for this same invocation. Expect version 1, WebSocket capability,
a serving epoch and all four requested topic records; an idle source is valid, fabricated
usage zeros are not. Browser Network should show one dashboard WebSocket, an initial
snapshot, and no recurring healthy state GETs. User actions and selected timed Logs are
documented exceptions. Ignore Vite's own HMR socket in connection counts.

## Recovery, timed Logs, and Clear

1. Open Home, select a node, move the map, and edit an unsaved model setting. Generate
   known source changes. Confirm current values arrive without remounting or erasing state.
2. Open Logs in Live mode in two views. Emit identified repeated-text events. Each event
   appears once; distinct IDs remain distinct. Scroll to older rows and verify the anchor.
3. Clear one view. Stored logs and the second view remain intact. Delay an already-started
   update; releasing it must not restore cleared entries.
4. Select 5s, 30s, and 1m for ten minutes each. Record monotonic update times: deadlines
   match within one second when reads complete within one second. No live log batches drive
   timed rendering. Hold a state/action GET across a log deadline: the independent log
   lane must still dispatch on time. Clear in timed mode must not subscribe to live Logs.
   Changing mode/Clear cannot leave old schedules running.
5. Use the controllable test fixture to drop only WebSocket traffic while HTTP remains
   functional. Expect labeled periodic refresh within ten seconds, current changes within
   ten seconds, and one aggregate read in flight. Restore upgrades; expect live delivery
   within 60 seconds with no duplicate rows or commands.
6. Blackhole established traffic (not merely graceful close) in the fixture. Verify the
   silence watchdog. Separately stop/restart the disposable agent: retained display stays
   readable; new epoch/current state replaces it on recovery without a page reload. Repeat
   with upgrades blocked throughout: GET alone must admit the new epoch and recover.
7. Trigger log continuity reset after Clear. The recovery tail is baseline-only, the gap
   remains visible, and subsequent eligible entries appear. A reset before any Clear may
   show available retained history.
8. Hide/restore and navigate using the application's links at least 20 times. Verify
   subscription replacement and cleanup rather than testing only full page reloads.

Do not use browser-wide Offline mode as the sole fallback test: it disables HTTP reads too.
Use it for the unavailable state, alongside the selective socket-failure fixture scenario.

## Physical private-cluster and performance acceptance

Use the documented existing CLI on a prepared disposable cluster:

```text
uv run kllm start
uv run kllm status
uv run kllm logs -n 20
uv run kllm ui --no-open
```

Join prepared workers using [the documented join procedure](../../docs/agent-install.md#4-post-install-bring-up-a-cluster),
with the same committed llama tag and private control/RPC network. Exercise dashboards
served from both master and worker, including the localhost control-proxy path where
applicable. Use an already available local model for run/load/chat checks. Never expose
worker RPC port 50052 publicly as part of this validation.

| Workload | Required evidence |
|---|---|
| Ten-minute idle baseline vs implementation, one and five visible views | At least 90% fewer browser application-state-check requests after initial loading; same cluster state and duration, no actions/recovery. |
| 1, 4, 16 nodes; one and five visible views | 95% source-available→render latency within two seconds; initial display within three seconds; controls within one second. |
| Five visible browser windows plus one and five active chat streams | Live observations, commands and chat make progress without ordinary HTTP-pool starvation. Repeat with permitted fallback and measure its ten-second freshness target. |
| 100 total log entries/second for ten minutes, one slow viewer | Healthy viewers meet latency targets; per-viewer memory/work stays bounded; gaps and 2,000-row display trimming are labeled. |
| Completed/failed/cancelled load/download across disconnect | Once-only effects or explicit unknown outcome; no repeated command, stranded active state, or same-name identity collision. |
| Master unreachable through worker, then recovered | Usage unavailable rather than zero; local-only logs labeled; no stale mirrored cluster view presented as current; coverage/reset semantics correct. |
| Windows, Linux, macOS | Record actual OS/backend and checks performed; transport/proxy/cancellation and existing command behavior work on each tested platform. |
| Five operators; 1280×720 and 390×844 | At least four distinguish live/fallback/unavailable/stale in 30 seconds; keyboard-only controls, visible focus, no page-wide overflow. |

Record three separate instants: `t_available` when a change becomes available in the
serving agent's authoritative source (before LiveFeed acquisition), optional
`t_feed_publish`, and `t_rendered` after the browser commits/renders it. SC-001 uses
`t_rendered - t_available`, including shared acquisition, queueing and transport delay.
For a worker view, availability begins when its serving source acquires upstream data;
record upstream acquisition separately rather than hiding it in a claimed cluster-wide metric.

For fixture tests, use a shared harness clock or record bounded clock offsets from timed
request/reply samples. For physical runs, document the alignment method and uncertainty;
an uncertainty crossing a pass threshold cannot establish a pass. Do not subtract clocks
from unrelated machines or use original log timestamps as the availability clock.

Each eligible matching Live log event is a sample; events that never render within the
deadline count as misses, including omissions, rather than disappearing from the denominator.
For current-state topics, a change is reflected when the browser shows it or a newer valid
state for the same subject/operation; permitted intermediate coalescing is not a requirement
to render every byte-progress callback. Terminal identity and outcome must still reconcile.

Separate source-collection delays, hidden-tab time, health controls, chat, history reads,
commands and development-server traffic from idle polling measurements. Record bounded
queue/frame counts and bytes, not merely the count of rendered rows.

If hardware, a platform, or participants are unavailable, report the missing matrix row
and exact follow-up workload. Synthetic 16-node tests do not certify a physical 16-node
cluster or inference performance. Save actual results with the implementation change;
do not rewrite this planned guide into a claim that the checks already passed.
