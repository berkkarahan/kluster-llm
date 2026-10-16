# Prima.cpp preparation and qualification

Generation profiling requires the prepared server's `model_structure_v1` capability.
Its `--inspect-model --model FILE` mode reports a versioned
`KLLM_PRIMA_MODEL_STRUCTURE` record with `architecture`, `stored_blocks`, and
`execution_layers`, using the same native hyperparameter loader as inference,
without loading weights or starting a server. Stored GGUF block counts are not
execution ownership: auxiliary predictor blocks can be excluded by the runtime.
Both profiling passes use the executable count; preparation-bound profiles must
also match the approved manifest. Older runtimes require preparing the current
managed Prima version. Inspection and generation logs retain native failure details.

Recipe 136 (Prima 0.0.3) also advertises `graph_execution_evidence_v1`: completed
prefill/decode matrix work is checked against the native graph and exact physical
device ownership, including fused and recurrent layers. Generation profiles bind
the approved F16/Q8 K/V cache and explicit Flash Attention setting into their cache
identity; a different setting cannot reuse the measurements. CPU and Vulkan passes
each require one warm-up and three completed measured samples before publication.

Recipe 137 (Prima 0.0.4) restores Llama's existing rank-aware Q/K/V tensor loader.
A stale dispatch had sent Llama into MiniCPM3's low-rank attention loader, causing
`tensor '__missing__' not found` after successful metadata inspection. The native
structure contract test now also loads a complete small Llama tensor fixture;
metadata inspection alone is not model-loading acceptance.

Local hardware verification on 2026-10-16 used the freshly managed 0.0.4 build
(`0832998f8ff6eb1194d2ffa49165cbaef8836cd8`), Windows, and an AMD Radeon RX
9070 XT. Saluki Qwen3.5 (65 stored blocks / 64 executable layers), TinyLlama
(22 / 22), and GPT-OSS-20B MXFP4 (24 / 24) each completed CPU and Vulkan
warm-up plus all three measured samples, passed device execution evidence,
published their profile cache, and had the matching measurements accepted by
HiGHS placement. GPT-OSS also served a successful authenticated chat completion.
These are single-node, single-device results; distributed and other hardware
coverage remain separate acceptance work.

Prima is an optional engine **under development**. The current integration supports
isolated source builds, topology previews and local profiling. It is not yet a
drop-in inference replacement. Normal target/local selection and inference launch
are blocked by source-controlled qualification gates; llama.cpp remains the default.

Execution qualification now binds each source-controlled `execution_scopes` entry
to the model/companion names and hashes, exact rank ranges and GPU/output placement, selected
device/driver/capacity, and launch settings (context, batching, cache, threads, load
mode and decoding/template options), plus CPU, core counts, RAM and OS build.
Each entry needs its own receipts and allowed
request features. Run IDs, ports and planner preferences do not grant additional
execution scope. The admitted context is persisted in `active_config`; gateway
requests recheck it, including policy revocation. All production scopes remain empty.

The runtime library can resolve and save the supported Prima source through its
existing endpoints using `engine: "primacpp"`, repository
`https://github.com/berkkarahan/prima.cpp.git`, and either the pinned commit ref
(ref_type `"commit"`) or the pinned release — recipe 137 pins
`berkkarahan/prima.cpp` release `0.0.4` (`source_release` /
`source_release_commit`) whose tag resolves to exactly the pinned commit; both
routes produce the same immutable identity. A moved or deleted release tag refuses
until the recipe is advanced. The response includes `recipe_digest`. The recipe
records upstream `OpenCPIL/prima.cpp` commit
`288df6a2cc2120387e12d6f40798f30cc525c9ea` as provenance. Native changes are already
committed in the fork, so preparation applies no local source patches. The fork's
integration branch was merged into `main` and released as `0.0.1`.
Build using the existing `/v2/node/runtime-builds` resource. Settings → llama.cpp →
Add version offers Prima under qualification (the dialog prefills the pinned
release); the Prima settings section offers "Install 0.0.1 and set target" for the
master. Build and logs are available; selection requires completed qualification.

## Recipe 133 — Saluki / Qwen3.5 dense text

| Recipe | Fork commit | Release | Recipe digest |
| --- | --- | --- | --- |
| 133 | `bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554` | `0.0.1` (moved from `be162f6`) | `27782ffb7cc01334f7305e78b1fd40550ed157d9bf8668aa3674917db40c26cf` |

This pin adds `qwen35` text inference on CPU and Vulkan, including separate
attention/recurrent cache state and original mixed IQ weight formats. Saluki's
GGUF declares 65 blocks including one MTP predictor; the execution plan has 64
trunk layers. MTP tensors are skipped and speculative execution stays disabled.
Model-layout accounting preserves the declared metadata but excludes predictor
weights from active placement. `qwen35moe` and vision are not included.

The runtime publishes registry-derived `architectures` and
`qwen35_segmented_v1`. Prima plan previews and the rank spawn boundary refuse
unimplemented families with `PRIMA_MODEL_UNSUPPORTED`; older reports without
the field produce `PRIMA_MODEL_SUPPORT_UNKNOWN` warnings instead. Native load
errors name the architecture, readiness failures include the rank-log tail, and
the dashboard uses the preview's refusal. This gate does not affect llama.cpp.

Local Windows/RX 9070 XT development runs matched 16 greedy token pieces against
the pinned llama.cpp reference for 5- and 104-token prompts on CPU. Vulkan also
matched those continuations and served chat; GPU sequence-state receipts include
all 64 trunk blocks and the output projection in both prefill and decode. Input
embedding lookup remains CPU-resident by design. Original IQ block/gather/matrix
checks and CPU/Vulkan save/restore/copy contracts passed. These results do not
establish distributed or performance qualification, and recipe 133 does not change
the existing qualification policy. Fresh managed-build and deployment receipts
are recorded separately in [spec 020](../specs/020-prima-qwen35-model-support/validation.md).

The moved-release guard remains intact: the tag must resolve to this exact commit.
Older prepared builds stay in the library but cannot stand in for this recipe.

Recipe22 corrects native kernel-profile operands: floats are encoded into actual
F16/quantized storage, completed multiplication is checked against decoded operands,
and unsupported/failed computation refuses measurement without CPU substitution.
Published CPU contracts cover12formats; physical RX9070XT checks cover9Vulkanformats
and3unsupported IQ refusals. These synthetic kernel estimates do not establish
full-model performance or partial-rank costs. Recipe23 adds typed full-model kernel
reports from complete GGUF metadata: actual attention dimensions/head norms, active
MoE arithmetic, full storage and absolute per-layer CPU/GPU estimates. It repairs
the Qwen3 extraction failure and removes the missing-fio dependency. Native probes
use checked GPU reads/copies and exclusive unbuffered I/O on the model filesystem.
An unwritable/unsupported filesystem refuses profiling; it cannot produce RAM-cache
disk claims. Temporary files are removed on ordinary exit or owned child death.
Windows alignment follows [Microsoft's buffering requirements](https://learn.microsoft.com/en-us/windows/win32/fileio/file-buffering).

Kluster validates schema2 backend/settings, complete ordered layers, rate tables,
storage bounds and derived estimates before caching. Context/threads/version and
observed host/device hardware are bound to cache identity; hardware is rechecked
before publication. A typed-capable native build cannot return the older schema1
Q4K-only contract. Existing older cache entries remain available but unmatched.
These roofline estimates are not end-to-end generation timings or qualification.
Fresh published23 managed jobs pass on the supplied dense/MoE models, with28/48
complete layer vectors and no retained process. Native contracts, physical selected
Vulkan/read checks and all CI jobs pass. Authoritative remote planner consumption still remains
T066/T017/T021 work. Physical distributed validations will be tested later.

Preparation-bound profile control now advertises prima_profile_version2. Workers
require an exact approved preparation/manifest/model/runtime/rank/settings and
persist operation/sender/authority ownership in the profile job. Another operation
cannot read/cancel it or adopt its request ID. Native work and cache publication
recheck current authority plus progress-renewed approval; cancellation is serialized
with publication. Explicit backend selection preserves a CPU coordinator on a
Vulkan build. The master rechecks signed node hardware before importing complete
typed costs. A real signed three-agent/process fixture covers these boundaries;
its measurements/model execution are controlled, not physical qualification.
Candidate comparison still needs fresh exact link/disk context and reservations.

Distributed asset preparation advertises `prima_asset_lease_version:1` and requires
that capability before worker preparation. Verified source grants begin when their
asset is about to transfer, so future shards/companions do not expire while another
file copies. Only an exact next-range acknowledgement renews a source grant; reads,
duplicate/wrong acknowledgements and changed sources do not. Authenticated successful
worker chunks extend the receiving job's600s approval, and an expired/cancelled job
cannot be revived by a late chunk or renewal.

Signed `prima_prepare_renew` binds `{job_id, progress}` to the original operation,
sender and master authority. The master renews enrolled workers while verified
source hashing/acknowledged bytes or tracked profiling work progresses, including
workers that finished before another transfer. Repeated unchanged progress is
refused; stalled/abandoned preparation expires. Final progress renewal aligns the
complete worker approvals before publishing master preparation. Worker cache checks
and chunk I/O run outside the command event loop; preparation requests use the
bounded60s signed-command window. These controls have simulated-clock, real-file and
signed local transport coverage; physical distributed transfer validation will be
tested later and qualification remains disabled.

Native listener admission has a separate source-tested foundation: signed
`prima_admit_ports` carries an exact manifest-bound contract and
`prima_release_ports` carries the owned run/network cleanup identity. The worker
checks its complete approved preparation, private assignments and current hardware
before forwarding the original signed envelope to the privileged helper. Windows
rules are restricted to the private local address, adapter, exact TCP listeners and
approved peer addresses; HTTP and RPC ports are not added by this contract.
The helper journals mutation intent and CIM snapshots, rejects replay or foreign
rules, restores cleanup only after restart, and refuses network restoration while
run-owned rules remain. Rules whose identity changed or whose creation snapshot
was lost stay pending for local review; they are never silently adopted or deleted.

Local listener recovery is available through token-authenticated, loopback-only
`GET/POST /v2/node/network-setup/recovery/{network_id}/prima/{run_id}`. GET reports
the durable and current rule snapshots, classifies each as exact/changed/unknown/
absent, and returns a `snapshot_digest`. POST takes `{decision, snapshot_digest}`:
`restore_owned` removes only rules still matching their creation receipts;
`keep_current` explicitly preserves every current rule and relinquishes Kluster's
listener ownership. Preserved rules then require manual management. Neither choice
adopts changed rules, resumes inference or releases other nodes' cluster ownership.
Stop the owned rank first. Active admission, stale reviews and foreign run/network
IDs are refused. Failed cleanup or persistence remains retryable. Setup status lists
`prima_listener_recovery` resources, and helper recovery capability1 is required;
existing admission capability2 remains unchanged. These paths have controlled
native-rule and HTTP API coverage, not physical Windows firewall/service acceptance.

The Private 10GbE network panel lists local pending Prima runs for masters and
workers. Review Prima rules shows the allowed TCP ports/peers and ownership status,
with full snapshots under Rule details. Remove unchanged Prima rules is available
only when every rule is exact or already absent. Keeping all current rules requires
acknowledging that they remain and need manual management. Refresh, close and each
decision attempt invalidate the displayed review; stale/foreign responses cannot
enable another decision. After local resolution, retry Stop on the master to finish
cluster cleanup. These controls use the existing token-authenticated local API.

Prepared distributed Run now performs signed capability checks and reserves every
native listener before spawning any rank. Membership advertises `prima_ports_version:2`;
older agents/helpers or unresolved native rule ownership are refused before mutation.
Attempted and admitted nodes are durable reservation fields. Stop drains ranks first,
then releases rules and narrows each acknowledged node; failure retains pending
ownership while allowing cleanup of other nodes. Node-local port intent survives
lost helper replies and agent restart. Its initial lease is bounded to600s; launch
and owned renewal switch it to60s. Validation-to-spawn and expiry share one lock;
expiry or reconstruction stops only the matching owned rank before rule release.
The protected helper has its own timer thread and monotonic deadlines, independent
of agent/IPC activity. Exact signed `prima_renew_ports` runs immediately after rank
startup and during owned renewal. It changes the helper's initial600s rule lease to90s,
leaving30s beyond the agent's60s rank lease. Status reads never renew it, and expired
or changed ownership cannot be revived. Restart resumes native-rule cleanup only.
Failed/changed rules stay pending while other expired runs are drained. Helper
capability2 plus watchdog_version1 is required; older agents/helpers need upgrading.
Cancelling a wait settles its outstanding helper call before cleanup proceeds;
coordinator generation checks fence delayed admission callbacks. Port-only ownership
also blocks new preparation/Run and is drained by Stop.

Direct administrator and Linux/macOS backends, physical/service acceptance of helper
expiry while its agent is absent, physical changed/lost-rule recovery acceptance,
protected-helper IPC and physical
OS validation remain T056 work. The current helper path requires its Windows backend
and the updated protected helper; rebuilding stages the binary without deploying it.
No live firewall changes or helper deployment were used for these checks. Signed
three-agent tests use controlled adapters/rules/model execution and real channels,
owned children, HTTP and persistence; they do not qualify physical connectivity.
The helper removes only its verified owned firewall rules. It does not kill ranks
or establish that existing TCP traffic/generation stopped; the coordinator retains
cluster ownership until rank stop acknowledgements. The timer thread/monotonic and
signature checks have controlled coverage, without live firewall/service deployment.
Existing helper journals lacking assignment revisions require owned-network cleanup
and fresh preparation before native listener admission. Physical distributed cluster
gates and validations will be tested later; qualification remains closed.

Local helper requests now share one monotonic deadline across their serialization
queue, connection and framed I/O:8s for queries and45s for mutations/recovery. On
Windows a thread-handle cancellation worker retries synchronous I/O cancellation
until the original call settles, then joins before releasing client ownership. The
server also retries cancellation across gaps between calls, and flushes named-pipe
reply bytes under its45s deadline before disconnecting, following the
[Windows pipe-disconnect contract](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-disconnectnamedpipe).
Ordinary file flushing does
not provide that pipe-consumption barrier. Malformed/oversized replies are refused;
the protected server image is checked before any request payload is written.

A TIMEOUT is distinct from missing administrator authorization in setup status and
the dashboard. An unresponsive helper blocks setup and shows recovery guidance
instead of prompting for a new grant. Timeout, cancellation or disconnect does not
prove that a server-side mutation or rank stopped: durable listener/cluster intents
remain pending until the normal cleanup acknowledgement. Isolated Windows pipe
tests exercise the transport and owned client-worker settlement; protected-service
deployment, authorization ACLs, service failure/restart and physical firewall
acceptance remain separate T056 work. Distributed cluster validations will be tested
later. The Windows cancellation behavior follows the
[Microsoft API contract](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelsynchronousio);
some operations can complete after cancellation is requested, so waiting for actual
settlement remains required.

Already elevated Windows agents now have a direct listener backend. It launches
the staged native helper as an owned child with `--stdio-prima` over private stdin/
stdout pipes. The native process requires administrator privileges itself and
reuses signed network/rank authority checks, exact listener contracts, durable CIM
ownership and the independent rule watchdog. It accepts listener operations and
read-only manual-address observation/bookkeeping; it cannot add/delete addresses,
change DHCP or replace administrator authorization. The existing manual adapter
keeps its address journal, event barrier and disabled-DHCP requirement.

Direct mode observes the signed prepared/applied address before admitting listeners,
including pre-existing user-owned addresses. Its scope marker prevents adopting
protected-helper address ownership; reconstruction never restores DHCP or removes
manual addresses. Native signed reconciliation updates peers only for an unchanged
continuously observed assignment after listener ownership is drained. Agent cleanup
checks its local Prima owner before removing a direct private address.

Startup retains a remembered direct ledger even if privileges were lost and keeps
older protected-service port ownership on that service. It never releases through
a different empty ledger. Grant changes require draining the old direct scopes/rules
before archiving their retired journal. Retirement requires explicit typed empty
ownership lists from the matching grant; missing/invalid fields refuse the change.
The replacement process must negotiate direct mode before receiving authorization.
Switching to the protected helper first
closes the direct process after cleanup. Agent/service shutdown owns that child;
EOF/broken pipes make the native process attempt exact rule cleanup and retain
failed/changed entries. This does not prove ranks or existing TCP generation stopped.

Each direct pipe request has an independent UUID echoed in its reply. Old replies
cannot acknowledge newer cleanup, and partial response framing survives a read
timeout. Partial writes or malformed framing abandon the owned pipe and settle the
old process before another owner starts. The original signed payload is unchanged.
Real subprocess-pipe checks use controlled policy replies; native signature/replay/
scope tests use the shared policy. This computer is not elevated, so the staged
native executable's administrator rejection is verified but accepted elevated
operation/firewall behavior remains physical T056 acceptance. Linux/macOS backends
and protected-service deployment/restart/ACL checks remain open. Physical distributed
cluster gates and validations will be tested later; qualification stays disabled.

Distributed Stop retains its run identity after monitor-driven coordinator retirement.
Only successful owned cleanup clears the matching Prima active configuration; a
different run's configuration is preserved. The direct-backend/Stop delivery suite
passes1478 Python tests (19 skipped), native28 checks and supported packaging/import.
These receipts do not close the broader compatibility, planning or performance work.

Vulkan GPU computation is a mandatory acceptance requirement on Windows, Linux and
macOS (MoltenVK). Qualification includes selected-device GPU offload, profiling,
placement, distributed generation and measured performance. CPU, Metal or CUDA results
cannot establish Vulkan support; a requested Vulkan backend must never fall back to them.

Identity includes repository, commit, engine and build recipe.
Recipe schema 2 / build recipe 23 pins the fork source. Saved builds from the previous
upstream-plus-patch recipe keep their records but must be resolved and rebuilt against
the new source before use; they cannot satisfy the current recipe identity. ZeroMQ dependency
commits and archive digests are pinned. Builds produce static server, CLI and profiler
artifacts in the isolated runtime library and never replace `vendor/bin`.

Recipe18 added OpenAI chat choice/stream token logprobs. Fresh managed Windows/
Vulkan gateway/profile/lifecycle assessments pass35 checks on each supplied dense/MoE
model. API/model/placement coverage remains incomplete and paired local performance
still fails, so ordinary selection remains blocked.

Recipe19 preserves F32 Vulkan matrix operands and view copies, honors explicit F32
precision, and requests it for attention. Tile geometry respects device shared-memory
limits. Physical regressions pass 237 matrix and 185 expert comparisons. Fresh managed
recipe 19 build, CI, and both published gateway suites pass. Exact published paired
benchmarks match bytes/counts but still fail throughput; qualification remains closed.
Physical distributed validations remain deferred for later.

Recipe20's fresh managed build and published gateway/profile/lifecycle suites pass38
checks on each supplied model. The dense two-slot suite passes36 checks. Exact
settings/placement/host qualification and request-time revocation are implemented;
production gates remain false while broader compatibility and acceptance are open.

The earlier MoE benchmark used different baseline transformer/output placement:
the pinned llama.cpp counts output within every positive GPU-layer count. Its old
ratios cannot establish matched performance. Comparison tools now translate that
count and force CPU projection, including tied embeddings, when required. A long
partial-offload dense probe verifies both engines' matrix placement. Published paired
timing must be repeated with the corrected harness; historical receipts are retained.
Physical distributed validations will be tested later.

Corrected published five-pair timing still fails overall: dense passes only the two
concurrency2 workloads, while normal MoE33/GPU-output fails throughput and long-prompt
generated-count matching. T032/T039 remain open. Profiling now sets batch threads
explicitly and keys its fixed512/128 batching plus batch-thread count; previews keep
capacity-first planning when the requested batch threads differ. Older cached probes
need regeneration. This cache correction does not pass a qualification gate.

Signed remote startup requires completed, unexpired preparation of the exact runtime,
manifest and declared assets by the same sender and master authority. Rank ownership
persists that binding for start/status/renew/stop; cached bytes alone cannot authorize
startup. Production distributed coordinator/readiness/admission wiring remains open.
Physical distributed cluster gates and validations will be tested later.

Recipe21 adds post-initialization native rank acknowledgements, applies the CLI
manifest before rank/world startup checks and fixes zero-transformer coordinator
warm-up. Signed worker status now validates the owned child's complete
`KLLM_PRIMA_RANK_READY` report before promoting it to ready; configured-only output
does not promote it. Rank zero additionally needs HTTP health. Dead processes and
wrong runtime/manifest/stage/layer/GPU counts are refused. Cancelled coordinator
cleanup retries pending stops and retains pending state until admission release.
Fresh published dense/MoE gateways each pass38 checks. Four controlled same-host
published CPU/Vulkan normal/zero-master cases pass readiness and generation, including
completed GPU ownership where requested. Complete participant qualification, native
rank-port admission and final distributed acceptance remain required; physical
distributed validation will be tested later.

Coordinator ownership is now durable across restarts: each launch intent is saved
before its command, and pending ranks remain owned until their stop is acknowledged
and admission is released. Reconstructed coordinators clean up previous runs instead
of resuming inference. Readiness and renewal check exact run/runtime, rank, layer/GPU
counts, backend and command authority. Recovery is bounded to three attempts;
cancellation and failed cleanup/persistence prevent another launch. Controlled
real-child restart coverage preserves unrelated processes. Prepared multi-rank
Run/Stop now uses that coordinator and Prima-specific private reservations with
signed diagnostics, exact assigned addresses and immutable manifest/authority/edge
identity. It starts no RPC relays or children. Worker renewal rechecks its private
owner; RPC startup/release cannot take over Prima ownership. Controlled three-agent
tests cover signed asset transfer, worker-first startup, the HTTP readiness barrier,
cancellation, full Stop and worker-crash recovery. Model execution and adapter changes
are fixtures there. Signed participant hardware/scope binding now uses node-local
observations for every rank; native port admission and qualification remain required.
Physical distributed cluster gates and validations will be tested later.

The final composed-dispatch checks pass175 focused tests (3 skipped) and1334 full
Python tests (19 skipped). Both published recipe21 local dense/MoE gateway/profile/
lifecycle suites pass38 checks. After initial readiness, serving keeps the validated
acknowledgement and checks owned process identity plus HTTP health; growing trace
logs do not invalidate that acknowledgement. Qualification remains closed pending
the remaining implementation and acceptance requirements.

Distributed preparation binds signed host/platform/source/recipe/build/artifact and
selected Vulkan UUID/driver/capacity/ordinal evidence into its manifest. Fresh signed
inventory checks run before any rank starts, and owned startup/renewal revalidate
current hardware. The qualification scope binds the complete observed participant
set, including an explicit zero-work CPU coordinator with required Vulkan workers.
Empty metadata is omitted so legacy/local canonical manifest digests stay unchanged.
Inventory establishes observed identity, not inference or performance acceptance.

## Preparation interfaces

All endpoints retain the runtime library's API-token and same-origin rules. Mutation
requires the local node application. IDs refer to persisted resources, not paths.

| Endpoint | Behavior |
| --- | --- |
| `POST /v2/node/runtimes/{runtime_id}/prima-plan` | Master-only, read-only PlanBody preview from current membership; returns ranks, network edges and qualification blockers. |
| `POST /v2/node/prima-profiles` | Start a local profiling job with runtime_id, model, context, threads and UUID request_id. Requires prepared Prima and no active local inference. |
| `GET /v2/node/prima-profiles/{job_id}` | Profiling state, estimate, provenance or failure. |
| `GET /v2/node/prima-profiles/{job_id}/logs` | Bounded profiler output. |
| `POST /v2/node/prima-profiles/{job_id}/cancel` | Cancel and clean up the owned profiler process. |

Topology uses the existing Kluster master as rank zero and includes only planned
workers. Layer windows encode cross-node work; each rank has its own `ngl`, device
order and tensor proportions. Rank launch arguments carry the planned `-ngl` and
`-ts` values; Vulkan device IDs are constrained with `GGML_VK_VISIBLE_DEVICES`.
Zero-layer coordinator windows are supported. Plans that cannot be represented are reported, not
reordered. Ports are derived from data/signal base ports plus rank; the ring and
worker/master edges must eventually pass network admission before any inference.

On macOS Vulkan source builds, the staged runtime includes a relocated Vulkan loader,
MoltenVK library and ICD manifest. The runtime manifest retains the staged ICD path,
and modified Mach-O files are ad-hoc signed after relocation. Device enumeration checks
this packaged path. It confirms MoltenVK discovery only; it does not qualify model
execution or performance.

Profiling is an explicit operation, bounded to ten minutes and 1 MiB output. Vulkan
profiles use a Vulkan-backend Q4K kernel measurement and expose Vulkan copy/bandwidth
measurements; the profiler refuses CPU fallback for its Vulkan throughput value. These
kernel estimates are not decode benchmarks and cannot satisfy the release performance
gate. Profiles are keyed by runtime/model/hardware/settings/backend, bounded to 256
cache records and expire after one day. Ordinary topology previews run no profiler,
transfer no model and start no runtime processes.

## Remaining qualification

Windows and all accelerator contracts, current server/model/launch/API features,
arbitrary placement, signed rank lifecycle, authenticated remote asset transfer,
remote profiling/planner integration and physical paired benchmarks remain open.
The source-controlled recipe records these gates, including a separate mandatory
`vulkan_gpu_inference` gate, as incomplete. Editing an artifact
manifest cannot turn them into successful qualification.

Track the full accepted implementation in [feature tasks](../specs/017-prima-integration/tasks.md).

## Manifest and cluster preparation foundation

Build recipe 7 requires `execution_manifest_v1`. Native manifest checking is available
through `--execution-manifest FILE --rank N --check-execution-manifest`. Manifest mode
prevents automatic profiling/scheduling and weak-node pruning even with a zero-layer
rank zero. Current execution is restricted to exact single-window layer layouts;
segmented revisits, row/tensor and remote-expert layouts fail explicitly. A configured
rank report is not proof that Vulkan executed its layers.

The master can preview a saved prepared runtime using `kllm plan --runtime-id ID --model MODEL`.
`kllm prepare --runtime-id ID --model MODEL` starts a master-owned asset job;
`kllm profile` also requests selected-worker kernel profiling. Workers need the same
prepared source/recipe and `prima_control_version: 1`; missing preparation reports an
upgrade/build error rather than launching a different runtime.

`POST /v2/cluster/prima-preparations` accepts runtime_id, settings, plan_digest,
request_id and include_profile. Status and bounded summaries are available at
`GET /v2/cluster/prima-preparations/{job_id}` and `/{job_id}/logs`; cancellation is
`POST /v2/cluster/prima-preparations/{job_id}/cancel`. Existing local profile routes remain.
The runtime Settings screen exposes preview, preparation, profiling and participant status.

Asset chunks travel from master to worker over the existing paired signed channel.
Each job declares digests/sizes and binds chunks to its operation and sender. Resumes
revalidate offsets and digest verification precedes publication. Partial assets remain
resumable after interruption; cancelled/expired jobs do not grant execution ownership.

The standalone [Windows assessment guide](prima-windows-assessment.md) describes the
published assessment runner and its limits. Baseline backports, production engine dispatch
and network admission, measured end-to-end profiles and physical distributed execution
remain incomplete. Qualification gates stay false.

## Windows local continuation

Recipe 11 pins published Prima commit `720da841239bc2ffef55b1fe08895562ade30bef`.
The managed Windows/Vulkan build and portable CI pass. Normal Kluster plan/run/stop
dispatch, authenticated text protocols, streaming and owned-process cleanup have
real isolated gateway coverage. The dense expanded assessment also covers queued
requests, disconnect cancellation, crash restart and completed GPU output projection
work. See the latest [validation receipt](../specs/017-prima-integration/validation.md)
for the exact tested scope. Both supplied models pass the 27-check recipe11 gateway/
profile/lifecycle suite after cleanup retry and late initialization fixes.

Model-backed generation profiling accepts `mode: generation`, explicit `gpu_layers`
and `output_gpu`. HTTP profiling, prepared-asset profiling and planning share one
registry-owned service/cache and startup reservation. Normal previews consume only
matching cached measurements and retain capacity-first planning when evidence is
missing. The output projection is separate from transformer layer counts and its
placement participates in profile matching and execution proof.

Local performance and remaining API/model/settings/placement compatibility are still
open; both source-controlled Windows qualification scopes remain false. Physical
distributed cluster gates and validations will be tested later, as requested. That
deferral does not pass them or defer the remaining distributed implementation work.

The current recipe12 pin is `5e3e2e13a4b0779d22b418d867088f86d8a3f8f1`, which
corrects telemetry opt-in: `KLLM_PRIMA_EXECUTION_EVIDENCE=0` disables it and `1`
enables it. Its fresh managed build/package/portable CI and sampled native protocol/
GPU checks pass. Both new paired matrices still fail. Historical recipe11
gateway passes do not qualify a different native source/recipe identity.

Recipe14 now pins `501585d9116457f998d5372341c49b1ad093e6c3`; its managed Windows/
Vulkan build, package/native import and portable CI pass. The local execution graph
is connected through input, transformer layers and output. Structured chat accepts
optional JSON code fences while returning parsed content and actual generated usage.
The published dense and MoE 27-check gateway/profile/lifecycle suites pass. Development
dense paired output bytes/token counts match, but throughput still fails. Final paired
acceptance remains open; see the feature validation record. The
qualification policy remains closed and distributed physical testing remains deferred.

The current recipe15 pin is `19adfe0800130e829544719136550257b21fd9f6`. It adds
small-batch Vulkan vector dispatch and corrects short/odd-vector bounds and strided
F32 matrix conversion. Physical development operation/protocol checks pass. Dense
paired concurrency-two throughput improves, but all four workloads remain below
acceptance. Its fresh managed build and CI are in progress; recipe14's published
gateway results remain historical evidence, not qualification for recipe15.

Recipe15's fresh build/package/native import and both portable CI runs now pass.
The published dense gateway/profile/lifecycle rerun passes27 checks after correcting
the test's stream-iterator lifetime during cancellation observation. Further native
performance work and final published-source model/paired acceptance remain required.

The equivalent published15 MoE gateway/profile/lifecycle suite also passes27 checks.
An unpublished floating-point view optimization improves dense concurrency-two timing,
but the full paired matrix remains below acceptance. Final measurements/publication
are pending; local and distributed qualification remain closed.

Current recipe16 pins `a024a7edc21d50c558f608878b7bb085bfde8aae`. It fixes batch
padding and fourth-dimensional Vulkan vector dispatch after25 reproduced operation
failures;194 supported matrix and185 expert GPU/CPU comparisons and both development
model protocols pass. Fresh published-source build/CI and affected acceptance are
pending. Worker CLI launches omit the HTTP alias while preserving decoder settings.
Coordinator/admission/readiness wiring and full compatibility remain open. Physical
distributed cluster validation is scheduled for later; no qualification gate is passed.

Recipe16's fresh managed build/package/native import, source/artifact verification
and both portable CI runs now pass. Published gateway and paired performance checks
still need execution; development protocol/operation checks do not replace them.

Final full Python rerun passes1271/19skipped and the published16 dense --profile
gateway passes27 checks. MoE equivalent and paired timing are pending. All remaining
API/placement/remote-dispatch work and the separate qualification gates remain open.

The published16 MoE gateway --profile suite also passes27 checks. Both supplied
models pass that subset; final published paired acceptance remains in progress.

Published16 dense paired timing now completes: bytes/counts match, but three of four
throughput workloads fail (approximately0.93/0.98/0.85/0.73 for short1/short2/long1/
long2). MoE timing remains in progress. The qualification policy stays closed.

Published16 MoE timing also fails throughput and intermittent long2 generated-count
matching, with identical bytes. Recipe17 now pins published `622d5c4f9fdc1fa399c3f20e5632a67a2b63fdca`:
measured quantized-loop improvement and bounded-grid contracts pass development
operation/protocol checks. Its dense matrix improves modestly but still fails overall.
The slower four-row experiment was withdrawn. Fresh17 build/CI/acceptance are pending;
remaining feature/placement/remote implementation and qualification remain open.

Fresh recipe17 managed build/package/native import, artifact/source verification
and both CI runs pass. Final published gateway/performance acceptance remains open.
The token diagnostic can repeat concurrent CPU/Vulkan requests on identical rendered
inputs to locate remaining count differences without modifying usage or gate criteria.


Preparation with include_profile now also requires prima_link_profile_version1 on
selected workers and collects each unique directed manifest edge on the existing
private diagnostic listener. Three integrity-checked 1MiB samples use 64KiB chunks,
signed readiness/sample receipts, exact private source binding and a ten-second
measurement deadline. Reported latency includes application authentication/receipt
work; throughput is acknowledged application payload throughput, not raw link speed
or native inference performance. Windows uses the high-resolution performance
counter rather than a clock that can quantize fast transfers to zero.

Workers retain their pinned master only. Temporary stream scopes delegate exact
source/target public keys through that master's signed channel, binding operation,
manifest, network, authority and assignment revisions. Local preparation, ownership,
expiry, current addresses/routes and pinned identities are rechecked before admission
and sample acknowledgement. Scopes expire after12s, cannot authorize a different
peer or survive diagnostic close, and never become durable peer pairing. Old workers
are refused before asset transfer. Collected links remain preparation evidence;
authoritative candidate scoring, disk-context integration and concurrent resource
reservations remain T017/T066 work. Physical distributed cluster gates and validations
will be tested later; no qualification gate or scope is enabled.


Typed distributed candidate comparison now consumes complete preparation-owned
schema2 costs, fresh signed participant/filesystem context and matching directed
link receipts. It sums absolute assigned CPU/GPU layer indices and rank-zero input/
CPU-output costs, uses completed model-width GPU host handoffs, and counts native
F32 embedding/shape/position and single-token metadata payloads. Application link
latency includes authentication/receipts and is explicitly a proxy for native TCP.
Logical weight bytes divided by the measured sequential filesystem read rate are
reported separately; they are not added to steady-state decode or called startup
TTFT. Decode estimate wins first, logical weight-read estimate breaks ties.

The estimate scope is resident layer execution, one token/slot, f16 K/V and Flash
Attention off, with matching effective context/threads and selected Vulkan identity.
Missing/stale/context-mismatched evidence for any candidate retains capacity-first
planning. GPU-output layouts need an actual logits-width download measurement;
embedding-copy measurements are never extrapolated to that output. Resource
reservations, profiling additional candidate contracts/edges and remaining execution
settings/layouts remain T017/T021/T066 work. Fresh context is read-only; previews do
not run profilers, transfer assets, recreate model layouts or populate caches.
Qualification remains disabled. Physical distributed cluster gates and validations
will be tested later.


Node resource reservations now serialize Prima preparation/profiling/link work and
rank startup with normal llama.cpp/RPC startup. The registry owns one node guard;
scopes bind operation/run, authority and immutable contract. Owned preparation can
run its own profile or link child, while foreign work and overlapping measurements
are refused. The guard reserves the whole node conservatively rather than admitting
competing CPU/GPU/disk work. Distributed preparation and Run require selected workers
to advertise prima_resources_version1 before asset transfer/listener mutation.

Worker asset preparation retains its claim until all verified bytes finish, cancellation
or expiry. Master preparation/profile claims remain held until their worker futures
settle; a cancelled job label is not process-stop proof. Native listener claims remain
until durable owned cleanup succeeds, so every participant is reserved before any
rank spawns. Partial admission drains listeners while preserving unrelated reservations.
Signed startup rechecks the same current approved preparation immediately before spawn.

Link receive grants retain their node claim until their one owned stream completes,
expiry cancels/settles its reader, or diagnostic close drains it. Unused grants have
a real12s timer; old release callbacks cannot release later owners. Source transfer
tasks are also tracked for expiry. Profile cleanup failure retains PID/start identity,
cleanup_pending state and its resource claim; explicit cleanup retry can release it
only after the owned child stops. Generation cleanup no longer drops a retained child.
Prepared profile/master jobs use settling until final worker cleanup completes.

These guards cover in-flight work; existing durable preparation, rank, listener and
coordinator records remain authoritative after agent restart. They do not replace
process identity, native/helper leases, network admission or cleanup proof. No live
firewall/helper deployment or physical cluster was changed. Physical distributed
cluster gates and validations will be tested later; qualification remains disabled.


Recipe25's logits_transfer_profile_v1 adds gpu_logits_transfer to schema2: method
completed_f32_logits_v1, exact bytes and five positive completed download_ms samples
after warmup. The probe initializes/checks the requested backend's F32 buffer at the
actual model vocabulary width. CPU reports null; absent advertised GPU evidence is
refused before cache publication. Median gpu_logits_download_ms and gpu_logits_bytes
remain distinct from embedding uploads/downloads and GPU-to-GPU KV copies.

Candidate scoring verifies logits bytes against the complete GGUF token embedding
tensor vocabulary dimension. Missing/wrong widths retain capacity-first planning.
GPU output uses its own projection estimate and completed logits download. Connected
local graphs avoid an intermediate embedding download for GPU output; distributed
rank zero's separate output graph includes its additional host embedding upload.
These are typed one-token/slot f16/off resident estimates, not generation timing or
qualification. Additional feasible candidate profiling/contracts/edges and remaining
settings/layouts stay open. Physical distributed cluster gates and validations will
be tested later; all qualification remains disabled.


Candidate-edge profiling now advertises prima_link_profile_version2. Explicit
include_profile derives additional directed profile_edges from representable feasible
candidates among the selected preparation's existing participants and private addresses.
These source/target pairs are immutable manifest content; empty profile_edges is omitted
to preserve existing canonical digests. Version2 is required before asset transfer when
extra pairs exist; version1 remains sufficient for execution-edge-only profiling.
The collector measures the union of execution/profile pairs once per directed pair
on the existing diagnostic listener. Profile approval creates no native listener rule
and changes no execution rank, layer, port or transfer edge. User node-priority and
required-node constraints still govern candidate generation. Missing candidate participants,
unsupported layouts/settings, stale evidence or missing links preserve capacity-first
planning. Preview rejects unapproved/duplicate directed receipts and performs no
measurements or cache writes. Physical distributed gates/validations will be tested
later; controlled four-agent transport coverage does not qualify physical inference.


Current recipe26 consumes published b7d101e85af5450e6138c969aacfc7675cc9aa94 with
small-batch Vulkan MoE selected-expert vectors and bounded general-matrix tails.
Fresh managed build/source/hash verification and dense/MoE typed jobs pass. The
published dense gateway passes38checks; the two-slot MoE gateway passes36checks.
Both CI38207372718/38207368689 pass all CPU/Windows-Vulkan jobs. Full Python1580pass/
19skipped63.75s and package/native import checks pass. The five-pair development
MoE benchmark improves concurrency2 throughput ratios to approximately0.749/0.908
(short/long), but every workload still fails local acceptance and long generated
counts remain mismatched. These are development observations; published26 paired
acceptance is pending. No qualification gate is enabled. Physical distributed
cluster gates and validations will be tested later. Broader compatibility/settings/
placement/platform/profile coverage and local correctness/performance remain open.


Published26 final paired acceptance completed: TTFT passes, but short/long
concurrency1/2 throughput ratios0.746/0.741/0.804/0.906 remain below0.95 and
long generated counts differ. All four workloads fail; no qualification is enabled.
T073 delivery is complete, while T032/T039 local acceptance remains required.
Physical distributed cluster gates and validations will be tested later.


Current recipe27 consumes published3e8ee697854e624d4485c552406205f9f9926eeb.
It preserves host expert-add chains while offering a device-memory packed reduction.
Fresh managed source/artifacts, native/GPU contracts and dense gateway checks pass.
Operation timings vary and do not establish a gain; published MoE gateway and strict
paired assessment remain in progress. Retention requires matched model evidence.
Qualification stays disabled. Physical distributed cluster gates and validations
will be tested later. Broader compatibility/profile/settings/placement/platform and
local correctness/performance acceptance remain required.


Published27 gateway verification completes: dense38checks and two-slot MoE36checks
pass. Final strict five-pair assessment is running; variable operation microtimings
do not establish a gain. Qualification remains disabled and broader implementation/
local acceptance work remains open. Physical distributed validation will be tested later.


Published27 final paired acceptance fails all4workloads: throughput ratios
0.723/0.722/0.722/0.829 remain below0.95, with long generated-count differences.
The packed reduction regresses against published26 and cannot be claimed as a
performance improvement. A direct-stride candidate is under development. All
qualification remains disabled; physical distributed validation will be tested later.


Current recipe28 consumes published f13088f3c8bc02f5f72c2e1cb6c09de632bbe202.
It replaces Vulkan packing with a bounded direct-stride serial-order reduction.
Fresh managed source/artifacts, native/GPU contracts, supplied-model typed jobs,
dense gateway and all CI jobs pass. Published MoE gateway and strict paired model
acceptance remain pending; operation samples do not qualify performance. All
qualification remains closed. Physical distributed cluster validations will be tested
later. Broader compatibility/settings/platform/placement/profile and local acceptance
remain required.


Published28 gateway verification completes: dense38checks and two-slot MoE36checks
pass. Final strict five-pair model assessment is running; no speed/qualification
claim follows from operation timings. Broader implementation/local acceptance remains
open. Physical distributed cluster gates and validations will be tested later.


### Recipe29 shared renderer / aggregation rollback - 2026-10-12

Published native704986f951b1747f28a9ad7362ebf543c2e81c45 restores the original
model expert-add chain after published28 final paired throughput ratios
0.722/0.724/0.754/0.841 failed the0.95 threshold. It retains the checked bounded
strided Vulkan primitive without selecting it in the default model graph.
Recipe29 digest e5476d47d77b4f8566788f8de03dc3425239425a3a00c3fa3118cea942057229
pins the exact published source; no hidden patches apply. Fresh managed build and
published CI38226117557/38226110468 are in progress; their acceptance is pending.

Native POST /apply-template shares generation rendering and advertises
apply_template_v1. Development contracts3/3pass14.78s; the final shared renderer
contract also passes. Real selected-Vulkan MoE HTTP rendering checks pass default/
true/history and six malformed/internal request refusals400. Exact per-engine
short/long prompt token IDs match the baseline; long chat still generates71 versus
baseline134tokens. Raw constrained generation remains130/68tokens on both engines.
Receipts: prima-template-render-development-native.log,
prima-template-render-shared-contract.log and
prima-template-render-http-moe-diagnostic.json/log. These are development evidence,
not qualification or proof that model/sampling/grammar differences are resolved.
The kllm-prima canonical/project skill now captures this prompt-token diagnosis.

Published managed source/artifact/model/contracts/gateway and strict paired checks
remain required. Broader API/model/assets/settings/platform/arbitrary placement/
profile and local correctness/performance requirements remain open. All committed
qualification gates stay false. Physical distributed cluster gates and validations
will be tested later, not counted as passes. Root work stays uncommitted.


### Recipe29 published build / gateway verification - 2026-10-12

Fresh artifacts/prima-template-published29 source is clean at published
704986f951b1747f28a9ad7362ebf543c2e81c45. Runtime/source/artifact capture verifies:
runtimeID93311aac76e239264f68035b195a6f53761fb5b74c1b58383d764bb814fb6399;
recipe29 digest e5476d47d77b4f8566788f8de03dc3425239425a3a00c3fa3118cea942057229;
CLI8e96da0e4070ad9b17bf35535adc93eba2b6dc74d1e5ac95923338da180f1a08;
server4daf84dcfd302fe5131afa8624e3072552e37f85cd374328a968a5d1bdaad58c;
profilere470313109b3e83383eba110f812e78259b7dc70f8e636f2a4fcf8a1808c558c.
Native contracts3/3pass14.53s. The selected physical RX9070XT passes54 reduction
and208 indexed cases with0 failure markers. Both real production typed-profile
jobs prepare28/48layers and retain0children. Published normal authenticated gateway
passes dense38/profile/logprobs checks and MoE36/two-slot/logprobs checks, including
owned Run/Stop/restart/crash recovery and relocated binaries under a path with spaces.
The isolated candidate policy permits checks but does not change committed qualification.

Exact-pin CI38226117557/38226110468 both complete successfully with all four
CPU/Windows-Vulkan jobs green. Full Python1580pass/19skipped63.45s. Supported
package rebuild exits0; native_availableTrue, recipe29 and digest import verify.
Skill tests55pass/2skipped4.03s. Canonical/project kllm-prima skill hashes match:
52a8f800467b9d0fd5a2f4df25ca546d7d0688d22d72337d59b8a534be02aee3.

Published model-backed template/token and final strict five-pair performance
receipts remain pending; development diagnostics do not qualify the published pin.
All broader API/model/assets/settings/platform/arbitrary placement/profile and
local correctness/performance work remains open. All qualification gates/scopes
stay disabled. Physical distributed cluster gates and validations will be tested
later, not counted as passes. Root work remains uncommitted; the full goal is active.


### Recipe29 published template diagnostics / paired assessment live

Published29 MoE template and completed generation diagnostic passes native HTTP
rendering default/true/history and six malformed/internal refusals400. Short and
long per-engine prompt token IDs match the baseline exactly; raw outputs still
use130/68tokens in both engines, while long chat uses71versus134. This is observed
arithmetic/sampling/grammar divergence, not a rendered-input difference or a fix.
Published dense eight-token prefix diagnostic independently passes the same HTTP
contracts and exact input matching. Its limited prefix is not complete generated-
answer correctness or performance acceptance. Receipts:
prima-template-published29-moe-token.json/log and
prima-template-published29-dense-template.json/log, both terminal0.

The final published29 matched five-pair benchmark is live,18816, writing
prima-template-published29-benchmark.json/log. Other local build/test/profile/gateway/
diagnostic handles are terminal. Keep this same benchmark handle; no final pass is
inferred from its running state, API checks or previous operation samples. T076
final paired recording and T032/T039 acceptance remain open. Qualification remains
closed. Physical distributed cluster gates and validations will be tested later.
Broader API/model/assets/settings/platform/arbitrary placement/profile/local
correctness/performance requirements and the full integration goal remain active.
Root work remains uncommitted.


### Recipe29 final paired result and fork README - 2026-10-12

Exact published29 five-pair matched benchmark completes,18816terminal1. Short1/2
and long1/2 throughput ratios0.729/0.697/0.727/0.792 remain below0.95; all four
workloads fail. TTFT ratios0.243/0.400/0.714/0.743 pass. Output bytes match in every
sample; long generated-token counts still differ and all four workloads record0OOM.
Receipt: prima-template-published29-benchmark.json/log, with separate placement proof
and timing telemetry disabled. Restoring original expert aggregation does not close
T032/T039; fresh API/native/source/build/jobs/gateway checks cannot replace that gate.
T076 delivery and final result recording are complete; full integration is incomplete.

Fork PR1 README is fully rewritten and published as documentation-only commit
5172e392bc49e0843b4da621848bda49fe31ec22. It identifies the fork immediately,
links original OpenCPIL/prima.cpp main, summarizes five major capability areas,
provides the verified CI build commands and retains attribution/license/citation.
Local Markdown links and control characters are checked, and the remote PR head
matches the published commit. This documentation-only change does not change the
native runtime contract; recipe29 remains pinned to the tested704986f source.

Broader API/model/assets/settings/platform/arbitrary placement/profile and local
correctness/performance requirements remain open. All qualification gates and
execution scopes remain disabled. Physical distributed cluster gates and
validations will be tested later, not counted as passes. Root work remains
uncommitted and the full integration goal remains active. All local handles from
this delivery are terminal. A clean baseline source inspection checkout at
7fe450e19305b828c199d602c23a8337aaa1f03b is available under
artifacts/prima-baseline-inspection/source for further model/kernel comparison.


### Recipe30 MSVC native CPU detection - 2026-10-12

Published acd7987703749871dfcc4047c69fda89e1571e19 repairs MSVC native ISA
selection. The previous serving binary reported AVX512_VBMI/VNNI/BF16=0 despite
this host supporting them. The new detector requires CPU feature bits, XSAVE/
OSXSAVE and exact XCR0 state, plus completed compiler-intrinsic probes. Each
extension is independent; native cross-compilation without a runnable target
refuses and GGML_NATIVE=OFF retains explicit flags. Reconfiguration refreshes
observations and detector-header changes trigger CMake.

Development native contracts3/3pass17.11s; final CPU/OS prerequisite vectors pass
with observed feature mask127. An actual Windows portable CPU build, native ISA
and AVX/FMA/F16C/AVX512/llamafile disabled, passes3/3contracts17.66s; final vectors
also pass and AVX512 extension definitions are absent from its generated project.
The real development two-slot MoE gateway/logprobs passes36checks and its serving
system_info reports all three extensions1. These are correctness/build observations,
not model throughput acceptance. Receipts: prima-msvc-native-development-contract.log,
prima-msvc-native-xsave-vectors.log, prima-msvc-portable-contract.log,
prima-msvc-portable-final-vectors.log and prima-msvc-native-development-moe-gateway.json/log.

Recipe30 digest f314719196e0127a46ed0d40f72cc3f41087674047d0e345f8cf8c0b9c07865e
pins the exact published source. Fresh managed30 build, full Python and exact-pin
CI38230770799/38230765736 are in progress; their final results remain pending.
Published native/GPU/source/artifact/jobs/gateway and strict paired model acceptance
remain required. Broader API/model/assets/settings/platform/arbitrary placement/
profile/local correctness/performance work remains open. All qualification remains
closed. Physical distributed cluster gates and validations will be tested later.
Root work is uncommitted and the full integration goal remains active.


### Recipe30 published verification / paired assessment live - 2026-10-12

Fresh artifacts/prima-msvc-native-published30 source is clean at published
acd7987703749871dfcc4047c69fda89e1571e19; all seven fresh MSVC CPU/OS/compiler
probes succeed. Source/artifact capture verifies runtimeID
8911728440dd298c422212dd81ae2c9e2f9e6f82ae753097bc23434901a27978,
recipe30 digest f314719196e0127a46ed0d40f72cc3f41087674047d0e345f8cf8c0b9c07865e;
CLIf0ebb7497a7c23908efe5a20aa42215b6716e63ea20a9886a073338f5bfddba8;
server027f23f349dcf3dad077b758d504be57fef517c0c1bcec80298d5ed4a54731e0;
profiler767d49a900d97725786dcb58452bb030efadffc40ff13271bfc847b669412dac.
Published native contracts3/3pass15.11s. Selected RX9070XT passes54 reduction
and208 indexed cases with0 failure markers. Production supplied-model typed jobs
prepare28/48layers and retain0children. Normal published authenticated gateway
passes dense38/profile/logprobs and MoE36/two-slot/logprobs checks. Published
serving uses the host-supported AVX512 VBMI/VNNI/BF16 extensions; checks retain
isolated candidate policy and do not change committed qualification or user state.

Both exact-pin CI38230770799/38230765736 finish successfully, all four CPU/
Windows-Vulkan jobs green. Full Python1580pass/19skipped72.06s. Supported package
rebuild/native import passes: native_availableTrue, recipe30 and digest verified.
Skills55pass/2skipped5.02s; canonical/project kllm-prima hashes match
8a37ec196b003eaf97913b7b57a92f4d9d50095cb8fb092b702e9db8d42d02d8.
An owned CPU-only baseline model startup independently confirms the loaded
module ggml-cpu-zen4.dll and all three extensions1; it settles normally. Receipt:
prima-msvc-baseline-cpu-context.json/log. This compares effective serving builds,
not cached CMake option labels, and is not performance acceptance.

The exact published30 five-pair benchmark is live,41897; final results remain
pending in prima-msvc-native-published30-benchmark.json/log. Other local delivery
handles are terminal. Do not restart the running benchmark on observation timeout.
T077 final recording and T032/T039 local acceptance remain open. Broader API/model/
assets/settings/platform/arbitrary placement/profile work remains open. All
qualification gates/scopes stay disabled. Physical distributed cluster gates and
validations will be tested later, not counted as passes. Root work remains
uncommitted and the full integration goal remains active.


### Recipe30 final paired result - 2026-10-12

Exact published30 five-pair matched benchmark finishes,41897terminal1. Short1/2
and long1/2 throughput ratios0.727/0.725/0.755/0.884 remain below0.95; all four
workloads fail. TTFT ratios0.256/0.374/0.731/0.760 pass. All output byte hashes agree,
long generated-token counts still differ, all four workloads record0OOM, and
placement was independently checked with timing telemetry disabled. Receipt:
prima-msvc-native-published30-benchmark.json/log and its placement receipt.

The MSVC native CPU detection repair is verified and delivered: observed serving
extensions match host support; native/portable vectors and contracts, published
CPU/GPU/source/artifacts/jobs/gateways, CI, full Python/package and skill checks
pass as recorded above. These checks do not establish model-level speed acceptance.
T077 delivery/final result recording is complete; T032/T039 and the broader API/
model/assets/settings/platform/arbitrary placement/profile/local acceptance work
remain open. The matched results still require further execution/sampling/grammar
and throughput investigation; no threshold, workload or usage count is changed.

Native fork and managed source remain clean at published
acd7987703749871dfcc4047c69fda89e1571e19. Root work remains uncommitted.
All local delivery handles are terminal. All qualification gates and execution
scopes remain disabled. Physical distributed cluster gates and validations will
be tested later, not counted as passes. The full integration goal remains active.


### Recipe31 CPU graph synchronization / cancellation - 2026-10-12

Published native6ecb08ab3062dbde12720b56f1f014f51df79d66 skips CPU metadata/
empty-node barriers, retains real-operation and final worker settlement, and uses
an atomic first-unexecuted node boundary instead of a shared abort boolean. Prior
cancellation returns before output mutation or worker kickoff.

The original multi-worker regression process is confirmed live after125.6s with
only0.875CPU seconds, then stopped using its exact executable/argument/PID/start
identity. Receipt: prima-cpu-graph-before-timeout.json (owned cleanup, not a pass).
The separate original single-worker check exits1 and records pre_cancel_clean0
(prima-cpu-graph-before-single.log). Fixed native checks pass1/2/8workers with64
normal computations,32prior/after-real-work cancellation-and-reuse cycles, metadata-
only and empty graphs. Final OpenMP and actual portable pthread receipts both pass
(prima-cpu-graph-final-contract.log, prima-cpu-graph-pthread-final.log).
Full development native contracts3/3pass14.46s and real two-slot MoE gateway/
logprobs36checks pass. Operation samples do not establish full-model performance.
Native profiler contract execution is bounded to120s in CTest; standalone regressions
use60s owned child deadlines.

Recipe31 digest05bb8ec5a211b6d082f966c2fe54d972d145b31ef5cd930f3da91bf8e310b193
consumes the exact published source. Fresh managed31 build, full Python and exact-pin
CI38236278785/38236272017 are running. Published source/artifact/native/GPU/model/
profiles/gateway and strict paired performance results remain pending. T032/T039
and broader API/model/assets/settings/platform/arbitrary placement/profile/local
acceptance work remain open. Qualification remains closed. Physical distributed
cluster gates and validations will be tested later. Root work stays uncommitted and
the full integration goal remains active.


### Recipe31 published verification / paired assessment live - 2026-10-12

Fresh artifacts/prima-cpu-graph-published31 source is clean at published
6ecb08ab3062dbde12720b56f1f014f51df79d66. Source/artifact capture verifies:
runtimeID4d862c13bda010e452d5792ca6791a42e566b5e3fbcb825f8cef6623287b6569;
recipe31 digest05bb8ec5a211b6d082f966c2fe54d972d145b31ef5cd930f3da91bf8e310b193;
CLIfd008f5aaf64c42b023dd5c98e57b29fcd60a784eda4e579e6528c2e30d6050f;
servere8e5f7040272ff97fda11392297b09ff72f40db7a71e0880eb50d6fbf1a1f0b5;
profilerd778d830f59d4a395d3db5164a802daaf22126960b2a9cedff066cd27300f49e.
Published native contracts3/3pass14.71s, including repeated CPU graph/cancellation/
alias/empty checks. Selected RX9070XT54reduction/208indexed cases pass with0failures.
Both supplied-model production typed jobs prepare28/48layers and retain0children.
Normal published authenticated gateway passes dense38/profile/logprobs and MoE36/
two-slot/logprobs checks, including Run/Stop/restart/crash/disconnect cancellation.
The isolated candidate policy does not change committed qualification or user state.

Exact-pin CI38236278785/38236272017 both finish successfully, all four CPU/
Windows-Vulkan jobs green. Full Python1580pass/19skipped69.72s. Supported package
rebuild exits0 and native_availableTrue/recipe31/digest import verifies. Skills
55pass/2skipped5.64s; canonical/project kllm-prima hashes match
ed7b7543d5dad329658547894265f5138c29a0f0b64a44fa23c8e86e19ab9485.
The separate real portable pthread graph cases also pass as previously recorded.

The exact published31 five-pair benchmark is live,45536; final acceptance remains
pending in prima-cpu-graph-published31-benchmark.json/log. Other local delivery
handles are terminal. Preserve its running handle rather than restarting on an
observation timeout. T078 final recording and T032/T039 local acceptance remain
open. All broader API/model/assets/settings/platform/arbitrary placement/profile
and local correctness/performance work remains open. Qualification gates/scopes
stay disabled. Physical distributed cluster gates and validations will be tested
later, not counted as passes. Root work remains uncommitted and the full integration
goal remains active.


### Recipe31 final paired result - 2026-10-12

Exact published31 matched five-pair benchmark finishes,45536terminal1. Short1/2
and long1/2 throughput ratios0.726/0.729/0.735/0.816 remain below0.95; all four
workloads fail. TTFT ratios0.247/0.659/0.724/0.724 pass. All output byte hashes agree,
long generated-token counts still differ and all four workloads record0OOM.
Separate placement checks are retained and execution telemetry is disabled during
timing. Receipts: prima-cpu-graph-published31-benchmark.json/log and placement file.
No accepted model-level gain follows from the metadata-barrier removal.

The CPU cancellation repair is independently verified: the original owned test
hang and prior-cancel output mutation are retained; fixed OpenMP/pthread cases
pass1/2/8workers,64normal computations,32cancellation/reuse cycles, aliases and
empty graphs. Published source/artifacts/native/GPU/jobs/gateways/CI/full Python/
package/skills pass as recorded above. T078 delivery/final result recording is
complete; T032/T039 and broader API/model/assets/settings/platform/arbitrary
placement/profile/local correctness/performance requirements remain open. No
threshold, workload, actual usage count or qualification policy is changed.

Native fork and managed source remain clean at published
6ecb08ab3062dbde12720b56f1f014f51df79d66. Root work remains uncommitted. All local
delivery handles are terminal. All qualification gates/scopes remain disabled.
Physical distributed cluster gates and validations will be tested later, not
counted as passes. The full integration goal remains active.


### Performance validation deferred by user - 2026-10-12

The user explicitly requests no further performance-gate validation here and will
check performance on a live Kluster cluster of three computers. Local paired
acceptance and further performance-gate diagnostic experiments are stopped.
Implementation and correctness checks, including functional profiler contracts,
continue. Existing benchmark failures remain historical evidence, not passes.
Preserve the no more than 5% regression and 10% representative distributed
improvement criteria until the user's live measurements can evaluate them. Do not
relax thresholds, retokenize usage, infer a pass or enable an unverified release scope.

Physical distributed cluster gates and validations will be tested later on the
user's three-computer setup. Performance and distributed qualification remain
unpassed. Their deferral is explicit and is not an implementation blocker for
finishing remaining API/model/assets/settings/placement/planner work.
The bounded timing diagnostic already performed is not a performance gate result:
its summary is corrected to insufficient_decode_samples because mixed token-count
prefill graphs include a final single-token projection. No new timing experiment
will extend that sample collection here. The optional diagnostic-mode correctness
repair and its benchmark-rejection checks can be finished independently.

### Prima template continuation - 2026-10-12

Published feature commit cfca7ee (recipe33) supports full Jinja embedded/inline/file templates and
advertises jinja_template_v1. Kluster resolves custom/preset choices before
preparation and freezes exact source bytes as prima_chat_template_source in the
signed manifest and qualification execution settings. Rank zero renders;
workers receive its tokens. Kluster now pins and builds this immutable source. Large templates use owned
content-named files at launch; changed files refuse without overwrite. This does
not enable qualification. Broader input-origin token marking
and automatic native tool parsing remain open. Performance and physical cluster
acceptance will be tested later on the user's live three-computer cluster.

### Named tool streams - 2026-10-12

Published Prima3e41d47 (recipe34) adds streaming named forced calls to Chat,
Responses and Messages, with stable call/item IDs, correct argument delta channels,
usage, native token logprobs and explicit partial termination. The native server
advertises streaming_named_tools_v1. This requires a new immutable managed build
and separate qualification feature evidence. Automatic tool choice/parallel parsing
and complete tool-history support remain open. Performance and physical cluster
acceptance will be tested later on the user's live three-computer cluster.

### Text tool history - 2026-10-12

Published Prima3707d0a (recipe35) adds complete supplied text tool history for
Chat assistant/tool messages, Responses function_call/function_call_output items
and Messages tool_use/tool_result blocks. Call IDs and historical call groups are
preserved. Jinja template capabilities govern argument representation; unsupported
history/content refuses instead of being silently discarded. The server advertises
tool_history_text_v1. Kluster requires tool_turns evidence for each protocol and
retains separate nested vision requirements and native invalid-input boundaries.
Automatic tool choice, multimodal history and stored Responses state remain open.
Performance and physical cluster acceptance will be tested later on the user's
live three-computer cluster; all release qualification remains unpassed.

### Automatic-tool parser foundation - 2026-10-12
Published Prima ccc8c594 (recipe36) adds the pinned baseline parser/grammar
foundation and lazy sampler activation. Native contracts cover generated grammar
compatibility, split/Unicode triggers, clone/reset and nullable-cycle refusal.
This is development infrastructure: automatic and parallel HTTP tool generation
remain refused until the native protocol adapters are completed. The clean managed
Vulkan build passes native5/5 and dense49/MoE49 functional gateway checks, with
separate real-vocabulary parser/sampler checks and portable CI success. Performance and physical cluster
acceptance will be tested later on the user's live three-computer cluster; source
qualification flags remain false and execution scopes remain empty.

### Automatic and parallel tools - 2026-10-12 (recipe38)
Published native37828feb156ab14082ce3569b2c8f6aa9d3012c6 serves automatic,
required and named text function calls across Chat/Responses/Messages. It uses
the pinned baseline template analysis, schema grammar and incremental parser.
Parallel calls retain stable call/item IDs, argument channels and terminal usage;
text and reasoning remain separate. Required grammars accept the existing assistant
prefix before generation and after reset, avoiding duplicate headers. User grammars
are never prefilled. Internal parameters refuse on every completion/infill path.
The server advertises automatic_tools_v1 and streaming_automatic_tools_v1.
Kluster additionally checks exact execution evidence for tools:auto, parallel_tools
and streaming_tools and verifies native capability before forwarding requests.
A model's unmarked JSON remains text, matching the baseline; explicit tagged
activation, ordinary decline and schema-bound required calls are checked separately.
Clean managed Vulkan build/source/artifact verification and Python1617/19skip pass.
Development and clean published dense100/MoE100 checks pass, including reasoning,
automatic logprobs, parallel calls, history, truncation, disconnect and reserved
parameters. Published native5/5 and both portable CI runs pass. Broader API/model/launch/placement/platform
work remains open. Performance and physical distributed acceptance will be tested
later on the user's live cluster of three computers, with thresholds unchanged and
all source qualification flags false / execution scopes empty.


### Explicit native device selection (recipe40)

The fork now advertises explicit_devices_v1 and accepts ordered --device
selection, including none for CPU-only ranks. Kluster maps UUID selectors to
physical visibility and remapped native device names, verifies the binary
capability/flag, and refuses unsupported multiple-device launches before startup.
Vulkan registry and selected-adapter pinned buffers preserve the chosen adapters;
layer proportions are checked against completed prefill/decode matrix ownership
and physical UUID/driver/name mapping. Native driver-unavailable handling retains
an empty GPU inventory, permits CPU-only operation and refuses explicit GPU
requests. Hosted CI and an actual missing-driver reproduction cover this path.

These changes support local layer placement correctness, not complete row,
tensor, remote-expert, model/API or platform qualification. Source qualification
flags remain false and execution scopes empty. Performance acceptance and
physical distributed validation will be tested later by the user on a live
three-computer cluster. Do not run local performance-gate diagnostics or claim
those gates passed from native contracts, device enumeration or local inference.


### Implementation-first validation (2026-10-12)

The user has deferred all unit test runs and test-fix work until the required
implementation is finished. Do not start pytest, ctest or model assessments
during this implementation phase. Production compilation may check source
changes without granting acceptance. Once implementation is complete, run the
relevant tests and fix failures before claiming completion of the integration.
Performance and physical distributed acceptance remain deferred to the user
on a live three-computer cluster. Qualification remains disabled.


### Rank-wide prompt cache implementation (recipe42)

Published Prima source: ab96f06556c847c0d1176a7c0c4e77f7584e3451.
Recipe42 digest: fab69be5f4a3d8abf1b99889593524a6d49f9126c8ef4652ac07ddf001eb1864.
The fork implements cache-ram with bounded rank-local serialized KV storage,
one eviction order and acknowledged save/restore/remove transactions. Partial
saves are removed everywhere; failed restores invalidate destination sequences
before recomputation. Missing acknowledgements stop uncertain contexts. Partial
rank serialization uses actual owned absolute layer indices and handles empty
heads; full-model state retains its existing format. System/adapter changes clear
snapshots. Kluster negotiates prompt_cache_v1, binds the budget to the manifest,
and reserves its full ceiling in host RAM on every selected participant,
including GPU workers. Unsupported measured cost contexts remain capacity-first.

Development production server/CLI/profiler compilation passed after correcting
a missing telemetry helper name. The clean published recipe42 build is in progress
(artifacts/prima-cache-published42-build.log); root production package build passed
(artifacts/prima-cache-pin42-package.log). No new unit, ctest or model assessment
runs were started. The published native commit skips CI because the user requires
all tests and test-fix work after implementation completion; skipped CI is unpassed.

Deferred test-phase cases include dense/MoE cache reuse/eviction/branching,
independent slots, cancellation, budget and malformed inputs, partial-layer state
round trips, zero-work-head worker restoration and lost-acknowledgement cleanup.
Checkpoints/SWA, remaining model/API/launch parity and arbitrary segmented, row,
tensor and expert placement remain implementation work. Cache and draft features
do not close the aggregate integration tasks. All qualification flags remain
false and execution scopes empty. Physical distributed and performance acceptance
will be tested later on the user's live three-computer cluster.


### Context checkpoint implementation (recipe43)

Published source: a5ef31839770df04b6e51628bf015336d1ce468d.
Recipe43 digest: b57f8d548c342f5c37f57977116fb0018a9862a19f925d495cb93e90ea37515b.
Context checkpoints have independent per-slot histories and a separate bounded
snapshot pool from cache-ram. Capture follows successful decode and uses completed
sequence positions rather than optimistic n_past across undecoded sub-batches.
Recurrent restore requires an entire matching prefix and a remaining token for
logits reconstruction. Counts, spacing and the planned byte ceiling are frozen
in the native manifest; context_checkpoints_v1 is required before preparation.
Kluster prices full target KV/state plus metadata and reserves the ceiling on
every selected participant, without double-charging the master. Prima's full
checkpoint state is larger than baseline partial recurrent/SWA state and is
priced accordingly. System/adapter changes and slot erase clear owned histories.
Draft contexts do not inherit target cache/checkpoint settings.

Development production server/CLI/profiler compilation passed. Root production
package build passed (artifacts/prima-checkpoint-pin43-package.log). The clean
published recipe43 build is in progress at
artifacts/prima-checkpoint-published43-build.log. Recipe42's clean build has now
completed; its source/artifact hashes match the published pin, as recorded in
artifacts/prima-cache-published42-manifest-check.json. These are compilation and
artifact identity receipts, not test acceptance. Native publication again skips
CI tests to honor the user's implementation-first schedule. No pytest, ctest,
model assessment, paired timing or test-fix work was started.

After required implementation finishes, validate checkpoint count/spacing/budget
refusal, independent cache/checkpoint limits, completed micro-batch prefix capture,
recurrent rollback and sampling, slot reuse/erase, system/adapter invalidation,
draft isolation, partial/zero-head ranks, failed restores and lost acknowledgement
cleanup. Remaining API/model/launch and arbitrary segmented/row/tensor/expert
placement work is still open; neither this feature nor cache/draft implementation
completes the aggregate integration. Qualification remains disabled, with all
flags false and execution scopes empty. Physical cluster and performance gates
will be tested later on the user's live three-computer cluster.


### Deployment template keyword defaults (recipe44)

Published source: 400a4ffb041dc5a36c9b344f9f7e5ad84cfa8267.
Recipe44 digest: 02938108e2f073d8eeb4b3fa631fadf6ac696641ff7bb185d0e6a4d7d043fcf8.
Native --chat-template-kwargs and its owned-file variant apply a bounded JSON
object to the shared chat/Responses/Messages/template-preview/input-token/
automatic-tool renderer. Request values override matching deployment defaults.
Root launch accepts this declared extra, validates finite JSON and preserves
dictionary insertion order. Rank zero receives a content-addressed JSON file
inside its owned run directory; workers receive rendered tokens. Existing file
bytes and symlinks are checked. Preparation requires template_kwargs_defaults_v1;
native startup compares parsed defaults with the approved execution manifest.
Named legacy aliases refuse nonempty defaults before readiness because they
cannot consume arbitrary template variables. Other launch extras remain open.

Development production server/CLI/profiler compilation and root package build
passed (artifacts/prima-template-defaults-development-build2.log and
artifacts/prima-template-defaults-pin44-package.log). The clean recipe44 build
is in progress at artifacts/prima-template-defaults-published44-build.log. The
clean recipe43 build completed and its source/artifact hashes match the published
pin (artifacts/prima-checkpoint-published43-manifest-check.json). These receipts
establish compilation/artifact identity only. Native publication skips CI tests
to honor implementation-first validation. No new unit, native contract, model
assessment or performance runs were started.

After implementation completion, validate deployment/request precedence on all
text protocols, automatic tool preparation, reasoning controls, template/input-
token agreement, object order, malformed/oversized input, manifest mismatch and
owned-file drift. Broader API/model/launch and arbitrary placement implementation
still remains; this does not close aggregate integration or qualification tasks.
All qualification flags remain false and execution scopes empty. Physical
distributed and performance gates will be tested later on the user's live
three-computer cluster.


### Native initialization and sampler failure handling (recipe45)

Published source: 9cc8b1e1f717b58a68359f6aa5e24b27490cd3ee.
Recipe45 digest: 3e1679508f636bf565d08b053b1af2a99a5663e8a796a6f8155ba932c0fd6cf5.
Model/context/backend/grammar entry points contain exceptions within the exported
C boundary and return failure; managed sockets use checked startup with partial
handle cleanup. Backend setup borrows its context so its caller frees failure
once, fixing double ownership. Context destruction closes distinct owned socket
aliases with zero linger. Invalid grammars cannot become unconstrained samplers.
Checked accept/apply/reset and clone paths expose callback failure without C
unwinding; nested chains retain failure at that boundary. Grammar reset/clone
retain allocation ownership and cloned candidate pointers refer to copied storage.
Managed serving fails the owning request and clears its head/worker sequence on
sampler errors during replay, generation or draft verification. Other request
sequences and valid snapshots remain owned. Fully masked or non-finite selected
tokens refuse instead of becoming valid output. Legacy void sampler calls remain
fail-closed; managed C++ callers use the checked interface.

Development production server/CLI/profiler compilation passed, including the
final artifacts/prima-errors-development-build3.log. Earlier relevant C4297
initialization warnings no longer appear in the affected development build.
Root package build passed (artifacts/prima-errors-pin45-package.log). The clean
recipe45 build is in progress at artifacts/prima-errors-published45-build.log.
Recipe44's clean build completed with matching source/artifact hashes, recorded
in artifacts/prima-template-defaults-published44-manifest-check.json. These
receipts establish compilation and artifact identity, not runtime acceptance.
Native publication skips CI tests. No unit/native contract/model assessment
or performance runs were started; test-fix work stays deferred until required
implementation completion.

Deferred validation includes invalid regular/lazy grammars, trigger storage/token
refusal, callback/chain errors, reset/clone ownership, empty/fully masked candidate
sets, malformed startup/device/backend/socket input, occupied private ports,
partial allocation cleanup and retry, request isolation and draft failures.
Broader model/API/launch and arbitrary segmented/row/tensor/expert placement
implementation remains open. All qualification flags remain false and execution
scopes empty. Physical distributed and performance gates will be tested later
on the user's live three-computer cluster.


### Absolute ownership foundation (recipe46)

Published source: cfdaff424baf9977c744c2ec1735e737e489da43.
Recipe46 digest: 38611f5b6f4863f518d291fa51593f705ffb599e57e8a3f59b5db45623b67b1c.
Native manifests supply an immutable absolute layer-owner table. Models copy
and validate the table against world/rank and actual GGUF layer count; contexts
borrow that model-owned table and reject drift. Tensor loading, KV indexing,
attention storage, graph callbacks and sequence snapshots use precomputed local
indices. Explicit GPU offload uses rank-local absolute ordering. Draft contexts
clear target ownership. The table also computes contiguous-visit graph indices
for the remaining segmented executor.

This is a prerequisite, not arbitrary-placement execution. Existing manifest
and mapped-model/context startup still require one worker-first window cycle;
segmented_placement is not advertised. Actual ordered segment handoff, repeated
rank visits, row/tensor/expert placement and architecture graph support remain
implementation work. T014 stays open. Do not replace the required executor with
a rounded window plan or describe map representation as completed execution.

Development production server/CLI/profiler compilation passed, including
artifacts/prima-layer-map-development-build2.log. Root package build passed
(artifacts/prima-layer-map-pin46-package.log). The clean published recipe46 build
is running at artifacts/prima-layer-map-published46-build.log. Recipe45's clean
build completed with matching source/artifact hashes, recorded in
artifacts/prima-errors-published45-manifest-check.json. These receipts establish
compilation and artifact identity only. Native publication skips CI tests to
honor implementation-first validation. No unit/native/model/performance runs
or test-fix work were started.

After full implementation completion, validate owner/count/window rejection,
model/context lifetime and equality, zero-head and partial GPU indices, tensor/
KV/snapshot ownership, actual approved graph visits and rank handoffs. Broader
integration remains open. All qualification flags are false and execution
scopes empty. Physical distributed and performance gates will be tested later
on the user's live three-computer cluster.


### Recipe47: segmented execution implementation in progress

Source: a314e91b0e13893c47bcf11e90b21d9700681bd0 on the fork PR #1 branch.
Recipe digest: 2908ff6a96b2e4edd5bb011561d8b91c2450d00edd5e4e15844db400a07a0185.
The native executor now has ordered absolute segment visits, activation forwarding
through the approved physical ring, bounded identity/shape checks and a complete
ring acknowledgement before the next micro-batch or KV operation. Native manifest
admission checks segment coverage against rank ownership. Segmented schedulers
reserve each local graph visit. The legacy canonical window executor is retained.

This remains incomplete managed integration. segmented_placement is NOT advertised;
Kluster still rejects these layouts at execution admission. Finish per-visit graph
memory accounting, rank launch translation and capability negotiation before enabling
it. Repeated-visit/relay layouts retain capacity-first, unmeasured planning because
the existing one-handoff-per-rank cost model does not cover them. Row/tensor/expert
placement and broader architecture/API support remain open; T091 is not complete.

Production server/CLI/profiler compilation passed in
artifacts/prima-segments-development-build3.log, and the root package build passed
in artifacts/prima-segments-pin47-package.log. The clean published recipe47 build
was started in artifacts/prima-segments-published47-build.log. Recipe46 clean source
and binary identities were verified in
artifacts/prima-layer-map-published46-manifest-check.json. These are compilation
and artifact-identity receipts, not runtime acceptance.

All unit/native/model tests and test-fix work remain deferred until the entire
required implementation is finished. Publication skips CI tests; skipped checks
must run in that later phase. Physical distributed/platform/mixed-backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification flags remain false and execution scopes empty. Thresholds remain
at most 5% regression and at least 10% representative distributed improvement.


### Segmented graph memory integration progress

The Prima allocator now derives actual contiguous rank visits from absolute
transformer ownership and reserves independent head input/output and local visit
schedulers. Extra host/device scratch and runtime estimates are added to each
physical pool without changing assigned layers. Device limits, node RAM/VRAM
limits and unified-memory limits are checked; excess retained graph reserves
make the plan overflow. This remains a conservative capacity estimate, not a
backend allocator measurement. Local single-rank placement retains its existing
shared graph estimate. Row/tensor/expert placements remain separate open work.

The internal prima_graphs flag is carried through immutable resolved/placement
settings and frozen execution qualification scope. Future segmented-capable
admission requires this planned reserve flag and validates absolute ownership.
The pinned native recipe47 still does not advertise segmented_placement. T091
remains open for capability/launch completion and the later execution checks.

Clean published recipe47 source/artifact identity is verified in
artifacts/prima-segments-published47-manifest-check.json. Production packaging
passed in artifacts/prima-segments-graph-reserve-package.log; edited Python
modules passed syntax compilation only. No unit/native/model tests or test-fix
work ran. Those start after all required implementation is complete. Physical
distributed and performance acceptance will be tested later on the user's live
three-computer cluster; all qualification flags remain false and scopes empty.


### Recipe48: segmented placement capability and graph admission

Source: 5c19eedfbfba5c1cdb7d34477670d73436c8c0d7 on the fork PR #1 branch.
Recipe digest: 1c5c41c88a99d44d1d9291c572b4a316b606fb4e6a4ec47ec02cca17ecd23d28.
The native runtime now advertises segmented_placement for its implemented layer
graph families (Llama, Qwen2, Qwen3 dense/MoE). This supersedes the recipe47 note
that the capability is absent; it does not enable inference qualification.

Planner graph visit counts now travel from typed placement results into signed
execution settings and the immutable qualification scope. Kluster and native
manifest admission independently derive visits from absolute ownership and reject
missing, altered or mismatched counts for noncanonical layouts. Rank launch
translation and candidate profile edge discovery support explicitly negotiated
segmentation, retaining legacy window validation for older runtimes. Existing
one-handoff-per-rank typed timing costs still keep segmented candidates capacity-
first and unmeasured. No native listener edge or authority scope was widened.

Production server/CLI/profiler compilation passed in
artifacts/prima-segments-admission-development-build.log, root package build
passed in artifacts/prima-segments-pin48-package.log, and edited Python modules
passed syntax compilation. Clean published recipe48 production compilation is
running in artifacts/prima-segments-published48-build.log. Recipe47's completed
clean build has matching source/artifact hashes recorded in
artifacts/prima-segments-published47-manifest-check.json.

T091 remains open for execution acceptance after full implementation completion;
compile/capability reports do not prove actual rank visits or correctness. Row/
tensor/expert placement, remaining model/API compatibility and platform work stay
open under the original requirements. No unit/native/model tests or test-fix work
ran; skipped publication CI must run later with that phase. Physical distributed,
mixed-platform/backend and performance acceptance will be tested later on the
user's live three-computer cluster. Qualification flags are false and execution
scopes empty; all original thresholds remain unchanged.


### Segmented planner cost implementation

The typed distributed planner now prices each GPU-containing graph visit and
routes one-token activation payloads through the actual approved physical ring,
including repeated visits, relays and the complete ring acknowledgement. Batch
metadata retains the published worker chain. Segment marker/JSON payload bounds
use the maximum wave-counter width; transport framing, buffering and relay CPU
costs remain outside this explicitly labelled application-link proxy estimate.
Cold logical weight reads remain separate from resident decode predictions.

Scoring requires the published segmented capability, matching planned graph visit
counts and all existing fresh signed source/model/settings/hardware/storage/link
receipts. Missing evidence retains capacity-first planning. This supersedes the
recipe48 note that segmented timing costs are entirely unavailable, but grants
no native-generation or performance acceptance. Canonical window layouts retain
their legacy wire estimate. Per-candidate wire_cost_model identifies which path
was priced. Production package build passed in
artifacts/prima-segments-costs-package.log; edited modules passed syntax compilation.

Recipe48 clean production build completed with matching source/artifact hashes
and the native segmented capability in
artifacts/prima-segments-published48-manifest-check.json. Tests and test-fix work
remain deferred until full required implementation completion. Physical distributed,
mixed-platform/backend and performance gates will be tested later on the user's
live three-computer cluster. Qualification flags are false and scopes empty.


### Recipe49: coordinator GPU output without transformer offload

Source: 54470aec16cf83c4def0031cecd8643d712b994b on the fork PR #1 branch.
Recipe digest: adb9bb2c0295769825211e143c4f9eb7b3803896efd0a72af64d2a26fc99b18f.
A rank-zero coordinator may now declare output_gpu=true with ngl=0 and one
selected device. ngl continues to count transformer layers only; output placement
is independent. Native manifest/device checks and CUDA scheduler reservation keep
that output graph on the selected GPU. Kluster preserves the output-only device,
Vulkan visibility, explicit launch selection, signed participant inventory and
backend readiness checks. Older runtimes refuse through output_only_gpu_v1.

Completed GPU evidence requires output projection in both prefill and decode;
zero transformer offload must not turn a Python negative-zero slice into all
assigned CPU layers. Typed planning checks the selected physical Vulkan identity
and separately prices the output embedding upload and vocabulary-width logits
download. Transformer GPU visit costs stay zero for this coordinator. The planner
still requires complete current signed evidence; this implementation is no
correctness, distributed or performance acceptance receipt. Remote output owners,
row/tensor/expert placement and wider compatibility remain open.

Production server/CLI/profiler compilation passed in
artifacts/prima-output-only-development-build.log; root package build passed in
artifacts/prima-output-only-pin49-package.log, and edited Python modules passed
syntax compilation. The clean published recipe49 build is running in
artifacts/prima-output-only-published49-build.log. No unit/native/model tests or
test-fix work ran, and publication skipped CI tests. Execute those later after
full implementation completion. Physical distributed/mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification flags stay false and execution scopes empty.


### Recipe50: rank-owned LoRA loading foundation

Source: aeba7fcf824e1121c486f75211401c73de8b28d5 on the fork PR #1 branch.
Recipe digest: 6260f74deeb152399328c0888315b279e55f43847556a425b50d9bafe248d3f9.
The native loader validates adapter metadata and complete tensor pairs on every
managed rank, then allocates only tensors owned by that rank's immutable absolute
layer map. Rank zero retains input/output ownership. The owning rank rejects
unknown target tensors before cluster readiness. Adapter metadata, device tensor
contexts, buffers and model registration now have exception-safe ownership; the
exported initializer contains failures and returns nullptr. Alpha and tensor
shape/rank checks refuse malformed adapters. Local drafts no longer inherit target
LoRA files that may be incompatible with their separate model.

This is native loading foundation, not completed managed LoRA support. Kluster
still rejects adapter extras, and no managed LoRA capability is advertised. Finish
verified adapter asset roles/transfers, immutable ordered file/scale bindings,
rank-aware weights and graph scratch pricing, launch remapping and native scale/
selection synchronization before enabling it. HTTP LoRA changes must reach every
rank and settle before decode; head-only scale changes would produce inconsistent
generation. Clear compatible cache/checkpoint state through the existing lifecycle.
Broader model/API/placement requirements remain open.

Production compilation passed in
artifacts/prima-lora-ownership-development-build2.log and root package build passed
in artifacts/prima-lora-ownership-pin50-package.log. Clean published recipe50 build
is running in artifacts/prima-lora-ownership-published50-build.log. Recipe49 clean
source/artifact identity and output-only GPU capability were verified in
artifacts/prima-output-only-published49-manifest-check.json. No unit/native/model
checks or test-fix work ran; publication skipped CI tests. Those start after full
implementation completion. Physical distributed/mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification flags remain false and execution scopes empty.


### Managed LoRA assets, launch and reservation integration

Kluster now parses ordered --lora/--lora-scaled declarations together with one
optional template keyword object. Scales are normalized to finite native F32.
Adapter GGUF metadata can be read without model block-count fields. Verified
adapter asset roles use the existing signed manifest, complete-byte transfer and
owned layout resolver; model-shard filename rules do not reinterpret adapters.
Repeated declarations retain ordered scales while one identical file is staged once.
Conflicting basenames and incomplete/symlink files refuse preparation.

Frozen prima_lora bindings and conservative storage/per-token scratch bounds are
carried through typed resolved/placement settings and qualification scope. Host
and selected allocation pools reserve adapter storage before model assignment;
scratch grows with effective micro-batch/slot size and retained graph visits.
These full-file/all-pair bounds may exceed actual rank-owned allocation and are
not backend allocator measurements. Launch verifies each staged file's size/SHA
and metadata-derived bounds again, then remaps paths under the owned model layout
for every rank. Original master paths are never worker launch paths.

Managed LoRA remains DISABLED: managed_lora_v1 is not advertised in recipe50.
Finish native ordered binding/admission, rank-wide selection/scale synchronization,
worker adapter-role negotiation, cache/checkpoint invalidation and completed GPU
adapter/output evidence before enabling it. Head-only changes and evidence that
omits LoRA matrices are insufficient. T092 and original compatibility tasks remain
open; these assets/reservations do not prove LoRA execution.

Production imports, syntax compilation and package build passed in
artifacts/prima-lora-assets-package.log. Recipe50 clean source/artifact identity is
verified in artifacts/prima-lora-ownership-published50-manifest-check.json. No
unit/native/model checks or test-fix work ran. Those start after full required
implementation completion. Physical distributed/mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification flags remain false and execution scopes empty.


### Recipe51: acknowledged native LoRA selection synchronization

Source: cf1890af47515c9a76e77f251cffe81bfc5e723a on the fork PR #1 branch.
Recipe digest: 0a9c776ab2374955b6173ee90e162a9a5296221e24d9e0d1934d03779f284b2d.
Native adapters have model-local ordered identities and deterministic application
order. The checked replacement API validates complete finite selections before
mutation. Manifest heads send bounded selection commands over the admitted ring,
bound to the exact manifest, revision and hop order, and wait for a complete
all-rank acknowledgement before committing. Foreign/stale/missing/failed replies
make the head context unusable; failed workers forward failure where possible and
terminate their decode path. Startup applies the same local selection before
readiness; manifest binding checks still need completion before managed enablement.

Replacement settles backend work and clears KV plus both snapshot pools. The HTTP
endpoint validates a full request before enqueueing immutable values, defers until
slots are idle, updates reported scales only after success, clears draft KV and
rebuilds the system prefix. Result waiting is registered before task publication.
Malformed requests and native failures return errors without success claims. The
common startup path checks replacement status and cleans failed initialization.

Production server/CLI/profiler compilation passed in
artifacts/prima-lora-sync-development-build4.log and root package build passed in
artifacts/prima-lora-sync-pin51-package.log. Clean published recipe51 production
build is running in artifacts/prima-lora-sync-published51-build.log. No unit/native/
model tests or test-fix work ran; publication skipped CI tests per the requested
schedule. These checks do not prove synchronization or LoRA correctness at runtime.

Managed LoRA remains disabled; managed_lora_v1 is not advertised. T092 stays open
for native manifest/file/scale admission, worker adapter-role negotiation, completed
GPU adapter/output evidence and later execution checks. The original model/API/
placement/platform requirements remain open. Physical distributed, mixed-platform/
backend and performance gates will be tested later on the user's live three-computer
cluster. Qualification flags remain false and execution scopes empty.


### Recipe52: LoRA admission, worker negotiation and completed-work tracing

Source: 9f7abdc0c8e780833f7d33f15d461f88d5def636 on the fork PR #1 branch.
Recipe digest: 5773afcf397cf5ee952631bffc991c734e48a0497f3f7bf362a87478e5c1b34e.
Native admission compares ordered adapter basenames/scales with the manifest,
verifies approved file sizes/SHA-256 using bounded streaming I/O and refuses
unreferenced assets or changed initial selection. Streaming retains the existing
string digest API; native digest vectors and file checks remain untested pending
the full implementation-complete test phase. File verification uses GGML's UTF-8
path opener. Legacy manifests without adapters retain optional settings/assets.

Workers advertise prima_lora_assets_version=1; preparation requires the exact
capability before adapter transfer. Basename bindings interpret master Windows/
POSIX separators consistently. Frozen adapter targets are rechecked against staged
metadata at launch and included in the qualification scope. Actual completed LoRA
operations are tagged by model-local adapter/target identity, component and role,
with selection/revision and output-weight context. Empty operations are excluded;
new completed observations accumulate per phase/epoch, preventing an early partial
batch from hiding later work. Tracing remains opt-in and is not timing evidence.

The verifier rejects foreign/stale selections, wrong scales/targets/backends and
missing A/B work across both prefill/decode observations. Native compilation passed
in artifacts/prima-lora-binding-development-build4.log. Root packaging passed in
artifacts/prima-lora-binding-pin52-package.log and edited modules passed syntax/
production imports. Clean recipe52 build is running in
artifacts/prima-lora-binding-published52-build.log. Recipe51 clean source/artifact
identity is verified in artifacts/prima-lora-sync-published51-manifest-check.json.

Managed LoRA remains disabled: managed_lora_v1 is not advertised. Finish input
embedding LoRA execution and tied output adapter buffer ownership, then complete
capability integration and the later correctness/protocol/hash checks. T092 and
original broader compatibility/placement/platform requirements remain open. No
unit/native/model tests or test-fix work ran; publication skipped CI tests. Physical
distributed, mixed-platform/backend and performance gates will be tested later on
the user's live three-computer cluster. Qualification flags are false and scopes
empty; compilation and trace implementation grant no execution acceptance.


### Recipe53: embedding and tied-output managed LoRA implementation

Source: f8aa37338cd8b41e599879e63ec53767a8fea8b7 on the fork PR #1 branch.
Recipe digest: 467861e3d85b7a2d8425faef87d95e85bc3cd11b685415c62d8e30f44e1b28c0.
Token embedding adapters use the baseline GGUF orientation (A: rank/vocabulary,
B: rank/embedding) and update gathered token embeddings. Tied output projection
uses an independently allocated pair on the actual output buffer, with decoded
B transposed into F32 and original A retained; original adapter bytes are unchanged.
Input and output operations retain original component identities in execution
tracing. Planner metadata checks now distinguish embedding and linear shapes.

Adapters load and apply before backend graph reservation. Native graph capacity
includes both input and tied-output operations, and effective scales are checked
against finite F32 bounds before selection changes. The blanket LoRA/Flash Attention
rejection was removed to match the baseline selection contract; compatibility still
requires later execution checks. The published native development capability now
includes managed_lora_v1. Qualification remains false and execution scopes empty.

Production server/CLI/profiler compilation completed in
artifacts/prima-lora-embedding-development-build3.log, and root packaging passed in
artifacts/prima-lora-embedding-pin53-package.log. Clean published recipe53 build is
running in artifacts/prima-lora-embedding-published53-build.log. Clean recipe52
source/artifact identity is recorded in
artifacts/prima-lora-binding-published52-manifest-check.json.

T092 implementation wiring now includes assets, admission, pricing, selection
settlement, embedding/tied output and operation evidence. Its acceptance stays open.
The broader original model/API/placement/platform requirements remain open. No
unit/native/model tests or test-fix work ran; publication uses [skip ci]. All tests
and test fixes start only after the full required implementation is finished.
Physical distributed, mixed-platform/backend and performance acceptance will be
tested later on the user's live three-computer cluster; no gate is marked passed.


Recipe53 published-build follow-up: the clean Windows/Vulkan production build
completed successfully. Source revision/cleanliness, staged executable SHA-256
values and the managed_lora_v1 development capability are verified in
artifacts/prima-lora-embedding-published53-manifest-check.json. This is build and
identity evidence only. Tests/test fixes remain deferred until full implementation
completion; physical cluster/performance gates remain deferred and unpassed.


### Recipe54: per-request LoRA selection and concurrent batch ownership

Source: 9808a107d65432fd021e0896d074778b850c72e5 on fork PR #1.
Recipe digest: d574f28ac3593e503b244acd5aeb61931aa6f2d672aca1ef5d2009935f2c080f.
Native generation slots now retain independent ordered adapter scales. An empty
request array uses server defaults, matching the baseline. Nonempty selections
zero unspecified adapters and reject invalid IDs/nonfinite scales. Chat, Responses
and Messages preserve the selection through request translation and generation
settings report effective per-slot values. Server-wide defaults change only after
successful rank settlement.

Round-robin batch selection groups compatible adapter configurations. A separate
native batch-selection API preserves other sequences' KV and snapshots; protocol2
binds that mode to the same manifest, monotonic revision and full-ring acknowledgement.
The original replacement API still clears caches. Prompt-cache/checkpoint metadata
includes adapter scales, storage charges include those values, and restoration
requires the exact selection. Slot reuse under changed scales clears its own state
and draft KV. With loaded adapters, global system prefixes are prepared separately
for each sequence under its weights instead of copying a foreign prefix. Prefix
preparation uses an independent batch and preserves other slots' pending work.

Kluster request qualification now recognizes lora/request_lora and requires the
native request_lora_v1 development capability. No qualification scope is enabled.
Production compilation passed in artifacts/prima-request-lora-development-build2.log
and the final server rebuild in artifacts/prima-request-lora-development-build3.log.
Root package build passed in artifacts/prima-request-lora-pin54-package.log. A clean
published Windows/Vulkan build is running in
artifacts/prima-request-lora-published54-build.log. An initial compiler member-name
error was corrected; that log is retained. No unit/native/model tests ran.

Per-request selection implementation is published, but correctness acceptance stays
open. Activated LoRA invocation handling, broader original API/model/launch parity,
row/tensor/expert placement and platform support still require implementation.
After the full implementation finishes, check default/empty/zero/negative/duplicate
selections, malformed IDs/scales, heterogeneous concurrent streams, fair scheduling,
slot reuse, cache/checkpoint selection isolation, system prefixes, draft behavior,
revision/evidence binding and lost-acknowledgement failure. Physical distributed,
mixed-platform/backend and performance gates will be tested later on the user's
live three-computer cluster and remain unpassed. All qualification flags remain
false; execution scopes remain empty. Publication skips unit CI per the schedule.


Recipe54 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, staged executable SHA-256 values
and request_lora_v1/managed_lora_v1 development capabilities are verified in
artifacts/prima-request-lora-published54-manifest-check.json. This grants no runtime
correctness or qualification. Tests/test fixes remain deferred until full required
implementation completion; physical cluster/performance acceptance stays deferred
to the live three-computer cluster and unpassed.


### Recipe55: activated LoRA invocation phases and neutral prefix ownership

Source: 19cfbf1d05d86231e52987c89836dc28b5ca8115 on fork PR #1.
Recipe digest: 0ad322867cf09ffaa0246043221de84d75545187c8cce1b9dc3646273de9ad84.
Native adapter loading parses bounded UINT32 invocation tokens and refuses IDs
outside the base vocabulary. Immutable query APIs expose them; adapter listing
includes token IDs and decoded invocation text. Kluster validates array types/bounds,
freezes invocation count/SHA-256 identities in prima_alora, rechecks staged metadata
and requires activated_lora_v1 before preparation. Invocation identities and the
activated_lora feature enter the immutable qualification scope.

Generation tokenizes before selecting a weight phase, finds the final invocation
with a linear-time overlapping-match scan, disables absent invocations and refuses
multiple pure activated adapters. The causal prompt batch stops at the baseline's
start-minus-one boundary; base and active phases settle over the existing rank-wide
batch-selection protocol. Mixed ordinary/activated selections retain the baseline
ordinary selection path. Per-sequence system prefixes use base weights for pure
activated adapters, restoring the selected batch weights before execution.

Live caches track a neutral prefix. Saved prompt/checkpoint entries record whether
their captured state is neutral; cross-adapter restoration is limited to neutral
entries within the new invocation boundary. Save neutral state at completed boundary
positions, and avoid labelling post-activation recurrent state as a base prefix.
Changed recurrent sequences clear their live state and can restore neutral snapshots.
Context shifts/truncation invalidate neutral reuse; speculative accepted tokens update
its accounting. Snapshot bookkeeping remains charged to existing byte ceilings.

Production server/CLI/profiler compilation passed in artifacts/prima-alora-development-build.log
and the final server rebuild passed in artifacts/prima-alora-development-build2.log.
Edited Python modules passed syntax/import checks. Root package build passed in
artifacts/prima-alora-pin55-package.log. A clean published Windows/Vulkan build is
running in artifacts/prima-alora-published55-build.log. No unit/native/model tests
or test fixes ran; publication skips CI tests according to the requested schedule.

Implementation is published; acceptance remains open. After the full required
implementation finishes, cover malformed/empty/oversized invocation metadata,
unknown vocabulary IDs, absent/repeated/overlapping invocations, boundary offsets,
negative scales, mixed/multiple adapters, truncation/context shift, concurrent
base/active phases, cache/checkpoint reuse and recurrent restoration, draft behavior,
noncausal embedding/rerank compatibility and lost rank acknowledgements. Remaining
original API/model/launch, row/tensor/expert placement and platform requirements
remain open. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the user's live three-computer cluster and remain unpassed.
All qualification flags are false and execution scopes remain empty.


Recipe55 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, staged executable SHA-256 values
and activated_lora_v1 development capability are verified in
artifacts/prima-alora-published55-manifest-check.json. This is build/identity evidence
only. Tests and test fixes remain deferred until full required implementation
completion; physical cluster/performance acceptance stays deferred and unpassed.


### Recipe56: baseline reasoning parser and token-budget implementation

Source: c699b6f425be9f44e3ea911015e0553b7f20ef7d on fork PR #1.
Recipe digest: 56ac11b8a7228e531a770cfa04b3254356cab378e776ba8f8869d5c391712fd6.
Jinja text generation now uses the existing baseline chat parser bridge for plain
responses as well as tools. The bridge accepts reasoning output formats, thinking
toggles, effort/none semantics, named tool choices and structured schemas. Reasoning
streams use the existing protocol item/delta machinery; deepseek-legacy keeps the
baseline stream-specific inline mode. Plaintext Responses reasoning summaries and
Messages thinking history become assistant reasoning content rather than vision.
Stored/opaque reasoning state remains outside this implementation step.

The fork ports the pinned baseline reasoning-budget state machine (MIT provenance
retained) and adapts its model/token/sampler API. Sampling applies budget constraints
before selection, suppresses lazy grammar while thinking, replays completed end tags
and preserves reset/clone state. Generation-prefix tokens initialize the budget;
UTF-8 completion and repeated reasoning blocks retain the baseline state machine.
Sampler allocation cleanup owns grammar, chain and budget through failures.

Deployment format, effort, token budget and thinking mode have native launch flags
and are checked against the immutable manifest on the head. Responses effort and
Messages enabled/disabled/adaptive thinking translate through the shared chat path.
Thinking budget aliases override deployment defaults. Kluster negotiates the new
reasoning_controls_v1 development capability and separately gates reasoning history.
Default managed model templates select Jinja rendering; qualification stays disabled.

Production server/CLI/profiler compilation passed in
artifacts/prima-reasoning-development-build4.log, after adapting the older sampler
factory API. Root package build passed in artifacts/prima-reasoning-pin56-package.log.
Edited Python modules passed syntax/import checks. Clean published Windows/Vulkan
build is running in artifacts/prima-reasoning-published56-build.log. No unit/native/
model tests or test-fix work ran; publication skips CI tests per the user's schedule.
README retains its initial fork/original-branch notice and five high-level change
categories, now including reasoning and managed adapters.

This is implementation and compilation evidence, not API/model acceptance.
Legacy template reasoning behavior, manual reasoning-control operations, opaque or
stored reasoning state and broader original API/model/placement/platform requirements
remain open. After all required implementation finishes, verify prompt/token parity,
effort/default precedence, no-tool/named/automatic/structured output, stream/history
schemas, partial/UTF-8/end-tag behavior, zero/unlimited/repeated budgets, reset/clone,
sampling failures, draft acceptance and manifest drift against the pinned baseline.
Physical distributed, mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster and remain unpassed. Qualification
flags remain false and execution scopes empty.


Recipe56 published-build follow-up: clean Windows/Vulkan production compilation
completed. Source revision/cleanliness, staged executable SHA-256 values and the
reasoning_controls_v1 development capability are verified in
artifacts/prima-reasoning-published56-manifest-check.json. Build/identity evidence
does not prove API/model correctness. Tests/test fixes remain deferred until full
required implementation completion; physical cluster/performance gates stay
explicitly deferred and unpassed.


### Recipe57: completion-scoped manual reasoning control and legacy semantics

Source: 59890f484ab5dbba01a264e94bf63723d08be7ed on fork PR #1.
Recipe digest: 64f57835ab455d6e5631eac541a00da8267b039648b5a9be6addabd5c8abd41a.
Native POST /v1/chat/completions/control accepts the baseline id/action contract
with reasoning_end. Opt-in reasoning_control arms the budget sampler even for an
unlimited budget. Server-generated external completion IDs are bound to tasks
before publication; Chat, Responses and Messages use their returned identity.
Control runs on the serialized inference task loop, requires a live armed slot
and returns baseline success/message results. Unknown, finished or reused-slot
identities cannot target another request. A successful pre-prompt force survives
initial sampler reset. Controls do not use a numeric slot ID or defer until idle.

Kluster exposes the endpoint under the same inference API authentication and
qualification boundary, forwarding only id/action and avoiding generation-default
injection. Separate reasoning_control_v1 and reasoning_delimiters_v1 native
capabilities gate manual control and request-supplied tags. Generation requests
carry the opt-in flag through all text protocol translations. Tagged raw/legacy
requests can provide validated start/end delimiters and a forced closing message.

The pinned baseline legacy renderer returns pure content without a thinking parser
or inferred delimiters; Prima now records that explicitly in shared preparation.
Existing legacy prompt rendering and named-tool behavior remain. Jinja schema
constraints are composed once by the baseline bridge, avoiding simultaneous JSON
schema/grammar installation and duplicate fence framing. Native Jinja preparation
retains baseline named/tool-schema composition and optional empty function schemas.
Opaque/stored response state remains implementation work, distinct from plaintext
reasoning history and legacy content behavior.

Production server/CLI/profiler compilation passed in
artifacts/prima-reasoning-control-development-build2.log and the final server rebuild
passed in artifacts/prima-reasoning-control-development-build3.log. Root package
build passed in artifacts/prima-reasoning-control-pin57-package.log; edited Python
modules passed syntax/import checks. Clean published Windows/Vulkan build is running
in artifacts/prima-reasoning-control-published57-build.log. No unit/native/model
tests or test-fix work ran; publication skips CI tests per the user's schedule.

T095 remains open for remaining opaque/stored behavior and later full parity checks.
After all required implementation finishes, validate authenticated control, arm/ID
binding, active/idle/done states, prompt-reset races, slot reuse, concurrent requests,
draft verification, tag aliases/limits, legacy raw content, composed structured/tool
constraints and disconnect/error cleanup. Original broader API/model/placement/
platform requirements remain open. Physical distributed, mixed-platform/backend
and performance gates will be tested later on the user's live three-computer cluster;
all remain unpassed. Qualification flags stay false and execution scopes empty.


Recipe57 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, staged executable SHA-256 values
and manual-control/delimiter development capabilities are verified in
artifacts/prima-reasoning-control-published57-manifest-check.json. This is build and
identity evidence only. Tests/test fixes remain deferred until full required
implementation completion; physical cluster/performance gates remain deferred and
unpassed, with qualification disabled.


### Recipe58: baseline Responses/Messages reasoning wire contracts

Source: fb1ff382d811b8d6b65f66f47b9f71a4ddf836f6 on fork PR #1.
Recipe digest: 1d33377d908ab840362cfa30087fbe82aa71b7027f8faf301166fffe9627f1c5.
Source inspection of the pinned baseline's tools/server/server-chat.cpp and
server-task.cpp corrects an earlier inferred requirement: the baseline rejects
nonempty previous_response_id and has no stored-response service or opaque-state
implementation. It accepts ordinary store/background fields without supplying
those services. This correction preserves the original baseline-parity objective;
it does not remove any requested baseline feature. T095 now records runtime parity
acceptance, rather than requiring an invented persistence/decryption service.

Responses reasoning uses content[{type:reasoning_text,text:...}], empty summary and
empty encrypted_content, matching the baseline. Replay requires the content array
and merges reasoning into the appropriate assistant turn. Streaming emits
response.reasoning_text.delta and completes the reasoning output item, without
invented summary part/text events. Empty previous_response_id is accepted; nonempty
references still refuse. Input/output retain plaintext reasoning history.

Messages thinking blocks include the baseline empty signature placeholder. Streams
keep content blocks open through their deltas, then close them in order; thinking
closure emits signature_delta before content_block_stop. This preserves repeated/
interleaved deltas without writing to closed blocks. The reasoning_wire_v1 native
capability separately gates the corrected reasoning content/stream contract for
qualified Responses/Messages reasoning. It grants no inference qualification.

Production server/CLI/profiler compilation passed in
artifacts/prima-reasoning-wire-development-build.log and final server compilation
passed in artifacts/prima-reasoning-wire-development-build2.log. Root package build
passed in artifacts/prima-reasoning-wire-pin58-package.log. The qualification module
passed syntax/import checks. Clean published Windows/Vulkan build is running in
artifacts/prima-reasoning-wire-published58-build.log. No unit/native/model tests or
test-fix work ran; publication skips CI according to the user's schedule.

After full required implementation finishes, verify complete/partial reasoning,
input replay and assistant/tool grouping, empty/encrypted placeholders, Messages
signature and block ordering, repeated/interleaved reasoning, default/store/background
fields and previous-ID errors against the exact baseline. Original broader API,
model, launch, placement and platform requirements remain open. Physical distributed,
mixed-platform/backend and performance gates will be tested later on the user's
live three-computer cluster and remain unpassed. Qualification stays disabled.


Recipe58 published-build follow-up: clean Windows/Vulkan production compilation
completed. Source cleanliness/revision, staged executable SHA-256 values and
reasoning_wire_v1 development capability are verified in
artifacts/prima-reasoning-wire-published58-manifest-check.json. Tests/test fixes
remain deferred until full required implementation completion; cluster/performance
gates remain deferred and unpassed, with qualification disabled.

Current source-registry inventory in artifacts/prima-architecture-source-inventory.json
records 153 baseline names and 52 Prima names, with exact source revisions/hashes.
This is a registry inventory, not loader/graph/backend support or model acceptance;
it includes specialized/dummy architectures and does not infer support from names.
The explicit missing-name list establishes a concrete starting point for remaining
T013 model implementation. Existing narrow Qwen/Llama work does not close that scope.

### Recipe59: attention sink groundwork for model parity

Source: af3ff6b5c18b623342bef7647a4c157e086589b7 on fork PR #1.
Recipe digest: 2d6c1fde715b172ef9851be8a8457ab882843f27fba4818addd7bcc230b17f3b.
Regular CPU/Vulkan softmax now includes one learned unscaled sink logit per
query head in its stabilized denominator. CPU Flash Attention applies the
corresponding correction to its online normalizer/value accumulator. Shared
KV/attention graph builders accept the optional tensor. Vulkan binds a separate
sink operand and bounds padded row dispatches before operand reads. Unsupported
accelerator implementations refuse the operand instead of silently ignoring it.

This is T013 model implementation groundwork, not full GPT-OSS support or a
qualification pass. GPT-OSS still needs loader/tensors, expert biases and
selected-logit routing, activation, alternating sliding-window attention,
per-layer RoPE, MXFP4, remaining backend kernels, exact placement and profiling.
The broader original model/API/platform/placement scope remains open.

Production server/CLI/profiler builds passed in
artifacts/prima-attention-sinks-development-build.log and the final repeat in
artifacts/prima-attention-sinks-development-build2.log. Root packaging passed in
artifacts/prima-attention-sinks-pin59-package.log. A fresh published-source
Windows/Vulkan build is running in artifacts/prima-attention-sinks-published59-build.log.
No unit/native/model tests or test-fix work ran. Those start only after the full
required implementation finishes. Later numerical coverage must include batched
head indexing, optional/F16/F32 masks, large sinks, fully masked keys, padded
Vulkan rows, regular/Flash Attention and unchanged no-sink behavior.

Physical distributed/mixed-platform/backend validation and performance gates
remain deferred to the user's live three-computer cluster and unpassed. All
qualification flags remain false. Publication skips CI under the implementation-
first schedule. Source/compile identity does not establish runtime acceptance.

Recipe59 published-build follow-up: clean Windows/Vulkan production compilation
completed. Source revision/cleanliness, recipe identity and staged executable
SHA-256 values are verified in
artifacts/prima-attention-sinks-published59-manifest-check.json. Full GPT-OSS
implementation remains open. No unit/native/model tests or test fixes ran;
those remain deferred until full required implementation completion. Physical
cluster/performance gates remain deferred and unpassed; qualification is disabled.

### Recipe60: GPT-OSS expert graph groundwork

Source: 1aeaf5004cf11da8c6c79e7b7adf28ca8a34a998 on fork PR #1.
Recipe digest: 107f234637f811f221bad33d5fd7fe19814b9c1016d9af9412816dff4db25a7e.
The native MoE builder now optionally applies router bias, selected expert
up/gate/down biases and softmax over the selected raw logits. Existing callers
retain their prior graph when these options are absent. Bias gathering preserves
token/expert order and compacts the strided top-k IDs. Vulkan has a genuine I32
copy pipeline for that compaction rather than reinterpreting IDs as floating point
or moving the compaction to the CPU.

A fused equal-shaped F32 ggml_swiglu_oai operation computes the baseline clipped
activation on CPU and Vulkan, using alpha=1.702 and limit=7 in the MoE builder.
It is included in CPU work planning, Vulkan graph preparation/dispatch/admission,
shader generation and debug graph cloning. This advances T013; it does not close
full GPT-OSS support. Loader/tensors, alternating sliding-window attention,
per-layer RoPE, tokenizer, MXFP4, remaining accelerators and exact placement/
profiling integration still remain. Original broader model/API/platform/placement
requirements remain open, and qualification stays disabled.

Production server/CLI/profiler compilation passed in
artifacts/prima-gptoss-moe-development-build.log and final repeat in
artifacts/prima-gptoss-moe-development-build2.log. Root packaging passed in
artifacts/prima-gptoss-moe-pin60-package.log. Fresh published-source Windows/Vulkan
compilation is running in artifacts/prima-gptoss-moe-published60-build.log.
No unit/native/model tests or test fixes ran. After the full required implementation
finishes, check bias gathering with multiple/distinct token/expert IDs and padded
top-k views, integer copy, raw-logit selection/normalization, activation clipping,
parameters/strides and unchanged ordinary MoE behavior. Source/build evidence does
not establish numerical, model or backend acceptance. Physical distributed,
mixed-platform/backend and performance gates remain deferred to the user's live
three-computer cluster and unpassed. Publication skips CI per the user's schedule.

Recipe60 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-gptoss-moe-published60-manifest-check.json. No unit/native/model
tests or test-fix work ran; those start after full required implementation
completion. GPT-OSS and broader integration remain open. Physical cluster and
performance gates remain deferred and unpassed, with qualification disabled.

### Recipe61: GPT-OSS loader, execution graph and tokenizer implementation

Source: 04aa51a2cc6f6dcb12a56fb862ce020e4e898403 on fork PR #1.
Recipe digest: 8de1f2daa44af50e311c7f47e5d5c09ecf2bcc72ac846873f2cff035d1cdfad2.
The native gpt-oss registry now has real metadata/tensor loading and a segmented
execution graph consuming the sink and expert operations from recipes59/60.
Rank-owned tensor loading uses absolute-to-local mapping, required output/expert
bias tensors and separate or fused QKV weights with optional attention biases.
Scalar/default or per-layer SWA patterns select each absolute layer's mask and
RoPE frequency; expert widths accept scalar/per-layer metadata. GPT-4o BPE splitting
uses the pinned baseline regex, with Harmony return/call EOG handling that keeps
intermediate end markers usable between analysis and final channels.

KV shift and defragmentation now map absolute owned layers to local cache tensors;
K-shift selects the same layer-specific rotary parameters as generation and skips
tensor work for a zero-transformer coordinator. Noncausal decoder masks preserve
cached sequence isolation while allowing future positions; SWA masks retain their
standard lower-window cutoff. Existing shared attention graph paths are preserved.

This advances T013/T014 implementation but does not establish full GPT-OSS support.
MXFP4, full model profiler roles/rates, compact SWA KV storage and remaining
accelerator operations remain open. Current SWA uses full-context KV storage with
masking; do not treat it as the baseline's compact cache memory contract. The broader
original model/API/platform/placement objective remains open, including arbitrary
row/tensor/expert placement. No qualification is enabled.

Production builds passed in artifacts/prima-gptoss-loader-development-build.log,
artifacts/prima-gptoss-loader-development-build2.log,
artifacts/prima-gptoss-loader-development-build3.log and the final
artifacts/prima-gptoss-loader-development-build4.log. Root package build passed in
artifacts/prima-gptoss-loader-pin61-package.log. Clean published Windows/Vulkan
compilation is running in artifacts/prima-gptoss-loader-published61-build.log.
After full required implementation completion, test separate/fused QKV, optional
biases, SWA metadata/masks/RoPE, tokenizer/Harmony multi-channel stopping,
noncausal/cached sequence isolation, repeated/zero-rank segments, KV shift/defrag,
cache snapshots, LoRA and existing model regressions. None of those unit/native/model
tests or test fixes ran in this phase. Physical distributed/mixed-platform/backend
and performance gates remain deferred to the user's live three-computer cluster
and unpassed. Publication skips CI per the implementation-first schedule.

Recipe61 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-gptoss-loader-published61-manifest-check.json. Fork PR #1 is open
with that exact head revision. The updated source-only registry inventory is in
artifacts/prima-architecture-source-inventory61.json (153 baseline/53 Prima names);
architecture names do not establish loader/model/backend acceptance. No tests
or test fixes ran, and full GPT-OSS/integration implementation remains open.
Physical cluster/performance gates remain deferred and unpassed, with all
qualification flags disabled.

### Recipe62: MXFP4 CPU/Vulkan format implementation

Source: c31c5fe22703dbbac621a8ce6f64e04bf67b2833 on fork PR #1.
Recipe digest: 338afe98ca70127017bb2a4a23dcbd0f215bd5551c2f06cfde54308ffb2813e1.
GGML/GGUF type39 now retains MXFP4's actual 32-value/17-byte block layout and
E8M0 scale, with reference encoding/decoding, reserved-exponent row validation,
Q8_0 CPU dot products and generic CPU quantized operations. Native file-type
identity/policy uses MXFP4 routed expert matrices and Q8_0 other quantized matrices,
with explicit input/output overrides preserved. The CPU reference implementation
is adapted from the exact pinned baseline rather than relabeling another format.

Vulkan shader generation and 33 independent pipeline registrations provide direct
compressed matrix/vector/indexed-expert execution, F32 accumulation selection,
row gathering and device dequantization. Packed exponent/nibble storage stays on
the device. CUDA/Metal/SYCL/CANN/Kompute admission refuses MXFP4 until their kernels
are implemented; refusal does not qualify the requested accelerator. Root GGUF
layout accounting already reads compressed file-offset spans, including alignment,
and needed no format-specific storage approximation.

This advances T013 model/format implementation. Full GPT-OSS profiler roles/rates,
compact sliding-window KV storage and remaining accelerator kernels still remain,
as do broader original model/API/platform/row/tensor/expert placement requirements.
It does not establish full GPT-OSS support or enable qualification.

Production builds passed in artifacts/prima-mxfp4-development-build.log and the
final artifacts/prima-mxfp4-development-build2.log. Root package build passed in
artifacts/prima-mxfp4-pin62-package.log. Clean published Windows/Vulkan compilation
is running in artifacts/prima-mxfp4-published62-build.log. No unit/native/model tests,
test-fix work or performance checks ran. After full required implementation
finishes, cover all FP4 codes/signs/zero, exponent boundaries/rejection, packed
block/row/expert strides, CPU input quantization, GPU matrix/vector/indexed/gather
and F32 dispatch, format policy/overrides and unchanged neighboring formats. Model
checks must use original complete MXFP4 GGUF bytes and native prompt/token evidence.
Physical distributed/mixed-platform/backend and performance acceptance remains
deferred to the user's live three-computer cluster and unpassed. Publication skips
CI under the implementation-first schedule; qualification remains disabled.

Recipe62 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-mxfp4-published62-manifest-check.json. Fork PR #1 is open at the
same source revision. No unit/native/model tests, test fixes or performance
checks ran; those tests start after full required implementation completion.
Full GPT-OSS/integration remains open. Physical cluster/performance gates remain
deferred and unpassed, with all qualification flags disabled.

### Recipe63: GPT-OSS typed full-model profiler implementation

Source: 901730e99979fa8d8dd0d4ec35772b69dce83ab2 on fork PR #1.
Recipe digest: 0865407e9c6c34973700fc132c939c3cd09ec98fce06baadb82de54905080f1b.
The typed native model-cost builder now recognizes GPT-OSS post-attention norm,
sinks, router/expert biases, per-layer expert widths and separate or fused QKV.
It checks required roles/dimensions, refuses ambiguous fused/separate weights and
unknown bias roles, and counts selected-expert arithmetic/active bytes while
retaining every expert's resident storage. Fused QKV includes rotary/attention/KV
work once. GPT-OSS activation, selected-logit softmax, router sorting and weighted
expert reduction contribute logical F32 work estimates.

Attention costs retain the full KV views used by the current graph; no compact
SWA cache or skipped masked-key computation is inferred. The existing typed
matrix-rate path encodes real MXFP4 through recipe62's CPU/Vulkan kernels. Native
profiling refuses unsupported requested GPU kernels, and GPT-OSS GPU profiling
explicitly refuses backends without its sink/activation operations, even for
ordinary matrix formats. Kluster's typed decoder already accepts matching rate
keys and recomputes each cost from its rate/byte tables; schema2 is preserved.

This advances T013/T010 implementation. These roofline costs, including non-matrix
work proxies, are not measured instructions, full-model generation timings,
quality/performance acceptance or qualification. Compact sliding-window KV,
remaining accelerator kernels and broader original model/API/platform/row/tensor/
expert placement requirements remain open. Measurements and unit/native/model
checks/test fixes remain deferred until the full required implementation finishes.

Production builds passed in artifacts/prima-gptoss-profiler-development-build.log
and final artifacts/prima-gptoss-profiler-development-build2.log. Root packaging
passed in artifacts/prima-gptoss-profiler-pin63-package.log. Clean published
Windows/Vulkan compilation is running in
artifacts/prima-gptoss-profiler-published63-build.log. No profiling measurements,
tests/test fixes or performance checks ran. Later checks cover malformed/missing
roles/dimensions, separate/fused equivalence, selected expert bytes/arithmetic,
activation/router estimates, original MXFP4 operand encoding/completed requested
backend work, model cost/rate vectors and signed source/model/hardware/settings
cache identity. Physical distributed/mixed-platform/backend and performance gates
remain deferred to the live three-computer cluster and unpassed. Qualification
stays disabled; publication skips CI under the implementation-first schedule.

Recipe63 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-gptoss-profiler-published63-manifest-check.json. Fork PR #1 is open
at that exact head. No profiling measurements, unit/native/model tests, test fixes
or performance checks ran. Full required implementation remains open, including
compact SWA KV and remaining accelerator kernels. Physical cluster/performance
gates remain deferred and unpassed, with all qualification flags disabled.

### Recipe64: owned KV storage-layout prerequisite for compact SWA

Source: b45ae7e2e3bb85eb586b0b564cb68d1b1914278f on fork PR #1.
Recipe digest: c7e2b39cdc8a6065aa94c8059baf6638b042d98380b1d9228d2d94f9ceb7c09b.
GPT-OSS KV initialization now records an immutable rank-local storage layout
classified by absolute owned layers. It computes the pinned baseline's compact
unified-pool size, pad256(min(full_cells, window*sequence_capacity+microbatch)),
with checked ownership/pattern/dimensions and allocation products. The incoming
microbatch needs room beside past window tokens so its earliest token retains
its required history. Zero-transformer ranks retain an empty owned-layer table.

This is a prerequisite, not compact KV execution. Allocation explicitly keeps
full-size mode; no compact capability or user flag is advertised. Separate cell
state/reclaim, attention/mask views, sequence lifecycle and snapshot serialization
must be connected before activation. Current profiler/capacity behavior still
reflects the active full views; no memory or performance gain is claimed. Those
compact-cache consumers, remaining accelerator kernels and broader original model/
API/platform/row/tensor/expert placement requirements remain open.

Production builds passed in artifacts/prima-swa-layout-development-build.log and
final artifacts/prima-swa-layout-development-build2.log. Root packaging passed in
artifacts/prima-swa-layout-pin64-package.log. Clean published Windows/Vulkan
compilation is running in artifacts/prima-swa-layout-published64-build.log.
No unit/native/model checks, test fixes, measurements or performance checks ran.
After full required implementation finishes, cover pool sizing/overflow/ownership,
full-mode allocation, compact microbatch overlap, shared/rewound/removed/copied
sequences, masks/attention, KV shifts/defrag, prompt/checkpoint snapshots and
coordinator/worker lifecycle. Physical distributed/mixed-platform/backend and
performance gates remain deferred to the user's live three-computer cluster and
unpassed. Qualification stays disabled; publication skips CI per the current
implementation-first schedule.

Recipe64 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-swa-layout-published64-manifest-check.json. Fork PR #1 is open
at that exact head. Compact execution remains disabled and its consumer wiring
remains implementation work. No tests, test fixes, measurements or performance
checks ran. Full required implementation remains open. Physical cluster and
performance gates remain deferred and unpassed; qualification stays disabled.


Recipe65 implementation progress: transactional compact SWA row metadata
(4ccaa9900099385dfdb4f38916236fd0a9b84a88). Physical rows refer to the full
cache's authoritative sequence cells. Reservation preparation is read-only;
commit rejects stale/foreign tickets. Restore validates the complete map before
publication, and defragmentation can remap global IDs without moving compact rows.
Full-size storage remains active: allocation/views/masks, sequence retention and
rewind, shifts, snapshots and managed budgets still need coordinated integration.
Development server/CLI/profiler compilation and Kluster packaging passed. No
unit/native/model tests or test fixes ran. All such checks remain deferred until
full required implementation finishes. Physical distributed and performance
acceptance remain deferred to the live three-computer cluster and unpassed.
Broader model/API/platform/accelerator/exact-placement implementation remains
open; qualification is disabled. Clean published build evidence will be recorded
separately after compilation completes.


Recipe65 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Exact published source, recipe identity and staged binary
SHA-256 values are recorded in
artifacts/prima-swa-map-published65-manifest-check.json. Packaging also passed.
No unit/native/model tests, test fixes or performance checks ran. Compact execution
remains disabled pending consumer wiring. Full required implementation remains
open, physical cluster/performance gates remain deferred and qualification is off.


Recipe66 implementation progress: compact SWA runtime consumers and sparse
snapshots (3c64365f2daf52e3bc688a25bcc29b0a4375ac6a). Decoder admission prepares
compact rows before full-cache reservation and refuses missing rewind history.
Layer-specific K/V views and masks use physical compact row order and extent;
K shifts use mapped deltas, while defrag preserves compact tensor row locations.
Versioned snapshots carry sparse compact rows by serialized global-cell ordinal,
validate pool dimensions/ownership/pattern and handle both V storage layouts.
Failed or truncated restore clears its destination. Reservation commit now uses
storage allocated before full-cache admission; contiguous search skips occupied
spans. No performance measurements establish a benefit.
Compact mode remains disabled pending managed budgets, complete sequence/replay
handling and coordinated replay decisions across differing rank ownership.
Broader model/API/platform/backend/exact-placement implementation remains open.
Final production compilation passed in
artifacts/prima-swa-consumers-development-build3.log; packaging passed in
artifacts/prima-swa-consumers-pin66-package.log. Clean published compilation is
running in artifacts/prima-swa-consumers-published66-build.log.
No unit/native/model tests or test fixes ran. All are deferred until full required
implementation finishes. Physical distributed/mixed-platform/backend and
performance acceptance remain deferred to the live three-computer cluster and
unpassed; qualification remains disabled.


Recipe66 published-build follow-up: clean Windows/Vulkan production compilation
completed. Exact clean source, recipe identity and staged executable SHA-256
values are recorded in
artifacts/prima-swa-consumers-published66-manifest-check.json. Packaging passed.
These are build/source checks only. Compact activation, managed budgets and
complete local/distributed replay handling remain implementation work; broader
integration requirements remain open. No unit/native/model tests, test fixes or
performance checks ran. Physical cluster and performance acceptance remain
explicitly deferred and unpassed; qualification stays disabled.


Recipe67 implementation progress: all-rank KV rewind and explicit full SWA
storage (a99c14d2674d2a023c5785199c036f30401bc281). Server prefix reuse and
speculative suffix trimming perform read-only preflight on every physical rank,
then settle commit or abort. Commands bind manifest, monotonic revision,
sequence/range, phase, world and hops; malformed/stale/foreign/lost settlement
faults the context rather than allowing generation with uncertain rank state.
A worker can request replay even when the coordinator owns no SWA layers.
Native --swa-full and C API defaults explicitly retain full storage. Kluster now
resolves/prices full SWA storage and checkpoints and passes that flag to each
rank, fixing the mismatch between compact estimates and actual full allocation.
This is current-layout accounting, not completion of compact managed budgets.
Compact activation remains disabled. Compact budgets, CLI reuse, context shifts
and positional transforms remain implementation work, as do broader original
model/API/platform/accelerator/exact-placement requirements.
Final development production compilation passed in
artifacts/prima-kv-rewind-development-build3.log. Changed Python source compiles;
packaging passed in artifacts/prima-kv-rewind-pin67-package.log. Clean published
compilation is running in artifacts/prima-kv-rewind-published67-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the live
three-computer cluster and unpassed. Qualification remains disabled.


Recipe67 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, recipe identity and staged executable hashes
are recorded in artifacts/prima-kv-rewind-published67-manifest-check.json.
Help/capability metadata exposes --swa-full, swa_full_v1 and kv_rewind_v1;
these metadata probes do not establish execution correctness. Packaging passed.
No unit/native/model tests, test fixes or performance checks ran. Compact budgets,
complete sequence transforms/replay and activation remain implementation work;
broader original integration scope remains open. Physical cluster and performance
acceptance remain deferred and unpassed; all qualification gates remain false.


Recipe68 implementation progress: CLI replay and checked positional transforms
(c96308fdfcc1a92f10762a9e9a5b4a9a320b5609). CLI session reuse checks the earliest
actual decode boundary, including logits reconstruction. Transformer ranks compare
a digest of their declared local session-token prefix and require the cached
predecessor; missing/mismatched rank sessions or evicted history cause clearing
and replay. Exact IDs are vocabulary/context bounded and hashed with a domain,
count and little-endian uint32 encoding. Checked mutation/actual decode invalidate
the declaration. Zero-transformer relays require no local tensor predecessor.
The local draft helper replays its selected prompt window when rewind fails.
Ordinary CLI context shifts keep exact evaluated token history independently of
session-file saving and use all-rank checked removal/shift or retained-token replay.
The native checked transform API simulates add/divide operations before mutation,
checks signed position/delta bounds, checks resulting SWA history for every shared
sequence, handles recurrent tails, and binds versioned operands to ring settlement.
Compact activation stays disabled pending compact budgets and complete server
context-shift/self-extend replay. Broader model/API/platform/backend/exact-placement
requirements remain open. Final production compilation passed in
artifacts/prima-cli-replay-development-build5.log; packaging passed in
artifacts/prima-cli-replay-pin68-package.log. Clean published compilation is
running in artifacts/prima-cli-replay-published68-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the user's
live three-computer cluster and unpassed. Qualification remains disabled.


Recipe68 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, recipe identity and staged executable hashes
are recorded in artifacts/prima-cli-replay-published68-manifest-check.json.
Advertised kv_transform_v1/kv_prefix_v1 metadata matches the published source;
metadata does not establish execution correctness. Packaging passed.
No unit/native/model tests, test fixes or performance checks ran. Compact managed
budgets, server context-shift/self-extend replay and activation remain open.
Server replay also needs completed-token history when prompt caching is disabled.
Broader original integration requirements remain open. Physical distributed and
performance acceptance remain deferred and unpassed; qualification stays false.


Recipe69 implementation progress: completed server history and ordinary context
replay (bbc57552674264992a9367bf2249c5d775871b3d). Per-slot token history records
successful evaluations independently of cache_prompt and retains system-prefix
and base/active aLoRA phase identity. Speculative history is trimmed after accepted
prefix settlement. Failed batch decoding trims optimistic cache entries to known
completed history before later reuse. Reused prompt prefixes initialize from the
exact incoming IDs. Ordinary context shift preserves the full system prefix,
uses checked all-rank remove/shift, and replays retained tokens with their stored
weight phases if required history is missing. Sampler/output state stays with the
request; failed replay clears the affected sequence through existing recovery.
This does not complete self-extend replay or compact managed budgets. Both remain
implementation work before compact activation. Broader original model/API/platform/
accelerator/exact-placement scope remains open.
Development production compilation passed in
artifacts/prima-server-history-development-build.log; packaging passed in
artifacts/prima-server-history-pin69-package.log. Clean published compilation is
running in artifacts/prima-server-history-published69-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the live
three-computer cluster and unpassed; qualification remains disabled.


Recipe69 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, exact recipe identity and staged executable
hashes are recorded in artifacts/prima-server-history-published69-manifest-check.json.
Packaging passed. This evidence covers source/build identity only; no unit/native/
model tests, test fixes or performance checks ran. Compact budgets, self-extend
position/replay handling and activation remain implementation work. Broader
original integration requirements remain open. Physical distributed and
performance acceptance remain deferred and unpassed; qualification stays false.


Recipe70 implementation progress: grouped KV transform programs and CLI
self-extend replay (023c788d4a12a6ee0af7880840b7855d303e8255). Native preflight
simulates up to eight ordered add/divide steps together, checks every intermediate
signed position/delta and checks SWA history only after the final result.
The complete program is bound to all-rank prepare/commit/abort settlement. CLI
self-extend submits its three operations as one program, tracks evaluated token
positions and replays all ranks at transformed positions when history is missing.
Its session files omit the transform schedule, so their tokens are rebuilt instead
of reusing compressed state as an ordinary prefix. Recurrent self-extend does not
accumulate transformer replay history. This does not establish model correctness
or performance. Server self-extend replay and compact managed budgets remain
implementation work before activation; broader original integration scope remains
open. Compact mode and inference qualification remain disabled.
Production development compilation passed in
artifacts/prima-transform-program-development-build3.log; packaging passed in
artifacts/prima-transform-program-pin70-package.log. Clean published compilation
is running in artifacts/prima-transform-program-published70-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the live
three-computer cluster and unpassed.


Recipe70 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, exact recipe identity and staged executable
hashes are recorded in artifacts/prima-transform-program-published70-manifest-check.json.
Advertised kv_transform_program_v1 metadata matches source; metadata is not runtime
acceptance. Packaging passed. No unit/native/model tests, test fixes or performance
checks ran. Server self-extend replay, compact budgets and activation remain open,
as does the broader original integration scope. Physical distributed and
performance acceptance remain deferred and unpassed; qualification stays false.


Recipe71 implementation progress: server self-extend replay and origin-aware
transform programs (bcd90dd6bcee3017f02c228c245d8a3eb851368d). The extended API
preserves the original program API/layout and adds division relative to a fixed
system-prefix origin. Server self-extend keeps evaluated positions and weight
phases, settles composed transforms on every rank, rebases pending rows and
replays at resulting positions when required history is missing. Counters advance
only for that slot's rows after a successful decode view; failed attempts and
other/idle slots do not advance them. Abandoned batches roll back optimistic
prompt progress to completed counts. Positive retry requires complete history and
restores the frozen batch selection; incomplete recurrent self-extend history is
refused rather than silently reconstructed. System prefixes now evaluate on each
real request sequence, avoiding shared sequence-zero mapping across head/workers
and independent RNN/transformed request state.
Compact mode stays disabled pending managed budgets and final activation review.
Broader original model/API/platform/accelerator/exact-placement scope remains open.
Development compilation passed in
artifacts/prima-server-selfextend-development-build4.log; packaging passed in
artifacts/prima-server-selfextend-pin71-package.log. Clean published compilation is
running in artifacts/prima-server-selfextend-published71-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the live
three-computer cluster and unpassed; qualification stays disabled.


Recipe71 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, exact recipe identity and staged executable
hashes are recorded in artifacts/prima-server-selfextend-published71-manifest-check.json.
Advertised kv_transform_program_v2 metadata matches source; metadata does not prove
runtime correctness. Packaging passed. No unit/native/model tests, test fixes or
performance checks ran. Compact budgets and final activation review remain open,
as does the broader original integration scope. Physical distributed and
performance acceptance remain deferred and unpassed; qualification stays false.


Recipe72 KV budgeting implementation status (2026-10-13):
The immutable Prima settings now bind a KV sequence ceiling of parallel_slots + 1
for the head's reserved sequence; each local draft context reserves one sequence.
Estimates price Prima's current unified pool, recurrent state, decode/verification
outputs, completed-history metadata and compact-map reservation/restore storage.
GPT-OSS scalar pattern zero means all sliding layers; malformed window/pattern
metadata refuses. Other Prima architectures retain full SWA tensor pricing until
their native compact paths exist. Full-cache execution remains explicitly active:
compact activation still requires final consumer review and launch wiring.
Native KV size helpers use actual per-layer dimensions and quantization block
storage, recurrent sequence cells and the configured GPT-OSS storage layout,
with checked bounds. These are estimates, not hardware allocator measurements.
Fork PR #1 now points to 48c6a57fb002f1129381f0452489f48ece005a38.
Recipe72 digest: 2c300fe5bbd719154f472e27c11f775041fb3aeb77c3e49e249e1b5f46768e3c.
Production development compilation and Python syntax checks passed; packaging
passed before the pin update. Clean published compilation is running in
artifacts/prima-kv-accounting-published72-build.log.
ALL unit/native/model tests and test-fix work remain deferred until the full
required implementation is finished. Physical distributed, mixed-platform/backend
and performance acceptance remain deferred to the user's live three-computer
cluster and unpassed. Qualification remains disabled. The broader original
model/API/platform/accelerator/exact-placement integration scope is still open.


Recipe72 reserve refinement: GPT-OSS dense and sliding input masks coexist,
including full-storage mode, so both masks are counted with peak attention
scratch. Prima output reservation includes a full microbatch as well as sequence
and speculative verification ceilings. Pin72 packaging and syntax compilation
passed in artifacts/prima-kv-accounting-pin72-package.log; tests remain deferred.


Recipe72 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. Exact recipe/source identity, clean source and staged
executable hashes are recorded in
artifacts/prima-kv-accounting-published72-manifest-check.json. Pin72 packaging and
syntax checks passed. No unit/native/model tests, test fixes, inference or
performance checks ran. Compact activation/review and the broader original
integration remain unfinished; qualification remains false. Tests start only
after the full required implementation is finished. Physical distributed and
performance acceptance remain pending for the user's live three-computer cluster.


Compact GPT-OSS activation implementation (recipe73, 2026-10-13):
The final storage-consumer review found and fixed worst-case graph reservation:
compact K/V write offsets now fit the compact pool and attention/mask reserves use
its full capacity rather than live occupancy. Initialization and reserves after
shift/defragmentation share that path without mutating live cache state.
Compact GPT-OSS storage is now the native/managed default, with --swa-full and
--swa-compact selecting an explicit mode on every rank. Native manifests bind the
mode, HTTP slots and reserved head sequence ceiling; preparation negotiates the
storage capability before execution. Draft contexts retain one sequence. The
server checks overflow before adding its reserved sequence.
Only GPT-OSS has this compact storage path. Other architectures remain fully
priced and broader original model/API/platform/accelerator/placement work remains
open. The new capability is implementation metadata, not runtime acceptance.
Production compilation is running; no unit/native/model tests or test-fix work
have run. All tests remain deferred until full required implementation finishes.
Physical distributed, mixed-platform/backend and performance acceptance will be
tested later on the user's live three-computer cluster; all are unpassed and
qualification remains disabled.


Recipe73 publication: PR #1 head/source pin is
33921e29c84ed46bc5f1fc240be8b010df657bb8; normalized recipe digest is
17bd83c73590d0d125987cf58cd145fbaaf3de48f58b6299e2e23d599b22cdc8.
Development server/CLI/profiler compilation completed in
artifacts/prima-compact-activation-development73-final.log. Pin73 packaging and
Python syntax checks passed. Clean published compilation is running in
artifacts/prima-compact-activation-published73-build.log. GPT-OSS Flash Attention
mask reservations also count the simultaneous F32 inputs and F16 casts.
Compact GPT-OSS execution is implemented, but model/native/unit acceptance is
unverified and deferred until the full required integration implementation is
finished. Broader original integration tasks remain open. Physical distributed,
mixed-platform/backend and performance gates will be tested later on the user's
live three-computer cluster. Qualification remains disabled.


Recipe73 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. The source checkout is clean and its commit, normalized
recipe identity and all three staged executable hashes match the published pin.
artifacts/prima-compact-activation-published73-manifest-check.json records this
source/build-only evidence. Published server capability metadata advertises
GPT-OSS compact storage; CLI/server help exposes both storage flags. Neither
metadata nor successful compilation is runtime correctness acceptance.
Packaging and syntax checks passed. No unit/native/model tests, test fixes,
inference, profiling measurements or performance checks ran. Those tests remain
deferred until full required integration implementation finishes. Other
architectures' compact paths and broader original model/API/platform/accelerator/
exact-placement work remain unfinished. Physical distributed, mixed-platform/
backend and performance gates will be tested later on the live three-computer
cluster. All qualification remains disabled.


Gemma3 model-parity implementation progress (recipe74, 2026-10-13):
Added the native architecture registry, metadata/pattern parser and rank-owned
loader for separate/fused QKV, per-head Q/K norms, post-attention/post-FFN norms,
optional/tied output and optional resident retrieval projections. The shared
manifest-controlled graph now implements Gemma3 token-only embedding scaling,
GELU gating, per-layer Q/K head counts, query scaling and optional final logit
softcapping. SWA uses the baseline six-layer pattern, independent SWA RoPE
frequency/scale and the existing checked compact admission/rewind/shift/snapshot
path; no-window variants remain dense. Model loading and segmented graph
admission include Gemma3. Native and managed KV/compute/checkpoint reservations
include compact/full storage and both attention masks. The typed profiler counts
head/post norms, GELU/query/embedding/softcap work and resident auxiliary weights;
these are estimates, not measured model acceptance or speedups.
The source registry is now 54 versus the pinned baseline's 153 (99 registry
entries still absent), recorded in artifacts/prima-architecture-source-inventory74.json.
Registry presence does not establish graph/API/backend correctness. Gemma3
retrieval-projection embedding output and vision/projector integration remain
unfinished. Embedding requests with those projections refuse explicitly while
the API contract is incomplete; ordinary generation still loads/prices the
resident weights. Broader original model/API/platform/accelerator/exact-placement
scope remains open. This work does not complete T013/T014 or full integration.
Production development compilation passed before the final profiler refinement;
its publication build is running in artifacts/prima-gemma3-development74-publish.log.
No unit/native/model tests, test fixes, inference, profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the user's live three-computer cluster and are unpassed.
All qualification remains disabled.


Recipe74 publication: PR #1/source pin is ec2d4f0ba5619b747ee8b8cb2efdefe571abab2e;
normalized recipe digest is 96f26a038f4b1731d5cace128d6f795c1881dd4188382924f84d88e64db86802.
Development server/CLI/profiler compilation completed in
artifacts/prima-gemma3-development74-publish.log. Pin74 packaging and Python
syntax checks passed. Clean published compilation is running in
artifacts/prima-gemma3-published74-build.log.
The architecture inventory also records the native graph dispatch allowlist:
llama, qwen2, qwen3, qwen3moe, gpt-oss and gemma3. Existing registry entries outside
that list still need complete execution paths; the 99 absent baseline names are
only one part of the remaining model-parity scope.
No unit/native/model tests or performance checks ran; the implementation-first
and live three-computer cluster deferrals remain in effect. Qualification is false.


Recipe74 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. Source checkout cleanliness, exact pin/recipe identity and
all three staged executable hashes are recorded in
artifacts/prima-gemma3-published74-manifest-check.json. General/Gemma3 compact
capability and both storage-option help entries are advertised metadata only.
Final packaging and syntax checks passed in
artifacts/prima-gemma3-pin74-package-final.log. Managed GGUF layouts classify
retrieval dense_2/dense_3 weights with output tensors, matching their native
output-device allocation and preventing a GPU output-head reservation gap.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Gemma3 retrieval embedding-output/vision contracts and broader original
model/API/platform/accelerator/exact-placement integration remain unfinished.
Physical distributed, mixed-platform/backend and performance gates will be
tested later on the user's live three-computer cluster. All qualification remains
false; source/build metadata does not establish model correctness or acceptance.


Embedding output and decode metadata implementation (recipe75, 2026-10-13):
Native output-width metadata/C API now separates transformer width from projected
embedding width. Gemma3 retrieval chains are checked on every rank; token vectors
project directly, while pooled means/CLS/LAST vectors aggregate across completed
microbatches before a head-only projection graph. This preserves the order of
pooling versus quantized projection. Head buffer allocation, reordering, getters
and token-vector session spans use output width. Pooled input columns use the
sequence ceiling, including sparse/shared IDs. Decoder extraction is implemented
and the previous refusal is removed. Embedding graphs retain result_norm and omit
an unused language-model head. Projection schedules reset before metadata cleanup,
including failure paths, and completed projection weights are included in device
execution evidence through the actual reused head scheduler.
Worker output counts now derive from the received batch, not an empty placeholder.
Bounded metadata includes all-output selection and flattened shared sequence tags;
receiver vectors own those bytes, reject duplicate/unknown/malformed fields and
check sequence/byte ceilings before allocation. Distributed preparation requires
decode_metadata_v2. This remains an admitted private ring, not a new public listener.
Native embedding server responses use projected width and distinct pooled/token
vectors. Every embedding token requests output without prompt-cache reuse. Native
routes use matrix responses; OpenAI requires pooling, supports float/base64,
reports evaluated-token usage and orders entries by input index. Normalization
controls are preserved, and bounded OpenAI dimensions are an added fork feature.
Requests exceeding batch capacity refuse instead of indefinitely waiting.
Managed estimates include projection intermediates, pooled matrix/input/output
copies and larger output vectors; resident projections remain charged to the
output device. These are conservative estimates, not allocator/performance proof.
Production builds passed before the final metadata refinements; their last build
is running in artifacts/prima-embedding-output-development75-final3.log. Python
syntax checks passed. API/gateway/model/backend and physical distributed acceptance
are unverified. Vision/projector and broader original model/API/platform/accelerator/
exact-placement integration remain open; this does not finish T013/T014 or the goal.
No unit/native/model tests, test fixes, inference, profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and are unpassed.
All qualification remains disabled.


Recipe75 publication: PR #1/source pin is
b1b5e7cff3b8ecdc44533e291a32fafeb36f4dee; normalized recipe digest is
7da25607787674ac6e24af041d36faf7383aed3d96bd9254ea9eb301c1cb930d.
Development server/CLI/profiler compilation completed, followed by production
embedding/retrieval companion-tool compilation in
artifacts/prima-embedding-output-development75-tools.log. Companion tools now use
projected output width. Packaging and Python syntax checks passed in
artifacts/prima-embedding-output-pin75-package.log. Clean published compilation
is running in artifacts/prima-embedding-output-published75-build.log.
Source/build evidence does not establish vector accuracy, API/gateway parity,
backend execution or distributed acceptance. Full required implementation remains
unfinished and all unit/native/model tests and test-fix work remain deferred.
Physical distributed/mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster; none is passed and qualification
remains disabled.


Recipe75 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. The source checkout is clean; exact source/recipe identity
and all three staged executable hashes match the published pin, recorded in
artifacts/prima-embedding-output-published75-manifest-check.json. Published metadata
advertises embedding_output_v1/decode_metadata_v2, embedding/pooling and explicit
storage options. This is advertised metadata, not API/model/backend acceptance.
Packaging and Python syntax checks passed. Embedding/retrieval companion tools
also compiled in the development checkout with projected-width allocation.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Embedding gateway/request-contract completion and acceptance, vision/
projector integration and the broader original model/API/platform/accelerator/
exact-placement scope remain open. Physical distributed/mixed-platform/backend
and performance gates will be tested later on the live three-computer cluster;
none is passed. Qualification remains disabled and the thread goal remains active.


Recipe75 role-specific help/launch follow-up: the first help inventory incorrectly
required the worker CLI to expose server embedding switches. That metadata check
refused; it was not a unit/model test or runtime acceptance. The corrected receipt
checks server embedding/pooling controls and both binaries' storage switches,
recording that CLI workers receive output selection through decode_metadata_v2.
Managed extras now accept bounded embedding/pooling options and emit them only on
rank zero. Manifest feature discovery uses embeddings rather than chat for that
mode and native embedding capability is negotiated. Unknown extras still refuse;
worker decoder settings remain separately explicit. The corrected role-specific
metadata is recorded in the published75 manifest receipt. Gateway/request-contract
completion and runtime acceptance remain open. Packaging passed before the final
pure parser validation adjustment; syntax compilation passed afterward. No unit/
native/model tests or performance checks ran. All implementation-first and live
three-computer cluster deferrals remain in effect; qualification stays disabled.


Recipe75 final managed-launch completion: embedding/pooling extras are validated,
preserved and routed to the head only. Parser syntax compilation and final
packaging passed in artifacts/prima-embedding-output-pin75-package-final2.log.
The role-specific published help receipt passed after correcting the inventory;
CLI workers intentionally receive output selection through batch metadata.
No tests or performance checks ran. Source/native API implementation is distinct
from unfinished gateway/request-contract completion and unverified model/backend/
physical distributed acceptance. Full required integration is still unfinished;
all unit/native/model tests and test fixes remain deferred until implementation
finishes. Physical cluster/performance gates remain pending for the live
three-computer cluster, and qualification remains disabled.


Embedding gateway and role-binding implementation (recipe76, 2026-10-13):
Added authenticated POST /v1/embeddings to the agent endpoint/route registration,
with inference activity observation and the existing proxy transport/error path.
The gateway requires a unique served model ID, rejects a mismatched requested ID
and a Prima catalog inconsistent with the active deployment, and preserves native
input/encoding/normalization fields without applying chat generation defaults.
Prima request feature discovery now distinguishes embeddings and explicit float/
base64, dimensions and normalization scopes; the native output capability is
required in addition to execution qualification. No qualification is granted by
this wiring. Existing generation routes retain their defaults.
Native manifests parse the approved embedding/pooling extras with their actual
arity, compare head mode/selected pooling and require workers to retain their
non-server role. Unexpected environment/argv mode overrides refuse. Preparation
negotiates embedding_role_v1 before execution. Rank pooling without a loaded
classifier refuses during context initialization rather than graph assertion.
This does not provide missing classifier/model architectures or claim rerank parity.
Development production compilation passed before the final guard/capability
refinement; its final build is running in
artifacts/prima-embedding-gateway-development76-final.log. Python syntax checks
passed before the last source additions. Source/API/backend/physical acceptance
remains unverified. Broader original model/API/platform/accelerator/exact-placement
work and the integration goal remain unfinished.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and are unpassed.
Qualification remains disabled.


Recipe76 publication: PR #1/source pin is
7f6a1361805e0f7c8324970f4588e5250849661c; normalized recipe digest is
e4dff45bd2eb084fcc969c7ce1e16b1a76becd302bf40793528d78b879b50d20.
Development server/CLI/profiler compilation completed in
artifacts/prima-embedding-gateway-development76-final.log. Packaging and final
Python syntax checks passed in artifacts/prima-embedding-gateway-pin76-package.log.
Clean published compilation is running in
artifacts/prima-embedding-gateway-published76-build.log. Authenticated gateway,
feature/served-model identity checks and native role binding are implemented
source paths; no API/model/backend acceptance is implied. All unit/native/model
tests and test-fix work remain deferred until full required implementation is
finished. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the live three-computer cluster. Qualification stays false.
Broader original model/API/platform/accelerator/exact-placement work remains open.


Recipe76 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. Exact source/recipe identity, source checkout cleanliness,
three staged executable hashes and advertised embedding role/output capabilities
are recorded in artifacts/prima-embedding-gateway-published76-manifest-check.json.
The gateway/API acceptance fields remain false: compilation and metadata do not
prove request behavior, vector accuracy or backend execution. Final packaging and
syntax checks passed in artifacts/prima-embedding-gateway-pin76-package-final.log.
Prima planning recognizes embedding/pooling extras whose storage is already
reserved by the estimator; other unknown extra effects remain unpriced warnings.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Those tests remain deferred until full required
implementation finishes. Physical distributed, mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster
and remain unpassed. All qualification is disabled. Broader original model/API/
platform/accelerator/exact-placement integration remains unfinished; the thread
goal stays active.


Qwen2-MoE continuation (recipe77, 2026-10-13):
Published PR #1 source pin 8b5cbe40c94dbce05adc5b54aadc6a5230fc7992;
normalized recipe digest 2aef72f0dea3c6afd7ade92689baf8388a5557fe28ae1c33f6334e0deca0f728.
The loader now creates only absolute manifest-owned layers using local contexts;
the shared segmented graph handles repeated rank visits and zero-work coordinators.
Routed experts use unnormalized selected full-router softmax weights, while the
always-active shared expert uses SILU and a direct sigmoid gate. Expert widths
accept scalar/per-layer metadata, with bounded counts/widths validated on all ranks.
Typed profiling includes complete shared storage/arithmetic and selected routed
work. Kluster conservatively reserves simultaneously live routed/shared scratch.
Production server/CLI/profiler compilation and Python syntax/package checks passed.
A clean published Windows/Vulkan build is running in
artifacts/prima-qwen2moe-published77-build.log; its completion is recorded separately.
Source graph admission now covers seven architectures; registry coverage is still
54 of the baseline's 153, with 99 absent entries. Registration is not execution
or model/backend acceptance. Arbitrary remote expert/row/tensor placement and
broader original API/model/platform/accelerator integration remain open.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance acceptance
will be tested later on the user's live three-computer cluster and remain unpassed.
All qualification remains disabled; the integration goal is unfinished.


Recipe77 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with LLAMA_BUILD_TESTS=OFF. Exact clean source/recipe identity,
three staged executable hashes and qwen2moe_segmented_v1 capability metadata are
recorded in artifacts/prima-qwen2moe-published77-manifest-check.json. Kluster package
and syntax checks passed in artifacts/prima-qwen2moe-pin77-package.log. Capability
metadata and compilation are not model/API/backend/profiler acceptance; those
acceptance fields remain false. No unit/native/model tests, test fixes, inference,
profiling measurements or performance checks ran. Tests remain deferred until full
required implementation is complete. Physical distributed, mixed-platform/backend
and performance gates will be tested later on the live three-computer cluster.
Qualification remains disabled. T012-T022 and the broader integration remain open.


Phi-3 continuation (recipe78, 2026-10-13):
Published PR #1 source pin b30588af67f4e832dfb8d6d0894f386fc2884206;
recipe digest f55f488a8d2f6dfb4c004ea4b0bab676874ea12e9ad5ddd7ec19f71fa0fbc871.
Phi-3 now uses rank-owned absolute loading and the segmented graph, with fused
or separate QKV, fused SwiGLU, optional/tied output and long/short RoPE factors
on each owning rank's layer buffers. Sliding attention is disabled to match the
pinned baseline. Logical context slots now exclude the head's reserved sequence
ID, while KV/pooling capacity retains it, so rank roles select the same factor
regime. Managed preparation requires logical_context_sequences_v1. Typed profiling
prices fused FFN work and conservative factor copies; Kluster reserves fused FFN
scratch and factor storage with assigned layers. This is source/build evidence,
not Phi-3 model or distributed acceptance.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-phi3-published78-build.log; its completion is recorded separately.
The graph admission list is now eight architectures, while the registry remains
54 versus baseline 153, with 99 absent entries. T012-T022 and broader original
API/model/platform/accelerator/exact-placement implementation remain unfinished.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance acceptance
will be tested later on the user's live three-computer cluster and remain unpassed.
All qualification is disabled; the integration goal stays active.


Recipe78 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and advertised phi3_segmented_v1 plus
logical_context_sequences_v1 capabilities are recorded in
artifacts/prima-phi3-published78-manifest-check.json. Python syntax and Kluster
package checks passed. Phi-3, logical context slots, API/model/backend/profiler
acceptance remain unverified; capability metadata and compilation are not those
acceptance results. No unit/native/model tests, test fixes, inference, profiling
measurements or performance checks ran. Tests remain deferred until full required
implementation is complete. Physical distributed, mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification remains disabled. T012-T022 and the broader integration remain open.


Gemma2 continuation (recipe79, 2026-10-13):
Published PR #1 source pin 99e179f6417030eaaf598903ed3a27fd58389531;
recipe digest 57200028d193952401d6ffe3290dc6b46957c0a58792e0942686018d44bc56cc.
Gemma2 now uses absolute rank-owned loading, tied output duplication on the
assigned output backend, fused/separate QKV and the shared segmented graph.
It retains the pinned baseline's query/input scaling, post-attention/post-FFN
norms, GELU gated FFN and attention/output soft-capping. Model metadata validates
window/pattern/norm/rotary/soft-cap bounds, including default alternating SWA and
independent sliding RoPE. Compact allocation, owned masks, shifts, snapshots/
rewind/replay and native KV estimates now use its per-layer storage layout.
Typed profiling prices norms, GELU, query/input scaling and soft-capping work;
Kluster reserves simultaneous dense/sliding masks and default-window compact KV.
A source-edit compilation error while removing the obsolete graph was corrected.
Final development server/CLI/profiler compilation, Python syntax and Kluster
packaging passed. Clean published compilation is running in
artifacts/prima-gemma2-published79-build.log; completion is recorded separately.
The graph admission list is now nine architectures. Registry coverage is still
54 versus baseline 153 with 99 absent entries; registration/admission is not
model or backend acceptance. T012-T022 and broader original API/model/platform/
accelerator/exact-placement integration remain unfinished.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance acceptance
will be tested later on the user's live three-computer cluster and remain unpassed.
All qualification stays disabled; the integration goal stays active.


Recipe79 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and gemma2_segmented_v1/gemma2_compact_swa_v1
capability metadata are recorded in
artifacts/prima-gemma2-published79-manifest-check.json. Python syntax and Kluster
packaging passed. Gemma2 model/backend/distributed/profiler acceptance remains
unverified; compilation and capability metadata do not prove it. No unit/native/
model tests, test fixes, inference, profiling measurements or performance checks
ran. Tests remain deferred until full required implementation is complete.
Physical distributed, mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster. Qualification stays disabled;
T012-T022 and the broader original integration remain unfinished.


Gemma/Granite dense and LongRoPE continuation (recipe80, 2026-10-13):
Published PR #1 source pin c7bd885bec4265204702fb40ce5913d4d1152236;
recipe digest 3b08d376e088e85c44d28498083122ecb58390650cd88ae35c195e7de453f46a.
Gemma now uses absolute rank-owned loading, tied output duplication, fused/separate
QKV, input/query scaling and GELU in the shared segmented graph without Gemma2/3
post norms. Granite dense uses the same ownership path with its optional biases,
embedding/residual/logit/attention scale controls and rotary-disable handling in
both attention and KV shifts. Factor tensors remain on owning layer buffers;
LongRoPE metadata enum and CLI parsing are implemented rather than rejecting the
mode before Phi/Granite factor loading. Typed costs price Gemma scaling/GELU and
Granite scale/factor work, omitting disabled rotary arithmetic. Kluster recognizes
Granite dimensions and conservatively prices factor copies with assigned layers.
Granite shared/routed experts and vision/deepstack injection remain open; positive
deepstack mappings or dense-architecture expert models explicitly refuse until
those required paths exist. Complete Granite parity is not claimed.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-gemma-granite-published80-build.log; completion is recorded separately.
The graph admission list is eleven architectures; registration/admission does not
prove execution. Registry coverage is still 54 versus baseline 153 with 99 absent
entries. T012-T022 and broader original API/model/platform/accelerator/exact-placement
integration remain unfinished. No unit/native/model tests, test fixes, inference/
profiling measurements or performance checks ran. Tests remain deferred until full
required implementation finishes. Physical distributed, mixed-platform/backend
and performance gates will be tested later on the user's live three-computer
cluster and remain unpassed. Qualification stays disabled; the goal stays active.


Recipe80 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and gemma_segmented_v1, granite_dense_segmented_v1
and longrope_v1 capability metadata are recorded in
artifacts/prima-gemma-granite-published80-manifest-check.json. Python syntax and
Kluster packaging passed. Gemma, Granite dense and LongRoPE model/backend/profiler
acceptance remain unverified; source/build metadata does not establish them.
Granite MoE/shared experts and vision/deepstack integration remain open. No unit/
native/model tests, test fixes, inference, profiling measurements or performance
checks ran. Tests remain deferred until full required implementation is complete.
Physical distributed, mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster. Qualification stays disabled;
T012-T022 and the broader original integration remain unfinished.


Granite routed/shared expert continuation (recipe81, 2026-10-13):
Published PR #1 source pin 953b6296225be1513eead4a707beeb6913e7129f;
recipe digest e84b6912cb7442c5c9220e16392339b3426ed36d63c446f37856fbec5841fafa.
Granite/Granite-MoE now load routed and always-active shared FFNs only for absolute
manifest-owned layers, with per-layer widths, optional routed gates and required
up/down/router tensors. Ungated experts apply SILU to up and pass the activated
result to down without multiplying by up again. Selected full-router softmax
weights are normalized; shared FFNs add independently without Qwen2's sigmoid gate.
The segmented path preserves scales, optional biases, long/short factors and dense
Granite's rotary switch separately from Granite-MoE's always-rotary semantics.
Typed profiling includes full expert/shared storage, selected work, ungated
activation, router normalization and shared FFN work. Kluster reserves simultaneous
routed/shared scratch and factor copies, recognizes Granite-MoE/Phi3 dimensions,
and rejects nonpositive Granite expert widths. No new measurements are inferred.
Granite vision/deepstack and other Granite family architectures remain open, along
with original model/API/platform/accelerator/exact-placement integration.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-granite-moe-published81-build.log; completion is recorded separately.
Graph admission now covers twelve architectures. Registry coverage remains 54
versus baseline 153 with 99 absent entries; admission is not model/backend proof.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and remain unpassed.
Qualification stays disabled; T012-T022 and the integration goal remain unfinished.


Recipe81 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and granite_moe_segmented_v1,
granite_shared_expert_v1/optional_expert_gate_v1 capabilities are recorded in
artifacts/prima-granite-moe-published81-manifest-check.json. Python syntax and
Kluster packaging passed. Granite routed/shared model/backend/profiler acceptance
remains unverified; source/build metadata does not prove it. Vision/deepstack,
other architectures and broader original API/platform/accelerator/exact-placement
work remain open. No unit/native/model tests, test fixes, inference, profiling
measurements or performance checks ran. Tests remain deferred until full required
implementation is complete. Physical distributed, mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification stays disabled; T012-T022 and the integration goal remain unfinished.


Granite SWA continuation (recipe82, 2026-10-13):
Published PR #1 source pin 731eb6103775ecb1b3f9736ed016a08b9beba33b;
recipe digest 66491d988b867d0a3047e69a9b08f05fb30f09053357fb6e9c9fd213733e5d8e.
Added the granite_swa registry/tensor roles, bounded per-layer selected-expert,
sliding and optional RoPE patterns, rank-owned loading, sinks, fused/separate
routed gate/up projections and fused shared FFNs. The segmented graph uses actual
per-layer expert counts and the baseline's global RoPE frequency, respects disabled
rotary layers in attention/shifts and integrates compact KV allocation/masks/
snapshots/replay/estimates. Fused routed outputs are sliced with token/expert strides
before activation. Typed costs count fused storage and selected arithmetic once;
ambiguous fused/separate roles refuse. Kluster recognizes fused expert weights,
prices simultaneous branch/slice scratch and both masks, validates patterns/counts
and reserves compact KV. MoE classification now includes Granite-MoE/Granite SWA.
Source inspection corrected the initial Switch assumption: Granite Switch has
stacked token-selected LoRA plus a persistent attention router, not these fused
expert semantics. Switch, Hybrid, vision/deepstack and broader original model/API/
platform/accelerator/exact-placement work remain open.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-granite-swa-published82-build.log; completion is recorded separately.
The graph admission list is thirteen architectures. Raw registry entries are 55
versus baseline 153 (both include their unknown placeholder); 98 named baseline
architectures remain absent. Registration/admission is not model/backend proof.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and remain unpassed.
Qualification stays disabled; T012-T022 and the integration goal remain unfinished.


Recipe82 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and granite_swa_segmented_v1,
granite_swa_compact_v1/fused_expert_projection_v1 capabilities are recorded in
artifacts/prima-granite-swa-published82-manifest-check.json. Python syntax and
Kluster packaging passed. Granite SWA/fused projections/compact storage model,
backend and profiler acceptance remain unverified. Source/build metadata does
not prove them. Switch, Hybrid, vision/deepstack, other architectures and broader
original API/platform/accelerator/exact-placement work remain open. No unit/native/
model tests, test fixes, inference, profiling measurements or performance checks
ran. Tests remain deferred until full required implementation is complete.
Physical distributed, mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster. Qualification stays disabled;
T012-T022 and the integration goal remain unfinished.


OLMo2 continuation (recipe83, 2026-10-13):
Published PR #1 source pin 483a90fda2d8ab08c976fb2392a3dafe294f5139;
recipe digest f2c5ffbee2d2cb7484f5fd79eae84736c8c9ac018a33bd2eb760c1f75c4dbb59.
Added olmo2 architecture/roles, validated projection/norm/window dimensions and
rank-owned loading with required output weights. The shared segmented graph
normalizes projected Q/K columns before head reshaping, omits attention/FFN
pre-norms and retains post-attention/FFN norms with SILU gating. Optional sliding
layers use the four-layer default pattern and plain rotary settings (scale 1,
extrapolation 0, attention factor 1) in attention and KV shifts. Compact allocation,
owned masks, snapshots/replay and native estimates include the architecture.
Typed profiler costs distinguish projected columns from per-head normalization;
Kluster recognizes OLMo2 dimensions and prices its sliding pattern, compact KV
and simultaneous masks. This is implemented source, not model/backend proof.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-olmo2-published83-build.log; completion is recorded separately.
Graph admission now covers fourteen architectures. Raw registry entries are 56
versus baseline 153 (both include their unknown placeholder); 97 named baseline
architectures remain absent. Original model/API/platform/accelerator/exact-placement
work remains unfinished, including Switch/Hybrid/vision and remote expert/row/tensor
placement. No unit/native/model tests, test fixes, inference/profiling measurements
or performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and remain unpassed.
Qualification stays disabled; T012-T022 and the integration goal remain unfinished.


Recipe83 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and olmo2_segmented_v1/olmo2_compact_swa_v1
capability metadata are recorded in artifacts/prima-olmo2-published83-manifest-check.json.
Python syntax and Kluster packaging passed. OLMo2 model/backend/compact/profiler
acceptance remains unverified; source/build metadata does not prove it. Other
architectures and broader original API/platform/accelerator/exact-placement work
remain open. No unit/native/model tests, test fixes, inference, profiling measurements
or performance checks ran. Tests remain deferred until full required implementation
is complete. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the user's live three-computer cluster. Qualification stays
disabled; T012-T022 and the integration goal remain unfinished.


OLMo/OLMoE continuation (recipe84, 2026-10-13):
Published PR #1 source pin d69be68a68694982d086f5ebb037d7953c59d513;
recipe digest 14d342f33f5b6def3159d5a6033e626bfbb8c90981a5fcf80c6c6f16b5ed8c95.
OLMo and OLMoE now load only absolute manifest-owned layers and use the shared
segmented graph. OLMo retains unweighted LayerNorm, optional projection clamping
and optional/tied output weights. OLMoE retains learned pre-norms, projected-column
Q/K RMS normalization, required output/gated experts and unnormalized selected
full-router probabilities. Metadata validates projection/norm/expert bounds;
OLMoE's key normalization dimensions match the baseline's full-column weights.
Typed profiler costs price unweighted norms/clamping and selected expert work.
Kluster recognizes both architectures, classifies OLMoE as MoE and reserves its
routed scratch using per-expert feed-forward widths. The obsolete all-layer
loaders/graphs were removed; OLMo2's distinct post-norm/sliding path is preserved.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-olmo-family-published84-build.log; completion is recorded separately.
Graph admission is now sixteen architectures. Raw registry entries remain 56
versus baseline 153 (both include their unknown placeholder), with 97 named
architectures absent. Registration/admission is not model/backend proof. Original
model/API/platform/accelerator/exact-placement work, including Switch/Hybrid/vision
and remote expert/row/tensor placement, remains unfinished. No unit/native/model
tests, test fixes, inference/profiling measurements or performance checks ran.
Tests remain deferred until full required implementation finishes. Physical
distributed, mixed-platform/backend and performance gates will be tested later on
the user's live three-computer cluster and remain unpassed. Qualification stays
disabled; T012-T022 and the integration goal remain unfinished.


Recipe84 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and olmo_segmented_v1/olmoe_segmented_v1
capability metadata are recorded in artifacts/prima-olmo-family-published84-manifest-check.json.
Python syntax and Kluster packaging passed. OLMo/OLMoE model/backend/profiler
acceptance remains unverified; source/build metadata does not prove it. Other
architectures and broader original API/platform/accelerator/exact-placement work
remain open. No unit/native/model tests, test fixes, inference, profiling measurements
or performance checks ran. Tests remain deferred until full required implementation
is complete. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the user's live three-computer cluster. Qualification stays
disabled; T012-T022 and the integration goal remain unfinished.


Recipe85 OpenELM implementation progress:# Implementation tasks

Current validation schedule: finish the full required implementation before
running any unit/native/model tests or starting test-fix work. Production builds
and source/artifact identity checks may continue. Historical test results below
do not qualify the current source. Physical cluster and performance acceptance
remain deferred to the user's live three-computer cluster and unpassed.

Physical distributed cluster gates and validations will be tested later, as directed
by the user on 2026-10-11. T019 and the physical distributed portions of T018/T022
are deferred acceptance work, not passed checks. Distributed qualification stays
false until those follow-up tests are executed and recorded. Local implementation
and correctness coverage continue. On 2026-10-12, the user also deferred performance
acceptance to their live three-computer cluster; no further local performance-gate
validation is required here.

- [x] T001 Preserve working changes and merge origin/main into feat/prima-integration.
- [x] T002 Prepare feature context from current governance and memory fallback.
- [x] T003 Add engine/recipe provenance while preserving existing llama identities and wire payloads.
- [x] T004 Add pinned dependency/patch source builds through the managed installer.
- [x] T005 Build and verify server/CLI/profile artifacts on macOS CPU in isolation.
- [x] T006 Add exact rank/layer/ngl/device contracts, network-edge derivation and topology preview.
- [x] T007 Add resumable content-addressed staging primitive with integrity/space/cancellation checks.
- [x] T008 Add local profiling jobs, owned-process cancellation, logs and bounded profile cache.
- [x] T009 Add conservative profiled candidate comparison and paired performance evaluator.
- [x] T010 Add dashboard engine source picker and enforce inference qualification gates.
- [x] T011 Add regression/process tests and document the preparation interfaces.
- [ ] T012 Implement Windows portability and qualify all baseline accelerator contracts;
      Vulkan GPU execution is mandatory across Windows/Linux/macOS, including MoltenVK.
- [ ] T013 Implement all baseline server APIs, launch settings, templates and model architectures in the Prima fork.
- [ ] T014 Support zero-work coordinator and arbitrary approved layer/MoE/row/tensor placement in the runtime.
- [ ] T015 Connect staged assets to authenticated remote transfers and companion/shard manifests.
- [ ] T016 Implement and wire signed rank commands, private network admission, native launch,
      distributed readiness, leases, monitor/stop/recovery and persistent cleanup.
- [ ] T017 Connect remote profiling and feasible candidate generation to the authoritative planner;
      include measured network/disk costs and resource reservation against concurrent launches.
- [ ] T018 Add complete engine API parity and distributed real-process tests.
- [ ] T019 Qualify physical Windows/Linux/macOS/mixed-backend clusters and execute paired benchmarks.
- [ ] T020 Release optional selection only after all qualification gates pass.
- [ ] T021 Implement Vulkan-specific profiling and scheduler inputs in the Prima fork.
- [ ] T022 Verify selected Vulkan devices actually execute planned GPU layers and
      distributed generation; benchmark all-Vulkan and mixed-backend clusters without
      CPU/Metal/CUDA substitution. CPU build success cannot close these tasks.

Incomplete tasks are intentionally open. The current preparation implementation
must not be described as a working drop-in distributed inference engine.

## Published manifest and assessment foundation

- [x] T023 Publish native manifest checking, disable autonomous scheduling in explicit
      mode, validate SHA-256 contract vectors, and add portable build CI.
- [x] T024 Add typed execution/assets contracts, signed operation-bound resumable asset
      pushes, master-owned preparation resources, and preparation CLI/runtime UI.
- [x] T025 Add controlled process ownership/readiness/expiry checks and the standalone
      evidence-only Windows assessment runner.

T012–T022 remain open: this foundation does not complete baseline backports,
arbitrary native placement, production engine/network dispatch, remote end-to-end
profiling/planner integration, or physical distributed/Vulkan qualification.

## Windows/Vulkan continuation (2026-10-11)

- [x] T026 Publish Qwen3 dense/MoE loading, completed Vulkan execution receipts,
      stable device identities and Windows/Vulkan contract CI in the Prima fork.
- [x] T027 Bind selected-device assessment and profiling to physical identities;
      validate prefill/decode attention and dense/expert matrix ownership.
- [x] T028 Separate immutable build identity from source-controlled local/distributed
      qualification policy; refuse combinations outside the recorded scope.
- [x] T029 Add guarded local run dispatch through verified assets and owned rank
      readiness/leases/cleanup, and explicitly refuse unsupported native settings.
- [x] T030 Add and install the canonical kllm-prima cross-repository development skill.
- [x] T031 Resolve Qwen3 MoE semantic mismatch against the pinned baseline.
- [ ] T032 Complete gateway lifecycle/API correctness coverage; defer performance
      acceptance to the user's live three-computer cluster. Keep qualification
      disabled until the required evidence is recorded.

Published fork commit: `91e586420eb357f45d80e33be0b31908fe4bc070`.
Portable and Windows/Vulkan CI run `38123362573` passed. Physical local receipts
prove dense and MoE GPU matrix execution; dense sampled API cases pass, while MoE
chat correctness fails. These observations do not close T018–T022 or T032.

T031 follow-up: commit `e4eb58b46cf2c7f2f320287d51ee26eb09070826` fixes the absent-mask
Vulkan softmax defect. Explicit Vulkan operation tests pass and fresh recipe 9
physical assessments pass sampled dense/MoE APIs and completed GPU execution.
T032 remains open: the dense paired performance matrix fails token-count matching
and decode throughput; full gateway/lifecycle acceptance still needs execution.

## Native protocols and generation profiling

- [x] T033 Publish native stateless text Responses/Messages/input-token backports;
      validate streaming, usage, forced tools, limits and malformed requests on both
      supplied models through the fresh published Windows/Vulkan build.
- [x] T034 Add model-backed CPU/Vulkan generation profiling, selected-device execution
      proof, owned cancellation/readiness and exact full-model score boundaries;
      execute real local profile jobs on the dense and MoE models.
- [x] T035 Connect cached generation measurements to normal plan/run previews,
      retaining capacity-first results when complete matching evidence is missing.

T033 pins `f221716d9b45077a906621ca7e10d8b4ada485ed` (recipe 10). This is a tested
protocol subset, not completion of every T013 API/model/template feature. T034 does
not qualify performance or distributed planner estimates. Both dense and MoE
alternating paired matrices currently fail local acceptance.

## Normal gateway and measured planning continuation

- [x] T036 Publish Vulkan memory discovery, explicit output projection placement,
      and completed batch-wide graph receipts; validate native placement contracts.
- [x] T037 Execute the expanded published-build gateway/profile/lifecycle suite on
      both supplied models, including disconnect cancellation, queued concurrent
      requests, crash cleanup/restart and exact output projection ownership.

Recipe 11 pins `720da841239bc2ffef55b1fe08895562ade30bef`. Fresh managed Windows/
Vulkan build and CI38131219709 pass. The dense published-build gateway assessment
passes all 27 checks, including real model-backed profiling and normal planner
cache use. The profile API and preparation/planner share one registry-owned service
and cache, with common startup reservations. Model/device/driver/settings/full-model
offload and output projection placement must match; incomplete candidate evidence
retains capacity-first planning. Free-memory counters do not change physical inventory
identity. Both dense and MoE recipe11 assessments now pass all 27 checks. The monitor
retains failed cleanup ownership and retries despite the cancellation flag; an
uninitialized Prima service no longer terminates the expiry task. T032 remains open for complete local
API/features/placement and paired performance acceptance. Physical distributed checks
remain deferred, not passed.

- [x] T038 Consume the published telemetry opt-in correction through recipe12,
      verify disabled tracing in paired timing runs, and repeat affected acceptance.

Recipe12 pins `5e3e2e13a4b0779d22b418d867088f86d8a3f8f1`. Native contract checks
pass; fresh managed build/package and CI pass. This change prevents the string
`0` from enabling GPU execution tracing. The paired benchmark now refuses logs
containing execution receipts. Recipe11 results remain historical evidence; no
qualification policy is enabled from them.

T038 follow-up: recipe12 build/package/CI and sampled native protocols/GPU execution
pass on both models. Both five-pair timing matrices verify disabled tracing and
still fail token-count matching and throughput. T032 remains open; these are executed
failures, not successful performance acceptance.

- [ ] T039 Resolve chat grammar/token-generation differences and local Vulkan decode
      overhead identified by identical-token CPU/Vulkan diagnostics; repeat strict
      gateway/performance acceptance on the final published source.

T039 continuation: recipe14 pins published `501585d9116457f998d5372341c49b1ad093e6c3`.
Recipe13 connected the single-rank input/transformer/output graph; recipe14 preserves
actual generated counts while parsing optional JSON code fences from chat content,
including streaming. Native contracts and both development model protocol assessments
pass. The development dense five-pair matrix now matches bytes and generated counts
but still fails throughput (ratios approximately 0.90/0.39/0.84/0.29). No thresholds
were relaxed. Recipe14's fresh managed Windows/Vulkan build, package/native import
and both portable CI runs pass. The final published dense --profile gateway assessment
passes all 27 checks, as does the equivalent MoE assessment. A developmental
small-batch Vulkan vector-kernel extension is under test and is not yet published.

T012–T022, T032 and T039 remain open. Physical distributed cluster gates and
validations will be tested later; implementation and local acceptance remain required.

- [x] T040 Publish small-batch Vulkan vector dispatch, short/odd-row bounds and
      strided F32-to-F16 matrix pipeline corrections, with physical operation and
      supplied-model regressions; consume the immutable source through recipe15.

T040 pins `19adfe0800130e829544719136550257b21fd9f6`. Physical development tests
compare 162 supported MUL_MAT and 185 supported MUL_MAT_ID cases to CPU; unsupported
cases remain separate. Both native contracts and dense/MoE protocol assessments
pass. The dense five-pair development matrix preserves output bytes/generated counts
and improves short concurrency2 throughput from approximately0.385 to0.932 and long
concurrency2 from0.287 to0.533; all workloads still fail the0.95 throughput gate.
Fresh recipe15 managed build/package/native import and both portable CI runs pass.
The published dense --profile gateway rerun passes27 checks after retaining the
stream iterator through cancellation observation; the initial harness failure remains
recorded. T032/T039 remain open for final model/performance acceptance and further
performance work. A second developmental extension uses existing contiguous copies
for floating-point views before small-batch vector dispatch; operation checks pass
and its first dense paired benchmark improves short2 to0.97 and long2 to0.75,
preserving bytes/counts but still failing overall. After restoring the prior unroll
hint, final paired timing is in progress. It is not yet published. Published recipe15
dense and MoE --profile gateway suites each pass27 checks.

T016 implementation follow-up: compose the coordinator into production dispatch;
connect private admission and signed start/status/renew/stop, bind worker ownership
to sender/authority/session/run, and implement worker acknowledgement plus master
HTTP readiness before the serving barrier. The existing signed start handler does
not automatically promote workers from starting to ready. Add controlled transport/
process regression coverage; physical multi-node generation and performance are
still scheduled for later testing, as requested.

- [x] T041 Publish and consume Vulkan tensor-view stride/dispatch correctness fixes,
      with reproduced failures and CPU-reference regression coverage.

Recipe16 pins published `a024a7edc21d50c558f608878b7bb085bfde8aae`. New batch-padding/
attention-permutation cases reproduce25 GPU failures before the fix and pass after
it. Supported physical matrix comparisons194 and expert comparisons185 pass, as do
native contracts and dense/MoE development protocols. Strided quantized views remain
explicitly unsupported. Fresh managed build/CI and final published acceptance are
pending; T032/T039 remain open.

Recipe16 follow-up: fresh managed build/package/native import, clean source/artifact
verification and both portable CI runs pass. Final published gateway and paired
acceptance remain pending. The full Python run has one SQLite child-start timeout;
the unchanged isolated test passes after stopping its verified orphan, and a full
suite rerun without compilation is in progress.

The full quiet Python rerun passes1271/19skipped. Published recipe16 dense --profile
gateway passes27 checks; MoE equivalent and final paired acceptance are in progress.

Published recipe16 MoE --profile gateway also passes27 checks. Both supplied models
pass the published gateway/profile/lifecycle subset; fresh published dense paired
timing is running, with MoE paired timing next. Full API/placement/distributed
implementation and performance remain open; physical distributed validations later.

Published16 dense five-pair benchmark executes with matching bytes/generated counts
but still fails overall throughput: short1/short2/long1/long2 approximately
0.93/0.98/0.85/0.73. Only short2 passes. The published16 MoE paired matrix is running.
A developmental four-row vector kernel and large-projection row-tail regressions
are being prepared; they are not yet built, tested or published. T032/T039 stay open.

- [x] T042 Publish the measured quantized-loop improvement and portable vector-grid
      coverage contracts, keeping rejected performance experiments out of the source.

Recipe17 pins `622d5c4f9fdc1fa399c3f20e5632a67a2b63fdca`. The four-row workgroup
experiment regressed and was withdrawn. The retained four-value quantized iteration
passes198 matrix/185 expert comparisons, both native contracts and both development
model protocols. Dense development throughput is approximately0.95/1.00/0.88/0.75
for short1/short2/long1/long2, with matching bytes/counts; overall still fails. CPU
grid contracts cover bounded/invalid limits. Large physical tail cases pass on both
old/new backends because this GPU's X limit is4294967295, so no physical truncation
failure is claimed. Fresh published17 build/CI/affected acceptance remain pending.

Published16 MoE paired timing also completes and fails throughput plus long2 generated
counts. The numerical/template/grammar diagnosis and full T032/T039 acceptance remain
open. Physical distributed gates and validations will be tested later.

Recipe17 fresh managed build/package/native import, clean source/artifact capture
and both CI runs pass. Focused Kluster tests142passed/4skipped. Final published17
gateway/performance checks remain required. The identical-token diagnostic now
supports CPU-only runs, two concurrent requests and repeated rounds with per-request
token/logprob receipts; its dense CPU2 smoke run passes and MoE CPU2 diagnosis is running.

T016 worker-launch correction: CLI ranks no longer receive the rank-zero HTTP alias;
all shared decoder settings, including parallel sequences, are retained. Focused
tests142passed/4skipped and actual published CLI help-based translation pass. This
does not complete the distributed coordinator/readiness/admission wiring.

## Recipe 18: chat token logprobs

- [x] T043 Publish OpenAI choice and streaming token logprobs, with raw-logit
      probabilities, UTF-8 byte preservation and bounded request validation.

Published and remote-verified source `83555d7b5a65a2dd84d3e49f27c237cabb98bbfd`,
recipe digest `3557aa2522b2dfc51d71514c7b479a3a3a6b7c5749cea888c5e31dd9fe074e90`.
Both native CTest contracts pass. Eight new model-backed native logprob cases fail
before the change and pass after it on both supplied models. Development gateway
`--profile --logprobs` assessments pass all 35 checks for dense and MoE. Fresh
published recipe18 build and CI are running; published-build acceptance remains
required. T013/T032/T039 remain open; this feature does not complete API parity.

The three-round MoE CPU-only concurrent diagnostic completes and reproduces chat
token-count variation (71/72/134 for long requests), while returned text agrees.
The defect is therefore not exclusive to Vulkan. Proper native logprobs now allow
comparison of actual generated token pieces and pre-sampling candidate margins.
Qualification remains false, and physical distributed validations will be tested later.

Recipe18 fresh managed build/package/native import, source/artifact capture and both
CI runs now pass. Both published --profile --logprobs gateway suites pass35 checks.
Local paired performance and the remaining broad compatibility requirements remain open.

- [x] T044 Persist signed rank sender/authority ownership and require a complete,
      current preparation from that authority before remote native startup.

Real-child and real signed-WebSocket regressions pass, including unapproved cache
refusal, approved start, run/authority mismatch refusal and cleanup. T016 remains
open for native worker readiness, coordinator composition, private-network admission,
monitoring/recovery and persistent distributed cleanup. Physical distributed tests later.

The published18 three-round CPU2 MoE diagnosis also reproduces long-chat counts
134/71,71/72,71/72, with each native logprob entry count matching actual usage.
Distributions vary before token choices diverge, so T039 now includes batch/sequence
state and numerical inspection alongside grammar diagnosis. This is not performance
acceptance and does not close T032/T039.

- [x] T045 Publish precision-preserving Vulkan matrix execution and F32 attention
      requests, with finite-range GPU regressions and shared-memory limit contracts.

Published/remote-verified `146ccb624ad86ed7bdcd84e0bbeedd4b20b1b80e`, recipe19 digest
`fa9bd217ff771acd3db395effe645c3e9f34d24e772a8b69f0a588d1520f92d0`.
Fifteen finite-range infinity failures reproduce before the change. Final physical
checks pass237 supported matrix comparisons (including39 finite-range cases),185
expert comparisons and both native contracts. Shared-memory contracts cover16/32/
64KiB limits. Both development model protocol suites, including logprobs, pass.
Dense five-pair development timing matches bytes/counts but still fails overall:
throughput ratios approximately1.12/1.05/0.92/0.83 for short1/short2/long1/long2.
MoE24-layer paired timing is running. Fresh recipe19 build/CI and published-model
acceptance remain required; T012/T013/T032/T039 stay open. Physical distributed
cluster gates and validations will be tested later.

Recipe 19 follow-up: both CI runs passed. The fresh managed Windows/Vulkan build,
supported package rebuild/native import, and clean source/artifact capture passed.
The published dense gateway suite passed all 35 checks. MoE published acceptance is
running. Development MoE five-pair timing matched bytes and generated counts across
every workload, including all long/concurrency2 requests, but failed throughput at
approximately 0.83/0.54/0.84/0.54. This corrects the tested matrix's count mismatch;
it does not resolve the separate CPU-only variation or pass local performance.

Published recipe 19 MoE gateway acceptance also passed all 35 checks. Both models'
published gateway/profile/lifecycle subset passes. Strict paired published-build
timing is running; T012–T022, T032 and T039 remain open for their broader requirements.

Final recipe 19 published paired matrices completed, with matching bytes and actual
generated counts throughout. Dense ratios 0.97/1.01/0.89/0.77 pass only the short
workloads; MoE 0.82/0.55/0.84/0.54 fails all throughput gates. Every MoE long2 pair
now generates 71/71. The sampled count mismatch is corrected, while CPU-only
variation and full performance acceptance remain open. All qualification stays false.

- [x] T046 Publish direct small-column strided Vulkan attention with paired query
      reads, explicit offsets/strides and focused operation tests.
- [x] T047 Bind local qualification to the exact measured launch settings and rank
      placement, including offload/output head, context, batching, cache and threads;
      align normal gateway acceptance with the benchmark configuration before release.

T046 source `857833e2102e8cf1402afe3442cf1ef616dadc41` is published and remote-verified.
Recipe20 digest `7f1d59d770b3b04dec598514e04bccedca77763bc8225bb41fe14840ba1e7b22`.
Physical checks pass36 attention,273 total matrix and185 expert comparisons, both
contracts and both development protocols. Dense five-pair development performance
passes all four workloads with matching bytes/counts (ratios1.16/1.07/1.01/1.01).
MoE development timing and CI completed; fresh recipe20 build/acceptance follow-ups
are recorded below. The release audit found missing effective-setting bindings and
gateway native defaults2048/512 versus benchmark512/128. T047 closes that gap while
all production gates remain false.

T047 follow-up: exact receipt-backed execution scopes now bind launch settings,
model/companion names/hashes, rank ranges, GPU/output placement, device/driver/capacity,
CPU/core/RAM/OS identity and per-preset request features. Context persists in active
state and is rechecked for serving. Final published dense/MoE one-slot gateway suites
pass38 checks each; final dense two-slot suite passes36. Full Python1293/19skipped,
focused Windows48passed, Ruff pass. Normal gateway and timing explicitly use512/128
batching and8 generation/batch threads. No production scope is enabled.

- [x] T048 Repeat published acceptance with verified matching baseline placement.
      The pinned 7fe450e baseline counts output in every positive GPU-layer count;
      older MoE timing compared different transformer/output layouts. Retain those
      receipts as historical observations, not matched-performance evidence. Use
      corrected counting, explicit/tied CPU output overrides and a long-prefill/decode
      matrix-placement probe before timing. Normal MoE planning currently uses33
      transformer GPU layers with GPU output, benchmarked separately below.

The new dense14-layer/CPU-output physical placement probe passes both engines. A
temporary unsupported --no-warmup argument was removed from the Prima probe; a tied
embedding output override was added after the baseline trace showed GPU projection.
These initial harness failures are retained separately. Native manifest placement
passes the long-prefill test; no native placement defect is inferred from scheduler
heuristics alone. MoE two-slot gateway and corrected paired matrices completed below.
Physical distributed cluster gates and validations will be tested later.

T048 execution is complete: corrected published five-pair matrices and their long
placement prerequisites ran for dense28/GPU-output and normal MoE33/GPU-output.
Dense throughput0.921/1.030/0.879/1.009 passes only concurrency2. MoE throughput
0.728/0.409/0.745/0.495 fails all workloads, with long1/2 actual generated-count
mismatches despite equal output bytes. This task records executed comparisons;
T032/T039 still require passing correctness/performance acceptance, and all gates
remain false. The initial scheduler-debug abort is retained separately.

- [x] T049 Bind generation profile cache identity to fixed probe batch/micro-batch
      and batch threads; refuse reuse for different effective batch-thread counts.

T049 reproduced a false profiled result for threads_batch4 against an8-thread probe.
The regression now passes; profiling explicitly launches and records batch threads,
and the expanded identity leaves older cache records available but unmatched. Focused
checks103passed/3skipped and fresh dense --profile --logprobs gateway38checks pass.
Final full Python1294passed/19skipped43.66s; Ruff and whitespace checks pass.
Remote profiling/planner integration in T017 remains required. Distributed physical
cluster gates and validations will be tested later.

- [x] T050 Consume published post-initialization rank readiness and zero-transformer
      coordinator fixes through recipe21; validate fresh managed gateway and
      controlled same-host CPU/Vulkan rank regressions before closing this task.
- [x] T051 Retry cancelled coordinator cleanup and persist cleanup-pending state
      until both remote stops and private admission release succeed.

Native `f11a4a20e7f34e2a67a6985164ac0d5c6c9b30e3` is published and remote-verified.
Recipe21 digest `6750661c6db4f05edf036ff7f384285db8a426400b37630293ea048d760c8801`.
CLI workers now apply rank/world before their startup assertion; native post-init
readiness binds runtime, manifest, rank, layer count and GPU count. A zero-transformer
coordinator no longer writes unallocated transformer inputs during warm-up. Four
development same-host CPU/Vulkan normal/zero-master cases pass generation and exact
GPU proofs where requested; these are controlled regressions, not physical cluster
qualification. Both native contracts, fresh managed build/package/import/capture,
both portable CI runs and focused Kluster171/3skipped pass. Fresh published gateway
and same-host rank acceptance remain in progress.

Signed worker status promotes only matching owned post-init reports. Configured,
partial, malformed, duplicate, wrong-stage/allocation/identity reports are refused;
dead PIDs never remain ready. Rank zero requires native readiness plus HTTP health.
Two coordinator cleanup failures were reproduced before T051 and now pass. Master
coordinator composition, admission, recovery and persistent distributed ownership
remain required in T016; arbitrary placements and broad compatibility remain open.
Physical distributed cluster gates and validations will be tested later.

T050 published acceptance: fresh dense and MoE --profile --logprobs gateway suites
each pass38 checks through native readiness plus HTTP health. All four controlled
same-host published CPU/Vulkan normal/zero-master cases pass readiness and generation;
Vulkan cases also pass exact completed matrix ownership proofs. Receipts use
prima-readiness-published-{cpu,vulkan}-{normal,zero}.json. Final full Python passes
1299/19skipped53.15s, with Ruff and whitespace checks passing. T014/T016 remain open for arbitrary placement, production composition,
private admission/recovery and persistent distributed lifecycle. Physical multi-machine
validation and performance will be tested later; no gate is enabled from these results.

- [x] T052 Persist coordinator launch intents, exact per-rank readiness and recovery
      attempts; restore previous runs for cleanup, fence concurrent cancellation,
      and exercise restart cleanup against owned real child processes.

T052 records every start intent before its signed command can be sent, retains
pending ranks after lost acknowledgements, and narrows ownership after each stop
acknowledgement. Restart never resumes inference: legacy records conservatively
retain all participants and current records preserve their exact pending set and
command authority. Recovery has a three-attempt budget; cancellation, changed
authority/inventory or failed cleanup/persistence cannot restart a run. Exact rank,
run/runtime, layer/GPU counts, backend and command owner are checked at readiness
and renewal. Ten regression failures were reproduced before their corrections;
focused Python135passed/3skipped and Ruff/whitespace checks pass. The real-child
restart test preserves an unrelated process. Full Python validation passes
1326/19skipped49.83s, with4 existing Starlette deprecation warnings.

T016 remains open for production coordinator dispatch, Prima-specific private
network admission, remote startup/HTTP barrier composition and end-to-end recovery.
Physical distributed cluster gates and validations will be tested later. T052
does not enable qualification or change the published native recipe21 source.

- [x] T053 Compose prepared distributed dispatch and Prima-specific private-network
      reservations with signed rank control; validate controlled three-agent
      lifecycle and affected published local gateways before closing this scope.

Production Run/Stop now dispatches prepared multi-rank plans through the owned
coordinator. Prima reservations bind immutable manifest/authority/assignment/edge
identity and use signed private diagnostics without launching RPC children/relays.
RPC release cannot discard Prima ownership, and RPC startup refuses an owned Prima
rank. Worker status/renewal recheck its prepared private assignment and owned
manifest. Restart instantiates durable Prima owners for cleanup; changed master
authority can settle an old rank only through a fresh signed idle proof.

Controlled three-agent transport/process coverage passes asset transfer, worker-first
startup, native-acknowledgement plus master HTTP readiness, cancellation during
validation/loading, stale preparation refusal, private-owner invalidation, complete
Stop and one owned restart after a worker crash. Adapter mutation, model execution
and runtime capture are fixtures; signed channels, diagnostic sockets, child
processes, HTTP health and persistence are real. Focused175passed/3skipped;
full Python1334passed/19skipped50.19s. Both affected published local gateway
reassessments pass38 checks, with final follow-up checks recorded below.

T053 local follow-up: published dense and MoE gateway/profile/lifecycle suites each
pass38 checks after retaining the dispatcher's active-manifest observation interface.
The initial dense assessment failure remains recorded separately. A controlled
post-readiness trace-volume regression is reproduced and fixed: serving uses its
persisted readiness acknowledgement plus PID/start-token and HTTP health, while
startup retains its bounded log check. Final focused175passed/3skipped14.08s and
full Python1334passed/19skipped46.21s pass; Ruff and whitespace checks pass.

T016 stays open for complete signed participant hardware/qualification context,
native rank-port admission and final end-to-end qualification/recovery acceptance.
T012–T022/T032/T039 retain their wider scope. Physical distributed cluster gates
and validations will be tested later; all production qualification remains false.

- [x] T054 Bind prepared distributed manifests and execution qualification to
      signed per-rank host/device/build observations, revalidate before startup
      and renewal, and validate metadata through the published native rank runner.

Signed read-only prima_inventory now reports node-local OS/CPU/core/RAM,
immutable source/recipe/build/artifact identity and selected Vulkan UUID/driver/
capacity/ordinal. Preparation binds the exact rank allocation and complete observed
participant set before asset transfer; fresh signed observations fence changed
hardware/builds before any process starts. Native ownership startup and worker
status/renewal recheck current evidence. The distributed execution scope now uses
all observed hosts/devices instead of unknown remote platform placeholders; an
explicit zero-work CPU coordinator can coexist with qualified Vulkan workers.
Observed hardware alone cannot qualify inference or enable a policy gate.

Empty evidence is omitted during serialization so legacy/local manifest canonical
bytes and digests remain unchanged. Controlled signed three-agent tests pass,
focused159/3skipped and final full Python1348/19skipped45.71s pass. Fresh published CPU/
Vulkan normal/zero-master native cases including evidence all pass. Native rank
port admission, remaining compatibility/placement/planner/performance and final
acceptance stay open; physical distributed cluster validations will be tested later.

Workers advertise prima_inventory_version1 separately from the existing control
capability. Older workers are refused with an upgrade-required preparation error
before unsupported signed inventory commands are sent. The immutable native source
and build recipe21 remain unchanged; no production qualification is enabled.

## Native listener admission foundation

- [x] T055 Add exact manifest-derived inbound listener contracts, signed worker
      admission/cleanup handlers and a privileged Windows helper ownership ledger.
      Validate authority/assignment/replay fences and durable partial cleanup with
      isolated Python/Rust checks and a shared signed-wire vector.
- [ ] T056 Compose native listener admission into the production coordinator before
      any rank starts; journal every attempted participant, release only after owned
      rank stops, recheck/renew admission through serving, and reconcile expiry/restart.
      Add worker capability negotiation, administrator/direct-adapter and Linux/macOS
      backends, native-helper IPC/process regression coverage, and physical OS acceptance.
- [x] T057 Wire all-node listener admission into prepared distributed Run, persist
      attempted/admitted participants, negotiate signed helper capability and enforce
      node-local pre-spawn/running leases with owned stop-before-release. Validate
      lost replies, cleanup retry, expiry/reconstruction and real signed three-agent
      process composition without physical firewall/model claims.
- [x] T058 Add an independent privileged-helper rule watchdog and signed renewal,
      require agent/helper port capability2 and verify timer expiry, non-revival,
      current/changed ownership and signed three-agent renewal with controlled rules.
- [x] T059 Keep active authenticated asset transfers and enrolled worker approvals
      alive through progress-bound leases, exact source acknowledgements and fresh
      signed renewal; offload worker cache/chunk I/O and refuse expired/replayed or
      cancelled renewal. Verify slow-copy clocks, bytes/hashes and signed transport.

T055 is source/contract coverage, not completion of T016. The helper only creates
exact private-address/interface/peer TCP rules and never adopts a pre-existing rule.
Its intent and successful CIM snapshots are persisted per run; missing/changed
ownership remains cleanup_pending. Helper restart restores cleanup only. Native
rules block network restoration/revocation until drained. Older helper journals
without assignment revisions need their owned network restored and prepared again.
No physical firewall mutation or privileged helper installation was performed for
these checks. T057 now invokes it during prepared distributed Run. Production gates
remain false. Physical distributed cluster gates and validations will be tested later;
local OS admission acceptance and remaining implementation are still required.

T056 remains open for complete administrator/direct-adapter and Linux/macOS paths,
physical/service acceptance of helper-independent expiry and physical recovery
of lost/changed CIM ownership, protected-helper IPC/service
failure and physical OS acceptance. T060 supplies the local recovery API source path.
The coordinator journals port intent before each signed/local call and admits every
selected rank before any spawn. Missing capability/helper ownership support is refused
before rule mutation. Each successful release narrows its durable participant list;
failed nodes do not prevent attempting cleanup on the others. Node-local admission
expires after at most600s before spawn and60s after launch/renewal; restart never
resumes inference. Controlled adapters/helper rules/model execution are fixtures;
signed channels, owned processes, HTTP, leases and persistence are real. This does
not close the broader T016, compatibility/placement/planner/performance or platform gates.

T058 runs the helper timer independently of IPC connections. Native rule admission
is bounded to600s before rank launch, then a fresh signed exact-contract renewal
switches the helper to90s (30s beyond the agent's60s rank lease). No unsigned status
or stale/expired/changed renewal extends it. Helper restart restores immediate
cleanup, not a new lease. Failed/changed rules remain pending while other expired
runs drain. The helper narrows only its verified firewall rules; it does not terminate
processes or prove existing traffic/generation stopped. Cluster admission still
requires owned rank stop acknowledgements before release. Physical OS, protected
service/IPC and helper failure/restart acceptance remain required. Physical distributed
cluster gates and validations will be tested later; production qualification stays false.

T059 closes fixed600s transfer/approval expiry during active preparation. Grants are
issued per asset just before use; exact acknowledged ranges renew their bounded
lifetime, and successful signed chunks renew worker approval. Progress-bound master
renewal keeps earlier completed workers current while others copy/profile. Idle or
repeated progress cannot indefinitely extend approval; fresh preparation is required
after expiry/cancellation. This does not close all T015 companion/shard native
execution compatibility or physical transfer/cluster acceptance. Those wider tasks
remain open; physical distributed gates and validations will be tested later.

- [x] T060 Add snapshot-bound local listener ownership review/recovery through the
      protected helper and loopback-only API, refuse live/foreign/stale decisions,
      preserve changed rules and durable retry state, and validate the packaged path.

T060 covers the local helper/API recovery source path. Administrator/direct-adapter
and Linux/macOS backends, protected service/IPC failure
and physical firewall/recovery acceptance remain T056 requirements. Physical
distributed cluster gates and validations will be tested later. Qualification stays
disabled until the wider integration and acceptance requirements pass.

- [x] T061 Connect local Prima listener recovery to the network dashboard for
      master/worker views, enforce snapshot/ownership/acknowledgement decisions and
      stale/closed-request handling, then verify browser layouts and the dashboard build.

T061 supplies local master/worker dashboard controls for T060's API. It does not
close T056 administrator/direct-adapter/platform implementations or protected
service/IPC and physical firewall/recovery acceptance. Physical distributed cluster
gates and validations will be tested later; all qualification remains disabled.

- [x] T062 Bound helper queue/connection/framed I/O, settle Windows cancellation
      and flush replies before disconnect, distinguish timeout from authorization,
      and verify isolated real-pipe admission/cancellation/cleanup plus API/UI behavior.

T062 is client/server source and isolated Windows pipe acceptance. Protected helper
deployment/ACL/service failure/restart, direct administrator/platform backends and
physical firewall/recovery acceptance still belong to T056. Physical distributed
cluster gates and validations will be tested later; qualification remains disabled.

- [x] T063 Add the elevated Windows direct listener backend with an owned native
      policy child, signed stable-scope reconciliation, durable provider/grant fences,
      correlated timeout-safe private pipes and owned shutdown; verify source/native/
      process boundaries while retaining physical elevated acceptance separately.

T063 implements the direct Windows source path; accepted elevated firewall/address/
service behavior is still T056 physical acceptance. Linux/macOS native listener
backends, broader engine compatibility/placement/assets/planning/performance and
qualification remain open. Physical distributed cluster gates and validations will
be tested later.

T063 delivery passes native28 tests, focused direct/ownership/Stop checks and the
full Python suite1478passed/19skipped48.63s. Supported packaging/native import and
canonical/project skill synchronization pass. Native administrator refusal is
verified on this unelevated machine; accepted elevated rule mutation remains open.
Grant rotation now requires explicit matching empty native ownership lists and a
fresh replacement-owner probe. The six original malformed/foreign status failures
are retained in prima-direct-grant-before.log; none can retire or authorize an owner.

- [x] T064 Retain the distributed run identity through monitor-driven coordinator
      retirement so successful Stop clears its matching active_config; preserve
      another run's configuration and add deterministic retirement regression checks.

T064 fixes the full-suite Stop race where the monitor had already retired the
coordinator but active_config still referred to the stopped run. The retained run
identity clears only the matching Prima configuration after acknowledged Stop.
Two deterministic regressions cover own and foreign configuration; final full
validation passes with the T063 delivery checks above. Qualification remains false.

- [x] T065 Correct native profiler tensor encoding, reject invalid/failed matrix
      computation and remove CPU fallback/leaked scheduler ownership. Reproduce
      original F16/Q4K/Q6K/Q8 failures, validate CPU plus physical selected Vulkan,
      publish the fork fix, rebuild the immutable recipe and recheck managed profiling.

T065 addresses a native kernel-measurement defect found during T017/T021 review.
The original profiler copies float bytes directly into F16/quantized tensors and
does not verify the multiplication result. Instrumented CPU checks reproduce four
incorrect representations (prima-profiler-contract-before.log). Development changes
and native/physical checks pass; the fix is published as e7ee8f6ced718dca7187cfba4407f44b5c88884d.
Recipe22 consumes it through a fresh managed build and verified artifacts. All four
CPU/Windows-Vulkan CI jobs pass. Published matrix rechecks pass12CPU/9GPU formats
with3unsupported GPU refusals. Fresh dense gateway/profile/logprobs38checks and
MoE gateway/logprobs36checks pass. Full-model kernel profiling still fails and is
explicitly T066 work; T065 closes only the verified matrix repair/delivery.
Broader remote planner work stays open; qualification stays disabled.

- [ ] T066 Repair the full-model kernel profiler and cost extraction for supported
      dense/MoE architectures and actual tensor types, supply valid Vulkan transfer/
      bandwidth measurements, and bind complete measured costs to remote planner inputs.
      Verify real managed jobs and signed remote results without extrapolating local
      end-to-end full-model timings to partial ranks. Physical cluster validation later.

T066 records the legacy Qwen3 kernel-job failure found during T065. Both unchanged
published21 and the development fix exit1 after Uncaught tensor. The fresh managed22
production ProfileJobs request also fails, retains no process and caches no profile.
Synthetic matrix correctness does not close this full-model/native/planner gap.

T066 native/consumer progress: published72d4edaba7f0111322fe192027c166fe85985e33
replaces descriptor mutation with complete GGUF metadata costs, including Qwen3
head norms, true attention dimensions and active expert arithmetic/full storage.
Native schema2 measures each required tensor type and validates GPU reads/KV copies.
Owned bounded native unbuffered disk I/O replaces absent fio; temporary file cleanup
on owned child death is verified. Kluster validates/cache-binds complete typed costs
and refuses capability downgrade, stale hardware and malformed/incomplete estimates.
Focused105/2skipped and full1498/19skipped49.53s pass; development dense/MoE managed
jobs prepare/cache28/48layers with no retained child. Recipe23 fresh managed build,
hash/source verification and published production jobs also pass28/48layers. Native
contracts3/3pass12.92s; selected physical Vulkan/read contracts and both portable CI
runs pass. Final Python1498/19skipped49.37s and dense gateway/profile/logprobs38checks
pass. MoE gateway/logprobs36checks also pass. Signed remote consumption, feasible candidate scoring,
network/disk cost integration and resource reservation remain required before closing
T066/T017/T021. Physical distributed cluster validations will be tested later.

- [x] T067 Bind signed profile control and imported typed results to exact current
      approved preparation/manifest/authority/model/runtime/rank/settings, advertise
      protocol2, preserve durable ownership/cancellation and verify real signed
      three-agent profile transfer/cache plus foreign status/cancel refusal.

T067 provides safe signed remote profile consumption, not full T066/T017 planning.
CPU ranks explicitly request CPU profiling even with a Vulkan build. Master imports
match current signed participant hardware and complete typed cost context. Controlled
profile subprocesses and signed channels pass; source/native/model/GPU qualification
is not inferred. Scoring with exact link/disk context and concurrent resource
reservation remains open. Physical distributed validations will be tested later.


- [x] T068 Add bounded authenticated private TCP stream measurements on the existing
      diagnostic listener, preparation-owned signed collection for every directed
      manifest edge, ephemeral master-approved worker peer scopes and real transport/
      three-agent regressions. Retain physical distributed acceptance separately.

T068 supplies link evidence, not authoritative measured planning. T017/T021/T066
remain open for feasible candidate scoring using complete typed layer/disk/link
context and concurrent resource reservations. Broader compatibility, placement,
platform, correctness/performance and qualification tasks remain open. Physical
distributed cluster gates and validations will be tested later, not counted as passes.


- [x] T069 Publish completed model-width GPU host handoff profiling and consume a
      fresh immutable runtime; bind profile storage to current owned context and wire
      absolute layer/private-link candidate comparison into normal previews, with
      strict complete-evidence fallback and CPU/GPU/signed-context regressions.

T069 covers the typed resident/one-token/f16/off/CPU-output estimate scope. T017/
T021/T066 remain open for resource reservations, additional feasible candidate
contracts/edges, GPU-output logits downloads and remaining execution settings/layouts.
Logical cold weight-read costs remain separate from decode; no generated timing or
qualification is claimed from roofline estimates. Physical distributed cluster gates
and validations will be tested later. Broad compatibility/platform/layout and local
correctness/performance requirements stay open.


- [x] T070 Add shared node work reservations across Prima preparation/profile/link/
      listener/rank and llama.cpp/RPC startup, retain worker transfer and owned cleanup
      claims, negotiate resource capability1 and verify cancellation, failed real-child
      cleanup/retry, grant expiry and all-node-before-spawn conflict handling.

T070 closes the controlled in-flight reservation boundary, not broad T017/T066.
Additional feasible candidate profiling/contracts/edges, GPU-output logits downloads,
remaining execution settings/layouts, compatibility/platform and local performance
acceptance remain open. Durable ownership/recovery stays on the existing supervisors.
Physical distributed cluster gates and validations will be tested later, not treated
as passed. Published Prima recipe24 is unchanged by this Kluster-only work.


- [x] T071 Publish checked vocabulary-width GPU logits downloads, consume the exact
      immutable recipe, retain/validate distinct output samples and use them for
      GPU-output candidate estimates with correct local/distributed graph handoffs.

T071 supplies the previously missing GPU-output cost boundary. It does not complete
T017/T021/T066's additional feasible candidate profile/contracts/edges or remaining
settings/layout coverage, nor broad model/API/assets/platform/placement and local
correctness/performance acceptance. Physical distributed cluster gates and validations
will be tested later. No qualification policy or threshold is changed.


- [x] T072 Derive immutable directed profiling approvals for feasible candidate
      ring edges among already prepared participants, require link protocol2 when
      extras exist, collect signed complete measurements and compare all covered
      candidates without widening native execution/listener ownership. Verify real
      four-agent preparation, old-agent refusal and incomplete-edge fallback.

T072 completes extra candidate-edge measurement within the selected preparation.
Unprepared candidate participants/devices, remaining settings/layout/profile coverage,
broad compatibility/platform/placement and local correctness/performance remain open.
Physical distributed cluster gates and validations will be tested later. All production
qualification remains disabled; this fixture coverage does not close T017/T021/T066.


- [x] T073 Publish bounded Vulkan indexed-matrix tails and selected-expert vectors
      for small decoding batches; verify every supported indexed operand type, consume
      the exact managed pin, exercise both models through the gateway and record the
      strict final published paired benchmark. Native publication/contracts/build/jobs/
      gateway checks pass; final published performance receipt completed and failed acceptance. This does
      not close T032/T039 or broader compatibility/platform/placement/profile work.
      Physical distributed cluster gates and validations will be tested later.


- [x] T074 Reduce device-memory MoE expert aggregation while preserving measured
      host-weight performance, verify exact reductions/storage bounds and actual
      CPU/GPU paths, publish/consume the immutable pin, and record model-backed
      gateway and strict paired acceptance. Native/development/CI checks pass;
      fresh managed27 build/profiles/contracts/gateways pass; final paired receipt completed and failed; packed reduction regressed model throughput. Broad T032/T039
      acceptance remains open. Physical distributed validations will be tested later.


- [x] T075 Replace the regressing Vulkan packed expert reduction with a bounded
      direct-stride serial-order kernel; preserve host/other-backend handling, verify
      unaligned views/cancellation arithmetic and incremental shader generation,
      publish/consume the immutable pin and record actual model/gateway/paired results.
      Native/development checks pass; fresh managed28/contracts/profiles/gateways pass; final paired receipt fails acceptance; model aggregation is being restored to
      the earlier add chain. Physical distributed cluster gates/validations will be tested later.


- [x] T076 Publish/consume the shared native template renderer and restore original
      model expert aggregation after matched regressions; verify native/HTTP prompt
      history/refusal contracts, exact per-engine token inputs, fresh managed artifacts,
      supplied-model gateway checks and record strict paired acceptance results.
      Published managed/API/native/GPU/jobs/gateway checks pass; the final five-pair
      benchmark is recorded and fails all four throughput gates. No qualification follows.
      T032/T039 and broad API/model/settings/placement/platform/profile work stay open.
      Physical distributed cluster gates and validations will be tested later.


- [x] T077 Repair MSVC native CPU ISA detection with explicit CPU/OS prerequisites
      and checked compiler intrinsics, preserve portable/manual target builds, publish
      and consume the exact pin, verify native/portable/model/GPU contracts and record
      strict paired model results. Development and published native/portable/GPU/models/
      jobs/gateway/CI/Python/package checks pass; the final five-pair benchmark is
      recorded and fails all four throughput gates. T032/T039/broader acceptance remain open.
      Physical distributed cluster gates and validations will be tested later.


- [x] T078 Remove redundant CPU metadata barriers while preserving real-operation
      dependencies and worker settlement; repair late-worker/prior cancellation,
      verify repeated OpenMP/pthread pool reuse, publish/consume the immutable pin,
      exercise both models and record strict paired acceptance results. Development
      and published native/portable/GPU/models/jobs/gateways/CI/Python/package checks
      pass; final five-pair throughput acceptance fails all four cases and is recorded.
      T032/T039/broader acceptance remain open. Physical distributed cluster gates and
      validations will be tested later.


### Performance validation deferred by user - 2026-10-12

The user explicitly requests no further performance-gate validation here and will
check performance on a live Kluster cluster of three computers. Local paired
acceptance and further performance-gate diagnostic experiments are stopped.
Implementation and correctness checks, including functional profiler contracts,
continue. Existing benchmark failures remain historical evidence, not passes.
Preserve the no more than 5% regression and 10% representative distributed
improvement criteria until the user's live measurements can evaluate them. Do not
relax thresholds, retokenize usage, infer a pass or enable an unverified release scope.

Physical distributed cluster gates and validations will be tested later on the
user's three-computer setup. Performance and distributed qualification remain
unpassed. Their deferral is explicit and is not an implementation blocker for
finishing remaining API/model/assets/settings/placement/planner work.
The bounded timing diagnostic already performed is not a performance gate result:
its summary is corrected to insufficient_decode_samples because mixed token-count
prefill graphs include a final single-token projection. No new timing experiment
will extend that sample collection here. The optional diagnostic-mode correctness
repair and its benchmark-rejection checks can be finished independently.

- [x] T079 Repair optional Vulkan timing-mode graph correctness and timestamp
      evidence; advertise and reject diagnostic builds for acceptance, bound/hash
      development capability probes, retain owned failure logs, publish and consume
      recipe32, and verify ordinary native/GPU/Python/package/model gateway checks.
      Dense36/MoE36 functional gateway checks pass. No performance gate is validated.
      Performance and physical distributed acceptance are deferred to the user's
      live three-computer cluster. Qualification remains unpassed; portable CI
      runs38242934671/38242929739 are pending at handoff.

- [x] T080 Add full server Jinja rendering from the pinned baseline, including
      embedded/custom/preset templates, kwargs/typed text and concurrent contexts;
      freeze source in signed manifests and qualification, check native capability,
      verify bundled-reference and real-model prompt/gateway correctness, publish
      and consume the immutable source. Input marking and automatic tool parsing
      remain distinct broader work. Performance/distributed acceptance is deferred.

- [x] T081 Add named forced-tool streams to all three text protocols, preserve
      stable call/item identities, fragmented arguments, partial termination,
      native token logprobs and post-error settlement; exercise model-backed
      gateway correctness and consume a published immutable native pin. Automatic
      choice/parallel tool parsing and tool history remain separate broader work.
      Performance and physical cluster acceptance remain deferred.

- [x] T082 Preserve text tool history across Chat/Responses/Messages, call IDs,
      historical call grouping, typed results and Jinja argument normalization;
      bind protocol-specific history/vision requirements to qualification, check
      baseline prompt/token parity and real model continuation/streaming, publish
      and consume the immutable pin. Automatic tools and multimodal history remain
      broader work; performance and physical distributed acceptance are deferred.

- [x] T083 Publish and consume the baseline automatic-tool parser/lazy grammar
      foundation, including generated-grammar compatibility, transitive nullable
      cycle refusal, fragmented/Unicode trigger acceptance, clone/reset and actual
      model vocabulary checks. Native source ccc8c594 is published; recipe36 clean
      managed build, native5/5, dense49/MoE49, vocabulary and portable CI pass. This scoped task
      does not advertise automatic HTTP tools or qualify inference.

- [x] T084 Wire baseline automatic/required tools through native HTTP generation
      and Chat/Responses/Messages parsing, including stable parallel call IDs,
      argument deltas, text/reasoning separation, strict schema policy, partial
      termination and cancellation. Add protocol-specific capability/qualification
      admission and dense/MoE authenticated gateway checks; publish/consume the
      completed implementation. Performance and physical distributed acceptance
      will be tested later on the user's live three-computer cluster.

- [x] T085 Publish/consume ordered explicit local devices, CPU-only selection,
      Vulkan registry and selected-adapter host buffers; verify exact weighted
      per-layer prefill/decode ownership, UUID visibility/remapping and malformed
      selection refusal. Keep row/tensor/expert and platform acceptance separate.
      Performance/physical distributed validation is deferred to three computers.

- [ ] T086 Integrate draft-simple speculative decoding through verified draft
      assets and immutable settings: isolate draft contexts from target manifests,
      ranks, GPU selection and autonomous profiling; preserve accepted-token
      accounting/logprobs/grammar/limits, typed cache settings, cancellation and
      sequence cleanup. Validate native contracts and dense/MoE gateway behavior
      without performance acceptance; retain broader speculative variants and
      physical distributed acceptance as explicit remaining work.


### Implementation-first validation boundary (2026-10-12)

The user now directs that all unit test runs and test-fix work be deferred until
implementation has finished. Continue remaining implementation without starting
pytest/ctest or treating passing scoped checks as completion of the integration.
After implementation completion, run the relevant tests and fix failures. Existing
receipts remain historical evidence; fresh recipe41 test/model acceptance is not
yet complete. Performance and physical distributed acceptance remain separately
deferred to the user's live three-computer cluster. Compilation builds may be
used to check implementation; they do not grant validation or qualification.


### Rank-wide prompt cache implementation (recipe42)

Published Prima source: ab96f06556c847c0d1176a7c0c4e77f7584e3451.
Recipe42 digest: fab69be5f4a3d8abf1b99889593524a6d49f9126c8ef4652ac07ddf001eb1864.
The fork implements cache-ram with bounded rank-local serialized KV storage,
one eviction order and acknowledged save/restore/remove transactions. Partial
saves are removed everywhere; failed restores invalidate destination sequences
before recomputation. Missing acknowledgements stop uncertain contexts. Partial
rank serialization uses actual owned absolute layer indices and handles empty
heads; full-model state retains its existing format. System/adapter changes clear
snapshots. Kluster negotiates prompt_cache_v1, binds the budget to the manifest,
and reserves its full ceiling in host RAM on every selected participant,
including GPU workers. Unsupported measured cost contexts remain capacity-first.

Development production server/CLI/profiler compilation passed after correcting
a missing telemetry helper name. The clean published recipe42 build is in progress
(artifacts/prima-cache-published42-build.log); root production package build passed
(artifacts/prima-cache-pin42-package.log). No new unit, ctest or model assessment
runs were started. The published native commit skips CI because the user requires
all tests and test-fix work after implementation completion; skipped CI is unpassed.

Deferred test-phase cases include dense/MoE cache reuse/eviction/branching,
independent slots, cancellation, budget and malformed inputs, partial-layer state
round trips, zero-work-head worker restoration and lost-acknowledgement cleanup.
Checkpoints/SWA, remaining model/API/launch parity and arbitrary segmented, row,
tensor and expert placement remain implementation work. Cache and draft features
do not close the aggregate integration tasks. All qualification flags remain
false and execution scopes empty. Physical distributed and performance acceptance
will be tested later on the user's live three-computer cluster.

- [ ] T087 Finish rank-wide prompt cache integration and, after all required
      implementation is complete, validate local/distributed cache state, budgets,
      independent slots, branching, cancellation and lost-acknowledgement cleanup.
      Native implementation is published in recipe42; test acceptance is deferred.


### Context checkpoint implementation (recipe43)

Published source: a5ef31839770df04b6e51628bf015336d1ce468d.
Recipe43 digest: b57f8d548c342f5c37f57977116fb0018a9862a19f925d495cb93e90ea37515b.
Context checkpoints have independent per-slot histories and a separate bounded
snapshot pool from cache-ram. Capture follows successful decode and uses completed
sequence positions rather than optimistic n_past across undecoded sub-batches.
Recurrent restore requires an entire matching prefix and a remaining token for
logits reconstruction. Counts, spacing and the planned byte ceiling are frozen
in the native manifest; context_checkpoints_v1 is required before preparation.
Kluster prices full target KV/state plus metadata and reserves the ceiling on
every selected participant, without double-charging the master. Prima's full
checkpoint state is larger than baseline partial recurrent/SWA state and is
priced accordingly. System/adapter changes and slot erase clear owned histories.
Draft contexts do not inherit target cache/checkpoint settings.

Development production server/CLI/profiler compilation passed. Root production
package build passed (artifacts/prima-checkpoint-pin43-package.log). The clean
published recipe43 build is in progress at
artifacts/prima-checkpoint-published43-build.log. Recipe42's clean build has now
completed; its source/artifact hashes match the published pin, as recorded in
artifacts/prima-cache-published42-manifest-check.json. These are compilation and
artifact identity receipts, not test acceptance. Native publication again skips
CI tests to honor the user's implementation-first schedule. No pytest, ctest,
model assessment, paired timing or test-fix work was started.

After required implementation finishes, validate checkpoint count/spacing/budget
refusal, independent cache/checkpoint limits, completed micro-batch prefix capture,
recurrent rollback and sampling, slot reuse/erase, system/adapter invalidation,
draft isolation, partial/zero-head ranks, failed restores and lost acknowledgement
cleanup. Remaining API/model/launch and arbitrary segmented/row/tensor/expert
placement work is still open; neither this feature nor cache/draft implementation
completes the aggregate integration. Qualification remains disabled, with all
flags false and execution scopes empty. Physical cluster and performance gates
will be tested later on the user's live three-computer cluster.

- [ ] T088 Finish context checkpoint integration and, after required implementation
      completion, validate completed prefix capture, full-state host pricing, slot
      histories, recurrent/draft restoration, bounded rank acknowledgement and
      cleanup. Native implementation is published in recipe43; tests are deferred.


### Deployment template keyword defaults (recipe44)

Published source: 400a4ffb041dc5a36c9b344f9f7e5ad84cfa8267.
Recipe44 digest: 02938108e2f073d8eeb4b3fa631fadf6ac696641ff7bb185d0e6a4d7d043fcf8.
Native --chat-template-kwargs and its owned-file variant apply a bounded JSON
object to the shared chat/Responses/Messages/template-preview/input-token/
automatic-tool renderer. Request values override matching deployment defaults.
Root launch accepts this declared extra, validates finite JSON and preserves
dictionary insertion order. Rank zero receives a content-addressed JSON file
inside its owned run directory; workers receive rendered tokens. Existing file
bytes and symlinks are checked. Preparation requires template_kwargs_defaults_v1;
native startup compares parsed defaults with the approved execution manifest.
Named legacy aliases refuse nonempty defaults before readiness because they
cannot consume arbitrary template variables. Other launch extras remain open.

Development production server/CLI/profiler compilation and root package build
passed (artifacts/prima-template-defaults-development-build2.log and
artifacts/prima-template-defaults-pin44-package.log). The clean recipe44 build
is in progress at artifacts/prima-template-defaults-published44-build.log. The
clean recipe43 build completed and its source/artifact hashes match the published
pin (artifacts/prima-checkpoint-published43-manifest-check.json). These receipts
establish compilation/artifact identity only. Native publication skips CI tests
to honor implementation-first validation. No new unit, native contract, model
assessment or performance runs were started.

After implementation completion, validate deployment/request precedence on all
text protocols, automatic tool preparation, reasoning controls, template/input-
token agreement, object order, malformed/oversized input, manifest mismatch and
owned-file drift. Broader API/model/launch and arbitrary placement implementation
still remains; this does not close aggregate integration or qualification tasks.
All qualification flags remain false and execution scopes empty. Physical
distributed and performance gates will be tested later on the user's live
three-computer cluster.

- [ ] T089 Finish deployment template keyword defaults and, after implementation
      completion, validate shared protocol rendering, request precedence, finite
      bounded JSON, manifest equality and owned-file integrity. Native implementation
      is published in recipe44; test acceptance is deferred.


### Native initialization and sampler failure handling (recipe45)

Published source: 9cc8b1e1f717b58a68359f6aa5e24b27490cd3ee.
Recipe45 digest: 3e1679508f636bf565d08b053b1af2a99a5663e8a796a6f8155ba932c0fd6cf5.
Model/context/backend/grammar entry points contain exceptions within the exported
C boundary and return failure; managed sockets use checked startup with partial
handle cleanup. Backend setup borrows its context so its caller frees failure
once, fixing double ownership. Context destruction closes distinct owned socket
aliases with zero linger. Invalid grammars cannot become unconstrained samplers.
Checked accept/apply/reset and clone paths expose callback failure without C
unwinding; nested chains retain failure at that boundary. Grammar reset/clone
retain allocation ownership and cloned candidate pointers refer to copied storage.
Managed serving fails the owning request and clears its head/worker sequence on
sampler errors during replay, generation or draft verification. Other request
sequences and valid snapshots remain owned. Fully masked or non-finite selected
tokens refuse instead of becoming valid output. Legacy void sampler calls remain
fail-closed; managed C++ callers use the checked interface.

Development production server/CLI/profiler compilation passed, including the
final artifacts/prima-errors-development-build3.log. Earlier relevant C4297
initialization warnings no longer appear in the affected development build.
Root package build passed (artifacts/prima-errors-pin45-package.log). The clean
recipe45 build is in progress at artifacts/prima-errors-published45-build.log.
Recipe44's clean build completed with matching source/artifact hashes, recorded
in artifacts/prima-template-defaults-published44-manifest-check.json. These
receipts establish compilation and artifact identity, not runtime acceptance.
Native publication skips CI tests. No unit/native contract/model assessment
or performance runs were started; test-fix work stays deferred until required
implementation completion.

Deferred validation includes invalid regular/lazy grammars, trigger storage/token
refusal, callback/chain errors, reset/clone ownership, empty/fully masked candidate
sets, malformed startup/device/backend/socket input, occupied private ports,
partial allocation cleanup and retry, request isolation and draft failures.
Broader model/API/launch and arbitrary segmented/row/tensor/expert placement
implementation remains open. All qualification flags remain false and execution
scopes empty. Physical distributed and performance gates will be tested later
on the user's live three-computer cluster.

- [ ] T090 Complete native C-boundary startup/sampling failure handling and, after
      required implementation completion, validate failure/cleanup/request-isolation
      behavior across checked callbacks and model/rank processes. Implementation
      is published in recipe45; test acceptance remains deferred.


### Absolute ownership foundation (recipe46)

Published source: cfdaff424baf9977c744c2ec1735e737e489da43.
Recipe46 digest: 38611f5b6f4863f518d291fa51593f705ffb599e57e8a3f59b5db45623b67b1c.
Native manifests supply an immutable absolute layer-owner table. Models copy
and validate the table against world/rank and actual GGUF layer count; contexts
borrow that model-owned table and reject drift. Tensor loading, KV indexing,
attention storage, graph callbacks and sequence snapshots use precomputed local
indices. Explicit GPU offload uses rank-local absolute ordering. Draft contexts
clear target ownership. The table also computes contiguous-visit graph indices
for the remaining segmented executor.

This is a prerequisite, not arbitrary-placement execution. Existing manifest
and mapped-model/context startup still require one worker-first window cycle;
segmented_placement is not advertised. Actual ordered segment handoff, repeated
rank visits, row/tensor/expert placement and architecture graph support remain
implementation work. T014 stays open. Do not replace the required executor with
a rounded window plan or describe map representation as completed execution.

Development production server/CLI/profiler compilation passed, including
artifacts/prima-layer-map-development-build2.log. Root package build passed
(artifacts/prima-layer-map-pin46-package.log). The clean published recipe46 build
is running at artifacts/prima-layer-map-published46-build.log. Recipe45's clean
build completed with matching source/artifact hashes, recorded in
artifacts/prima-errors-published45-manifest-check.json. These receipts establish
compilation and artifact identity only. Native publication skips CI tests to
honor implementation-first validation. No unit/native/model/performance runs
or test-fix work were started.

After full implementation completion, validate owner/count/window rejection,
model/context lifetime and equality, zero-head and partial GPU indices, tensor/
KV/snapshot ownership, actual approved graph visits and rank handoffs. Broader
integration remains open. All qualification flags are false and execution
scopes empty. Physical distributed and performance gates will be tested later
on the user's live three-computer cluster.

- [ ] T091 Finish arbitrary ordered layer execution using immutable absolute
      ownership: implement actual segmented graph/rank handoffs and repeated
      visits, update native admission/planning/capabilities, then validate after
      full implementation completion. Ownership/indexing foundation is published
      in recipe46; segmented execution and its acceptance remain unfinished.


### Recipe47: segmented execution implementation in progress

Source: a314e91b0e13893c47bcf11e90b21d9700681bd0 on the fork PR #1 branch.
Recipe digest: 2908ff6a96b2e4edd5bb011561d8b91c2450d00edd5e4e15844db400a07a0185.
The native executor now has ordered absolute segment visits, activation forwarding
through the approved physical ring, bounded identity/shape checks and a complete
ring acknowledgement before the next micro-batch or KV operation. Native manifest
admission checks segment coverage against rank ownership. Segmented schedulers
reserve each local graph visit. The legacy canonical window executor is retained.

This remains incomplete managed integration. segmented_placement is NOT advertised;
Kluster still rejects these layouts at execution admission. Finish per-visit graph
memory accounting, rank launch translation and capability negotiation before enabling
it. Repeated-visit/relay layouts retain capacity-first, unmeasured planning because
the existing one-handoff-per-rank cost model does not cover them. Row/tensor/expert
placement and broader architecture/API support remain open; T091 is not complete.

Production server/CLI/profiler compilation passed in
artifacts/prima-segments-development-build3.log, and the root package build passed
in artifacts/prima-segments-pin47-package.log. The clean published recipe47 build
was started in artifacts/prima-segments-published47-build.log. Recipe46 clean source
and binary identities were verified in
artifacts/prima-layer-map-published46-manifest-check.json. These are compilation
and artifact-identity receipts, not runtime acceptance.

All unit/native/model tests and test-fix work remain deferred until the entire
required implementation is finished. Publication skips CI tests; skipped checks
must run in that later phase. Physical distributed/platform/mixed-backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification flags remain false and execution scopes empty. Thresholds remain
at most 5% regression and at least 10% representative distributed improvement.


### Segmented graph memory integration progress

The Prima allocator now derives actual contiguous rank visits from absolute
transformer ownership and reserves independent head input/output and local visit
schedulers. Extra host/device scratch and runtime estimates are added to each
physical pool without changing assigned layers. Device limits, node RAM/VRAM
limits and unified-memory limits are checked; excess retained graph reserves
make the plan overflow. This remains a conservative capacity estimate, not a
backend allocator measurement. Local single-rank placement retains its existing
shared graph estimate. Row/tensor/expert placements remain separate open work.

The internal prima_graphs flag is carried through immutable resolved/placement
settings and frozen execution qualification scope. Future segmented-capable
admission requires this planned reserve flag and validates absolute ownership.
The pinned native recipe47 still does not advertise segmented_placement. T091
remains open for capability/launch completion and the later execution checks.

Clean published recipe47 source/artifact identity is verified in
artifacts/prima-segments-published47-manifest-check.json. Production packaging
passed in artifacts/prima-segments-graph-reserve-package.log; edited Python
modules passed syntax compilation only. No unit/native/model tests or test-fix
work ran. Those start after all required implementation is complete. Physical
distributed and performance acceptance will be tested later on the user's live
three-computer cluster; all qualification flags remain false and scopes empty.


### Recipe48: segmented placement capability and graph admission

Source: 5c19eedfbfba5c1cdb7d34477670d73436c8c0d7 on the fork PR #1 branch.
Recipe digest: 1c5c41c88a99d44d1d9291c572b4a316b606fb4e6a4ec47ec02cca17ecd23d28.
The native runtime now advertises segmented_placement for its implemented layer
graph families (Llama, Qwen2, Qwen3 dense/MoE). This supersedes the recipe47 note
that the capability is absent; it does not enable inference qualification.

Planner graph visit counts now travel from typed placement results into signed
execution settings and the immutable qualification scope. Kluster and native
manifest admission independently derive visits from absolute ownership and reject
missing, altered or mismatched counts for noncanonical layouts. Rank launch
translation and candidate profile edge discovery support explicitly negotiated
segmentation, retaining legacy window validation for older runtimes. Existing
one-handoff-per-rank typed timing costs still keep segmented candidates capacity-
first and unmeasured. No native listener edge or authority scope was widened.

Production server/CLI/profiler compilation passed in
artifacts/prima-segments-admission-development-build.log, root package build
passed in artifacts/prima-segments-pin48-package.log, and edited Python modules
passed syntax compilation. Clean published recipe48 production compilation is
running in artifacts/prima-segments-published48-build.log. Recipe47's completed
clean build has matching source/artifact hashes recorded in
artifacts/prima-segments-published47-manifest-check.json.

T091 remains open for execution acceptance after full implementation completion;
compile/capability reports do not prove actual rank visits or correctness. Row/
tensor/expert placement, remaining model/API compatibility and platform work stay
open under the original requirements. No unit/native/model tests or test-fix work
ran; skipped publication CI must run later with that phase. Physical distributed,
mixed-platform/backend and performance acceptance will be tested later on the
user's live three-computer cluster. Qualification flags are false and execution
scopes empty; all original thresholds remain unchanged.


### Segmented planner cost implementation

The typed distributed planner now prices each GPU-containing graph visit and
routes one-token activation payloads through the actual approved physical ring,
including repeated visits, relays and the complete ring acknowledgement. Batch
metadata retains the published worker chain. Segment marker/JSON payload bounds
use the maximum wave-counter width; transport framing, buffering and relay CPU
costs remain outside this explicitly labelled application-link proxy estimate.
Cold logical weight reads remain separate from resident decode predictions.

Scoring requires the published segmented capability, matching planned graph visit
counts and all existing fresh signed source/model/settings/hardware/storage/link
receipts. Missing evidence retains capacity-first planning. This supersedes the
recipe48 note that segmented timing costs are entirely unavailable, but grants
no native-generation or performance acceptance. Canonical window layouts retain
their legacy wire estimate. Per-candidate wire_cost_model identifies which path
was priced. Production package build passed in
artifacts/prima-segments-costs-package.log; edited modules passed syntax compilation.

Recipe48 clean production build completed with matching source/artifact hashes
and the native segmented capability in
artifacts/prima-segments-published48-manifest-check.json. Tests and test-fix work
remain deferred until full required implementation completion. Physical distributed,
mixed-platform/backend and performance gates will be tested later on the user's
live three-computer cluster. Qualification flags are false and scopes empty.


### Recipe49: coordinator GPU output without transformer offload

Source: 54470aec16cf83c4def0031cecd8643d712b994b on the fork PR #1 branch.
Recipe digest: adb9bb2c0295769825211e143c4f9eb7b3803896efd0a72af64d2a26fc99b18f.
A rank-zero coordinator may now declare output_gpu=true with ngl=0 and one
selected device. ngl continues to count transformer layers only; output placement
is independent. Native manifest/device checks and CUDA scheduler reservation keep
that output graph on the selected GPU. Kluster preserves the output-only device,
Vulkan visibility, explicit launch selection, signed participant inventory and
backend readiness checks. Older runtimes refuse through output_only_gpu_v1.

Completed GPU evidence requires output projection in both prefill and decode;
zero transformer offload must not turn a Python negative-zero slice into all
assigned CPU layers. Typed planning checks the selected physical Vulkan identity
and separately prices the output embedding upload and vocabulary-width logits
download. Transformer GPU visit costs stay zero for this coordinator. The planner
still requires complete current signed evidence; this implementation is no
correctness, distributed or performance acceptance receipt. Remote output owners,
row/tensor/expert placement and wider compatibility remain open.

Production server/CLI/profiler compilation passed in
artifacts/prima-output-only-development-build.log; root package build passed in
artifacts/prima-output-only-pin49-package.log, and edited Python modules passed
syntax compilation. The clean published recipe49 build is running in
artifacts/prima-output-only-published49-build.log. No unit/native/model tests or
test-fix work ran, and publication skipped CI tests. Execute those later after
full implementation completion. Physical distributed/mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification flags stay false and execution scopes empty.


### Recipe50: rank-owned LoRA loading foundation

Source: aeba7fcf824e1121c486f75211401c73de8b28d5 on the fork PR #1 branch.
Recipe digest: 6260f74deeb152399328c0888315b279e55f43847556a425b50d9bafe248d3f9.
The native loader validates adapter metadata and complete tensor pairs on every
managed rank, then allocates only tensors owned by that rank's immutable absolute
layer map. Rank zero retains input/output ownership. The owning rank rejects
unknown target tensors before cluster readiness. Adapter metadata, device tensor
contexts, buffers and model registration now have exception-safe ownership; the
exported initializer contains failures and returns nullptr. Alpha and tensor
shape/rank checks refuse malformed adapters. Local drafts no longer inherit target
LoRA files that may be incompatible with their separate model.

This is native loading foundation, not completed managed LoRA support. Kluster
still rejects adapter extras, and no managed LoRA capability is advertised. Finish
verified adapter asset roles/transfers, immutable ordered file/scale bindings,
rank-aware weights and graph scratch pricing, launch remapping and native scale/
selection synchronization before enabling it. HTTP LoRA changes must reach every
rank and settle before decode; head-only scale changes would produce inconsistent
generation. Clear compatible cache/checkpoint state through the existing lifecycle.
Broader model/API/placement requirements remain open.

Production compilation passed in
artifacts/prima-lora-ownership-development-build2.log and root package build passed
in artifacts/prima-lora-ownership-pin50-package.log. Clean published recipe50 build
is running in artifacts/prima-lora-ownership-published50-build.log. Recipe49 clean
source/artifact identity and output-only GPU capability were verified in
artifacts/prima-output-only-published49-manifest-check.json. No unit/native/model
checks or test-fix work ran; publication skipped CI tests. Those start after full
implementation completion. Physical distributed/mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification flags remain false and execution scopes empty.

- [ ] T092 Complete managed LoRA: verified ordered adapter assets/scales, rank-aware memory pricing, owned launch remapping and rank-wide selection/scale synchronization; then validate after full implementation completion. Native ownership/loading foundation is recipe50, not feature acceptance.


### Managed LoRA assets, launch and reservation integration

Kluster now parses ordered --lora/--lora-scaled declarations together with one
optional template keyword object. Scales are normalized to finite native F32.
Adapter GGUF metadata can be read without model block-count fields. Verified
adapter asset roles use the existing signed manifest, complete-byte transfer and
owned layout resolver; model-shard filename rules do not reinterpret adapters.
Repeated declarations retain ordered scales while one identical file is staged once.
Conflicting basenames and incomplete/symlink files refuse preparation.

Frozen prima_lora bindings and conservative storage/per-token scratch bounds are
carried through typed resolved/placement settings and qualification scope. Host
and selected allocation pools reserve adapter storage before model assignment;
scratch grows with effective micro-batch/slot size and retained graph visits.
These full-file/all-pair bounds may exceed actual rank-owned allocation and are
not backend allocator measurements. Launch verifies each staged file's size/SHA
and metadata-derived bounds again, then remaps paths under the owned model layout
for every rank. Original master paths are never worker launch paths.

Managed LoRA remains DISABLED: managed_lora_v1 is not advertised in recipe50.
Finish native ordered binding/admission, rank-wide selection/scale synchronization,
worker adapter-role negotiation, cache/checkpoint invalidation and completed GPU
adapter/output evidence before enabling it. Head-only changes and evidence that
omits LoRA matrices are insufficient. T092 and original compatibility tasks remain
open; these assets/reservations do not prove LoRA execution.

Production imports, syntax compilation and package build passed in
artifacts/prima-lora-assets-package.log. Recipe50 clean source/artifact identity is
verified in artifacts/prima-lora-ownership-published50-manifest-check.json. No
unit/native/model checks or test-fix work ran. Those start after full required
implementation completion. Physical distributed/mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification flags remain false and execution scopes empty.


### Recipe51: acknowledged native LoRA selection synchronization

Source: cf1890af47515c9a76e77f251cffe81bfc5e723a on the fork PR #1 branch.
Recipe digest: 0a9c776ab2374955b6173ee90e162a9a5296221e24d9e0d1934d03779f284b2d.
Native adapters have model-local ordered identities and deterministic application
order. The checked replacement API validates complete finite selections before
mutation. Manifest heads send bounded selection commands over the admitted ring,
bound to the exact manifest, revision and hop order, and wait for a complete
all-rank acknowledgement before committing. Foreign/stale/missing/failed replies
make the head context unusable; failed workers forward failure where possible and
terminate their decode path. Startup applies the same local selection before
readiness; manifest binding checks still need completion before managed enablement.

Replacement settles backend work and clears KV plus both snapshot pools. The HTTP
endpoint validates a full request before enqueueing immutable values, defers until
slots are idle, updates reported scales only after success, clears draft KV and
rebuilds the system prefix. Result waiting is registered before task publication.
Malformed requests and native failures return errors without success claims. The
common startup path checks replacement status and cleans failed initialization.

Production server/CLI/profiler compilation passed in
artifacts/prima-lora-sync-development-build4.log and root package build passed in
artifacts/prima-lora-sync-pin51-package.log. Clean published recipe51 production
build is running in artifacts/prima-lora-sync-published51-build.log. No unit/native/
model tests or test-fix work ran; publication skipped CI tests per the requested
schedule. These checks do not prove synchronization or LoRA correctness at runtime.

Managed LoRA remains disabled; managed_lora_v1 is not advertised. T092 stays open
for native manifest/file/scale admission, worker adapter-role negotiation, completed
GPU adapter/output evidence and later execution checks. The original model/API/
placement/platform requirements remain open. Physical distributed, mixed-platform/
backend and performance gates will be tested later on the user's live three-computer
cluster. Qualification flags remain false and execution scopes empty.


### Recipe52: LoRA admission, worker negotiation and completed-work tracing

Source: 9f7abdc0c8e780833f7d33f15d461f88d5def636 on the fork PR #1 branch.
Recipe digest: 5773afcf397cf5ee952631bffc991c734e48a0497f3f7bf362a87478e5c1b34e.
Native admission compares ordered adapter basenames/scales with the manifest,
verifies approved file sizes/SHA-256 using bounded streaming I/O and refuses
unreferenced assets or changed initial selection. Streaming retains the existing
string digest API; native digest vectors and file checks remain untested pending
the full implementation-complete test phase. File verification uses GGML's UTF-8
path opener. Legacy manifests without adapters retain optional settings/assets.

Workers advertise prima_lora_assets_version=1; preparation requires the exact
capability before adapter transfer. Basename bindings interpret master Windows/
POSIX separators consistently. Frozen adapter targets are rechecked against staged
metadata at launch and included in the qualification scope. Actual completed LoRA
operations are tagged by model-local adapter/target identity, component and role,
with selection/revision and output-weight context. Empty operations are excluded;
new completed observations accumulate per phase/epoch, preventing an early partial
batch from hiding later work. Tracing remains opt-in and is not timing evidence.

The verifier rejects foreign/stale selections, wrong scales/targets/backends and
missing A/B work across both prefill/decode observations. Native compilation passed
in artifacts/prima-lora-binding-development-build4.log. Root packaging passed in
artifacts/prima-lora-binding-pin52-package.log and edited modules passed syntax/
production imports. Clean recipe52 build is running in
artifacts/prima-lora-binding-published52-build.log. Recipe51 clean source/artifact
identity is verified in artifacts/prima-lora-sync-published51-manifest-check.json.

Managed LoRA remains disabled: managed_lora_v1 is not advertised. Finish input
embedding LoRA execution and tied output adapter buffer ownership, then complete
capability integration and the later correctness/protocol/hash checks. T092 and
original broader compatibility/placement/platform requirements remain open. No
unit/native/model tests or test-fix work ran; publication skipped CI tests. Physical
distributed, mixed-platform/backend and performance gates will be tested later on
the user's live three-computer cluster. Qualification flags are false and scopes
empty; compilation and trace implementation grant no execution acceptance.


### Recipe53: embedding and tied-output managed LoRA implementation

Source: f8aa37338cd8b41e599879e63ec53767a8fea8b7 on the fork PR #1 branch.
Recipe digest: 467861e3d85b7a2d8425faef87d95e85bc3cd11b685415c62d8e30f44e1b28c0.
Token embedding adapters use the baseline GGUF orientation (A: rank/vocabulary,
B: rank/embedding) and update gathered token embeddings. Tied output projection
uses an independently allocated pair on the actual output buffer, with decoded
B transposed into F32 and original A retained; original adapter bytes are unchanged.
Input and output operations retain original component identities in execution
tracing. Planner metadata checks now distinguish embedding and linear shapes.

Adapters load and apply before backend graph reservation. Native graph capacity
includes both input and tied-output operations, and effective scales are checked
against finite F32 bounds before selection changes. The blanket LoRA/Flash Attention
rejection was removed to match the baseline selection contract; compatibility still
requires later execution checks. The published native development capability now
includes managed_lora_v1. Qualification remains false and execution scopes empty.

Production server/CLI/profiler compilation completed in
artifacts/prima-lora-embedding-development-build3.log, and root packaging passed in
artifacts/prima-lora-embedding-pin53-package.log. Clean published recipe53 build is
running in artifacts/prima-lora-embedding-published53-build.log. Clean recipe52
source/artifact identity is recorded in
artifacts/prima-lora-binding-published52-manifest-check.json.

T092 implementation wiring now includes assets, admission, pricing, selection
settlement, embedding/tied output and operation evidence. Its acceptance stays open.
The broader original model/API/placement/platform requirements remain open. No
unit/native/model tests or test-fix work ran; publication uses [skip ci]. All tests
and test fixes start only after the full required implementation is finished.
Physical distributed, mixed-platform/backend and performance acceptance will be
tested later on the user's live three-computer cluster; no gate is marked passed.


Recipe53 published-build follow-up: the clean Windows/Vulkan production build
completed successfully. Source revision/cleanliness, staged executable SHA-256
values and the managed_lora_v1 development capability are verified in
artifacts/prima-lora-embedding-published53-manifest-check.json. This is build and
identity evidence only. Tests/test fixes remain deferred until full implementation
completion; physical cluster/performance gates remain deferred and unpassed.


### Recipe54: per-request LoRA selection and concurrent batch ownership

Source: 9808a107d65432fd021e0896d074778b850c72e5 on fork PR #1.
Recipe digest: d574f28ac3593e503b244acd5aeb61931aa6f2d672aca1ef5d2009935f2c080f.
Native generation slots now retain independent ordered adapter scales. An empty
request array uses server defaults, matching the baseline. Nonempty selections
zero unspecified adapters and reject invalid IDs/nonfinite scales. Chat, Responses
and Messages preserve the selection through request translation and generation
settings report effective per-slot values. Server-wide defaults change only after
successful rank settlement.

Round-robin batch selection groups compatible adapter configurations. A separate
native batch-selection API preserves other sequences' KV and snapshots; protocol2
binds that mode to the same manifest, monotonic revision and full-ring acknowledgement.
The original replacement API still clears caches. Prompt-cache/checkpoint metadata
includes adapter scales, storage charges include those values, and restoration
requires the exact selection. Slot reuse under changed scales clears its own state
and draft KV. With loaded adapters, global system prefixes are prepared separately
for each sequence under its weights instead of copying a foreign prefix. Prefix
preparation uses an independent batch and preserves other slots' pending work.

Kluster request qualification now recognizes lora/request_lora and requires the
native request_lora_v1 development capability. No qualification scope is enabled.
Production compilation passed in artifacts/prima-request-lora-development-build2.log
and the final server rebuild in artifacts/prima-request-lora-development-build3.log.
Root package build passed in artifacts/prima-request-lora-pin54-package.log. A clean
published Windows/Vulkan build is running in
artifacts/prima-request-lora-published54-build.log. An initial compiler member-name
error was corrected; that log is retained. No unit/native/model tests ran.

Per-request selection implementation is published, but correctness acceptance stays
open. Activated LoRA invocation handling, broader original API/model/launch parity,
row/tensor/expert placement and platform support still require implementation.
After the full implementation finishes, check default/empty/zero/negative/duplicate
selections, malformed IDs/scales, heterogeneous concurrent streams, fair scheduling,
slot reuse, cache/checkpoint selection isolation, system prefixes, draft behavior,
revision/evidence binding and lost-acknowledgement failure. Physical distributed,
mixed-platform/backend and performance gates will be tested later on the user's
live three-computer cluster and remain unpassed. All qualification flags remain
false; execution scopes remain empty. Publication skips unit CI per the schedule.

- [ ] T093 Validate per-request LoRA selection, heterogeneous batching and cache identity after full required implementation completion; implementation is published in recipe54.


Recipe54 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, staged executable SHA-256 values
and request_lora_v1/managed_lora_v1 development capabilities are verified in
artifacts/prima-request-lora-published54-manifest-check.json. This grants no runtime
correctness or qualification. Tests/test fixes remain deferred until full required
implementation completion; physical cluster/performance acceptance stays deferred
to the live three-computer cluster and unpassed.


### Recipe55: activated LoRA invocation phases and neutral prefix ownership

Source: 19cfbf1d05d86231e52987c89836dc28b5ca8115 on fork PR #1.
Recipe digest: 0ad322867cf09ffaa0246043221de84d75545187c8cce1b9dc3646273de9ad84.
Native adapter loading parses bounded UINT32 invocation tokens and refuses IDs
outside the base vocabulary. Immutable query APIs expose them; adapter listing
includes token IDs and decoded invocation text. Kluster validates array types/bounds,
freezes invocation count/SHA-256 identities in prima_alora, rechecks staged metadata
and requires activated_lora_v1 before preparation. Invocation identities and the
activated_lora feature enter the immutable qualification scope.

Generation tokenizes before selecting a weight phase, finds the final invocation
with a linear-time overlapping-match scan, disables absent invocations and refuses
multiple pure activated adapters. The causal prompt batch stops at the baseline's
start-minus-one boundary; base and active phases settle over the existing rank-wide
batch-selection protocol. Mixed ordinary/activated selections retain the baseline
ordinary selection path. Per-sequence system prefixes use base weights for pure
activated adapters, restoring the selected batch weights before execution.

Live caches track a neutral prefix. Saved prompt/checkpoint entries record whether
their captured state is neutral; cross-adapter restoration is limited to neutral
entries within the new invocation boundary. Save neutral state at completed boundary
positions, and avoid labelling post-activation recurrent state as a base prefix.
Changed recurrent sequences clear their live state and can restore neutral snapshots.
Context shifts/truncation invalidate neutral reuse; speculative accepted tokens update
its accounting. Snapshot bookkeeping remains charged to existing byte ceilings.

Production server/CLI/profiler compilation passed in artifacts/prima-alora-development-build.log
and the final server rebuild passed in artifacts/prima-alora-development-build2.log.
Edited Python modules passed syntax/import checks. Root package build passed in
artifacts/prima-alora-pin55-package.log. A clean published Windows/Vulkan build is
running in artifacts/prima-alora-published55-build.log. No unit/native/model tests
or test fixes ran; publication skips CI tests according to the requested schedule.

Implementation is published; acceptance remains open. After the full required
implementation finishes, cover malformed/empty/oversized invocation metadata,
unknown vocabulary IDs, absent/repeated/overlapping invocations, boundary offsets,
negative scales, mixed/multiple adapters, truncation/context shift, concurrent
base/active phases, cache/checkpoint reuse and recurrent restoration, draft behavior,
noncausal embedding/rerank compatibility and lost rank acknowledgements. Remaining
original API/model/launch, row/tensor/expert placement and platform requirements
remain open. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the user's live three-computer cluster and remain unpassed.
All qualification flags are false and execution scopes remain empty.

- [ ] T094 Validate activated LoRA invocation phases and neutral cache/snapshot reuse after full required implementation completion; implementation is published in recipe55 and grants no model/API acceptance.


Recipe55 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, staged executable SHA-256 values
and activated_lora_v1 development capability are verified in
artifacts/prima-alora-published55-manifest-check.json. This is build/identity evidence
only. Tests and test fixes remain deferred until full required implementation
completion; physical cluster/performance acceptance stays deferred and unpassed.


### Recipe56: baseline reasoning parser and token-budget implementation

Source: c699b6f425be9f44e3ea911015e0553b7f20ef7d on fork PR #1.
Recipe digest: 56ac11b8a7228e531a770cfa04b3254356cab378e776ba8f8869d5c391712fd6.
Jinja text generation now uses the existing baseline chat parser bridge for plain
responses as well as tools. The bridge accepts reasoning output formats, thinking
toggles, effort/none semantics, named tool choices and structured schemas. Reasoning
streams use the existing protocol item/delta machinery; deepseek-legacy keeps the
baseline stream-specific inline mode. Plaintext Responses reasoning summaries and
Messages thinking history become assistant reasoning content rather than vision.
Stored/opaque reasoning state remains outside this implementation step.

The fork ports the pinned baseline reasoning-budget state machine (MIT provenance
retained) and adapts its model/token/sampler API. Sampling applies budget constraints
before selection, suppresses lazy grammar while thinking, replays completed end tags
and preserves reset/clone state. Generation-prefix tokens initialize the budget;
UTF-8 completion and repeated reasoning blocks retain the baseline state machine.
Sampler allocation cleanup owns grammar, chain and budget through failures.

Deployment format, effort, token budget and thinking mode have native launch flags
and are checked against the immutable manifest on the head. Responses effort and
Messages enabled/disabled/adaptive thinking translate through the shared chat path.
Thinking budget aliases override deployment defaults. Kluster negotiates the new
reasoning_controls_v1 development capability and separately gates reasoning history.
Default managed model templates select Jinja rendering; qualification stays disabled.

Production server/CLI/profiler compilation passed in
artifacts/prima-reasoning-development-build4.log, after adapting the older sampler
factory API. Root package build passed in artifacts/prima-reasoning-pin56-package.log.
Edited Python modules passed syntax/import checks. Clean published Windows/Vulkan
build is running in artifacts/prima-reasoning-published56-build.log. No unit/native/
model tests or test-fix work ran; publication skips CI tests per the user's schedule.
README retains its initial fork/original-branch notice and five high-level change
categories, now including reasoning and managed adapters.

This is implementation and compilation evidence, not API/model acceptance.
Legacy template reasoning behavior, manual reasoning-control operations, opaque or
stored reasoning state and broader original API/model/placement/platform requirements
remain open. After all required implementation finishes, verify prompt/token parity,
effort/default precedence, no-tool/named/automatic/structured output, stream/history
schemas, partial/UTF-8/end-tag behavior, zero/unlimited/repeated budgets, reset/clone,
sampling failures, draft acceptance and manifest drift against the pinned baseline.
Physical distributed, mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster and remain unpassed. Qualification
flags remain false and execution scopes empty.

- [ ] T095 Validate legacy/manual reasoning, parser budgets, streams/history and defaults after full required implementation completion. Recipe58 corrects baseline reasoning content contracts. Stored/opaque-state services are absent from the pinned baseline and are not required for parity; runtime acceptance remains open.


Recipe56 published-build follow-up: clean Windows/Vulkan production compilation
completed. Source revision/cleanliness, staged executable SHA-256 values and the
reasoning_controls_v1 development capability are verified in
artifacts/prima-reasoning-published56-manifest-check.json. Build/identity evidence
does not prove API/model correctness. Tests/test fixes remain deferred until full
required implementation completion; physical cluster/performance gates stay
explicitly deferred and unpassed.


### Recipe57: completion-scoped manual reasoning control and legacy semantics

Source: 59890f484ab5dbba01a264e94bf63723d08be7ed on fork PR #1.
Recipe digest: 64f57835ab455d6e5631eac541a00da8267b039648b5a9be6addabd5c8abd41a.
Native POST /v1/chat/completions/control accepts the baseline id/action contract
with reasoning_end. Opt-in reasoning_control arms the budget sampler even for an
unlimited budget. Server-generated external completion IDs are bound to tasks
before publication; Chat, Responses and Messages use their returned identity.
Control runs on the serialized inference task loop, requires a live armed slot
and returns baseline success/message results. Unknown, finished or reused-slot
identities cannot target another request. A successful pre-prompt force survives
initial sampler reset. Controls do not use a numeric slot ID or defer until idle.

Kluster exposes the endpoint under the same inference API authentication and
qualification boundary, forwarding only id/action and avoiding generation-default
injection. Separate reasoning_control_v1 and reasoning_delimiters_v1 native
capabilities gate manual control and request-supplied tags. Generation requests
carry the opt-in flag through all text protocol translations. Tagged raw/legacy
requests can provide validated start/end delimiters and a forced closing message.

The pinned baseline legacy renderer returns pure content without a thinking parser
or inferred delimiters; Prima now records that explicitly in shared preparation.
Existing legacy prompt rendering and named-tool behavior remain. Jinja schema
constraints are composed once by the baseline bridge, avoiding simultaneous JSON
schema/grammar installation and duplicate fence framing. Native Jinja preparation
retains baseline named/tool-schema composition and optional empty function schemas.
Opaque/stored response state remains implementation work, distinct from plaintext
reasoning history and legacy content behavior.

Production server/CLI/profiler compilation passed in
artifacts/prima-reasoning-control-development-build2.log and the final server rebuild
passed in artifacts/prima-reasoning-control-development-build3.log. Root package
build passed in artifacts/prima-reasoning-control-pin57-package.log; edited Python
modules passed syntax/import checks. Clean published Windows/Vulkan build is running
in artifacts/prima-reasoning-control-published57-build.log. No unit/native/model
tests or test-fix work ran; publication skips CI tests per the user's schedule.

T095 remains open for remaining opaque/stored behavior and later full parity checks.
After all required implementation finishes, validate authenticated control, arm/ID
binding, active/idle/done states, prompt-reset races, slot reuse, concurrent requests,
draft verification, tag aliases/limits, legacy raw content, composed structured/tool
constraints and disconnect/error cleanup. Original broader API/model/placement/
platform requirements remain open. Physical distributed, mixed-platform/backend
and performance gates will be tested later on the user's live three-computer cluster;
all remain unpassed. Qualification flags stay false and execution scopes empty.


Recipe57 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, staged executable SHA-256 values
and manual-control/delimiter development capabilities are verified in
artifacts/prima-reasoning-control-published57-manifest-check.json. This is build and
identity evidence only. Tests/test fixes remain deferred until full required
implementation completion; physical cluster/performance gates remain deferred and
unpassed, with qualification disabled.


### Recipe58: baseline Responses/Messages reasoning wire contracts

Source: fb1ff382d811b8d6b65f66f47b9f71a4ddf836f6 on fork PR #1.
Recipe digest: 1d33377d908ab840362cfa30087fbe82aa71b7027f8faf301166fffe9627f1c5.
Source inspection of the pinned baseline's tools/server/server-chat.cpp and
server-task.cpp corrects an earlier inferred requirement: the baseline rejects
nonempty previous_response_id and has no stored-response service or opaque-state
implementation. It accepts ordinary store/background fields without supplying
those services. This correction preserves the original baseline-parity objective;
it does not remove any requested baseline feature. T095 now records runtime parity
acceptance, rather than requiring an invented persistence/decryption service.

Responses reasoning uses content[{type:reasoning_text,text:...}], empty summary and
empty encrypted_content, matching the baseline. Replay requires the content array
and merges reasoning into the appropriate assistant turn. Streaming emits
response.reasoning_text.delta and completes the reasoning output item, without
invented summary part/text events. Empty previous_response_id is accepted; nonempty
references still refuse. Input/output retain plaintext reasoning history.

Messages thinking blocks include the baseline empty signature placeholder. Streams
keep content blocks open through their deltas, then close them in order; thinking
closure emits signature_delta before content_block_stop. This preserves repeated/
interleaved deltas without writing to closed blocks. The reasoning_wire_v1 native
capability separately gates the corrected reasoning content/stream contract for
qualified Responses/Messages reasoning. It grants no inference qualification.

Production server/CLI/profiler compilation passed in
artifacts/prima-reasoning-wire-development-build.log and final server compilation
passed in artifacts/prima-reasoning-wire-development-build2.log. Root package build
passed in artifacts/prima-reasoning-wire-pin58-package.log. The qualification module
passed syntax/import checks. Clean published Windows/Vulkan build is running in
artifacts/prima-reasoning-wire-published58-build.log. No unit/native/model tests or
test-fix work ran; publication skips CI according to the user's schedule.

After full required implementation finishes, verify complete/partial reasoning,
input replay and assistant/tool grouping, empty/encrypted placeholders, Messages
signature and block ordering, repeated/interleaved reasoning, default/store/background
fields and previous-ID errors against the exact baseline. Original broader API,
model, launch, placement and platform requirements remain open. Physical distributed,
mixed-platform/backend and performance gates will be tested later on the user's
live three-computer cluster and remain unpassed. Qualification stays disabled.


Recipe58 published-build follow-up: clean Windows/Vulkan production compilation
completed. Source cleanliness/revision, staged executable SHA-256 values and
reasoning_wire_v1 development capability are verified in
artifacts/prima-reasoning-wire-published58-manifest-check.json. Tests/test fixes
remain deferred until full required implementation completion; cluster/performance
gates remain deferred and unpassed, with qualification disabled.

Current source-registry inventory in artifacts/prima-architecture-source-inventory.json
records 153 baseline names and 52 Prima names, with exact source revisions/hashes.
This is a registry inventory, not loader/graph/backend support or model acceptance;
it includes specialized/dummy architectures and does not infer support from names.
The explicit missing-name list establishes a concrete starting point for remaining
T013 model implementation. Existing narrow Qwen/Llama work does not close that scope.

### Recipe59: attention sink groundwork for model parity

Source: af3ff6b5c18b623342bef7647a4c157e086589b7 on fork PR #1.
Recipe digest: 2d6c1fde715b172ef9851be8a8457ab882843f27fba4818addd7bcc230b17f3b.
Regular CPU/Vulkan softmax now includes one learned unscaled sink logit per
query head in its stabilized denominator. CPU Flash Attention applies the
corresponding correction to its online normalizer/value accumulator. Shared
KV/attention graph builders accept the optional tensor. Vulkan binds a separate
sink operand and bounds padded row dispatches before operand reads. Unsupported
accelerator implementations refuse the operand instead of silently ignoring it.

This is T013 model implementation groundwork, not full GPT-OSS support or a
qualification pass. GPT-OSS still needs loader/tensors, expert biases and
selected-logit routing, activation, alternating sliding-window attention,
per-layer RoPE, MXFP4, remaining backend kernels, exact placement and profiling.
The broader original model/API/platform/placement scope remains open.

Production server/CLI/profiler builds passed in
artifacts/prima-attention-sinks-development-build.log and the final repeat in
artifacts/prima-attention-sinks-development-build2.log. Root packaging passed in
artifacts/prima-attention-sinks-pin59-package.log. A fresh published-source
Windows/Vulkan build is running in artifacts/prima-attention-sinks-published59-build.log.
No unit/native/model tests or test-fix work ran. Those start only after the full
required implementation finishes. Later numerical coverage must include batched
head indexing, optional/F16/F32 masks, large sinks, fully masked keys, padded
Vulkan rows, regular/Flash Attention and unchanged no-sink behavior.

Physical distributed/mixed-platform/backend validation and performance gates
remain deferred to the user's live three-computer cluster and unpassed. All
qualification flags remain false. Publication skips CI under the implementation-
first schedule. Source/compile identity does not establish runtime acceptance.

Recipe59 published-build follow-up: clean Windows/Vulkan production compilation
completed. Source revision/cleanliness, recipe identity and staged executable
SHA-256 values are verified in
artifacts/prima-attention-sinks-published59-manifest-check.json. Full GPT-OSS
implementation remains open. No unit/native/model tests or test fixes ran;
those remain deferred until full required implementation completion. Physical
cluster/performance gates remain deferred and unpassed; qualification is disabled.

### Recipe60: GPT-OSS expert graph groundwork

Source: 1aeaf5004cf11da8c6c79e7b7adf28ca8a34a998 on fork PR #1.
Recipe digest: 107f234637f811f221bad33d5fd7fe19814b9c1016d9af9412816dff4db25a7e.
The native MoE builder now optionally applies router bias, selected expert
up/gate/down biases and softmax over the selected raw logits. Existing callers
retain their prior graph when these options are absent. Bias gathering preserves
token/expert order and compacts the strided top-k IDs. Vulkan has a genuine I32
copy pipeline for that compaction rather than reinterpreting IDs as floating point
or moving the compaction to the CPU.

A fused equal-shaped F32 ggml_swiglu_oai operation computes the baseline clipped
activation on CPU and Vulkan, using alpha=1.702 and limit=7 in the MoE builder.
It is included in CPU work planning, Vulkan graph preparation/dispatch/admission,
shader generation and debug graph cloning. This advances T013; it does not close
full GPT-OSS support. Loader/tensors, alternating sliding-window attention,
per-layer RoPE, tokenizer, MXFP4, remaining accelerators and exact placement/
profiling integration still remain. Original broader model/API/platform/placement
requirements remain open, and qualification stays disabled.

Production server/CLI/profiler compilation passed in
artifacts/prima-gptoss-moe-development-build.log and final repeat in
artifacts/prima-gptoss-moe-development-build2.log. Root packaging passed in
artifacts/prima-gptoss-moe-pin60-package.log. Fresh published-source Windows/Vulkan
compilation is running in artifacts/prima-gptoss-moe-published60-build.log.
No unit/native/model tests or test fixes ran. After the full required implementation
finishes, check bias gathering with multiple/distinct token/expert IDs and padded
top-k views, integer copy, raw-logit selection/normalization, activation clipping,
parameters/strides and unchanged ordinary MoE behavior. Source/build evidence does
not establish numerical, model or backend acceptance. Physical distributed,
mixed-platform/backend and performance gates remain deferred to the user's live
three-computer cluster and unpassed. Publication skips CI per the user's schedule.

Recipe60 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-gptoss-moe-published60-manifest-check.json. No unit/native/model
tests or test-fix work ran; those start after full required implementation
completion. GPT-OSS and broader integration remain open. Physical cluster and
performance gates remain deferred and unpassed, with qualification disabled.

### Recipe61: GPT-OSS loader, execution graph and tokenizer implementation

Source: 04aa51a2cc6f6dcb12a56fb862ce020e4e898403 on fork PR #1.
Recipe digest: 8de1f2daa44af50e311c7f47e5d5c09ecf2bcc72ac846873f2cff035d1cdfad2.
The native gpt-oss registry now has real metadata/tensor loading and a segmented
execution graph consuming the sink and expert operations from recipes59/60.
Rank-owned tensor loading uses absolute-to-local mapping, required output/expert
bias tensors and separate or fused QKV weights with optional attention biases.
Scalar/default or per-layer SWA patterns select each absolute layer's mask and
RoPE frequency; expert widths accept scalar/per-layer metadata. GPT-4o BPE splitting
uses the pinned baseline regex, with Harmony return/call EOG handling that keeps
intermediate end markers usable between analysis and final channels.

KV shift and defragmentation now map absolute owned layers to local cache tensors;
K-shift selects the same layer-specific rotary parameters as generation and skips
tensor work for a zero-transformer coordinator. Noncausal decoder masks preserve
cached sequence isolation while allowing future positions; SWA masks retain their
standard lower-window cutoff. Existing shared attention graph paths are preserved.

This advances T013/T014 implementation but does not establish full GPT-OSS support.
MXFP4, full model profiler roles/rates, compact SWA KV storage and remaining
accelerator operations remain open. Current SWA uses full-context KV storage with
masking; do not treat it as the baseline's compact cache memory contract. The broader
original model/API/platform/placement objective remains open, including arbitrary
row/tensor/expert placement. No qualification is enabled.

Production builds passed in artifacts/prima-gptoss-loader-development-build.log,
artifacts/prima-gptoss-loader-development-build2.log,
artifacts/prima-gptoss-loader-development-build3.log and the final
artifacts/prima-gptoss-loader-development-build4.log. Root package build passed in
artifacts/prima-gptoss-loader-pin61-package.log. Clean published Windows/Vulkan
compilation is running in artifacts/prima-gptoss-loader-published61-build.log.
After full required implementation completion, test separate/fused QKV, optional
biases, SWA metadata/masks/RoPE, tokenizer/Harmony multi-channel stopping,
noncausal/cached sequence isolation, repeated/zero-rank segments, KV shift/defrag,
cache snapshots, LoRA and existing model regressions. None of those unit/native/model
tests or test fixes ran in this phase. Physical distributed/mixed-platform/backend
and performance gates remain deferred to the user's live three-computer cluster
and unpassed. Publication skips CI per the implementation-first schedule.

Recipe61 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-gptoss-loader-published61-manifest-check.json. Fork PR #1 is open
with that exact head revision. The updated source-only registry inventory is in
artifacts/prima-architecture-source-inventory61.json (153 baseline/53 Prima names);
architecture names do not establish loader/model/backend acceptance. No tests
or test fixes ran, and full GPT-OSS/integration implementation remains open.
Physical cluster/performance gates remain deferred and unpassed, with all
qualification flags disabled.

### Recipe62: MXFP4 CPU/Vulkan format implementation

Source: c31c5fe22703dbbac621a8ce6f64e04bf67b2833 on fork PR #1.
Recipe digest: 338afe98ca70127017bb2a4a23dcbd0f215bd5551c2f06cfde54308ffb2813e1.
GGML/GGUF type39 now retains MXFP4's actual 32-value/17-byte block layout and
E8M0 scale, with reference encoding/decoding, reserved-exponent row validation,
Q8_0 CPU dot products and generic CPU quantized operations. Native file-type
identity/policy uses MXFP4 routed expert matrices and Q8_0 other quantized matrices,
with explicit input/output overrides preserved. The CPU reference implementation
is adapted from the exact pinned baseline rather than relabeling another format.

Vulkan shader generation and 33 independent pipeline registrations provide direct
compressed matrix/vector/indexed-expert execution, F32 accumulation selection,
row gathering and device dequantization. Packed exponent/nibble storage stays on
the device. CUDA/Metal/SYCL/CANN/Kompute admission refuses MXFP4 until their kernels
are implemented; refusal does not qualify the requested accelerator. Root GGUF
layout accounting already reads compressed file-offset spans, including alignment,
and needed no format-specific storage approximation.

This advances T013 model/format implementation. Full GPT-OSS profiler roles/rates,
compact sliding-window KV storage and remaining accelerator kernels still remain,
as do broader original model/API/platform/row/tensor/expert placement requirements.
It does not establish full GPT-OSS support or enable qualification.

Production builds passed in artifacts/prima-mxfp4-development-build.log and the
final artifacts/prima-mxfp4-development-build2.log. Root package build passed in
artifacts/prima-mxfp4-pin62-package.log. Clean published Windows/Vulkan compilation
is running in artifacts/prima-mxfp4-published62-build.log. No unit/native/model tests,
test-fix work or performance checks ran. After full required implementation
finishes, cover all FP4 codes/signs/zero, exponent boundaries/rejection, packed
block/row/expert strides, CPU input quantization, GPU matrix/vector/indexed/gather
and F32 dispatch, format policy/overrides and unchanged neighboring formats. Model
checks must use original complete MXFP4 GGUF bytes and native prompt/token evidence.
Physical distributed/mixed-platform/backend and performance acceptance remains
deferred to the user's live three-computer cluster and unpassed. Publication skips
CI under the implementation-first schedule; qualification remains disabled.

Recipe62 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-mxfp4-published62-manifest-check.json. Fork PR #1 is open at the
same source revision. No unit/native/model tests, test fixes or performance
checks ran; those tests start after full required implementation completion.
Full GPT-OSS/integration remains open. Physical cluster/performance gates remain
deferred and unpassed, with all qualification flags disabled.

### Recipe63: GPT-OSS typed full-model profiler implementation

Source: 901730e99979fa8d8dd0d4ec35772b69dce83ab2 on fork PR #1.
Recipe digest: 0865407e9c6c34973700fc132c939c3cd09ec98fce06baadb82de54905080f1b.
The typed native model-cost builder now recognizes GPT-OSS post-attention norm,
sinks, router/expert biases, per-layer expert widths and separate or fused QKV.
It checks required roles/dimensions, refuses ambiguous fused/separate weights and
unknown bias roles, and counts selected-expert arithmetic/active bytes while
retaining every expert's resident storage. Fused QKV includes rotary/attention/KV
work once. GPT-OSS activation, selected-logit softmax, router sorting and weighted
expert reduction contribute logical F32 work estimates.

Attention costs retain the full KV views used by the current graph; no compact
SWA cache or skipped masked-key computation is inferred. The existing typed
matrix-rate path encodes real MXFP4 through recipe62's CPU/Vulkan kernels. Native
profiling refuses unsupported requested GPU kernels, and GPT-OSS GPU profiling
explicitly refuses backends without its sink/activation operations, even for
ordinary matrix formats. Kluster's typed decoder already accepts matching rate
keys and recomputes each cost from its rate/byte tables; schema2 is preserved.

This advances T013/T010 implementation. These roofline costs, including non-matrix
work proxies, are not measured instructions, full-model generation timings,
quality/performance acceptance or qualification. Compact sliding-window KV,
remaining accelerator kernels and broader original model/API/platform/row/tensor/
expert placement requirements remain open. Measurements and unit/native/model
checks/test fixes remain deferred until the full required implementation finishes.

Production builds passed in artifacts/prima-gptoss-profiler-development-build.log
and final artifacts/prima-gptoss-profiler-development-build2.log. Root packaging
passed in artifacts/prima-gptoss-profiler-pin63-package.log. Clean published
Windows/Vulkan compilation is running in
artifacts/prima-gptoss-profiler-published63-build.log. No profiling measurements,
tests/test fixes or performance checks ran. Later checks cover malformed/missing
roles/dimensions, separate/fused equivalence, selected expert bytes/arithmetic,
activation/router estimates, original MXFP4 operand encoding/completed requested
backend work, model cost/rate vectors and signed source/model/hardware/settings
cache identity. Physical distributed/mixed-platform/backend and performance gates
remain deferred to the live three-computer cluster and unpassed. Qualification
stays disabled; publication skips CI under the implementation-first schedule.

Recipe63 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-gptoss-profiler-published63-manifest-check.json. Fork PR #1 is open
at that exact head. No profiling measurements, unit/native/model tests, test fixes
or performance checks ran. Full required implementation remains open, including
compact SWA KV and remaining accelerator kernels. Physical cluster/performance
gates remain deferred and unpassed, with all qualification flags disabled.

### Recipe64: owned KV storage-layout prerequisite for compact SWA

Source: b45ae7e2e3bb85eb586b0b564cb68d1b1914278f on fork PR #1.
Recipe digest: c7e2b39cdc8a6065aa94c8059baf6638b042d98380b1d9228d2d94f9ceb7c09b.
GPT-OSS KV initialization now records an immutable rank-local storage layout
classified by absolute owned layers. It computes the pinned baseline's compact
unified-pool size, pad256(min(full_cells, window*sequence_capacity+microbatch)),
with checked ownership/pattern/dimensions and allocation products. The incoming
microbatch needs room beside past window tokens so its earliest token retains
its required history. Zero-transformer ranks retain an empty owned-layer table.

This is a prerequisite, not compact KV execution. Allocation explicitly keeps
full-size mode; no compact capability or user flag is advertised. Separate cell
state/reclaim, attention/mask views, sequence lifecycle and snapshot serialization
must be connected before activation. Current profiler/capacity behavior still
reflects the active full views; no memory or performance gain is claimed. Those
compact-cache consumers, remaining accelerator kernels and broader original model/
API/platform/row/tensor/expert placement requirements remain open.

Production builds passed in artifacts/prima-swa-layout-development-build.log and
final artifacts/prima-swa-layout-development-build2.log. Root packaging passed in
artifacts/prima-swa-layout-pin64-package.log. Clean published Windows/Vulkan
compilation is running in artifacts/prima-swa-layout-published64-build.log.
No unit/native/model checks, test fixes, measurements or performance checks ran.
After full required implementation finishes, cover pool sizing/overflow/ownership,
full-mode allocation, compact microbatch overlap, shared/rewound/removed/copied
sequences, masks/attention, KV shifts/defrag, prompt/checkpoint snapshots and
coordinator/worker lifecycle. Physical distributed/mixed-platform/backend and
performance gates remain deferred to the user's live three-computer cluster and
unpassed. Qualification stays disabled; publication skips CI per the current
implementation-first schedule.

Recipe64 published-build follow-up: clean Windows/Vulkan production compilation
completed. Published source revision/cleanliness, recipe identity and staged
executable SHA-256 values are verified in
artifacts/prima-swa-layout-published64-manifest-check.json. Fork PR #1 is open
at that exact head. Compact execution remains disabled and its consumer wiring
remains implementation work. No tests, test fixes, measurements or performance
checks ran. Full required implementation remains open. Physical cluster and
performance gates remain deferred and unpassed; qualification stays disabled.


Recipe65 implementation progress: transactional compact SWA row metadata
(4ccaa9900099385dfdb4f38916236fd0a9b84a88). Physical rows refer to the full
cache's authoritative sequence cells. Reservation preparation is read-only;
commit rejects stale/foreign tickets. Restore validates the complete map before
publication, and defragmentation can remap global IDs without moving compact rows.
Full-size storage remains active: allocation/views/masks, sequence retention and
rewind, shifts, snapshots and managed budgets still need coordinated integration.
Development server/CLI/profiler compilation and Kluster packaging passed. No
unit/native/model tests or test fixes ran. All such checks remain deferred until
full required implementation finishes. Physical distributed and performance
acceptance remain deferred to the live three-computer cluster and unpassed.
Broader model/API/platform/accelerator/exact-placement implementation remains
open; qualification is disabled. Clean published build evidence will be recorded
separately after compilation completes.


Recipe65 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Exact published source, recipe identity and staged binary
SHA-256 values are recorded in
artifacts/prima-swa-map-published65-manifest-check.json. Packaging also passed.
No unit/native/model tests, test fixes or performance checks ran. Compact execution
remains disabled pending consumer wiring. Full required implementation remains
open, physical cluster/performance gates remain deferred and qualification is off.


Recipe66 implementation progress: compact SWA runtime consumers and sparse
snapshots (3c64365f2daf52e3bc688a25bcc29b0a4375ac6a). Decoder admission prepares
compact rows before full-cache reservation and refuses missing rewind history.
Layer-specific K/V views and masks use physical compact row order and extent;
K shifts use mapped deltas, while defrag preserves compact tensor row locations.
Versioned snapshots carry sparse compact rows by serialized global-cell ordinal,
validate pool dimensions/ownership/pattern and handle both V storage layouts.
Failed or truncated restore clears its destination. Reservation commit now uses
storage allocated before full-cache admission; contiguous search skips occupied
spans. No performance measurements establish a benefit.
Compact mode remains disabled pending managed budgets, complete sequence/replay
handling and coordinated replay decisions across differing rank ownership.
Broader model/API/platform/backend/exact-placement implementation remains open.
Final production compilation passed in
artifacts/prima-swa-consumers-development-build3.log; packaging passed in
artifacts/prima-swa-consumers-pin66-package.log. Clean published compilation is
running in artifacts/prima-swa-consumers-published66-build.log.
No unit/native/model tests or test fixes ran. All are deferred until full required
implementation finishes. Physical distributed/mixed-platform/backend and
performance acceptance remain deferred to the live three-computer cluster and
unpassed; qualification remains disabled.


Recipe66 published-build follow-up: clean Windows/Vulkan production compilation
completed. Exact clean source, recipe identity and staged executable SHA-256
values are recorded in
artifacts/prima-swa-consumers-published66-manifest-check.json. Packaging passed.
These are build/source checks only. Compact activation, managed budgets and
complete local/distributed replay handling remain implementation work; broader
integration requirements remain open. No unit/native/model tests, test fixes or
performance checks ran. Physical cluster and performance acceptance remain
explicitly deferred and unpassed; qualification stays disabled.


Recipe67 implementation progress: all-rank KV rewind and explicit full SWA
storage (a99c14d2674d2a023c5785199c036f30401bc281). Server prefix reuse and
speculative suffix trimming perform read-only preflight on every physical rank,
then settle commit or abort. Commands bind manifest, monotonic revision,
sequence/range, phase, world and hops; malformed/stale/foreign/lost settlement
faults the context rather than allowing generation with uncertain rank state.
A worker can request replay even when the coordinator owns no SWA layers.
Native --swa-full and C API defaults explicitly retain full storage. Kluster now
resolves/prices full SWA storage and checkpoints and passes that flag to each
rank, fixing the mismatch between compact estimates and actual full allocation.
This is current-layout accounting, not completion of compact managed budgets.
Compact activation remains disabled. Compact budgets, CLI reuse, context shifts
and positional transforms remain implementation work, as do broader original
model/API/platform/accelerator/exact-placement requirements.
Final development production compilation passed in
artifacts/prima-kv-rewind-development-build3.log. Changed Python source compiles;
packaging passed in artifacts/prima-kv-rewind-pin67-package.log. Clean published
compilation is running in artifacts/prima-kv-rewind-published67-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the live
three-computer cluster and unpassed. Qualification remains disabled.


Recipe67 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, recipe identity and staged executable hashes
are recorded in artifacts/prima-kv-rewind-published67-manifest-check.json.
Help/capability metadata exposes --swa-full, swa_full_v1 and kv_rewind_v1;
these metadata probes do not establish execution correctness. Packaging passed.
No unit/native/model tests, test fixes or performance checks ran. Compact budgets,
complete sequence transforms/replay and activation remain implementation work;
broader original integration scope remains open. Physical cluster and performance
acceptance remain deferred and unpassed; all qualification gates remain false.


Recipe68 implementation progress: CLI replay and checked positional transforms
(c96308fdfcc1a92f10762a9e9a5b4a9a320b5609). CLI session reuse checks the earliest
actual decode boundary, including logits reconstruction. Transformer ranks compare
a digest of their declared local session-token prefix and require the cached
predecessor; missing/mismatched rank sessions or evicted history cause clearing
and replay. Exact IDs are vocabulary/context bounded and hashed with a domain,
count and little-endian uint32 encoding. Checked mutation/actual decode invalidate
the declaration. Zero-transformer relays require no local tensor predecessor.
The local draft helper replays its selected prompt window when rewind fails.
Ordinary CLI context shifts keep exact evaluated token history independently of
session-file saving and use all-rank checked removal/shift or retained-token replay.
The native checked transform API simulates add/divide operations before mutation,
checks signed position/delta bounds, checks resulting SWA history for every shared
sequence, handles recurrent tails, and binds versioned operands to ring settlement.
Compact activation stays disabled pending compact budgets and complete server
context-shift/self-extend replay. Broader model/API/platform/backend/exact-placement
requirements remain open. Final production compilation passed in
artifacts/prima-cli-replay-development-build5.log; packaging passed in
artifacts/prima-cli-replay-pin68-package.log. Clean published compilation is
running in artifacts/prima-cli-replay-published68-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the user's
live three-computer cluster and unpassed. Qualification remains disabled.


Recipe68 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, recipe identity and staged executable hashes
are recorded in artifacts/prima-cli-replay-published68-manifest-check.json.
Advertised kv_transform_v1/kv_prefix_v1 metadata matches the published source;
metadata does not establish execution correctness. Packaging passed.
No unit/native/model tests, test fixes or performance checks ran. Compact managed
budgets, server context-shift/self-extend replay and activation remain open.
Server replay also needs completed-token history when prompt caching is disabled.
Broader original integration requirements remain open. Physical distributed and
performance acceptance remain deferred and unpassed; qualification stays false.


Recipe69 implementation progress: completed server history and ordinary context
replay (bbc57552674264992a9367bf2249c5d775871b3d). Per-slot token history records
successful evaluations independently of cache_prompt and retains system-prefix
and base/active aLoRA phase identity. Speculative history is trimmed after accepted
prefix settlement. Failed batch decoding trims optimistic cache entries to known
completed history before later reuse. Reused prompt prefixes initialize from the
exact incoming IDs. Ordinary context shift preserves the full system prefix,
uses checked all-rank remove/shift, and replays retained tokens with their stored
weight phases if required history is missing. Sampler/output state stays with the
request; failed replay clears the affected sequence through existing recovery.
This does not complete self-extend replay or compact managed budgets. Both remain
implementation work before compact activation. Broader original model/API/platform/
accelerator/exact-placement scope remains open.
Development production compilation passed in
artifacts/prima-server-history-development-build.log; packaging passed in
artifacts/prima-server-history-pin69-package.log. Clean published compilation is
running in artifacts/prima-server-history-published69-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the live
three-computer cluster and unpassed; qualification remains disabled.


Recipe69 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, exact recipe identity and staged executable
hashes are recorded in artifacts/prima-server-history-published69-manifest-check.json.
Packaging passed. This evidence covers source/build identity only; no unit/native/
model tests, test fixes or performance checks ran. Compact budgets, self-extend
position/replay handling and activation remain implementation work. Broader
original integration requirements remain open. Physical distributed and
performance acceptance remain deferred and unpassed; qualification stays false.


Recipe70 implementation progress: grouped KV transform programs and CLI
self-extend replay (023c788d4a12a6ee0af7880840b7855d303e8255). Native preflight
simulates up to eight ordered add/divide steps together, checks every intermediate
signed position/delta and checks SWA history only after the final result.
The complete program is bound to all-rank prepare/commit/abort settlement. CLI
self-extend submits its three operations as one program, tracks evaluated token
positions and replays all ranks at transformed positions when history is missing.
Its session files omit the transform schedule, so their tokens are rebuilt instead
of reusing compressed state as an ordinary prefix. Recurrent self-extend does not
accumulate transformer replay history. This does not establish model correctness
or performance. Server self-extend replay and compact managed budgets remain
implementation work before activation; broader original integration scope remains
open. Compact mode and inference qualification remain disabled.
Production development compilation passed in
artifacts/prima-transform-program-development-build3.log; packaging passed in
artifacts/prima-transform-program-pin70-package.log. Clean published compilation
is running in artifacts/prima-transform-program-published70-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the live
three-computer cluster and unpassed.


Recipe70 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, exact recipe identity and staged executable
hashes are recorded in artifacts/prima-transform-program-published70-manifest-check.json.
Advertised kv_transform_program_v1 metadata matches source; metadata is not runtime
acceptance. Packaging passed. No unit/native/model tests, test fixes or performance
checks ran. Server self-extend replay, compact budgets and activation remain open,
as does the broader original integration scope. Physical distributed and
performance acceptance remain deferred and unpassed; qualification stays false.


Recipe71 implementation progress: server self-extend replay and origin-aware
transform programs (bcd90dd6bcee3017f02c228c245d8a3eb851368d). The extended API
preserves the original program API/layout and adds division relative to a fixed
system-prefix origin. Server self-extend keeps evaluated positions and weight
phases, settles composed transforms on every rank, rebases pending rows and
replays at resulting positions when required history is missing. Counters advance
only for that slot's rows after a successful decode view; failed attempts and
other/idle slots do not advance them. Abandoned batches roll back optimistic
prompt progress to completed counts. Positive retry requires complete history and
restores the frozen batch selection; incomplete recurrent self-extend history is
refused rather than silently reconstructed. System prefixes now evaluate on each
real request sequence, avoiding shared sequence-zero mapping across head/workers
and independent RNN/transformed request state.
Compact mode stays disabled pending managed budgets and final activation review.
Broader original model/API/platform/accelerator/exact-placement scope remains open.
Development compilation passed in
artifacts/prima-server-selfextend-development-build4.log; packaging passed in
artifacts/prima-server-selfextend-pin71-package.log. Clean published compilation is
running in artifacts/prima-server-selfextend-published71-build.log.
No unit/native/model tests, test fixes or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance acceptance remain deferred to the live
three-computer cluster and unpassed; qualification stays disabled.


Recipe71 published-build follow-up: clean Windows/Vulkan server/CLI/profiler
compilation completed. Clean source, exact recipe identity and staged executable
hashes are recorded in artifacts/prima-server-selfextend-published71-manifest-check.json.
Advertised kv_transform_program_v2 metadata matches source; metadata does not prove
runtime correctness. Packaging passed. No unit/native/model tests, test fixes or
performance checks ran. Compact budgets and final activation review remain open,
as does the broader original integration scope. Physical distributed and
performance acceptance remain deferred and unpassed; qualification stays false.


Recipe72 KV budgeting implementation status (2026-10-13):
The immutable Prima settings now bind a KV sequence ceiling of parallel_slots + 1
for the head's reserved sequence; each local draft context reserves one sequence.
Estimates price Prima's current unified pool, recurrent state, decode/verification
outputs, completed-history metadata and compact-map reservation/restore storage.
GPT-OSS scalar pattern zero means all sliding layers; malformed window/pattern
metadata refuses. Other Prima architectures retain full SWA tensor pricing until
their native compact paths exist. Full-cache execution remains explicitly active:
compact activation still requires final consumer review and launch wiring.
Native KV size helpers use actual per-layer dimensions and quantization block
storage, recurrent sequence cells and the configured GPT-OSS storage layout,
with checked bounds. These are estimates, not hardware allocator measurements.
Fork PR #1 now points to 48c6a57fb002f1129381f0452489f48ece005a38.
Recipe72 digest: 2c300fe5bbd719154f472e27c11f775041fb3aeb77c3e49e249e1b5f46768e3c.
Production development compilation and Python syntax checks passed; packaging
passed before the pin update. Clean published compilation is running in
artifacts/prima-kv-accounting-published72-build.log.
ALL unit/native/model tests and test-fix work remain deferred until the full
required implementation is finished. Physical distributed, mixed-platform/backend
and performance acceptance remain deferred to the user's live three-computer
cluster and unpassed. Qualification remains disabled. The broader original
model/API/platform/accelerator/exact-placement integration scope is still open.


Recipe72 reserve refinement: GPT-OSS dense and sliding input masks coexist,
including full-storage mode, so both masks are counted with peak attention
scratch. Prima output reservation includes a full microbatch as well as sequence
and speculative verification ceilings. Pin72 packaging and syntax compilation
passed in artifacts/prima-kv-accounting-pin72-package.log; tests remain deferred.


Recipe72 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. Exact recipe/source identity, clean source and staged
executable hashes are recorded in
artifacts/prima-kv-accounting-published72-manifest-check.json. Pin72 packaging and
syntax checks passed. No unit/native/model tests, test fixes, inference or
performance checks ran. Compact activation/review and the broader original
integration remain unfinished; qualification remains false. Tests start only
after the full required implementation is finished. Physical distributed and
performance acceptance remain pending for the user's live three-computer cluster.


Compact GPT-OSS activation implementation (recipe73, 2026-10-13):
The final storage-consumer review found and fixed worst-case graph reservation:
compact K/V write offsets now fit the compact pool and attention/mask reserves use
its full capacity rather than live occupancy. Initialization and reserves after
shift/defragmentation share that path without mutating live cache state.
Compact GPT-OSS storage is now the native/managed default, with --swa-full and
--swa-compact selecting an explicit mode on every rank. Native manifests bind the
mode, HTTP slots and reserved head sequence ceiling; preparation negotiates the
storage capability before execution. Draft contexts retain one sequence. The
server checks overflow before adding its reserved sequence.
Only GPT-OSS has this compact storage path. Other architectures remain fully
priced and broader original model/API/platform/accelerator/placement work remains
open. The new capability is implementation metadata, not runtime acceptance.
Production compilation is running; no unit/native/model tests or test-fix work
have run. All tests remain deferred until full required implementation finishes.
Physical distributed, mixed-platform/backend and performance acceptance will be
tested later on the user's live three-computer cluster; all are unpassed and
qualification remains disabled.


Recipe73 publication: PR #1 head/source pin is
33921e29c84ed46bc5f1fc240be8b010df657bb8; normalized recipe digest is
17bd83c73590d0d125987cf58cd145fbaaf3de48f58b6299e2e23d599b22cdc8.
Development server/CLI/profiler compilation completed in
artifacts/prima-compact-activation-development73-final.log. Pin73 packaging and
Python syntax checks passed. Clean published compilation is running in
artifacts/prima-compact-activation-published73-build.log. GPT-OSS Flash Attention
mask reservations also count the simultaneous F32 inputs and F16 casts.
Compact GPT-OSS execution is implemented, but model/native/unit acceptance is
unverified and deferred until the full required integration implementation is
finished. Broader original integration tasks remain open. Physical distributed,
mixed-platform/backend and performance gates will be tested later on the user's
live three-computer cluster. Qualification remains disabled.


Recipe73 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. The source checkout is clean and its commit, normalized
recipe identity and all three staged executable hashes match the published pin.
artifacts/prima-compact-activation-published73-manifest-check.json records this
source/build-only evidence. Published server capability metadata advertises
GPT-OSS compact storage; CLI/server help exposes both storage flags. Neither
metadata nor successful compilation is runtime correctness acceptance.
Packaging and syntax checks passed. No unit/native/model tests, test fixes,
inference, profiling measurements or performance checks ran. Those tests remain
deferred until full required integration implementation finishes. Other
architectures' compact paths and broader original model/API/platform/accelerator/
exact-placement work remain unfinished. Physical distributed, mixed-platform/
backend and performance gates will be tested later on the live three-computer
cluster. All qualification remains disabled.


Gemma3 model-parity implementation progress (recipe74, 2026-10-13):
Added the native architecture registry, metadata/pattern parser and rank-owned
loader for separate/fused QKV, per-head Q/K norms, post-attention/post-FFN norms,
optional/tied output and optional resident retrieval projections. The shared
manifest-controlled graph now implements Gemma3 token-only embedding scaling,
GELU gating, per-layer Q/K head counts, query scaling and optional final logit
softcapping. SWA uses the baseline six-layer pattern, independent SWA RoPE
frequency/scale and the existing checked compact admission/rewind/shift/snapshot
path; no-window variants remain dense. Model loading and segmented graph
admission include Gemma3. Native and managed KV/compute/checkpoint reservations
include compact/full storage and both attention masks. The typed profiler counts
head/post norms, GELU/query/embedding/softcap work and resident auxiliary weights;
these are estimates, not measured model acceptance or speedups.
The source registry is now 54 versus the pinned baseline's 153 (99 registry
entries still absent), recorded in artifacts/prima-architecture-source-inventory74.json.
Registry presence does not establish graph/API/backend correctness. Gemma3
retrieval-projection embedding output and vision/projector integration remain
unfinished. Embedding requests with those projections refuse explicitly while
the API contract is incomplete; ordinary generation still loads/prices the
resident weights. Broader original model/API/platform/accelerator/exact-placement
scope remains open. This work does not complete T013/T014 or full integration.
Production development compilation passed before the final profiler refinement;
its publication build is running in artifacts/prima-gemma3-development74-publish.log.
No unit/native/model tests, test fixes, inference, profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the user's live three-computer cluster and are unpassed.
All qualification remains disabled.


Recipe74 publication: PR #1/source pin is ec2d4f0ba5619b747ee8b8cb2efdefe571abab2e;
normalized recipe digest is 96f26a038f4b1731d5cace128d6f795c1881dd4188382924f84d88e64db86802.
Development server/CLI/profiler compilation completed in
artifacts/prima-gemma3-development74-publish.log. Pin74 packaging and Python
syntax checks passed. Clean published compilation is running in
artifacts/prima-gemma3-published74-build.log.
The architecture inventory also records the native graph dispatch allowlist:
llama, qwen2, qwen3, qwen3moe, gpt-oss and gemma3. Existing registry entries outside
that list still need complete execution paths; the 99 absent baseline names are
only one part of the remaining model-parity scope.
No unit/native/model tests or performance checks ran; the implementation-first
and live three-computer cluster deferrals remain in effect. Qualification is false.


Recipe74 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. Source checkout cleanliness, exact pin/recipe identity and
all three staged executable hashes are recorded in
artifacts/prima-gemma3-published74-manifest-check.json. General/Gemma3 compact
capability and both storage-option help entries are advertised metadata only.
Final packaging and syntax checks passed in
artifacts/prima-gemma3-pin74-package-final.log. Managed GGUF layouts classify
retrieval dense_2/dense_3 weights with output tensors, matching their native
output-device allocation and preventing a GPU output-head reservation gap.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Gemma3 retrieval embedding-output/vision contracts and broader original
model/API/platform/accelerator/exact-placement integration remain unfinished.
Physical distributed, mixed-platform/backend and performance gates will be
tested later on the user's live three-computer cluster. All qualification remains
false; source/build metadata does not establish model correctness or acceptance.


Embedding output and decode metadata implementation (recipe75, 2026-10-13):
Native output-width metadata/C API now separates transformer width from projected
embedding width. Gemma3 retrieval chains are checked on every rank; token vectors
project directly, while pooled means/CLS/LAST vectors aggregate across completed
microbatches before a head-only projection graph. This preserves the order of
pooling versus quantized projection. Head buffer allocation, reordering, getters
and token-vector session spans use output width. Pooled input columns use the
sequence ceiling, including sparse/shared IDs. Decoder extraction is implemented
and the previous refusal is removed. Embedding graphs retain result_norm and omit
an unused language-model head. Projection schedules reset before metadata cleanup,
including failure paths, and completed projection weights are included in device
execution evidence through the actual reused head scheduler.
Worker output counts now derive from the received batch, not an empty placeholder.
Bounded metadata includes all-output selection and flattened shared sequence tags;
receiver vectors own those bytes, reject duplicate/unknown/malformed fields and
check sequence/byte ceilings before allocation. Distributed preparation requires
decode_metadata_v2. This remains an admitted private ring, not a new public listener.
Native embedding server responses use projected width and distinct pooled/token
vectors. Every embedding token requests output without prompt-cache reuse. Native
routes use matrix responses; OpenAI requires pooling, supports float/base64,
reports evaluated-token usage and orders entries by input index. Normalization
controls are preserved, and bounded OpenAI dimensions are an added fork feature.
Requests exceeding batch capacity refuse instead of indefinitely waiting.
Managed estimates include projection intermediates, pooled matrix/input/output
copies and larger output vectors; resident projections remain charged to the
output device. These are conservative estimates, not allocator/performance proof.
Production builds passed before the final metadata refinements; their last build
is running in artifacts/prima-embedding-output-development75-final3.log. Python
syntax checks passed. API/gateway/model/backend and physical distributed acceptance
are unverified. Vision/projector and broader original model/API/platform/accelerator/
exact-placement integration remain open; this does not finish T013/T014 or the goal.
No unit/native/model tests, test fixes, inference, profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and are unpassed.
All qualification remains disabled.


Recipe75 publication: PR #1/source pin is
b1b5e7cff3b8ecdc44533e291a32fafeb36f4dee; normalized recipe digest is
7da25607787674ac6e24af041d36faf7383aed3d96bd9254ea9eb301c1cb930d.
Development server/CLI/profiler compilation completed, followed by production
embedding/retrieval companion-tool compilation in
artifacts/prima-embedding-output-development75-tools.log. Companion tools now use
projected output width. Packaging and Python syntax checks passed in
artifacts/prima-embedding-output-pin75-package.log. Clean published compilation
is running in artifacts/prima-embedding-output-published75-build.log.
Source/build evidence does not establish vector accuracy, API/gateway parity,
backend execution or distributed acceptance. Full required implementation remains
unfinished and all unit/native/model tests and test-fix work remain deferred.
Physical distributed/mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster; none is passed and qualification
remains disabled.


Recipe75 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. The source checkout is clean; exact source/recipe identity
and all three staged executable hashes match the published pin, recorded in
artifacts/prima-embedding-output-published75-manifest-check.json. Published metadata
advertises embedding_output_v1/decode_metadata_v2, embedding/pooling and explicit
storage options. This is advertised metadata, not API/model/backend acceptance.
Packaging and Python syntax checks passed. Embedding/retrieval companion tools
also compiled in the development checkout with projected-width allocation.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Embedding gateway/request-contract completion and acceptance, vision/
projector integration and the broader original model/API/platform/accelerator/
exact-placement scope remain open. Physical distributed/mixed-platform/backend
and performance gates will be tested later on the live three-computer cluster;
none is passed. Qualification remains disabled and the thread goal remains active.


Recipe75 role-specific help/launch follow-up: the first help inventory incorrectly
required the worker CLI to expose server embedding switches. That metadata check
refused; it was not a unit/model test or runtime acceptance. The corrected receipt
checks server embedding/pooling controls and both binaries' storage switches,
recording that CLI workers receive output selection through decode_metadata_v2.
Managed extras now accept bounded embedding/pooling options and emit them only on
rank zero. Manifest feature discovery uses embeddings rather than chat for that
mode and native embedding capability is negotiated. Unknown extras still refuse;
worker decoder settings remain separately explicit. The corrected role-specific
metadata is recorded in the published75 manifest receipt. Gateway/request-contract
completion and runtime acceptance remain open. Packaging passed before the final
pure parser validation adjustment; syntax compilation passed afterward. No unit/
native/model tests or performance checks ran. All implementation-first and live
three-computer cluster deferrals remain in effect; qualification stays disabled.


Recipe75 final managed-launch completion: embedding/pooling extras are validated,
preserved and routed to the head only. Parser syntax compilation and final
packaging passed in artifacts/prima-embedding-output-pin75-package-final2.log.
The role-specific published help receipt passed after correcting the inventory;
CLI workers intentionally receive output selection through batch metadata.
No tests or performance checks ran. Source/native API implementation is distinct
from unfinished gateway/request-contract completion and unverified model/backend/
physical distributed acceptance. Full required integration is still unfinished;
all unit/native/model tests and test fixes remain deferred until implementation
finishes. Physical cluster/performance gates remain pending for the live
three-computer cluster, and qualification remains disabled.


Embedding gateway and role-binding implementation (recipe76, 2026-10-13):
Added authenticated POST /v1/embeddings to the agent endpoint/route registration,
with inference activity observation and the existing proxy transport/error path.
The gateway requires a unique served model ID, rejects a mismatched requested ID
and a Prima catalog inconsistent with the active deployment, and preserves native
input/encoding/normalization fields without applying chat generation defaults.
Prima request feature discovery now distinguishes embeddings and explicit float/
base64, dimensions and normalization scopes; the native output capability is
required in addition to execution qualification. No qualification is granted by
this wiring. Existing generation routes retain their defaults.
Native manifests parse the approved embedding/pooling extras with their actual
arity, compare head mode/selected pooling and require workers to retain their
non-server role. Unexpected environment/argv mode overrides refuse. Preparation
negotiates embedding_role_v1 before execution. Rank pooling without a loaded
classifier refuses during context initialization rather than graph assertion.
This does not provide missing classifier/model architectures or claim rerank parity.
Development production compilation passed before the final guard/capability
refinement; its final build is running in
artifacts/prima-embedding-gateway-development76-final.log. Python syntax checks
passed before the last source additions. Source/API/backend/physical acceptance
remains unverified. Broader original model/API/platform/accelerator/exact-placement
work and the integration goal remain unfinished.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and are unpassed.
Qualification remains disabled.


Recipe76 publication: PR #1/source pin is
7f6a1361805e0f7c8324970f4588e5250849661c; normalized recipe digest is
e4dff45bd2eb084fcc969c7ce1e16b1a76becd302bf40793528d78b879b50d20.
Development server/CLI/profiler compilation completed in
artifacts/prima-embedding-gateway-development76-final.log. Packaging and final
Python syntax checks passed in artifacts/prima-embedding-gateway-pin76-package.log.
Clean published compilation is running in
artifacts/prima-embedding-gateway-published76-build.log. Authenticated gateway,
feature/served-model identity checks and native role binding are implemented
source paths; no API/model/backend acceptance is implied. All unit/native/model
tests and test-fix work remain deferred until full required implementation is
finished. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the live three-computer cluster. Qualification stays false.
Broader original model/API/platform/accelerator/exact-placement work remains open.


Recipe76 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed. Exact source/recipe identity, source checkout cleanliness,
three staged executable hashes and advertised embedding role/output capabilities
are recorded in artifacts/prima-embedding-gateway-published76-manifest-check.json.
The gateway/API acceptance fields remain false: compilation and metadata do not
prove request behavior, vector accuracy or backend execution. Final packaging and
syntax checks passed in artifacts/prima-embedding-gateway-pin76-package-final.log.
Prima planning recognizes embedding/pooling extras whose storage is already
reserved by the estimator; other unknown extra effects remain unpriced warnings.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Those tests remain deferred until full required
implementation finishes. Physical distributed, mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster
and remain unpassed. All qualification is disabled. Broader original model/API/
platform/accelerator/exact-placement integration remains unfinished; the thread
goal stays active.


Qwen2-MoE continuation (recipe77, 2026-10-13):
Published PR #1 source pin 8b5cbe40c94dbce05adc5b54aadc6a5230fc7992;
normalized recipe digest 2aef72f0dea3c6afd7ade92689baf8388a5557fe28ae1c33f6334e0deca0f728.
The loader now creates only absolute manifest-owned layers using local contexts;
the shared segmented graph handles repeated rank visits and zero-work coordinators.
Routed experts use unnormalized selected full-router softmax weights, while the
always-active shared expert uses SILU and a direct sigmoid gate. Expert widths
accept scalar/per-layer metadata, with bounded counts/widths validated on all ranks.
Typed profiling includes complete shared storage/arithmetic and selected routed
work. Kluster conservatively reserves simultaneously live routed/shared scratch.
Production server/CLI/profiler compilation and Python syntax/package checks passed.
A clean published Windows/Vulkan build is running in
artifacts/prima-qwen2moe-published77-build.log; its completion is recorded separately.
Source graph admission now covers seven architectures; registry coverage is still
54 of the baseline's 153, with 99 absent entries. Registration is not execution
or model/backend acceptance. Arbitrary remote expert/row/tensor placement and
broader original API/model/platform/accelerator integration remain open.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance acceptance
will be tested later on the user's live three-computer cluster and remain unpassed.
All qualification remains disabled; the integration goal is unfinished.


Recipe77 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with LLAMA_BUILD_TESTS=OFF. Exact clean source/recipe identity,
three staged executable hashes and qwen2moe_segmented_v1 capability metadata are
recorded in artifacts/prima-qwen2moe-published77-manifest-check.json. Kluster package
and syntax checks passed in artifacts/prima-qwen2moe-pin77-package.log. Capability
metadata and compilation are not model/API/backend/profiler acceptance; those
acceptance fields remain false. No unit/native/model tests, test fixes, inference,
profiling measurements or performance checks ran. Tests remain deferred until full
required implementation is complete. Physical distributed, mixed-platform/backend
and performance gates will be tested later on the live three-computer cluster.
Qualification remains disabled. T012-T022 and the broader integration remain open.


Phi-3 continuation (recipe78, 2026-10-13):
Published PR #1 source pin b30588af67f4e832dfb8d6d0894f386fc2884206;
recipe digest f55f488a8d2f6dfb4c004ea4b0bab676874ea12e9ad5ddd7ec19f71fa0fbc871.
Phi-3 now uses rank-owned absolute loading and the segmented graph, with fused
or separate QKV, fused SwiGLU, optional/tied output and long/short RoPE factors
on each owning rank's layer buffers. Sliding attention is disabled to match the
pinned baseline. Logical context slots now exclude the head's reserved sequence
ID, while KV/pooling capacity retains it, so rank roles select the same factor
regime. Managed preparation requires logical_context_sequences_v1. Typed profiling
prices fused FFN work and conservative factor copies; Kluster reserves fused FFN
scratch and factor storage with assigned layers. This is source/build evidence,
not Phi-3 model or distributed acceptance.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-phi3-published78-build.log; its completion is recorded separately.
The graph admission list is now eight architectures, while the registry remains
54 versus baseline 153, with 99 absent entries. T012-T022 and broader original
API/model/platform/accelerator/exact-placement implementation remain unfinished.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance acceptance
will be tested later on the user's live three-computer cluster and remain unpassed.
All qualification is disabled; the integration goal stays active.


Recipe78 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and advertised phi3_segmented_v1 plus
logical_context_sequences_v1 capabilities are recorded in
artifacts/prima-phi3-published78-manifest-check.json. Python syntax and Kluster
package checks passed. Phi-3, logical context slots, API/model/backend/profiler
acceptance remain unverified; capability metadata and compilation are not those
acceptance results. No unit/native/model tests, test fixes, inference, profiling
measurements or performance checks ran. Tests remain deferred until full required
implementation is complete. Physical distributed, mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification remains disabled. T012-T022 and the broader integration remain open.


Gemma2 continuation (recipe79, 2026-10-13):
Published PR #1 source pin 99e179f6417030eaaf598903ed3a27fd58389531;
recipe digest 57200028d193952401d6ffe3290dc6b46957c0a58792e0942686018d44bc56cc.
Gemma2 now uses absolute rank-owned loading, tied output duplication on the
assigned output backend, fused/separate QKV and the shared segmented graph.
It retains the pinned baseline's query/input scaling, post-attention/post-FFN
norms, GELU gated FFN and attention/output soft-capping. Model metadata validates
window/pattern/norm/rotary/soft-cap bounds, including default alternating SWA and
independent sliding RoPE. Compact allocation, owned masks, shifts, snapshots/
rewind/replay and native KV estimates now use its per-layer storage layout.
Typed profiling prices norms, GELU, query/input scaling and soft-capping work;
Kluster reserves simultaneous dense/sliding masks and default-window compact KV.
A source-edit compilation error while removing the obsolete graph was corrected.
Final development server/CLI/profiler compilation, Python syntax and Kluster
packaging passed. Clean published compilation is running in
artifacts/prima-gemma2-published79-build.log; completion is recorded separately.
The graph admission list is now nine architectures. Registry coverage is still
54 versus baseline 153 with 99 absent entries; registration/admission is not
model or backend acceptance. T012-T022 and broader original API/model/platform/
accelerator/exact-placement integration remain unfinished.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance acceptance
will be tested later on the user's live three-computer cluster and remain unpassed.
All qualification stays disabled; the integration goal stays active.


Recipe79 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and gemma2_segmented_v1/gemma2_compact_swa_v1
capability metadata are recorded in
artifacts/prima-gemma2-published79-manifest-check.json. Python syntax and Kluster
packaging passed. Gemma2 model/backend/distributed/profiler acceptance remains
unverified; compilation and capability metadata do not prove it. No unit/native/
model tests, test fixes, inference, profiling measurements or performance checks
ran. Tests remain deferred until full required implementation is complete.
Physical distributed, mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster. Qualification stays disabled;
T012-T022 and the broader original integration remain unfinished.


Gemma/Granite dense and LongRoPE continuation (recipe80, 2026-10-13):
Published PR #1 source pin c7bd885bec4265204702fb40ce5913d4d1152236;
recipe digest 3b08d376e088e85c44d28498083122ecb58390650cd88ae35c195e7de453f46a.
Gemma now uses absolute rank-owned loading, tied output duplication, fused/separate
QKV, input/query scaling and GELU in the shared segmented graph without Gemma2/3
post norms. Granite dense uses the same ownership path with its optional biases,
embedding/residual/logit/attention scale controls and rotary-disable handling in
both attention and KV shifts. Factor tensors remain on owning layer buffers;
LongRoPE metadata enum and CLI parsing are implemented rather than rejecting the
mode before Phi/Granite factor loading. Typed costs price Gemma scaling/GELU and
Granite scale/factor work, omitting disabled rotary arithmetic. Kluster recognizes
Granite dimensions and conservatively prices factor copies with assigned layers.
Granite shared/routed experts and vision/deepstack injection remain open; positive
deepstack mappings or dense-architecture expert models explicitly refuse until
those required paths exist. Complete Granite parity is not claimed.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-gemma-granite-published80-build.log; completion is recorded separately.
The graph admission list is eleven architectures; registration/admission does not
prove execution. Registry coverage is still 54 versus baseline 153 with 99 absent
entries. T012-T022 and broader original API/model/platform/accelerator/exact-placement
integration remain unfinished. No unit/native/model tests, test fixes, inference/
profiling measurements or performance checks ran. Tests remain deferred until full
required implementation finishes. Physical distributed, mixed-platform/backend
and performance gates will be tested later on the user's live three-computer
cluster and remain unpassed. Qualification stays disabled; the goal stays active.


Recipe80 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and gemma_segmented_v1, granite_dense_segmented_v1
and longrope_v1 capability metadata are recorded in
artifacts/prima-gemma-granite-published80-manifest-check.json. Python syntax and
Kluster packaging passed. Gemma, Granite dense and LongRoPE model/backend/profiler
acceptance remain unverified; source/build metadata does not establish them.
Granite MoE/shared experts and vision/deepstack integration remain open. No unit/
native/model tests, test fixes, inference, profiling measurements or performance
checks ran. Tests remain deferred until full required implementation is complete.
Physical distributed, mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster. Qualification stays disabled;
T012-T022 and the broader original integration remain unfinished.


Granite routed/shared expert continuation (recipe81, 2026-10-13):
Published PR #1 source pin 953b6296225be1513eead4a707beeb6913e7129f;
recipe digest e84b6912cb7442c5c9220e16392339b3426ed36d63c446f37856fbec5841fafa.
Granite/Granite-MoE now load routed and always-active shared FFNs only for absolute
manifest-owned layers, with per-layer widths, optional routed gates and required
up/down/router tensors. Ungated experts apply SILU to up and pass the activated
result to down without multiplying by up again. Selected full-router softmax
weights are normalized; shared FFNs add independently without Qwen2's sigmoid gate.
The segmented path preserves scales, optional biases, long/short factors and dense
Granite's rotary switch separately from Granite-MoE's always-rotary semantics.
Typed profiling includes full expert/shared storage, selected work, ungated
activation, router normalization and shared FFN work. Kluster reserves simultaneous
routed/shared scratch and factor copies, recognizes Granite-MoE/Phi3 dimensions,
and rejects nonpositive Granite expert widths. No new measurements are inferred.
Granite vision/deepstack and other Granite family architectures remain open, along
with original model/API/platform/accelerator/exact-placement integration.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-granite-moe-published81-build.log; completion is recorded separately.
Graph admission now covers twelve architectures. Registry coverage remains 54
versus baseline 153 with 99 absent entries; admission is not model/backend proof.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and remain unpassed.
Qualification stays disabled; T012-T022 and the integration goal remain unfinished.


Recipe81 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and granite_moe_segmented_v1,
granite_shared_expert_v1/optional_expert_gate_v1 capabilities are recorded in
artifacts/prima-granite-moe-published81-manifest-check.json. Python syntax and
Kluster packaging passed. Granite routed/shared model/backend/profiler acceptance
remains unverified; source/build metadata does not prove it. Vision/deepstack,
other architectures and broader original API/platform/accelerator/exact-placement
work remain open. No unit/native/model tests, test fixes, inference, profiling
measurements or performance checks ran. Tests remain deferred until full required
implementation is complete. Physical distributed, mixed-platform/backend and
performance gates will be tested later on the user's live three-computer cluster.
Qualification stays disabled; T012-T022 and the integration goal remain unfinished.


Granite SWA continuation (recipe82, 2026-10-13):
Published PR #1 source pin 731eb6103775ecb1b3f9736ed016a08b9beba33b;
recipe digest 66491d988b867d0a3047e69a9b08f05fb30f09053357fb6e9c9fd213733e5d8e.
Added the granite_swa registry/tensor roles, bounded per-layer selected-expert,
sliding and optional RoPE patterns, rank-owned loading, sinks, fused/separate
routed gate/up projections and fused shared FFNs. The segmented graph uses actual
per-layer expert counts and the baseline's global RoPE frequency, respects disabled
rotary layers in attention/shifts and integrates compact KV allocation/masks/
snapshots/replay/estimates. Fused routed outputs are sliced with token/expert strides
before activation. Typed costs count fused storage and selected arithmetic once;
ambiguous fused/separate roles refuse. Kluster recognizes fused expert weights,
prices simultaneous branch/slice scratch and both masks, validates patterns/counts
and reserves compact KV. MoE classification now includes Granite-MoE/Granite SWA.
Source inspection corrected the initial Switch assumption: Granite Switch has
stacked token-selected LoRA plus a persistent attention router, not these fused
expert semantics. Switch, Hybrid, vision/deepstack and broader original model/API/
platform/accelerator/exact-placement work remain open.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-granite-swa-published82-build.log; completion is recorded separately.
The graph admission list is thirteen architectures. Raw registry entries are 55
versus baseline 153 (both include their unknown placeholder); 98 named baseline
architectures remain absent. Registration/admission is not model/backend proof.
No unit/native/model tests, test fixes, inference/profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and remain unpassed.
Qualification stays disabled; T012-T022 and the integration goal remain unfinished.


Recipe82 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and granite_swa_segmented_v1,
granite_swa_compact_v1/fused_expert_projection_v1 capabilities are recorded in
artifacts/prima-granite-swa-published82-manifest-check.json. Python syntax and
Kluster packaging passed. Granite SWA/fused projections/compact storage model,
backend and profiler acceptance remain unverified. Source/build metadata does
not prove them. Switch, Hybrid, vision/deepstack, other architectures and broader
original API/platform/accelerator/exact-placement work remain open. No unit/native/
model tests, test fixes, inference, profiling measurements or performance checks
ran. Tests remain deferred until full required implementation is complete.
Physical distributed, mixed-platform/backend and performance gates will be tested
later on the user's live three-computer cluster. Qualification stays disabled;
T012-T022 and the integration goal remain unfinished.


OLMo2 continuation (recipe83, 2026-10-13):
Published PR #1 source pin 483a90fda2d8ab08c976fb2392a3dafe294f5139;
recipe digest f2c5ffbee2d2cb7484f5fd79eae84736c8c9ac018a33bd2eb760c1f75c4dbb59.
Added olmo2 architecture/roles, validated projection/norm/window dimensions and
rank-owned loading with required output weights. The shared segmented graph
normalizes projected Q/K columns before head reshaping, omits attention/FFN
pre-norms and retains post-attention/FFN norms with SILU gating. Optional sliding
layers use the four-layer default pattern and plain rotary settings (scale 1,
extrapolation 0, attention factor 1) in attention and KV shifts. Compact allocation,
owned masks, snapshots/replay and native estimates include the architecture.
Typed profiler costs distinguish projected columns from per-head normalization;
Kluster recognizes OLMo2 dimensions and prices its sliding pattern, compact KV
and simultaneous masks. This is implemented source, not model/backend proof.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-olmo2-published83-build.log; completion is recorded separately.
Graph admission now covers fourteen architectures. Raw registry entries are 56
versus baseline 153 (both include their unknown placeholder); 97 named baseline
architectures remain absent. Original model/API/platform/accelerator/exact-placement
work remains unfinished, including Switch/Hybrid/vision and remote expert/row/tensor
placement. No unit/native/model tests, test fixes, inference/profiling measurements
or performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed, mixed-platform/backend and performance gates will
be tested later on the user's live three-computer cluster and remain unpassed.
Qualification stays disabled; T012-T022 and the integration goal remain unfinished.


Recipe83 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and olmo2_segmented_v1/olmo2_compact_swa_v1
capability metadata are recorded in artifacts/prima-olmo2-published83-manifest-check.json.
Python syntax and Kluster packaging passed. OLMo2 model/backend/compact/profiler
acceptance remains unverified; source/build metadata does not prove it. Other
architectures and broader original API/platform/accelerator/exact-placement work
remain open. No unit/native/model tests, test fixes, inference, profiling measurements
or performance checks ran. Tests remain deferred until full required implementation
is complete. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the user's live three-computer cluster. Qualification stays
disabled; T012-T022 and the integration goal remain unfinished.


OLMo/OLMoE continuation (recipe84, 2026-10-13):
Published PR #1 source pin d69be68a68694982d086f5ebb037d7953c59d513;
recipe digest 14d342f33f5b6def3159d5a6033e626bfbb8c90981a5fcf80c6c6f16b5ed8c95.
OLMo and OLMoE now load only absolute manifest-owned layers and use the shared
segmented graph. OLMo retains unweighted LayerNorm, optional projection clamping
and optional/tied output weights. OLMoE retains learned pre-norms, projected-column
Q/K RMS normalization, required output/gated experts and unnormalized selected
full-router probabilities. Metadata validates projection/norm/expert bounds;
OLMoE's key normalization dimensions match the baseline's full-column weights.
Typed profiler costs price unweighted norms/clamping and selected expert work.
Kluster recognizes both architectures, classifies OLMoE as MoE and reserves its
routed scratch using per-expert feed-forward widths. The obsolete all-layer
loaders/graphs were removed; OLMo2's distinct post-norm/sliding path is preserved.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-olmo-family-published84-build.log; completion is recorded separately.
Graph admission is now sixteen architectures. Raw registry entries remain 56
versus baseline 153 (both include their unknown placeholder), with 97 named
architectures absent. Registration/admission is not model/backend proof. Original
model/API/platform/accelerator/exact-placement work, including Switch/Hybrid/vision
and remote expert/row/tensor placement, remains unfinished. No unit/native/model
tests, test fixes, inference/profiling measurements or performance checks ran.
Tests remain deferred until full required implementation finishes. Physical
distributed, mixed-platform/backend and performance gates will be tested later on
the user's live three-computer cluster and remain unpassed. Qualification stays
disabled; T012-T022 and the integration goal remain unfinished.


Recipe84 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and olmo_segmented_v1/olmoe_segmented_v1
capability metadata are recorded in artifacts/prima-olmo-family-published84-manifest-check.json.
Python syntax and Kluster packaging passed. OLMo/OLMoE model/backend/profiler
acceptance remains unverified; source/build metadata does not prove it. Other
architectures and broader original API/platform/accelerator/exact-placement work
remain open. No unit/native/model tests, test fixes, inference, profiling measurements
or performance checks ran. Tests remain deferred until full required implementation
is complete. Physical distributed, mixed-platform/backend and performance gates
will be tested later on the user's live three-computer cluster. Qualification stays
disabled; T012-T022 and the integration goal remain unfinished.


Recipe85 OpenELM implementation progress: source commit
cec1a40a228c8703188bf87833d44e782e9e63d7 is pushed to fork PR #1 and pinned
by the managed recipe. OpenELM now loads only rank-owned layers and executes
through the shared segmented graph with per-layer attention/FFN widths,
required fused QKV, per-head Q/K norms, partial rotary dimensions and tied
output on its assigned buffer. Native typed costs retain actual dimensions;
Kluster reserves wider projection scratch and sizes KV using the native
first-layer head-width default when key/value widths are absent.
Development Windows/Vulkan server/CLI/profiler compilation, Python syntax and
Kluster packaging passed. Clean published compilation is running in
artifacts/prima-openelm-published85-build.log; completion is recorded separately.
Graph admission is now seventeen architectures. Raw registries remain 56 versus
baseline 153, both including an unknown placeholder; 97 named architectures
remain absent. Registration/admission and compilation do not establish acceptance.
The full original model/API/platform/accelerator/exact-placement scope remains
unfinished, including remote expert/row/tensor placement. No unit/native/model
tests, test fixes, inference, profiling measurements or performance checks ran.
All tests remain deferred until full required implementation finishes. Physical
distributed, mixed-platform/backend and performance gates remain for the user's
live three-computer cluster, with the original thresholds unchanged and unpassed.
Qualification is disabled; T012-T022 and the integration goal remain open.


Recipe85 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and openelm_segmented_v1 capability metadata
are recorded in artifacts/prima-openelm-published85-manifest-check.json.
Kluster packaging and receipt syntax checks passed. OpenELM model/backend/profiler
acceptance remains unverified; compilation and metadata do not establish it.
No unit/native/model tests, test fixes, inference, profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
is complete. Physical distributed, mixed-platform/backend and performance gates
remain for the user's live three-computer cluster. Qualification stays disabled;
T012-T022 and the full integration goal remain unfinished.


Recipe86 BERT embedding groundwork: fork PR #1 now points to
f984f778e65993d8dfd1bf580fd728b3049ece1a, pinned by recipe 86. BERT loads only
owned transformer layers, with head-owned position/type embeddings, input norms
and classifier tensors. The segmented graph preserves bidirectional F32 attention,
optional projection biases, post LayerNorm and ungated GELU; final raw row selection
preserves handoff bounds. Head-only coordinators initialize position/pooling inputs.
Typed costs omit rotary/cache writes and a vocabulary head; Kluster reserves actual
materialized score scratch, while retaining native-allocated KV storage in budgets.
Scalar rank pooling requires its loaded output classifier. Multi-class output,
other embedding architectures, and model/API/backend/profiler acceptance remain
open; this does not establish full BERT parity. README now uses a short high-level
change summary, with fork attribution and original main branch at the beginning.
Development production targets, Python syntax and Kluster packaging passed.
Clean managed compilation is running in artifacts/prima-bert-published86-build.log.
Graph admission is 18 architectures; registry counts remain 56 versus baseline 153
(including unknown placeholders), with 97 named entries missing. Broader original
API/model/platform/accelerator/remote-expert/row/tensor placement work remains open.
No unit/native/model tests, test fixes, inference or profiling measurements ran.
All tests remain deferred until full required implementation finishes. Physical
distributed, mixed-platform/backend and performance gates will be tested later on
the user's live three-computer cluster; original thresholds remain unchanged and
unpassed. Qualification stays disabled; T012-T022 and the integration goal stay open.


Recipe86 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three executable hashes and bert_segmented_v1/coordinator_pooling_inputs_v1
capability metadata are recorded in artifacts/prima-bert-published86-manifest-check.json.
Kluster packaging and syntax checks passed. BERT model/API/backend/profiler and
coordinator-pooling acceptance remain unverified. Full original implementation
remains unfinished; no unit/native/model tests, test fixes, inference, profiling
measurements or performance checks ran. All tests remain deferred until the full
required implementation finishes. Physical distributed/mixed-platform/backend
and performance gates will be tested later on the live three-computer cluster.
Qualification stays disabled with empty execution scopes; T012-T022 stay open.


Recipe87 classifier-output groundwork: fork PR #1 now points to
2632e0fa2942373099ba0821b166d87aa8746091, pinned by recipe 87. The native
loader reads GGUF classifier output labels, keeps scalar default for absent/empty
labels, sizes classifier output and bias to the label count, and permits a direct
output projection without an intermediate classifier. Native C API exposes the
class count/labels; rank pooling preserves full class vectors. The server's
rerank score remains the first class, matching the pinned baseline. Kluster
reserves class-vector output scratch. Development production targets, Python
syntax and Kluster packaging passed. Clean managed compilation is running in
artifacts/prima-bert-classes-published87-build.log. Model/API/backend/profiler
acceptance and other embedding architectures remain open. No unit/native/model
tests, test fixes, inference, profiling measurements or performance checks ran.
All tests remain deferred until full required implementation finishes. Physical
distributed, mixed-platform/backend and performance gates will be tested later
on the user's live three-computer cluster, with original thresholds unchanged
and unpassed. Qualification stays disabled; T012-T022 and the goal stay open.


Recipe87 published-build completion: clean Windows/Vulkan server, CLI and profiler
compilation completed with tests disabled. Exact clean source/recipe identity,
three staged executable hashes and classifier_output_labels_v1 metadata are
recorded in artifacts/prima-bert-classes-published87-manifest-check.json.
Kluster packaging and Python syntax checks passed. All qualification gates are
false and execution scopes empty. Classifier model/API/backend/profiler acceptance
remains unverified; source/build metadata is insufficient to close it. The full
original integration remains unfinished. No unit/native/model tests, test fixes,
inference, profiling measurements or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance gates will be tested later on the user's
live three-computer cluster. T012-T022 remain open.


Recipe88 rerank API groundwork: fork PR #1 points to
077c9c56f0cb7d6347788bc4abb0c4cfd47ef2eb, pinned by recipe 88. Kluster's
authenticated gateway exposes Prima's four rerank aliases against the exact
served model and requires a distinct rerank execution feature and native
capability. The head-only `--reranking` mode rejects an embedding combination or
non-rank pooling. Prima's rerank response keeps original document indexes,
orders by relevance, applies bounded `top_n` and accounts for evaluated tokens.
Development Windows/Vulkan production compilation and Python syntax passed;
the clean managed build is recorded separately when complete. This establishes
implementation, not model/API/backend acceptance. All qualification gates remain
false with empty execution scopes. Unit/native/model tests and test-fix work
remain deferred until full implementation finishes; physical distributed and
performance checks remain deferred to the live three-computer cluster. T012-T022
and the full integration goal remain open.


Recipe88 published-build completion: the clean Windows/Vulkan server, CLI and
profiler compilation completed with tests disabled. Exact clean source/recipe
identity, three staged executable hashes and rerank_api_v1 capability metadata
are recorded in artifacts/prima-rerank-published88-manifest-check.json. Kluster
packaging and Python syntax checks passed. All qualification gates remain false
and execution scopes empty. Rerank model/API/backend acceptance remains
unverified; source/build metadata does not close T013 or the full integration.
No unit/native/model tests, test fixes, inference, profiling measurements or
performance checks ran. Tests remain deferred until full required implementation
finishes. Physical distributed/mixed-platform/backend and performance gates
will be tested later on the live three-computer cluster. T012-T022 remain open.


Recipe89 TEI rerank parity groundwork: fork PR #1 now points to
146ccc715301ae780604e5b7a5adcf1c240e8747, pinned by recipe 89. Prima
accepts `texts` as the baseline rerank request variant and returns a TEI score
array, with source text only for `return_text: true`. Original indexes, score
order and `top_n` remain bound to the input documents. Kluster asks for a
separate request feature and rerank_tei_v1 native capability for this variant.
Development Windows/Vulkan server compilation, Python syntax and packaging
passed; clean managed build evidence is recorded separately when complete.
No model-backed API/backend acceptance, tests, inference, profiling or
performance checks ran. Qualification remains disabled with empty execution
scopes. Tests and test-fix work remain deferred until full implementation;
physical distributed/mixed-platform/backend and performance gates remain
deferred to the user's live three-computer cluster. T012-T022 stay open.


Recipe89 published-build completion: clean Windows/Vulkan server, CLI and
profiler compilation completed with LLAMA_BUILD_TESTS=OFF. Exact clean
source/recipe identity, three staged executable hashes and rerank_tei_v1
capability metadata are recorded in
artifacts/prima-rerank-tei-published89-manifest-check.json. Kluster packaging
and Python syntax checks passed. All qualification gates remain false with
empty execution scopes. TEI/Jina rerank model/API/backend acceptance is still
unverified; source/build metadata is not an acceptance result. The original
integration remains unfinished. No unit/native/model tests, test fixes,
inference, profiling measurements or performance checks ran. Tests remain
deferred until full required implementation finishes. Physical distributed,
mixed-platform/backend and performance gates remain deferred to the user's
live three-computer cluster. T012-T022 stay open.


Recipe90 tokenizer/template API groundwork: fork PR #1 now points to
aaee8b4db20db70aeef5e71d97acc7d2c8752ea5, pinned by recipe 90. Kluster
exposes `/tokenize`, `/detokenize` and `/apply-template` through authenticated
routes that check the single served model and request-specific qualification.
Prima's tokenizer now honors the pinned baseline `parse_special` flag while
retaining mixed string/token input and piece output. Template requests retain
tool/reasoning feature checks and require native apply_template_v1 metadata.
Development Windows/Vulkan server, CLI and profiler compilation, Python syntax
and Kluster packaging passed; clean managed build evidence follows separately.
This is source/build implementation evidence, not model/API/backend acceptance.
All qualification gates remain false and execution scopes empty. No unit/native/
model tests, test fixes, inference, profiling or performance checks ran. Tests
remain deferred until full implementation. Physical distributed and performance
checks remain deferred to the user's live three-computer cluster. T012-T022 and
the original integration goal remain open.


Recipe90 published-build completion: clean Windows/Vulkan server, CLI and
profiler compilation completed with LLAMA_BUILD_TESTS=OFF. Exact clean
source/recipe identity, three staged executable hashes and tokenizer/template
capability metadata are recorded in
artifacts/prima-token-utilities-published90-manifest-check.json. Kluster
packaging and Python syntax checks passed. All qualification gates remain false
with empty execution scopes. Tokenizer/template model/API/backend acceptance
remains unverified; source/build metadata does not close T013 or the full goal.
No unit/native/model tests, test fixes, inference, profiling measurements or
performance checks ran. Tests remain deferred until full implementation.
Physical distributed/mixed-platform/backend and performance gates remain
deferred to the user's live three-computer cluster. T012-T022 stay open.


Recipe91 OpenAI text-completion groundwork: fork PR #1 now points to
b18ae11ddf87ccc5fc407a75791ac92db333593c, pinned by recipe 91. Prima's
`/v1/completions` path emits text-completion choices, indexed multi-prompt/
multi-choice results, usage, bounded logprob output and SSE completion events
with `[DONE]`; the raw completion aliases retain their native response format.
Kluster exposes the OpenAI path through an authenticated, single-served-model
gateway with separate request features and text_completion_v1 capability
admission. Development Windows/Vulkan server, CLI and profiler compilation,
Python syntax and Kluster packaging passed; clean managed build evidence
follows separately. Model-backed format, stream, cancellation and backend
acceptance remain unverified. Qualification stays disabled with empty execution
scopes. No unit/native/model tests, test fixes, inference, profiling or
performance checks ran. Tests remain deferred until full implementation;
physical distributed and performance checks remain deferred to the user's
live three-computer cluster. T012-T022 and the goal stay open.


Recipe91 published-build completion: clean Windows/Vulkan server, CLI and
profiler compilation completed with LLAMA_BUILD_TESTS=OFF. Exact clean
source/recipe identity, three staged executable hashes and text_completion_v1
metadata are recorded in
artifacts/prima-text-completion-published91-manifest-check.json. Kluster
packaging and Python syntax checks passed. All qualification gates remain
false and execution scopes empty. Text-completion model/API/backend/stream
acceptance is unverified; source/build evidence is insufficient to close T013
or the integration goal. No unit/native/model tests, test fixes, inference,
profiling measurements or performance checks ran. Tests remain deferred until
full implementation finishes. Physical distributed/mixed-platform/backend
and performance gates remain deferred to the live three-computer cluster.
T012-T022 stay open.

Recipe 92 adds authenticated `/infill` routing and repairs Prima's FIM task
creation: optional prompt defaults to an empty string, model FIM tokens and
structured prefix/suffix/extra input are checked, and supplied text is
tokenized literally. The fork changes are published in PR #1 at
`6681aec03ada66df2c5084c9e90a82cc72fd86a1`; the managed Windows/Vulkan
server, CLI and profiler compiled from that clean pin. Exact source/recipe
identity, staged executable hashes and disabled qualification are in
`artifacts/prima-infill-published92-final-manifest-check.json`. Kluster syntax
and packaging checks passed. FIM model/API/backend/stream behavior is still
unverified. Unit/native/model tests and fixes remain deferred until the full
implementation is finished; physical cluster and performance acceptance remain
deferred to the user's live three-computer cluster. T012-T022 stay open.

Recipe 93 exposes native-format `/completion` and `/completions` through
Kluster's authenticated gateway with exact served-model binding, separate
request qualification and `raw_completion_v1` capability metadata. Prima now
reports malformed raw completion and infill requests as invalid requests, and
its infill API documentation reflects the implemented optional prompt, extra
context and streaming options. PR #1 publishes the source at
`f4317eaa5f2e7831d0d74d30f1a59ab1573be53e`. The source/build receipt is
`artifacts/prima-raw-completion-published93-manifest-check.json`. Model/API/
backend/stream acceptance remains unverified; no unit/native/model tests or
test-fix work ran. Physical distributed and performance gates await the live
three-computer cluster. T012-T022 stay open with qualification disabled.

Recipe 93 also binds the baseline native-format `/embedding` and `/embeddings` aliases through Kluster's authenticated gateway, keeping `content` input and native output distinct from `/v1/embeddings`. The same published source/build receipt now records embedding_output_v1 metadata. Model/API/backend acceptance remains unverified, tests remain deferred until full implementation, and physical distributed/performance gates remain for the live three-computer cluster.

The current Kluster T015 layout resolver now requires every manifest asset before native launch, verifies each basename's declared size and SHA-256, rejects symlink or changed entries, and publishes new hardlinks/copies atomically. This closes a gap where an existing corrupt shard or missing companion could silently survive layout construction. A focused regression check was written but not run under the user's test deferral. Python syntax and Kluster packaging passed. Native companion/shard execution and physical transfer acceptance remain unverified; T015 and qualification remain open. Physical distributed/performance gates remain deferred to the live three-computer cluster.

T015 Windows portability follow-up: asset basenames are now unique under Unicode case folding in both collected files and execution manifests, preventing case-insensitive shard/companion collisions before staging. A focused regression case was added but not run under the deferred test schedule; qualification and physical cluster gates remain unpassed.

T015 native loader compatibility: selected split GGUF models and companions must start with shard 00001, because the pinned Prima loader rejects a later shard as the entrypoint. Asset collection now refuses later selected shards before transfer or launch. A regression case was written but not run; split-GGUF model execution acceptance remains deferred with other unit/native/model tests until implementation finishes.

T017 source progress: the Prima planner now enumerates bounded combinations of optional worker removals while honoring required nodes and explicit priority, retaining at least one native worker for network-bound candidates. It keys distinct candidates by participant set as well as capacity placement so equal-looking CPU plans with different network edges are not collapsed. A regression check was written but not run under the deferred unit/native/model test schedule. This is source and packaging evidence only, not measured planning or performance acceptance; T017 and all qualification gates remain open. Physical distributed and performance checks await the live three-computer cluster.

T016 lifecycle follow-up: if the initial listener journal write fails before the native helper starts, the supervisor now reconciles the durable record. It releases the in-memory owner and node reservation only when the journal definitely has no owner; an uncertain or persisted write retains cleanup ownership. Restarted listener ownership restores its node reservation, and a serving local run retains its run reservation until rank cleanup. A restored rank also reclaims its reservation before unrelated work can start. If the rank journal cannot record completed cleanup, its in-memory owner remains pending so the reservation stays fenced. Regression cases for these paths were written but not run under the deferred test schedule. Python syntax and Kluster packaging passed. Distributed lifecycle and physical acceptance remain open, with all qualification gates disabled.

T016 rank ownership follow-up: the rank supervisor now keeps its own node reservation for the full lifetime of an owned child, including signed single-rank starts and restart recovery. A failure to publish the first rank journal record triggers owned child cleanup inside the supervisor; failed cleanup retains ownership for retry. Source-level regression cases were added without executing them. Physical distributed and performance gates remain deferred to the live three-computer cluster.

T016 spawn handoff follow-up: if process-registry registration fails after native launch, Kluster now passes the known PID and start token into the rank supervisor, journals that identity, and drains the child under the normal owned stop path. The failure cannot silently become a serving rank. A focused regression case was written but not run; restart and physical process acceptance remain open.

T016 native launcher follow-up: the Kluster Rust process registry now acquires its child lock before spawning a model or log-sink process. A poisoned registry can no longer leave a newly spawned child without a tracked handle, and a missing log-sink stdin handle drains that helper. This is a source/build change only; unit/native/model tests remain deferred until implementation is finished, and physical distributed/performance acceptance remains for the live three-computer cluster.

T014 segmented placement follow-up: preview and launch now share the same absolute layer-visit calculation. A noncontiguous rank plan is surfaced as unsupported at preview unless the selected runtime advertises segmented placement and the capacity plan reserves the exact native graph count for every rank. This preserves the plan for diagnosis while refusing an unlaunchable candidate early. Regression cases were written but not run under the deferred test schedule. Row/tensor and remote-expert execution remain unimplemented; physical distributed and performance validation remain deferred to the live cluster.

T014 candidate follow-up: signed candidate-link preparation and measured candidate selection now apply that same exact graph-visit rule. Profiling cannot authorize an alternative segmented placement with missing or incorrect rank reservations. A focused regression case was written but not run; the Kluster package build passed. Native row/tensor and remote-expert execution, model/API acceptance, and physical distributed qualification remain open.

T014 device-placement follow-up: translating a selected worker GPU from llama.cpp RPC now resolves its named GPU through the RPC inventory, where CPU entries may precede GPU entries, and confirms that GPU still exists in the worker inventory. Interleaved per-device layers are refused because native layer splitting cannot preserve that order. For contiguous multi-GPU blocks, the signed rank uses whole-layer split weights so native cumulative boundaries match the approved layer counts. A regression case was written but not run under the implementation-first test schedule. Physical selected-device execution and distributed qualification remain deferred and unpassed.

T022 selected-device contract follow-up: Kluster now requires the native explicit-device capability for every GPU rank, including a single selected GPU or an output-only GPU. The Prima fork's manifest loader also refuses GPU work when the launch omitted an explicit device selection. Published PR #1 commit `a9d109972532b3d90891f7d34aee0110da7bab74` is pinned by recipe 94; the clean published-source Windows/Vulkan build and artifact hashes are in `artifacts/prima-explicit-device94/manifest.json`. Native server, CLI, profiler and contract targets compiled, but the contract executable and all unit/native/model tests were not run under the implementation-first schedule. Device execution, API/model parity, physical distributed and performance gates remain unpassed; the latter two await the live three-computer cluster.

T032 gateway follow-up: the Responses input-token endpoint now passes the active Prima run configuration into request qualification and applies the same deployment defaults as generation. It requires native response_input_tokens_v1 metadata. Both Prima Responses routes now bind to the uniquely served model and active deployment identity. Previously the token-count path could reach native without the selected run's feature and scope checks, token counting could omit prompt-affecting defaults, and Responses model selection was unchecked at the gateway. Regression cases were added but not run under the deferred test schedule. API parity and all qualification gates remain open.

T016 monitor ownership follow-up: distributed coordinator and local-rank retirement only clear their matching active Prima run state. A newer or unrelated run is not erased or given the retired run's error. Regression source was added but not executed while tests remain deferred. Physical distributed acceptance and qualification remain open.

T016 runtime-registry follow-up: owned Prima cleanup now atomically retires only its own engine/run record. A replacement runtime record is retained; a failed registry journal leaves the old in-memory owner available for cleanup retry. Rank stop likewise requires matching run configuration before clearing a tracked process PID. Regression cases were written but not run. Distributed lifecycle acceptance and qualification remain open.

T016 serving-monitor follow-up: a stale distributed Prima monitor drains its own coordinator when another run owns the global serving configuration, including if ownership changes during refresh. Prima launch/refresh claims the runtime-registry slot only if empty or already owned by the same engine/run. It does not publish its PID or cleanup error over another run; local cleanup errors follow the same ownership rule. Regression source was written but not executed under the deferred test schedule. Physical distributed acceptance remains open.

T021 Vulkan profiler identity follow-up: the published Prima fork now records the physical UUID, driver version and physical index of the sole visible Vulkan GPU in each typed kernel profile. Kluster requires the new native capability and rejects a profile unless that identity exactly matches its selected GPU. Recipe 95 pins commit `35d9aad5b38f98d576ba541432e0f0b05f9efc14`; the clean managed Windows/Vulkan server, CLI and profiler artifact hashes are recorded in `artifacts/prima-profiler-device95/manifest.json`. This is source/build evidence, not a completed GPU profile or selected-device qualification. Unit/native/model tests and performance validation remain deferred, and all qualification gates remain disabled.

Model-backed generation profiles now rediscover the selected Vulkan device after profiling and compare the stable node/device inventory with the launch-time inventory before caching measurements. Python syntax and Kluster package-build checks passed. Model-backed execution and all tests remain deferred until implementation is complete; physical distributed and performance acceptance remain with the live three-computer cluster.

Signed worker rank start, fresh profile-context lookup, private-listener admission/renewal, rank status/renewal and stop now offload asset verification and participant/native work from the async command loop. Start, profile-context, status and renewal commands use the protocol's 60-second window; admission cleanup runs only after an attempted admission. Python syntax and package-build checks pass. Distributed process behavior and qualification remain unverified until the deferred test and physical-cluster phases.

Distributed typed profiles now bind their reported backend and physical Vulkan UUID/driver to the signed participant inventory both when the master retains a remote profile and when it scores candidate placements. A CPU profile cannot claim GPU identity, and a Vulkan profile requires exactly one selected physical device until multi-device profiling is implemented. Syntax and packaging checks passed; no profile, model or cluster validation ran. The measured-planner and selected-device gates remain unpassed.

Signed asset preparation now treats distinct basenames with identical SHA-256 content as one resumable transfer and rejects conflicting sizes or portable basename collisions. Before a worker marks preparation complete, it verifies that the staged execution manifest is within the native 4 MiB bound, belongs to this runtime and participant, and declares exactly the received assets. The same binding is checked again for launch approval. Syntax and packaging checks passed; physical transfer, split-model execution, unit/native/model tests and distributed/performance acceptance remain deferred.

Preparation lifecycle follow-up: expired prepared approvals are revoked, restart recovery persists interrupted preparation/profile records, and a failed worker preparation journal write releases its provisional resource reservation. Signed cancellation and renewal move owned-process waits and journal writes off the worker async loop, with the protocol's 60-second command window. A complete partial asset left by a lost final-chunk acknowledgement is verified and reused instead of re-sent. Python syntax and package builds passed; unit/native/model tests and physical transfer/lifecycle acceptance remain deferred.

Recipe 96 advances native GPT-2 placement. Published fork commit `ab3eea95ab6f0b18aa080f0769f5fd5eaca0e5ce` replaces the single-rank GPT-2 graph with rank-local absolute tensor loading and ordered segment visits; rank zero retains the input and output head, including when it owns no transformer layers. The source advertises `gpt2_segmented_v1`. Development and clean managed Windows/Vulkan production targets compiled, and `artifacts/prima-gpt2-segmented96/manifest.json` verifies the clean source, recipe digest and staged server/CLI/profiler hashes. No GPT-2 model execution or unit/native/model test ran. Other architecture, row/tensor/expert placement and API/platform work remain. Qualification is disabled; physical distributed and performance gates will be tested later on the live three-computer cluster.
