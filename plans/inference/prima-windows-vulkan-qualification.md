# Prima Windows/Vulkan integration and qualification

> **Created:** 2026-10-11
> **Status:** In progress
> **Repo / scope:** kluster-llm and its pinned Prima fork; Windows/Vulkan integration, separately qualified local release

Native listener continuation: T055 supplies exact manifest-derived contracts,
signed worker handlers and a source-tested privileged Windows rule ledger. T057
composes all-node admission before spawn, durable partial acknowledgements, owned
rank stop before port release, node-local renewal/expiry and capability negotiation.
T058 adds independent helper rule expiry with fresh signed running renewals and
capability2. T056 retains OS/admin paths, helper expiry/service acceptance, ownership
review/protected IPC recovery and physical acceptance.
No live firewall change or helper installation was used for this foundation.
Physical distributed gates and performance acceptance will be tested later on the
user's live three-computer cluster. Implementation and correctness checks continue;
no further local performance-gate validation is required. Production qualification
stays closed.

Earlier continuation: recipe20 is built from published857833. Exact receipt-backed
execution/host/asset/placement presets and persisted serving-context revocation are
implemented (T047). Final dense/MoE gateways pass38 checks each and two-slot suites
pass36 each. The baseline placement audit invalidates historical MoE ratios as matched
performance. Corrected published timing and mandatory placement probes completed:
dense passes throughput only at concurrency2; normal MoE fails throughput and long
generated-count matching. The batch-thread cache regression is fixed; full Python
passes1294/19skipped. Remaining compatibility, placement and distributed implementation
stay required. Physical distributed cluster gates and validations will be tested later.

Recipe21 now pins publishedf11a4a2. The CLI startup/world assertion and zero-transformer
warm-up defects reproduce in real same-host ranks and are fixed. Native post-init
readiness feeds owned signed worker status; rank zero also requires HTTP health.
Pending coordinator cleanup/admission release is retried without publishing idle
early. Fresh managed21 build/package/import/capture and CI pass; published dense
and MoE gateways pass38checks each; all four published controlled same-host cases
pass readiness/generation and requested GPU ownership. Production
coordinator composition/private admission/recovery and broader compatibility remain
required. Physical distributed cluster gates and validations will be tested later.

Earlier recipe22 pins published e7ee8f6ced718dca7187cfba4407f44b5c88884d.
Typed native profiler matrix inputs and completed-result checks pass twelve CPU
formats and nine physical RX9070XT Vulkan formats; three unsupported IQ GPU formats
refuse measurement. Fresh managed build/artifact verification, three native contracts
and all four portable CI jobs pass. Full Python1478/19skipped passes. The legacy Qwen3
kernel-profile job still fails, including on unchanged published21, and no result is
cached; repairing full-model cost extraction/profiling remains prerequisite to the
remote planner. Physical distributed gates and validations will be tested later.

Current recipe23 pins published72d4edaba7f0111322fe192027c166fe85985e33. It repairs
full-model Qwen3 kernel reports using complete GGUF metadata, typed rates and owned
unbuffered I/O without fio. Fresh published managed jobs prepare/cache28/48-layer
dense/MoE costs, with exact host/device/settings binding and no retained process.
Native3contracts, physical selected Vulkan/read checks, all CI jobs and full Python
1498/19skipped pass. These remain roofline estimates; authoritative remote planner
consumption/resource reservations and local inference correctness/performance
qualification remain required. Physical distributed validations will be tested later.

## Conventions captured at creation

- Kluster revision `e8e0e7f`; Prima recipe 7 pins `aeb3afdd44b58c93e70e53d47ed4ac7e29452ff8`. Baseline llama.cpp `v0.5.0` pins `7fe450e19305b828c199d602c23a8337aaa1f03b`.
- `src/kllm/modules/primacpp/interactors/recipe.py:require_qualified` currently applies global source-controlled gates. Manifest edits cannot qualify a runtime.
- `src/kllm/modules/primacpp/interactors/control.py:spawn_rank` reaches the Rust process gateway; Kluster owns membership, placement and lifecycle.
- `tests/prima_assessment.py:model_assessment` hardcodes Vulkan device zero and does not prove GPU execution. `benchmark.evaluate` requires physical distributed improvement.
- Feature modules own their patch surfaces; use uv and the supported installer/build flow. Native Prima changes belong in the fork, with immutable source pins and no hidden patches.
- Available hardware: RX 9070 XT, AMD integrated graphics, about 64 GB RAM; no physical worker available.

## Implementation sequence

1. Reproduce Windows Vulkan managed builds; repair compiler/dependency/runtime packaging; verify relocated staged server, CLI and profiler; add Windows Vulkan build/contract CI.
2. Replace hardcoded device ordinals with explicit discovery and selected-device propagation. Prove completed GPU work, device identity and layer/tensor ownership during prefill/decode. Refuse unintended backend fallback.
3. Audit Kluster-setting/native-flag translations; modernize Prima model/server/template/cache/batching APIs; implement exact segmented/device/expert placement and zero-work coordination. Preserve baseline behavior or refuse an unqualified requested combination explicitly.
4. Wire existing run/stop/status APIs to Prima coordination, verified prepared assets, readiness, reservations, signed commands, leases, rollback, monitoring and cleanup. Test distributed paths with isolated local agents without weakening production topology rules.
5. Add model-backed end-to-end profiling and complete evidence-based planner scoring; retain capacity-first planning when measurements are incomplete.
6. Introduce source-controlled Windows/Vulkan local and distributed qualification scopes bound to implementation/model/device/feature/placement identities. Separate qualification policy from build identity. Enable only qualified local combinations, keeping multi-node and other platforms blocked.

## Initial release and acceptance

Physical distributed cluster gates/validations will be tested later at the user's
request (2026-10-11). Keep their policy gates false and record them as deferred;
complete local implementation and Windows/Vulkan acceptance independently.

Initial supplied qualification assets: Qwen3-0.6B Q8_0 dense and Qwen3-30B-A3B Q4_K_M MoE, pinned revisions and SHA-256 hashes. Other architectures, quantizations and companion combinations need their own model-backed evidence.

Validate authenticated gateway APIs, streaming, tools, structured output, identity, request cancellation, concurrency, exact GPU execution, partial offload, memory bounds, stop/restart and cleanup. Test Windows relocation/paths with spaces, stale ownership/manifests, dependency failures and interrupted asset transfers.

Run focused tests, then Python/Rust/dashboard suites and supported package rebuild/native import. Fixtures and local multi-rank runs do not establish physical distributed execution.

For each local workload: identical assets/prompts/settings/device/offload, one warm-up and at least five alternating pairs, short/long prompts and concurrency 1/2. Require TTFT ratio <=1.05, throughput ratio >=0.95, deterministic correctness, zero OOMs and bounded memory. Preserve the independent physical distributed >=10% improvement gate.

Update feature 017 tasks/receipts and operational/API docs with separate implementation, controlled-test, physical-local and physical-distributed statuses. Local release requires normal Kluster flows to serve qualified Windows/Vulkan combinations and reject unsupported ones. Global qualification remains open.

## Implementation evidence (2026-10-11)

Recipe 8 now pins published fork commit `91e586420eb357f45d80e33be0b31908fe4bc070`;
recipe digest `21574abe7be8907595037677003ebee153e12bce4ad13a36837e96cda2c64711`.
Fresh managed Windows/Vulkan build and fork CI run `38123362573` passed. Native
contract CTest passed; Kluster Rust tests passed (42). Physical RX 9070 XT receipts
in `artifacts/prima-published-models.json` prove prefill/decode GPU matrix work for
both models. Dense sampled API cases pass; MoE chat correctness fails against the
baseline. The policy deliberately remains unqualified.

Implemented scoped policy, physical-device selection, execution-proof validation,
profiling identity, guarded local lifecycle dispatch and explicit launch translation
refusals. Added the canonical kllm-prima skill and its project discovery copy.
Complete gateway/API qualification, native arbitrary placement, end-to-end planner
measurements and paired performance acceptance remain open, together with physical
distributed qualification. This plan is still in progress.

Latest implementation: recipe10 pins `f221716d9b45077a906621ca7e10d8b4ada485ed`.
Fresh managed build and CI38127635068 passed. Published dense/MoE native protocol
conformance and GPU proofs pass for stateless text Responses/Messages, structured
Responses and forced nonstream tools. Model-backed CPU/Vulkan ProfileJobs pass on
both models; cancelling after actual Vulkan acknowledgement stops the owned child.
Python1260passed/19skipped. Both paired matrices still fail token-count matching and
decode throughput. Normal planner cache integration, full gateway/lifecycle/API
acceptance and remaining native feature/placement work remain required. Physical
distributed gates/validations remain explicitly deferred for later testing.

Recipe11 now pins published `720da841239bc2ffef55b1fe08895562ade30bef`, with a fresh
managed build and CI38131219709 passing. Explicit output projection placement and
batch-wide completed graph proof preserve normal Kluster GPU plans. The dense
published gateway suite passes 27 profile/planning/API/cancellation/concurrency/
lifecycle checks. Profile API and planning use a shared node-owned cache and startup
reservation, matching output placement as well as model/device/driver/settings.
Final Python1270passed/19skipped; both supplied models pass all 27 recipe11 checks
after retaining/retrying failed cleanup and surviving late Prima initialization. Paired performance
and the broader feature/placement acceptance remain open. No qualification policy is enabled.

Recipe12 pins the published telemetry correction `5e3e2e13a4b0779d22b418d867088f86d8a3f8f1`.
Native contracts pass; fresh managed build/CI and affected model/performance checks
are pending. Only explicit value1 enables tracing; paired runs refuse unexpected
execution receipts. Keep source/recipe-qualified evidence separate from recipe11.

Recipe12 build/package/CI and dense+MoE sampled native protocols/GPU proof pass.
Its dense five-pair matrix still fails token counts and throughput, despite matching
output bytes and verified disabled tracing. The MoE paired matrix is running. The
local path currently executes separate input/transformer/output graphs and copies
intermediate activations through host buffers even with one rank; investigate that
cost and compare generation on identical rendered input tokens before changing kernels.

Both corrected paired matrices fail; disabled tracing is verified. Dense identical-
token diagnostics show equal raw-generation counts within CPU/Vulkan pairs, but
baseline chat differs from raw generation. Reconcile native template/grammar parsing
with baseline Jinja/PEG generation, and investigate Vulkan graph/synchronization
overhead independently. Preserve real output counts and all performance thresholds.

Recipe14 publishes `501585d9116457f998d5372341c49b1ad093e6c3` after recipe13's
connected single-rank graph. Fresh managed Windows/Vulkan build, package/native import
and both CI runs pass. Optional JSON chat framing now preserves generated token counts
while stripping code fences from streamed/nonstream content. Development dense paired
bytes/counts match, but throughput remains below acceptance on all four workloads.
Published dense and MoE --profile gateway suites each pass 27 checks.
Next investigate small decode batches currently entering the slower Vulkan matrix
path, validate operations and model semantics before publication, then repeat strict
paired acceptance. Remaining feature/placement/distributed implementation is required;
physical distributed cluster gates and validations will be tested later.

Recipe15 pins published `19adfe0800130e829544719136550257b21fd9f6`, correcting
vector bounds and strided-matrix conversion while extending contiguous vector
dispatch to batches up to8. Supported physical matrix/expert tests, native contracts
and dense/MoE development protocols pass. Short concurrency2 throughput rises from
approximately0.385 to0.93, long concurrency2 from0.287 to0.53; neither passes0.95.
Fresh published build/package/native import and both CI runs pass. The dense gateway
rerun passes27 checks after fixing premature stream disposal in its cancellation
observation. Final model/performance acceptance remains required. A developmental
extension using contiguous copies for floating-point views passes operation checks;
its dense paired timing run is in progress.

Published15 MoE --profile gateway also passes27 checks. The first view-extension
matrix matches bytes/counts and reaches approximately0.97 short2 and0.75 long2,
but fails overall. Restored shader unroll hint passes operation/expert checks;
repeat affected dense/MoE paired timing before publishing that developmental change.

Recipe16 publishes `a024a7edc21d50c558f608878b7bb085bfde8aae`: float-view packing
and specialized vector dispatch now account for outer batch padding/fourth dimensions.
25 reproduced incorrect products pass after correction;194 supported matrix and185
expert comparisons, native contracts and both development protocols pass. Pre-fix
final dense/MoE matrices still fail; MoE long2 also has a token-count mismatch. Rebuild
and repeat on published16. Fresh build/CI are in progress. CLI worker translation now
omits the HTTP alias without dropping decoder settings; full remote launch wiring
remains implementation work, while physical distributed validation will be tested later.

Fresh recipe16 build/package/native import, source/artifact capture and both CI runs
pass. Full Python rerun is pending after a child-start timeout during compilation;
unchanged isolated SQLite test passes after verified orphan cleanup. Execute final
published gateway/paired checks before considering policy updates.

The quiet Python rerun passes1271/19skipped. Published16 dense --profile gateway
passes27 checks; MoE equivalent is running and final paired timing is next. Keep the
performance thresholds and source-controlled qualification policy unchanged.

Published16 MoE gateway --profile passes27 checks as well. Both model suites pass;
the fresh published dense paired matrix is running and MoE paired timing follows.
Performance and remaining implementation remain open; physical distributed testing
will be performed later as requested.

Published16 dense paired timing completes with matching bytes/counts and only short2
passing throughput; overall fails. MoE paired timing is running. Prepare a four-row
vector workgroup to reuse activation loads, with independent large-projection tail
regressions that expose truncated dispatch despite whole-matrix error tolerances.
Validate against the prior published backend before testing/publishing the new source.

The four-row workgroup experiment passes correctness but regresses throughput and
is withdrawn. Recipe17 publishes `622d5c4f9fdc1fa399c3f20e5632a67a2b63fdca`, retaining
a smaller quantized-loop improvement (dense0.95/1.00/0.88/0.75, overall fails), portable
bounded-grid contracts and sparse tail checks. Both development model protocols and
operation/native contracts pass. Old/new tail cases both pass on this GPU with X
limit4294967295; lower-limit coverage is a CPU contract, not physical qualification.
Published16 MoE counts/throughput still fail. Fresh17 build/CI and affected published
acceptance are required; all thresholds remain unchanged, distributed tests later.

Fresh17 managed build/package/native import, source/artifact capture and both CI runs
pass. Extend identical-token diagnosis to repeated two-request CPU/Vulkan waves to
separate template/grammar conversion from backend/model concurrency behavior. Dense
CPU2 semantic/proof smoke passes but raw counts differ; MoE CPU2 diagnosis is running.
Final published17 acceptance, remaining implementation and deferred distributed
physical tests remain tracked independently.

Recipe18's published raw-logit chat logprobs pass native contracts and model-backed
protocol tests. Fresh managed build/package/import/capture and both CI runs pass;
published dense and MoE gateway --profile --logprobs each pass35 checks. Repeated
CPU2 MoE diagnosis reproduces token-count variance, narrowing the investigation beyond
Vulkan alone. Continue exact token/margin diagnosis and strict paired local acceptance.

Signed rank ownership now persists sender/master authority, and remote startup requires
its current complete preparation instead of cache presence. Controlled real-child and
signed-WebSocket regressions pass. Coordinator/readiness/admission and remote planner
implementation remain required; physical distributed gates/validations will be tested later.

Recipe19 publishes precision-preserving F32 matrix execution and attention requests.
Physical before/after range regressions and final237 matrix/185 expert comparisons
pass, as do shared-memory limit contracts and native protocol subsets. Dense paired
development timing still fails long throughput; MoE partial-offload timing is running.
Rebuild and validate the exact published19 source before using its receipts in any
qualification decision. CPU concurrency variance, complete compatibility/placement,
coordinator/readiness and remote planning remain open. Physical distributed tests later.

Fresh published recipe 19 build/package/import/capture, CI and both 35-check gateway
suites pass. Published five-pair dense/MoE matrices match bytes and actual usage but
both still fail throughput. Continue performance work without relaxing the gate;
CPU-only variation and full compatibility/placement/distributed implementation stay open.

Recipe 20's direct batched attention passes operation/protocol checks and every
development dense performance workload. MoE throughput still fails. Verify the fresh
published runtime, then align normal gateway batch/thread settings with timing and
bind every qualified execution setting/placement. The current policy does not yet
enforce those exact bounds, so keep qualification false until that gap is closed.
Physical distributed cluster gates and validations will be tested later.


Preparation-owned private link profiling (T068) now collects every directed manifest
edge with bounded signed and integrity-checked application stream probes, including
worker-to-worker links authorized by ephemeral master-approved peer scopes. Existing
private diagnostic ports and pinned pairing remain unchanged. Focused64/2skipped,
full1518/19skipped and supported package/import pass. Model/adapters/rules and mapped
network assignments in the three-agent test remain fixtures; physical networking or
inference is not qualified by this transport evidence. T017/T021/T066 still require
complete authoritative candidate scoring, disk/link context and concurrent resource
reservation. Published native recipe23 is unchanged. Physical distributed cluster
gates and validations will be tested later; all qualification remains disabled.


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

### Recipe36 final functional receipts
The clean managed Vulkan source ccc8c5947a0dd108f9c99f95af920ee5be9c160e
passes native5/5, dense49/MoE49 authenticated gateway/lifecycle checks and both
real-vocabulary Qwen-preset parser/sampler checks. Python1606/19skip, package,
skill sync and both CI runs38259661919/38259669671 pass. All owned local handles
are terminal. Preserve development failures and distinguish their source changes
from final immutable receipts. T083 is complete; T084 automatic HTTP/parallel
streaming integration is open and unadvertised. No performance gate is run or
passed. Physical distributed and performance acceptance will be tested later
on the user's live cluster of three computers, retaining existing thresholds.

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


### Explicit device ownership follow-up (T085)

Continue correctness-only native/managed device selection: ordered --device,
CPU-only none, Vulkan backend registry and per-selected-device pinned buffers,
physical UUID visibility/remapping and exact weighted layer execution evidence.
Development checks pass CPU-only, Vulkan0, nonzero visible Vulkan1, and a 3:1
split over both local adapters; these are local functional checks, not a
performance comparison or distributed acceptance. Preserve all qualification
flags as false and execution scopes as empty. Performance and physical
distributed acceptance will be tested later on the user's live three-computer
cluster, using the existing thresholds. Publication/rebuild evidence remains
pending until the immutable fork pin is consumed.


### T085 published source and clean build

Native ad2a03c8653e9c7c63f241bd0d6b43c6820ab03e is pushed to
berkkarahan/prima.cpp codex/kluster-integration (PR1). Recipe39 digest is
911c9db6f42090f275ffce49b8a92e6cf95529abc0d4c976e6b70de8ac1c489b; clean
managed runtime33d923336cf47711c06678c002f83498d6db3db8826b7266accc5e8366114b52
passes source/artifact binding and native5/5. Development dense/MoE gateways
each pass100 checks; published dense100, CPU-only/no-visible-GPU and reversed
1:3 two-adapter ownership checks pass. Python1623/19skip and source/skill
follow-up103/4skip pass. Packaging passes after the owned test process releases
the Windows extension lock. Published MoE/nonzero-device and Vulkan CI checks
remain in progress. No local performance gate is run or passed. All qualification
flags remain false with empty scopes; physical distributed/performance acceptance
is deferred to the live three-computer cluster. Broader integration tasks remain
open. Receipts are artifacts/prima-devices-*; retain the initial package-lock
failure and pre-manifest GPU-evidence failure as developmental evidence.


### T085 driver-unavailable follow-up

Recipe39 published dense/MoE100, native5/5, CPU/no-visible-GPU, nonzero logical
Vulkan1 and reversed1:3 adapter checks pass. Hosted Vulkan CI fails its server
contract with0xc0000409 when no driver is available. The same failure is reproduced
locally using an owned missing-driver manifest; an empty visibility mask alone
does not test this branch. MSVC /EHsc C-call assumptions make the exported
initializer's exception rethrow unsafe. Native c8a3744e30b78fe9dcb40668505c0158e1228a61
now retains initialization failure as an empty GPU inventory, refuses unavailable
backend/buffer handles, and adds a forced-driver-unavailable CI server contract.
Development native5/5, unavailable-driver contract/discovery, CPU model inference
and explicit GPU refusal pass. Recipe40 clean build/CI is in progress. Preserve
recipe39's failed CI and the local reproduction. Performance and physical
distributed validation remain deferred to the live three-computer cluster; all
source qualification flags and scopes remain false/empty.


### T085 final published correctness checkpoint

T085 completes scoped ordered local device ownership on native
c8a3744e30b78fe9dcb40668505c0158e1228a61, consumed by recipe40
97f82a0542f4fde687d17240bef8d3d24a71de50090b7154e63bf268997c802b. Clean
managed runtime5d90c864bc9373d2b259f3b14600345666fb75005278a67fe40d239f042f9b0a
passes source/artifact binding, native5/5, dense100/MoE100 gateway checks, reversed
1:3 adapter ownership and driver-unavailable contract/CPU model inference. Local
nonzero-device/unavailable-selection checks also pass. Python1623/19skip, final
pin/skill103/4skip, Ruff, packaging and skill sync pass. PR CI38280837875 and
push CI38280831455 pass all four configured variants, including the new forced
unavailable-driver check. All owned check/build/model handles are terminal.

Retain recipe39 failed CI and the exact local no-driver crash reproduction;
recipe40 corrects the exported C exception boundary rather than skipping the
contract. Local two-adapter native correctness and controlled UUID-launch tests
do not establish managed multi-node, all-platform/backend or complete placement
qualification. Broader T012-T022/T032/T039/T056/T066 requirements remain open.
No performance-gate validation or paired timing is run or granted. Physical
distributed/performance acceptance will be tested later on the user's live
three-computer cluster; thresholds stay unchanged, source flags stay false and
execution scopes stay empty. Complete receipts: artifacts/prima-devices-published40-*
and artifacts/prima-devices-*.


### T086 development correctness checkpoint

Native local draft construction now clears the target manifest, enforces one
local rank/independent KV, honors CPU placement and cache types, and skips native
autonomous scheduling. Recipe40 reproduction failed before readiness and showed
CPU-requested draft weights on Vulkan; the development fix passes. Target
verification now preserves per-token grammar/logprobs/usage/limits and checked,
bounded draft-prefix batches. Worker rollback IDs retain the existing intentional
head-to-worker offset. Slot initialization/cleanup and target-vocabulary proposal
bounds are guarded. Modern draft aliases and draft_simple_v1 are implemented.

Managed active draft assets/shards use verified layouts and the existing resolver;
capability/asset-role checks occur before staging, and each independent CPU draft
context is priced/bound. Source snapshot remains recipe40 until publication.
Development dense/MoE gateway suites each pass101 checks; dense boundary/long
prefix checks pass, including accepted tokens, all logprobs, exact four-token
usage and invalid-budget refusal. Controlled two-native-rank generation with a
zero-work CPU head, Vulkan worker and CPU draft passes. Native5/5 and final
Python1636/19skip pass; temporary gateway failures (path/flag/resolver issues)
are retained. Native proposal-bound follow-up and publication/clean rebuild/CI
remain pending. No performance gate is run or granted. Physical distributed and
performance acceptance will be tested later on the live three-computer cluster;
all qualification remains false with empty scopes. Other speculative variants,
complete API/model/placement/platform requirements remain open.


### T086 published implementation / clean build in progress

Native a03140456a4153a6023b8a3da8e46b566f1db4ab is pushed to fork PR1 and
consumed by recipe41. Development native5/5, dense101/MoE101 gateway checks,
accepted-token/logprob/length/budget/long-prefill checks and controlled two-rank
zero-work-head/Vulkan-worker draft verification pass. Final Python1636/19skip
and post-pin/skill149/5skip pass; clean managed rebuild and CI are in progress.
The fixed resolver path and native modern aliases were verified through the
gateway; earlier failures remain retained. Proposal IDs beyond the target vocab
are truncated before verification, preserving compatible extra vocabulary tails.
Inactive drafts do not require assets. Root/source qualifications remain false
with empty scopes; performance and physical distributed acceptance will be
tested later on the live three-computer cluster. No local performance gate is
run or passed. Other speculative strategies and broader integration requirements
remain open. See artifacts/prima-draft-* for the scoped receipts.


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
