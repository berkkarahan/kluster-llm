# Research: Node and process usage monitor

Research completed 2026-09-28 against the current agent, dashboard, existing spec 001 and installed project dependencies. No `NEEDS CLARIFICATION` remains.

## Measurement and process identity

**Decision:** Sample only `STATE.process_pid` for `STATE.process_role` (`master` / `worker`) in a dedicated agent loop at two-second intervals. Use existing `psutil` for `Process.create_time()`, delta CPU and RSS, `cpu_count(logical=True)`, host CPU/memory and supported sensors. Normalize `Process.cpu_percent()` by logical-processor count so one saturated processor of eight is 12.5% machine capacity. Prime the first CPU reading as unavailable; record failure/status rather than a fabricated zero. A lifetime key is node ID + actual PID + process creation timestamp; recover the same identity after an agent restart, and verify it on every sample so PID reuse cannot attach to an old lifetime.

**Rationale:** `python/kllm/agent/__init__.py:_refresh_local_capacity` already reads tracked RSS for planning, but does not collect CPU or historical identity. `pyproject.toml` includes psutil. `_tick_membership` performs potentially slow worker probing and `_worker_post_heartbeat` can wait on network, so its nominal two-second interval cannot guarantee independent samples. Plan/run, membership replan and worker join/leave all mutate the tracked PID.

**Alternatives considered:** Browser polling stops when the tab closes; generic OS process enumeration records unrelated applications; PID-only keys merge distinct lifetimes; sending first-call CPU zero implies a measured idle state.

## Device and memory semantics

**Decision:** Expose measured host CPU and physical RAM and measured process CPU/RSS on Windows/Linux/macOS. Emit per-device readings (utilization, used/total bytes, temperature °C, power W) only where a supported sensor/tool can identify the specific device (for example bounded, optional `nvidia-smi` probing); expose process-attributed accelerator use only with direct PID attribution. Include scope, device ID, units, source/measurement timestamp and unavailable reason for unsupported/failing fields. Present shared/unified RAM as one physical pool, never add it to itself. Reuse `dashboard/src/lib/format-memory.ts:formatGib` for one-decimal binary GiB and distinguish physical capacity, measured consumption and estimated planned allocation.

**Rationale:** Existing `native.list_gpus()` and member `devices` describe inventory/capacity; `last_plan` describes estimates; `inference_activity.sample()` describes master-side activity. None proves device-wide utilization or an individual worker's work. `specs/001-fix-vram-layer-default/data-model.md` specifies the shared-memory and estimate display distinction.

**Alternatives considered:** Advertising planning VRAM as process VRAM, summing device and system RAM on unified-memory Macs, or interpreting a missing sensor as measured zero is incorrect. Vendor probes beyond available platform tooling should be added only after hardware-backed verification.

## Transport, access and membership

**Decision:** Send worker samples through a bounded, join-key-protected `POST /v1/usage/ingest` to the master via `_master_control_base()` and the existing control client. Keep sampling independent of heartbeats, use the master's own sampler for its child, and query cluster history from the master. On a worker-served dashboard, proxy history and live read requests server-side through the same private route, without passing the join key to the browser. Live membership remains authoritative in `/v1/status`; master-to-worker known relationships come from registered membership, not invented traffic edges. Save historical node identity before membership is removed; a disconnected selected node can remain inspectable.

**Rationale:** `python/kllm/agent/__init__.py:heartbeat` already validates the master join key and updates last contact; `membership.sweep_liveness` drops workers after 20 seconds, with a nominal two-second tick. Existing logs demonstrate same-origin history and private proxy patterns. Browser reads use the existing private-cluster agent access boundary; historical reads must not leak a new key or expose RPC 50052. Keep usage ingestion separate from liveness so telemetry failures cannot kick workers out or stall inference.

**Alternatives considered:** Putting all readings into heartbeat couples samples to blocked probes; browser-to-master cross-origin requests reveal access and key assumptions; per-worker-only history loses cluster-wide departed-node inspection. A new public telemetry port is unnecessary.

## Persistence, retention and failures

**Decision:** Store cluster readings in a master-owned SQLite file at `home_dir()/usage.sqlite3` with indexed lifetime/time keys, separate retained node/lifetime/coverage metadata and transactional writes at least every ten seconds under healthy recording. Use a serialized, bounded writer. Enforce a rolling 24-hour interval and a single 256 MiB physical allowance including journal/sidecars, pruning oldest readings first and reclaiming disk pages; reserve space for transaction overhead and check actual file sizes rather than only row counts. Preserve metadata required by remaining records and report earliest retained time, storage truncation, recording status and last successful save. Persist timestamped samples rather than synthetic zeros; infer/record gaps from missed two-second intervals, explicit collection/transport failure and agent restart. Invalid/duplicate submissions do not write; late valid observations may join history but may not roll live state backwards. Database/disk read or write errors surface without terminating inference or deleting good history.

**Rationale:** `python/kllm/state.py:ClusterState.save` rewrites cluster JSON, and `python/kllm/cluster_log.py` maintains a log journal; neither is a transactional bounded indexed process time series. At 16 nodes and two seconds, one day can be ~691,200 readings; the 256 MiB budget may shorten coverage. SQLite from the standard library provides indexing, unique constraints and crash-safe transactions. Deleting SQLite rows alone does not necessarily release physical bytes; retention checks must include real file size and reclamation.

**Alternatives considered:** Browser storage disappears with tab/host changes; JSONL scans/rewrite are expensive and fragile for filters/oldest-first retirement; a row-count limit does not satisfy physical-size retention. Retaining every sample offline for delayed upload is not promised by the spec; interrupted periods remain visible gaps.

## Dashboard interaction and testing

**Decision:** Extend the existing Home workspace with stable-ID keyed node buttons and compact master–worker SVG edges, a textual node list/fallback, selectable detail/history, Fit/zoom/pan controls and bounded plot plus keyboard-inspectable exact-reading pages. Use the existing status poll for membership/activity and a distinct latest-usage read for measurement freshness; fetch history on filter/selection changes rather than every status tick. Keep cluster buttons/chat and existing Run navigation. Apply amber accents locally to the monitoring panel, preserve global theme, provide focus/text/scope/units, and stack panels at narrow widths.

**Rationale:** `dashboard/src/routes/+page.svelte` already owns the three-pane layout; `store.svelte.ts:AppStore.refresh` is browser-scoped, and `types.ts` is its wire contract. `dashboard/src/app.css` supplies shared panels, buttons and dark theme; `dashboard/DESIGN.md` gives general OpenDesign direction. 43,200 samples/process/day makes an unbounded DOM/chart inappropriate; the 1–16-node map needs no graph or chart package.

**Alternatives considered:** A graph/chart dependency without a demonstrated need adds weight; hover-only plots or desktop scaled down for mobile lose required accessibility; replacing the global accent changes unrelated pages.

**Design dependency:** The feature spec requests a feature-specific OpenDesign MCP artifact. No OpenDesign MCP interface is exposed in this planning session. The implementation must generate/review that artifact and record its reference before visual execution; this plan does not claim that the existing generic `dashboard/DESIGN.md` fulfills it.
