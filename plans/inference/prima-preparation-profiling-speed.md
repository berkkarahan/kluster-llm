# Prima model preparation and profiling speed

> **Created:** 2026-10-16
> **Status:** Planned
> **Repo / scope:** `D:\dev\kluster-llm` — Prima asset staging, transfer, progress reporting and profile preparation; measured, bounded optimizations within Kluster's existing ownership contracts.

## Conventions captured at creation

- Owning root verified with `git rev-parse --show-toplevel`; date verified with `Get-Date -Format yyyy-MM-dd`. HEAD was `ffdabee17623552d0b42f14c6fd58bd70c7039bd`, with existing modifications including `interactors/planner.py`, `tests/test_prima_integration.py`, managed-network code and `docs/troubleshooting.md`. References below describe inspected working-tree source, not a clean release. Preserve those changes and re-check affected symbols before implementation.
- Memory preparation followed the read-only fallback in `.specify/memory/workflow.md:17-20`: reviewed `docs/memory/INDEX.md`, architecture entries `2026-09-29-structural-standards.md` and `2026-10-09-viper-modules.md`, decision `2026-10-09-viper-owning-module-patch-surface.md`, and existing `specs/018-cross-platform-planning/memory-synthesis.md` and `specs/019-highs-scheduler-planning/memory-synthesis.md`. No durable memory or synthesis was written.
- Governance: `.specify/memory/constitution.md` 1.3.0 requires behavior-focused tests, real boundary checks and reproducible evidence for performance claims. Its older `python/kllm/` sentence conflicts with current `AGENTS.md` and `.specify/memory/architecture_constitution.md:13-17`; this plan follows their explicit `src/kllm/` layout. Historical synthesis is context; current source and architecture rules govern implementation.
- Feature logic belongs in `src/kllm/modules/primacpp/interactors/`; helpers receive dependencies explicitly and tests patch the defining module. Cross-feature channel changes remain in their owning managed-network module. No general architecture refactor is needed. Python uses `uv`; installation/builds follow `docs/agent-install.md` and `scripts/install.py` only when needed.
- `control.py:PrimaControl._prepare` (631 onward) owns one preparation, stages ranks sequentially, then optionally profiles ranks and links. Its preparation executor's single worker is an ownership boundary, not a throughput setting. Preserve node-resource reservations and settlement through actual cleanup.
- `control.py:PrimaControl._file_chunks` (1055) currently reads the transfer constant `CHUNK_BYTES`; `transfers.py:17` defines 24 KiB for base64 inside signed control envelopes. `managed_network/interactors/identity.py:MAX_FRAME` is 64 KiB. Local file copy does not need this wire limit.
- `staging.py:ModelStager.prepare` (34) resumes digest-addressed `.part` files, checks remaining disk space plus 64 MiB reserve, hashes the complete partial, then uses atomic publication. Existing finals are rehashed before reuse. File length alone does not establish content integrity.
- `transfers.py:TransferGrants.grant/chunk/acknowledge` bind source identity, node, operation, digest, size and lease. A single `served_range` must be acknowledged exactly and in order. Reading alone does not renew a grant. `PreparationLeases` renews prepared participants only as owned work progresses.
- `control.py:PrimaControl.execute` authorizes each signed command and binds it to preparation/authority. Its `prima_asset_chunk` branch calls `PushReceiver.push`, updates offsets/expiry and saves jobs before replying. `PushReceiver.offset/push` handles incomplete files, final SHA-256, lost-final-ack recovery and verified cache entries.
- `preparation.py:ProfileJobs._run` hashes before spawning the native profiler with `-t request.threads`; typed results bind settings, hardware and storage. `kernel_profile.py:kernel_settings`, `profiles.py:profile_key/ProfileCache`, and `control.py:PrimaControl._profile_scope` make thread count part of workload/cache/manifest identity. Profiles are estimates, not proof of generation throughput.

Paths without a directory prefix in the body refer to `src/kllm/modules/primacpp/interactors/`, unless otherwise stated.

## Goal and operating boundary

Reduce preparation wall time and make its actual costs visible, preserving authenticated transfer, complete digest validation, resumability and execution/profile provenance. Ship local improvements first; change the remote protocol only when measurements justify it.

This deliverable is a plan only. Live preparation `5b83057f-729a-4c3f-9dd3-fe6568e46d50` must continue unchanged. Implementation, instrumentation deployment and hardware experiments are future work after that job finishes and a separate execution window is agreed. Do not restart agents, cancel/resubmit the job, modify settings/networking, hash the live 59 GiB file as a diagnostic, or run competing benchmarks now.

### Supplied observation, not a baseline

- Model: `openai_gpt-oss-120b-MXFP4.gguf`, 63,387,345,344 bytes (about 59.0 GiB); Prima reported as 0.0.5.
- Nodes: Windows `049f6c77`, Ryzen 7800X3D (8 physical / 16 logical cores), RX 9070 XT; Mac `master1`, M1 Pro with 32 GiB RAM; dedicated nominal 1 Gbps Ethernet.
- The job was started with `include_profile=false`. Last supplied observation: local staging on the master around 26 MiB/s. This is neither network throughput nor kernel profiling; no bottleneck has been established.
- `threads=8` does not control copy buffers, transfer windows or progress bookkeeping. Exact selected backends, runtime commit/recipe, storage paths/filesystems, available disk/RAM and actual link performance must be captured for later comparisons.

## Findings and prioritized design

### P0 — Make phase costs and units explicit

Instrument the actual preparation path, with monotonic durations and bounded counters, not per-chunk logs or large sample histories. Add optional versioned diagnostics to job status, outside canonical execution manifests and profile keys:

1. Asset enumeration/source hashing during materialization.
2. Local cache verification, resumed-prefix handling, local read/write copy, final verification and publication.
3. Remote grant hashing, source reads, encode/sign/send, acknowledgement wait, receiver decode/write, checkpoint persistence and receiver final verification.
4. Model-layout hardlink/copy and verification, model inspection, profile preflight digest, native profiler execution, report validation/cache publication.
5. Signed private-link profiling, separate from asset-transfer throughput and physical adapter negotiated speed.

For each stage record node/asset identifier, monotonic start/end, logical bytes accepted, actual bytes copied/read/hashed, resume/cache-hit bytes, callback/publication counts, aggregate durations and bounded latency buckets. Keep cumulative preparation completion separate from current-stage rate. Local copy rate is copied bytes/second; remote payload rate uses acknowledged bytes; hash rate uses bytes hashed. Cache hits must not appear as instantaneous network transfer. Do not derive transfer speed from profiler RSS: `ProfileJobs._run` currently uses RSS as a progress heuristic, which is not bytes read or kernel completion.

Use sampled timing for hot calls and aggregate at most a few times per second; include instrumentation overhead in later comparisons. Optional worker metrics require negotiated support rather than changing legacy strict requests. Keep stage boundaries and terminal updates immediate, and retain backward-compatible progress fields/labels for existing clients. A verifying stage can have all copy bytes complete while the job is still incomplete; suppress misleading ETA until that stage has its own usable estimate.

**Exit:** fixture runs reconcile stage byte totals and expose hashing/persistence/ack wait separately. A later hardware baseline identifies dominant costs before P2/P3 selection.

### P1 — Local buffer separation and bounded progress (first implementation)

**Buffer:** make local staging buffer size configurable through `KLLM_PRIMA_LOCAL_CHUNK_MIB`, an environment variable supported when launching the `kllm` CLI/agent. Default to **4 MiB** when unset (user-selected on 2026-10-16). Accept integer MiB values from 1 through 64; reject empty, malformed, fractional, zero, negative and out-of-range values with an actionable error naming the variable and accepted range. Resolve and validate once at preparation admission, before expensive hashing/copying or worker commands, then hold that value for the job. Keep this operational tuning outside canonical execution manifests, plan digests and profiling cache identities. The setting controls local disk staging only, independently of the network frame size.

Document PowerShell and POSIX examples for setting the variable before `uv run kllm ...`, including that a CLI attached to an already-running agent cannot change that agent's environment: the setting must be present when the owning agent starts. Inspect existing agent spawn/environment propagation and preserve explicit environment filtering contracts; add forwarding only if needed. Do not restart the current live agent to activate it. Include unset/default, valid override, invalid input, CLI-to-new-agent inheritance and already-running-agent semantics in appropriate isolated checks. Compare 1 MiB, 4 MiB and 8 MiB during later acceptance measurements. Keep legacy `CHUNK_BYTES=24*1024` and `MAX_FRAME` unchanged. Use bounded streaming buffers, retain arbitrary-byte resume offsets and call ownership/cancellation checks between chunks. A larger local buffer must not introduce whole-model allocation or remove final validation.

**Progress:** replace `_update_job_progress`'s append-and-full-list-filter on every callback with fixed-cadence sampling and a count/time-bounded deque (proposed: 250 ms samples, 8-second window, at most 40 samples). Keep cheap exact byte accounting on every accepted chunk, but format/publish rate, ETA and labels at most four times per second. Force initial, stage-transition, failure/cancel and completion events; clear histories at phase/rank/asset changes, byte reset and job settlement. Synchronize snapshots with existing ownership locks so cancel/terminal state cannot be overwritten by a late callback.

The present algorithm scans every retained sample per callback: with callback rate `r` and window `W`, steady-state work is approximately `O(r²W)` per second, versus amortized constant work per event after bounding. If 26 MiB/s were sustained with 24 KiB callbacks, it would produce about 1,100 callbacks/second and roughly 8,900 retained samples, potentially millions of tuple visits/second. This arithmetic is a hypothesis about overhead, not a measured CPU profile. The whole file involves about 2.58 million 24 KiB iterations.

Throttle presentation independently from authorization, cancellation and lease progress. Do not sample away exact acknowledgements or renewals. Add a feature-owned cancellable hashing helper where needed to report hash progress/check cancellation without changing the behavior of every caller of shared `registry.py:file_digest`.

**Exit:** identical bytes/final SHA-256, compatible wire format, bounded progress memory and prompt cancellation; isolate the performance effects of larger buffers and progress throttling in separate comparisons.

### P2 — Reduce repeated I/O without weakening integrity

Use P0 measurements to choose changes in this order:

1. **Open-source lifetime:** replace per-chunk source open/seek/close with a bounded operation/asset-owned read handle in `TransferGrants`. Revalidate descriptor identity and current path identity at authorization/acknowledgement boundaries; detect replacement, mutation, symlinks and truncation. Preserve exact legacy `served_range` semantics. Close handles on completion, revoke, expiry, cancel, authority loss, exception and service shutdown. Start with one active source handle per sequential stream; avoid holding the grants lock around unrelated slow I/O. Test Windows file-sharing/deletion behavior explicitly.
2. **Receiver overhead/checkpoints:** measure `PushReceiver.push`'s per-chunk open/stat/free-space calls and `PrimaControl.execute`'s per-chunk full `jobs.json` rewrite. If material, use an owned receiver handle and bounded time/byte checkpoints. Persist initial ownership/manifest before accepting bytes and terminal/error/cancel transitions immediately. Distinguish accepted/written offsets from durably checkpointed offsets; do not acknowledge bytes still only in an application buffer. After a crash, reconcile `.part` length with checkpoints and reauthorize a new session; never revive stale authority from cached bytes. Choose explicit flush/fsync semantics and a bounded retransmission budget before reducing persistence. Keep the current per-chunk persistence until crash tests prove the replacement contract.
3. **Hash pass inventory:** count full reads in `execution.py:asset_files`, grant creation, cache-hit validation, stager final validation, `_layout_asset` and profile preflight. Reuse verification only within a demonstrably unchanged owned snapshot/handle, with before/after identity checks and authority checks still performed independently. Stat metadata alone is not a permanent integrity certificate. On restart, mutation, replacement, ambiguous identity or trust-boundary crossing, rehash. Keep complete receiver/local final-file SHA-256 before publication in the initial rollout.
4. **Copy/layout reuse:** prefer existing verified digest-cache bytes and existing same-volume layout hardlinks. Do not hardlink the mutable downloaded source into the cache. Evaluate filesystem copy-on-write clones only if duplicate-copy time is material and immutable ownership, allocation/free-space behavior and atomic fallback are proven on both OSes. Cross-volume or unsupported filesystems retain verified streaming copy. Do not add a mandatory platform dependency.

Incremental SHA-256 can overlap copy and hashing, but hashing the input stream alone does not validate bytes read back from the destination. It must not silently replace the current final-file verification. A resumed stream requires hashing the existing prefix too; standard SHA-256 digests cannot be concatenated. Any proposal to eliminate final read-back needs a separately documented integrity-equivalence decision and fault tests; retain read-back if that proof is unavailable.

**Long final verification:** the current last remote chunk runs a full-file hash before its reply; `_call` grants that command 60 seconds. A large-file hash can exceed that even if transfer is healthy. Measure this explicitly. For legacy peers preserve lost-final-ack recovery through a fresh authorized preparation and `PushReceiver.offset`; never blindly replay an already accepted chunk. If necessary, introduce a negotiated asynchronous `verifying` state with bounded status polling and hash-progress-based lease renewal. New peers must not report `prepared` or release reservations until final SHA-256 and exact manifest checks succeed. Merely enlarging every command timeout is not the design.

**Exit:** measured reduction in opens, metadata writes or redundant reads, with source-change, crash, cancellation and authority tests passing. No persistent authorization is inferred from a digest-cache hit.

### P3 — Remote transport decision after baseline

The current stop-and-wait loop offers an approximate ceiling of 24 KiB divided by complete per-chunk round-trip service time. It includes filesystem work, JSON/base64, signatures and persistence, not just Ethernet RTT. `ChannelSession.request_runtime` allows at most 16 pending commands; `ChannelSession.pump` awaits each worker handler serially. Sending many existing `prima_asset_chunk` commands concurrently would overwrite a grant's single `served_range` and violate acknowledgement semantics.

Evaluate two bounded prototypes outside production traffic; implement only the option justified by evidence:

| Option | Proposed shape | Decision criterion |
|---|---|---|
| Negotiated window over existing signed channel | New transfer version with a bounded outstanding-range ledger, ordered sending/writing and contiguous acknowledgements; test windows 2/4/8, reserve channel capacity for lifecycle traffic, retain 24 KiB frames. | Prefer when RTT dominates and reduced round trips suffice. Serial worker handling, per-frame signing and persistence may still cap throughput; preserve generic mutation serialization. |
| Dedicated authenticated bulk stream | Signed control negotiation authorizes an operation-scoped stream carrying bounded binary frames, with backpressure and authenticated completion/acknowledgements. Start with 1 MiB frames and an 8 MiB total window, subject to measurement. | Consider when per-frame control processing remains dominant or blocks lifecycle control. Additional protocol and listener lifecycle must earn their complexity. |

For either choice, define these contracts before implementation:

- Add an explicit agent asset-transfer capability/version, advertised through `cluster/presenters/heartbeat_payload.py:HeartbeatPayloadBuilder.build` and carried through membership validation. Membership advertisement is a hint; confirm supported version/limits through the paired signed preparation handshake. This is distinct from native Prima runtime version. Missing/unknown capability selects legacy serial transfer, per peer.
- Bind transfer/session ID, peer identities, operation, preparation, authority term/session/channel, manifest, asset digest/size, permitted offsets, expiry and negotiated limits. Authenticate every data frame or use a mutually authenticated stream whose transcript is bound to the signed grant. A final SHA-256 is not a substitute for sender authentication. Keep any session secret out of plaintext signed messages, URLs and logs; use an established authenticated key exchange or an existing protected credential channel.
- The receiver accepts only the next contiguous range; no unbounded reordering buffer. Sender credit is returned only for validated contiguous accepted bytes. Reject gaps, overlaps, wrong hashes/sizes/peers, oversized frames, booleans masquerading as offsets, out-of-window and stale-session acknowledgements. Define duplicates/lost acknowledgements precisely: explicit status/resume or validated idempotent replay, never assumed progress.
- Renew source grants only on genuine forward acknowledgements; preparation verification may renew from genuine owned hash progress, not idle polling. Preserve expiry when a receiver or verifier stalls. Bound frame-read deadlines, verification idle periods, retries, queued bytes and total open sessions.
- Cancellation, authority replacement, disconnect and process shutdown must stop new writes, drain/close owned handles and preserve resumable partials. A new authority must obtain a new preparation. Resources remain reserved until stream/verifier cleanup actually finishes. Make control cancellation/status responsive under backpressure and during verification.
- A separate listener requires exact private-interface binding, authenticated peer admission, owned port lifetime and cleanup integrated with existing managed-network contracts. Reuse an appropriate existing authenticated endpoint if feasible; do not broaden listeners or silently alter firewall/network settings. The transport decision must include this cost.
- On transport failure, fence and close the old stream before status reconciliation and legacy fallback. Unsupported capability may fall back; authentication failure, source mutation or digest mismatch must surface as failure, not an automatic downgrade. Preserve existing `.part`/final naming and validate any resume offset before use.

Keep rank staging sequential initially, even with a per-stream window. Parallel destinations could compete for the same disk/link and multiply memory/reservations; do not raise preparation executor `max_workers`. Cross-rank overlap is a separate evidence-dependent decision, not required by this plan.

### P4 — Profile preparation and thread experiments

Separate model digest/layout/inspection time from native kernel time and private-link measurements before tuning. Reuse only complete fresh profiles with exact runtime/model/settings/device/driver/storage evidence and valid preparation authority; keep read-only previews read-only. If adding preparation-time cache reuse, expose cache hit/miss reasons and retain explicit refresh behavior. Do not weaken `_profile_scope` or cache identity to avoid work.

For later CPU experiments, compare Windows 4/8/16 threads under identical approved settings; record actual Mac performance/efficiency core topology before choosing its sweep. More threads can help parallel CPU kernels but can lose to memory bandwidth limits, SMT contention, oversubscription or thermals. GPU-bound work, disk reads, signatures and serial SHA-256 do not automatically accelerate. Thread changes require a new planned workload/manifest and fresh matching measurements, never reuse or relabel the eight-thread result. Do not silently optimize with a thread count different from the eventual run.

Profile nodes sequentially during acceptance to avoid storage/network interference. Kernel roofline estimates and actual inference performance remain separate evidence. Native profiler changes, if measurements eventually demand them, need a follow-up Prima-fork plan and immutable recipe/source integration; this plan does not promise native kernel acceleration.

## File / symbol change map

| Phase | Owning surface | Focus |
|---|---|---|
| P0/P1 | `control.py:PrimaControl.start_preparation/_prepare`, `_file_chunks`, `_update_job_progress`, `_save`, `cancel`, `_settle_preparation`; owning CLI/agent spawn gateway if explicit forwarding is needed | Phase metrics, validated `KLLM_PRIMA_LOCAL_CHUNK_MIB` environment configuration (default 4 MiB), bounded samples, terminal flush/cleanup; retain single job ownership. |
| P0/P1/P2 | `staging.py:ModelStager.prepare/offset` | Cancellable phase-aware validation, exact resume, atomic final publication, disk accounting. |
| P0/P2/P3 | `transfers.py:TransferGrants`, `PushReceiver`, `PreparationLeases`; `control.py:PrimaControl.execute/_call/close` | Handle lifetime, checkpoint recovery, validation deadlines, then negotiated stream/window semantics. |
| P0/P2 | `execution.py:asset_files`; `planner.py:materialize`; `control.py:_layout_asset/PrimaControl.cached_model` | Attribute digest/layout passes and pass scoped verification evidence only where justified. Keep manifest bytes stable. |
| P0/P4 | `preparation.py:ProfileJobs._run/_update_progress/start`; `profiles.py:ProfileCache/profile_key`; `kernel_profile.py:kernel_settings` | Accurate phase timing, provenance-preserving cache reuse, thread experiment identity. |
| P3 only | `src/kllm/modules/managed_network/interactors/network_channel.py:ChannelSession.request_runtime/pump`; `identity.py:MAX_FRAME/LocalIdentity/ChannelFence` | Validate channel constraints and negotiated authentication; preserve legacy limits and generic serial mutation handling. |
| P3 only | `src/kllm/modules/cluster/presenters/heartbeat_payload.py:HeartbeatPayloadBuilder.build`; owning membership schemas/parsers | Round-trip a new optional asset capability; enumerate both join/heartbeat ingestion paths before editing. |
| P0 presentation | `src/kllm/modules/runtimes/router_http.py:build_router` preparation status/log handlers; `dashboard/src/lib/modules/run/view/PrimaRunPage.svelte`; `dashboard/src/lib/modules/runtimes/view/PrimaPreparation.svelte` | Optional stage/rate diagnostics and truthful verification labels using existing polling; no dashboard restructuring. |
| Validation/docs | Existing Prima tests below; proposed new `tests/prima_preparation_benchmark.py`; `docs/control-api-v2.md`, `docs/troubleshooting.md`, `docs/cli.md` | Isolated benchmark/report utility, compatible status semantics, practical phase diagnosis and environment-variable usage/inheritance/default/range documentation. |

If P3 selects a bulk stream, add a narrowly scoped Prima-owned transport interactor with injected channel/OS gateways rather than placing feature policy in `shared/`. Its exact module/interface is a P3 decision, not an existing symbol.

## Correctness and regression acceptance

Run future checks in isolated temporary roots, never against live `KLLM_HOME`. Extend behavioral cases rather than tests that merely assert a chosen constant:

- **Staging/integrity:** byte-for-byte equivalence across local buffer sizes; resumed offsets not aligned to a buffer; empty/truncated/oversized chunks; wrong digest; corrupt prefix; complete partial with lost final acknowledgement; cached valid/invalid final; source/path replacement and mutation; no premature final visibility; cancellation during copy and hash; low-space admission and disk-full during writes. Existing suites: `tests/test_prima_integration.py`, `tests/test_prima_lifecycle.py`.
- **Progress:** fake-clock burst and steady streams spanning several windows; bounded updates/history, correct rate units, zero/idle rate, no negative ETA, cache hits, phase/rank/asset reset, immediate failure/cancel/completion, and no late callback reviving terminal progress. Assert externally meaningful publication/memory bounds; keep timing speed tests out of normal CI.
- **Leases/authority:** retain and extend `tests/test_prima_transfer_leases.py`: exact forward acknowledgements outlive the initial lease; no renewal on reads, repeats, expired/foreign/boolean offsets; verification progresses beyond an initial command interval only under its negotiated owned job; stalled verification expires; authority change interrupts queued/in-flight work.
- **Resources/crash:** `tests/test_prima_resources.py` plus process-boundary fault injection around write/flush/checkpoint/rename/terminal save; fresh restart must reconcile files without accepting stale ownership or overstated durability. Ensure every acquired handle/socket/reservation closes or reports cleanup pending on every terminal path; serving/profile/preparation conflicts still refuse.
- **Real protocol boundary:** extend `tests/test_prima_lifecycle.py:test_real_signed_channel_pushes_only_operation_bound_assets` using isolated real signed WebSockets/loopback peers. Test both versions, absent/unknown capability, partial writes, disconnect/lost ack, replay, backpressure, full queue, control responsiveness and fallback fencing. Bulk transport needs the same real authenticated boundary coverage, not only mocked `_call` responses.
- **Profiles:** `tests/test_prima_kernel_profile.py`, `tests/test_prima_measured_planning.py`, `tests/test_prima_candidate_profiles.py` cover thread/settings/storage/device/runtime invalidation, failed/incomplete-result nonpublication and unchanged read-only previews. Profile phase bytes must not represent RSS as measured transfer bytes.
- **Portability:** Windows and macOS real temporary filesystem tests for open-handle replacement, atomic rename, hardlinks/cross-volume fallback, paths with spaces and interruption; run affected portable tests on Linux too. Physical Windows/Vulkan and Mac backend measurements remain separate from fixture success.

Suggested first-phase command, after implementation and outside the current live task:

```text
uv run pytest -q tests/test_prima_transfer_leases.py tests/test_prima_lifecycle.py tests/test_prima_resources.py tests/test_prima_integration.py
```

Add the profile suites for P4 and affected channel/membership integration suites for P3. Run dashboard check/build if UI contracts change; native tests and supported rebuild/import only if native/packaging changes become necessary. Run `git diff --check` and review user modifications before integration. No test suite or hardware benchmark was run to produce this plan.

## Hardware benchmark method and proposed targets

All numbers below are **proposed acceptance targets, not measured results or promised speedups**.

1. After live preparation completes, record exact code/runtime commit/recipe, model digest and size, OS/filesystem/source/destination volumes, free disk, available RAM, backend/device/driver, thread/context settings, route/interface and negotiated link speed. Preserve production assets/jobs; use isolated cache roots with sufficient free space and only explicitly owned test artifacts.
2. First test small deterministic files and medium files (for example 256 MiB and 4 GiB), then the full 63,387,345,344-byte model when disk budget permits. Cover cold destination, valid warm cache, partial resume, corrupt partial, local-only staging and remote-only transfer. Record source/OS-cache warmth; do not present repeated cached reads as disk performance or forcibly flush system caches. A 59 GiB file exceeds the Mac's physical RAM; watch swap, memory pressure and filesystem caching explicitly.
3. Baseline versus P1 buffer-only, progress-only and combined runs; then independently assess P2 changes and the selected P3 transport. Use at least three alternating matched baseline/candidate pairs on medium workloads and, where budget permits, three full-model pairs. Report each run, median/range and unresolved cache/thermal confounders. Fewer full runs are preliminary evidence, not a stable performance claim.
4. Record phase wall/CPU time, read/write/hash bytes, callback/publication counts, peak agent RSS, system memory pressure/swap, source/receiver disk activity, socket bytes, acknowledgement latency and command responsiveness. Measure direct sequential-copy throughput with the same source/destination and buffer independently of progress overhead, including a separate verified-copy measurement.
5. Measure raw private-link TCP throughput separately in both directions, with a bounded diagnostic stream on the existing approved route during the test window. Adapter speed is not measured payload bandwidth; existing signed link-profiler evidence describes its specific workload, not necessarily bulk file-copy throughput. Keep asset payload, encoded wire bytes and raw TCP results separate. No network configuration changes are required for comparison.
6. Hold `include_profile=false` for staging/transport comparisons. Later profile-only comparisons use verified caches and identical manifests/settings; record hash/layout/inspection, native kernel and link phases separately. Do not include these as staging speed gains.

| Area | Proposed acceptance |
|---|---|
| Correctness | Zero accepted corruption, premature publication or unauthorized continuation; existing resume and lease behavior preserved. |
| Instrumentation/progress | At most four regular publications/second plus forced boundary events; at most 40 samples/job; instrumentation overhead target under 2% on matched medium-file runs. |
| Local copy | Copy-stage throughput target at least 80% of matched direct streaming-copy baseline; total verified staging no more than 5% slower than baseline, with measured gain reported if present. These exclude an assumed universal disk rate. |
| Remote optimized path | Payload-stage target at least 75% of the measured minimum of raw-link payload, source read and receiver write throughput; measure final verification separately and total wall time as well. Legacy fallback must remain correct and avoid material regression. |
| Memory | Initial transport-buffer budget at most 8 MiB per active stream and proposed additional agent RSS budget under 64 MiB/node, excluding OS page cache and native profiler memory. No whole-file allocation or unbounded queue. |
| Responsiveness | Healthy-I/O local cancellation and supported optimized-transfer control response p95 target under 1 second; record OS-blocked I/O exceptions separately. Verification must be cancellable between bounded reads. |
| Resource recovery | No retained handles/sockets/reservations after successful cleanup; crash recovery retransmits only within its explicitly chosen checkpoint/window budget and never trusts partial bytes without final validation. |

For scale only, nominal 1 Gbps equals 125 MB/s or about 119.2 MiB/s before overhead. Transferring this file alone has an idealized lower bound of roughly 507 seconds (8.45 minutes), excluding local copy, hashes, protocol overhead and profiling. This arithmetic is not a cluster measurement or end-to-end target.

Disk planning must include downloaded source, remaining `.part`, any invalid existing final, cache final and possible full layout-copy fallback, plus companion/shard files and benchmark roots. Do not double-count same-filesystem atomic `.part`→final rename as a second allocation, but do reserve for actual cross-volume copies. Keep the existing reserve at minimum; preflight all simultaneously needed artifacts and fail cleanly if another process consumes space. Never free space by deleting the ongoing job's caches.

## Compatibility and rollout

1. Land P0/P1 first as wire-compatible changes, with the original transfer frame bound and digest publication contract. Metrics are optional status data and do not alter canonical manifest identity.
2. Gate P2 checkpoint/verification reuse changes independently; retain conservative rehash/copy paths when identity or filesystem support is uncertain. Keep old on-disk partial/final formats readable.
3. Deploy P3 receiver support before enabling master selection. Mixed agents explicitly use legacy mode; select and record negotiated transport per job. Keep a supported legacy selection for subsequent jobs and a tested rollback path. Rollback does not hot-swap an active transfer or revive expired grants.
4. Asynchronous verification/new status values require explicit peer negotiation; legacy jobs never receive unsupported state semantics. Terminal publication and ownership records remain durable under either version.
5. P4 cache/thread work follows correctness and measured profile-preparation costs. Existing runtime qualification and hardware acceptance gates stay authoritative; faster preparation does not qualify inference.

## Open questions and decision gates

- How much of the supplied local 26 MiB/s is progress-list CPU overhead, local storage, memory pressure or another factor? No stack/counter evidence was collected during this planning task.
- What are the active source/cache/layout paths, filesystem types, physical volumes and available disk on both nodes? Is layout fallback duplicating a full model? Current checked-out source may differ from the running agents.
- How many full-file hash passes occur in the real selected preparation/profile flow, and which can share a genuinely immutable snapshot? The complete native profiler's internal I/O/kernel timeline has not been assessed here.
- Does last-chunk hashing exceed the 60-second signed deadline on either machine? How does current final-ack timeout recovery appear to users? Verify before selecting asynchronous completion semantics.
- What durability guarantee should an acknowledgement make, and what maximum retransmission/checkpoint interval is acceptable? Persistent receiver handles and reduced JSON writes depend on this answer.
- Does RTT, signing/JSON, worker persistence, source opens or receiver storage dominate remote transfer? Does a bounded signed-channel window meet the target without compromising control responsiveness, or is an authenticated binary stream justified?
- If a bulk stream is justified, can an existing authenticated endpoint carry it, and how will mutual authentication/key establishment and private listener ownership be implemented without widening access?
- Which exact backends and profile capabilities are selected on the Mac and Windows nodes, and what native memory demand does this model's kernel profiler have? More threads and parallel ranks are not assumed improvements.

The recommended first deliverable is P0/P1: trustworthy phase attribution, an independent local-copy buffer and bounded progress updates. P2/P3 are measured follow-ons with integrity and lifecycle gates, not prerequisites for gaining evidence or fixing avoidable local overhead.
