---
name: kllm-prima
description: "Add features, improvements or bug fixes to berkkarahan/prima.cpp while integrating and validating them through Kluster LLM's managed Prima runtime. Use for Prima native changes, source/recipe updates, Windows/Vulkan assessment and engine qualification; use kllm-develop for unrelated Kluster changes."
---

# Develop Prima through Kluster LLM

Work across Kluster and `https://github.com/berkkarahan/prima.cpp`. Kluster owns
membership, final placement, authenticated preparation and process lifecycle;
Prima owns native model/backend execution, profiling and its server behavior.
Prima is an optional engine. Inspect current qualification before treating it as
selectable or production-ready.

## Establish both sources

1. Follow Kluster's `AGENTS.md`, memory workflow and `kllm-develop` conventions.
   Read `docs/prima-integration.md`, `docs/prima-windows-assessment.md` and
   `specs/017-prima-integration/{spec,plan,tasks,validation}.md` where present.
   Consult current source when historical receipts disagree with it.
2. Read `src/kllm/modules/primacpp/recipe.json`; its source repository/commit and
   dependency pins are authoritative. Compute the current digest through
   `kllm.modules.primacpp.interactors.recipe.recipe_digest`. Do not hardcode a
   digest or infer the pin from the fork branch's moving HEAD.
3. Locate an existing Prima checkout or create an isolated checkout of the pinned
   fork commit. Check both repositories' status, origin and branch before edits.
   Use CodeGraph first when that checkout has its own `.codegraph/`; unindexed
   native sources can be read directly. Retain unrelated work and downloaded models.
4. Identify the failing behavior and its boundary: native execution/server/profiler,
   managed installer/packaging, Kluster manifests/planning/lifecycle, or gateway/UI.
   Fix the owner. Modernize Prima's existing execution path; do not silently swap
   engines or replace the accepted architecture with a different runtime.

## Implement and measure

- Follow the user's validation schedule. For the current Prima integration,
  finish the required implementation before running any unit tests or starting
  test-fix work. Do not substitute native contracts or model assessments for the
  deferred test phase. Production compilation may check implementation, but it
  does not count as acceptance. After implementation is complete, run relevant
  tests and fix failures before claiming the integration is finished. Performance
  and physical distributed acceptance remain deferred to the user's live
  three-computer cluster; keep qualification disabled until evidence exists.
- Reproduce defects using an isolated managed build and supplied, complete GGUF
  assets. Record source/recipe/artifact/model hashes, effective settings, physical
  device UUID, driver and OS. Capture the original failure before changing source.
- Native changes go in the Prima Git checkout, with runnable C++ contract or
  model-backed regression checks. Kluster changes stay in its owning feature
  modules; run relevant Python/Rust/dashboard checks through `kllm-test`.
- Use `scripts/install.py --llama-only --engine primacpp --from-source` with the
  resolved `--source-repo`, `--revision`, `--recipe-digest`, `--runtime-dir` and
  explicit backend. Follow `docs/agent-install.md` for toolchain recovery.
  Managed builds must not overwrite bootstrap `vendor/bin`.
- During development, build the edited fork using its CMake targets and retain
  its diff plus real test results. Label worktree evidence as developmental;
  modified files and manually restamped artifacts do not qualify a published pin.
- On Windows, build Release server, CLI, profiler and native contract tests;
  check staged executables and relocation to a path containing spaces outside
  the compiler shell. Preserve only actual required runtime dependencies.
  Compare effective compiler ISA and the serving binary's system_info with the
  baseline; cached CMake option values may differ from native-detected flags.
  MSVC native instruction detection must check CPUID, OSXSAVE/XCR0 and runnable
  compiler intrinsics rather than optimized-away vectors. Enable AVX-512 extensions
  independently only when supported, refresh probes on reconfiguration, and retain
  GGML_NATIVE=OFF/manual ISA handling. Check missing CPU/OS prerequisites through
  deterministic vectors and an actual portable build; this does not prove host
  throughput or permit distributing a host-specific artifact to another CPU.
  For CPU graph scheduling changes, preserve real-operation dependencies and final
  worker settlement while skipping metadata-only work. Cancellation must stop all
  workers at the same node boundary; a shared boolean can strand a late worker.
  Check prior cancellation leaves outputs untouched, cancellation after real work,
  repeated pool reuse, alias/empty graphs and both OpenMP/pthread implementations
  with one/multiple workers. Bound regression processes and retain timed-out owned
  process evidence; termination is cleanup, not a passing test or performance result.
- For Vulkan, map logical visible ordinals to physical identities explicitly.
  Never assume device zero, use adapter display names as unique identities, or
  substitute CPU/Metal/CUDA. Completed prefill/decode GPU work and exact assigned
  layers/tensors establish execution; device enumeration and configured ranks do not.
- For kernel profiling, encode operands into their actual tensor type, check compute
  status and completed output against decoded operands, and refuse unsupported GPU
  operations without CPU substitution. Verify every affected CPU format and the
  selected physical GPU separately. GFLOPS from malformed storage are not evidence;
  successful synthetic matrices do not prove the full-model profiler or planner.
  Invalidate affected cached measurements through immutable source/recipe identity.
  For indexed MoE kernels, verify input and down projections, distinct token/expert
  offsets, padded ID views, one/multiple selected experts, odd output widths and the
  small-batch/general dispatch boundary on CPU and the requested physical GPU.
  Compare against decoded stored operands; account explicitly for CPU dot-input
  quantization and any existing F16 decoding with a numerical rounding bound. Keep
  model correctness/performance gates unchanged. Run paired timing after contract
  processes/builds settle; identical text can still have different generated token
  sequences, which must remain visible rather than being retokenized as usage.
  Compare graph optimizations independently on CPU and the selected GPU. Fewer
  graph nodes can still regress host execution. Preserve host-weight paths when
  measurements show that regression and select device-memory optimizations by
  actual buffer ownership. Check signed/odd-width/multi-token expert reductions,
  neighboring tensor preservation and completed alternating operation timings;
  operation microbenchmarks do not replace full-model paired acceptance.
  Reject a graph optimization when the final matched model receipt regresses,
  even if a short operation sample improves. For expert sums, include cancellation-
  sensitive F32 values: tree and serial additions can differ and change greedy
  generation. Vulkan direct-stride paths must retain host/other-backend handling,
  check view offsets/stride widths/dispatch bounds and refuse unsupported layouts.
  Adding shaders must update incremental generation dependencies as well as pass
  a clean managed build; an old generated header is not current-source evidence.
  For full-model kernel jobs, extract complete GGUF metadata without replacing live
  model tensors. Count actual attention dimensions, head norms and active MoE experts,
  retain full storage costs and refuse missing/unknown roles. Validate typed per-layer
  and input/output estimates against their rate tables and exact context/threads/backend.
  Bind cache identity to observed host/device hardware and recheck before publication.
  Keep roofline estimates distinct from generation timings and qualification. Native
  I/O probes must use an exclusive owned file on the model filesystem, explicit cache/
  alignment settings, checked reads, bounded work and handle-owned cleanup on child
  death. Record unavailable storage separately; do not substitute paged/RAM throughput.
  Signed profile start/status/cancel must bind the exact approved preparation,
  operation, manifest, sender/authority, model/runtime/rank and effective settings.
  Advertise/require the profile protocol capability before transferring assets;
  generic signed-channel capability is insufficient. Private link measurements must
  follow actual directed rank edges on the approved private listener, with checked
  bytes, signed sample receipts and exact source/assignment revisions. Delegate
  worker peer keys only through ephemeral master-approved preparation/manifest/
  authority scopes, recheck ownership during measurement and preserve pinned pairing.
  Candidate exclusion may create new directed ring links. Derive extra profile_edges
  only from representable feasible candidates among already prepared participants,
  bind them to the immutable signed manifest, and require link protocol2 before
  asset transfer when extras exist. Preserve legacy canonical digests when absent.
  Measurement approval never widens native listener rules or changes execution ranks.
  Reject unapproved/duplicate receipts; missing any candidate link keeps capacity-first
  planning. Check a four-agent removal case over real signed transport and distinguish
  fixture rates/adapters from physical distributed acceptance.
  Bound stream work, concurrency and cancellation; use a high-resolution clock for
  elapsed transfer timing on Windows. Label authentication/receipt latency and
  acknowledged application throughput explicitly; they do not prove raw NIC speed,
  native generation or qualification. Require link protocol capability before assets. Long jobs recheck current
  authority and the progress-renewed preparation lease rather than extending the
  original wire command deadline. Persist owners, refuse foreign status/cancel and
  request-ID adoption, and keep cancellation atomic with cache publication. Import
  peer results only after matching current signed hardware evidence and complete
  typed context; normal previews remain read-only until link/context evidence exists.
  Distinguish device memory reads/KV copies from host uploads/downloads. Measure
  completed exact model-width F32 handoffs on the requested GPU, warm once, retain
  checked sample arrays and refuse CPU substitution or advertised capability downgrade.
  Bind profile storage to the OS filesystem device identifier and owned directory,
  recheck before publication and through preparation-owned signed fresh context.
  Score absolute assigned CPU/GPU indices and explicit input/output ownership only
  when every feasible candidate has matching source/model/settings/hardware/storage
  and fresh exact directed links. Keep application link proxies and logical weight-read
  estimates labelled; do not add cold loading to steady-state decode. Embedding handoffs
  cannot supply GPU logits download costs. Measure and byte-check completed F32
  downloads at actual model vocabulary width, retain exact positive samples and refuse
  missing advertised logits capability before publication. Bind the cost to GGUF
  token embedding dimensions. Account for connected local versus separate distributed
  transformer/output graphs: GPU output avoids the local intermediate embedding
  download, while the distributed output graph uploads its host embedding again. Missing evidence preserves capacity-first
  selection, and previews must not run measurements, restage layouts or write caches.
  Reserve node work before model resolution or native listener/rank startup, using
  the same registry guard for Prima and llama.cpp/RPC. Bind sharing to exact operation,
  authority and contract; owned preparation may profile its own assets, while overlapping
  measurements or foreign work refuse. Keep worker transfer claims through complete
  verified bytes and listener claims through durable cleanup. Cancellation/terminal
  labels cannot release unfinished futures or live owned children. Retain failed
  generation cleanup identity and cleanup_pending ownership for retry. Tie receive
  grants to a real bounded timer and settle source/reader tasks before releasing their
  token. Resource guards do not replace durable supervisor journals or leases; negotiate
  resource capability before distributed transfer/admission and test all-node refusal
  before spawn, unrelated-owner preservation and startup across both engines.
- Preserve exact placement and fail unsupported layouts/settings explicitly.
  Manifest mode must not invoke autonomous profiling, prune ranks or reschedule.
  Test zero-work coordination and partial offload where affected.
- Keep model shards, drafts, projectors and templates in verified asset manifests.
  When chat generation differs, compare each engine's actual rendered prompt token
  IDs through its own /apply-template and tokenizer before attributing the failure
  to model execution. Share native rendering with generation, exercise history and
  malformed flags at the HTTP boundary, and negotiate apply_template_v1. Equal
  prompts or output bytes do not establish equal generated tokens or API parity.
  Keep native usage counts and logprob token pieces; never replace them with output
  retokenization. Development capability reports must come from the tested binary,
  and development overrides remain explicitly unqualified until published/rebuilt.
  Wire operations stay signed and bound to sender/run/authority. Preserve readiness,
  PID/start-token ownership, leases, cancellation, cleanup and recovery boundaries.
- Exercise transfers beyond their initial approval lifetime with complete bytes and
  hashes. Issue source grants just before each asset, extend them only on exact
  forward acknowledgements and reject source drift. Use fresh operation/sender/
  authority-bound preparation progress renewals for already-complete participants;
  duplicate, stalled, expired or cancelled work must not revive approval. Keep cache
  verification and chunk I/O off the command event loop and verify signed transport
  separately from simulated clocks and physical multi-machine acceptance.
- Require owned post-initialization native rank acknowledgement for readiness;
  configured output and PID presence alone are insufficient. Rank zero also needs
  HTTP health. Exercise the CLI worker's actual manifest startup and zero-transformer
  coordinator path with controlled same-host model processes before claiming those
  boundaries work. Keep physical distributed acceptance separately deferred when
  the user requests it. Retry pending cancellation/cleanup and admission release
  without publishing idle or restarting a cancelled run early.
  After validated readiness, retain the acknowledgement in owned state and check
  PID/start token plus HTTP health; growing serving logs must not trigger the
  startup parser's size limit or require another native startup acknowledgement.
- Journal each coordinator launch intent before sending a signed start; a lost
  reply can still leave a child running. Persist the exact pending ranks and
  command authority, narrow them only on stop acknowledgement, and restore runs
  for cleanup after an agent restart. Test bounded recovery, cancellation during
  readiness publication, persistence/release failures and unrelated-process
  preservation. Reconstructed owners must not resume inference automatically.
- Keep Prima network reservations distinct from RPC admission: bind exact private
  assignments/edges, manifest and master authority, and obtain signed route/address
  diagnostics without starting RPC children or relays. Release only after every
  owned rank acknowledges idle; a dead master is insufficient. Verify normal
  prepared Run/Stop, HTTP readiness, cancellation and recovery over real signed
  channels with owned fixture processes. Label mapped sockets, simulated adapters
  and fixture model execution explicitly, and retain physical acceptance separately.
- Bind distributed qualification to signed node-local platform/CPU/core/RAM,
  source/recipe/build/artifact and selected Vulkan UUID/driver/capacity observations.
  Recheck them before any spawn and during owned renewal; membership labels and
  inventory discovery alone cannot prove inference. Preserve legacy manifest
  canonical digests when adding optional metadata. Test the resulting manifest
  with published native ranks, including an explicit zero-work CPU coordinator.
- Derive inbound native listener ports and exact peer addresses from the approved
  manifest edges. Keep privileged mutations behind the protected local helper and
  its pinned-master signature/assignment/replay checks. Journal each run's intent
  before mutation and retain exact rule instance snapshots; refuse adoption or
  removal of foreign/changed rules. Restore cleanup only after helper restart and
  drain owned listeners before network restoration or authorization revocation.
  Contract vectors and non-executing OS script parsing do not prove privileged
  firewall mutation, coordinator admission/renewal or physical connectivity. Complete
  those production paths and record their acceptance separately before qualification.
  For changed/lost creation receipts, use the loopback-only token-authenticated
  listener recovery API after the owned rank stops. Bind local decisions to a fresh
  digest of durable ownership and current snapshots. `restore_owned` must retain
  changed/unknown rules; `keep_current` explicitly preserves rules and relinquishes
  local ownership without adopting them or releasing the cluster reservation.
  Cover stale/foreign/live-rank denial, helper upgrade, persistence failure and
  cleanup retry. Keep HTTP/native fixtures separate from privileged OS acceptance.
  Connect local recovery to the existing network dashboard for master and worker
  views. Bind reviews to the selected run/manifest, disable restoration for changed
  or unknown ownership, require acknowledgement before keeping rules and invalidate
  decisions after refresh/close/failure. Verify real browser interactions at narrow
  and wide CSS widths with production styles and viewport metadata. Local recovery
  must not optimistically release the cluster reservation or restart inference.
  Bound local helper queue, connection and framed I/O with one monotonic deadline.
  Exercise isolated real Windows pipes with fragmented/oversized replies, stalled
  reads/writes, queue expiry and cancelled Prima waits. Synchronous cancellation
  must target an owned thread handle and settle before the client lock opens;
  retry across call gaps and flush server replies before disconnect under the
  same deadline. Never treat cancellation requests/timeouts as proof that a native
  mutation or rank stopped. Preserve pending ownership and distinguish helper
  timeout from missing authorization in API/UI. Keep fixture installation checks,
  real protected-service image rejection and privileged service/ACL/OS acceptance
  separate; do not weaken the protected identity check to make a fixture pass.
  On already elevated Windows manual adapters, use the direct native policy child
  over private stdio; do not silently switch durable protected/direct ledgers.
  Require native administrator refusal, mode negotiation, signed scope/rank commands,
  exact address observation and the same rule/watchdog ledger. Direct mode must never
  mutate DHCP/addresses or restore them on restart. Correlate replies with request
  UUIDs, preserve partial frames across read timeout and settle abandoned write/framing
  before another owner starts. Guard local rank/port ownership before manual network
  cleanup, reconcile only unchanged signed assignments and drain old scopes/rules
  before changing grants. Require explicit typed empty scope/rule lists from the
  matching native grant before retirement; missing fields are not empty ownership.
  Probe the replacement native owner before forwarding signed authorization.
  Test owned child shutdown and separate controlled pipe
  replies/native signature checks from accepted elevated physical OS evidence.
- Negotiate native listener capability before mutation and persist every attempted
  participant before sending admission. Require all rank listeners before any child
  starts; hold the node admission lock through validation/spawn, then bind running
  leases. On lost replies, cancellation, expiry or reconstruction, stop only the
  matching owned rank before releasing its rules. Narrow durable cleanup per
  acknowledgement and continue draining reachable nodes when one release fails.
  Cancelling an async wait does not cancel its blocking helper call: settle that
  call before acknowledging cleanup, and generation-fence delayed admissions.
  Keep node-local agent leases distinct from a privileged helper's independent
  watchdog; one does not prove cleanup when the other process is absent. Preserve
  old-worker upgrade errors and never infer OS/helper availability from wire version.
  Require fresh exact-contract signed native renewals: status reads cannot extend
  the helper watchdog. Check expiry/non-revival and changed-rule retention with a
  timer independent of IPC activity. Keep rule removal separate from owned process
  termination and cluster-release proof; it cannot establish that existing TCP
  traffic or generation stopped. Validate privileged OS/service behavior separately.

## Publish and consume an immutable change

When committing/publishing is authorized by the task or existing session:

1. Inspect the fork diff, run affected native checks and check whitespace. Commit
   only the relevant source/tests/CI/docs. Never include build trees or model assets.
2. Compare the remote branch with the tested base. Prefer a normal fast-forward
   push to the task's branch (the existing integration branch is
   `codex/kluster-integration`). If it advanced, integrate its changes and rerun
   affected checks; do not force-push away another change. Report unresolved
   conflicts or authentication failures instead of rewriting history.
3. Verify the published full SHA with the remote. Update Kluster's recipe to that
   exact commit, increment its build recipe when its contract changes, and preserve
   upstream/dependency provenance. The normal managed path applies no hidden local
   patches. Existing builds remain historical and require fresh resolution/build.
4. Resolve/rebuild from the published source in a fresh isolated runtime directory.
   Verify source cleanliness, artifact hashes, capabilities and relocation. Rebuild
   Kluster through `uv run python scripts/build.py` when packaging/native bindings
   change, and check the imported native extension.

If publication is not authorized, leave a reviewable fork diff and report the
required publication step. A skill alone does not authorize a push, live runtime
switch, cluster/network change or qualification-policy relaxation. Honor authorization
already granted; do not request it repeatedly.

## Qualification and delivery

For tool history, preserve returned call IDs and group consecutive historical
calls. Normalize each public protocol before Jinja rendering: Chat assistant/tool
messages, Responses function_call/function_call_output items, and Messages
tool_use/tool_result blocks. Match the pinned baseline's text joining and template
argument representation. Reject malformed IDs/arguments, unsupported non-text
blocks and templates that lose calls. Discover tool_turns in each protocol without
misclassifying text tool blocks as vision or changing native HTTP error boundaries;
nested image results still require vision evidence. Check real generated call
follow-ups, streaming continuation and exact baseline prompts/token IDs separately
from automatic choice and stored-response support.

For tool streams, preserve one call/item identity through start, argument deltas
and completion in Chat, Responses and Messages. Keep arguments in the protocol's
tool channel, retain terminal usage and native logprobs even when framing or UTF-8
buffering produces no visible text, and ignore aggregate terminal content as a
second delta. Cover partial JSON/length limits, empty completion, repeated terminal
records and errors after start. Test actual model-backed streams through the
authenticated gateway. Named forced calls do not prove automatic choice, parallel
tool parsing or complete tool-history support; keep those features gated separately.

For template work, use the parser/runtime pinned to Kluster's baseline and retain
source/license provenance. Compare every bundled preset against an independent
Jinja reference, then check actual model prompts/token IDs and serving behavior.
Freeze selected source bytes into the signed manifest before preparation; bind
those bytes to qualification and require the native Jinja capability. Rank zero
renders prompts and workers consume its token stream. Keep execution contexts
separate under concurrent requests, bound file/source size, reject malformed
templates before readiness, and share generation/apply-template rendering. Do not
append legacy thinking suffixes after a Jinja template already rendered them.
Keep input-origin token marking and automatic tool parsing distinct from plain
template rendering; a renderer pass cannot qualify these broader features.

Honor the current task's validation boundary. For this integration, the user has
deferred performance acceptance and physical distributed validation to a live
Kluster cluster of three computers. Do not run further local paired acceptance
or performance-gate diagnostic experiments. Continue implementation and correctness
checks, preserve the existing thresholds, and keep deferred gates unpassed.

Optional GGML_VULKAN_PERF builds are diagnostic artifacts. Require their actual
diagnostic_vulkan_timing_v1 capability and refuse them as acceptance inputs before
starting model or placement processes. Capture development binary capabilities
with a bounded owned probe and verify its hash is unchanged; do not reuse the
published runtime's capability record. Retain owned server logs through failure
and cleanup. GPU timestamp queries must preserve normal batching, check queue
support and result availability after completion, handle counter wrap and units,
bound query allocation, and never wait for unused slots or invent missing samples.
Restore ordinary non-instrumented builds for normal correctness validation.

Use the source-controlled qualification policy, not writable runtime manifests.
Evaluate OS/backend, topology, exact model/feature/placement combinations and
device/driver evidence. Policy updates must not invalidate the build they qualify;
implementation changes invalidate affected evidence.

Keep controlled tests, physical local Vulkan, local multi-rank and physical multi-node
results separate. Local Windows/Vulkan qualification cannot enable workers, other
platforms, untested models or features. Missing assets/hardware/checks are unavailable,
not passes. Match paired benchmarks on bytes/settings/device/offload, include warm-up
and repeated alternating pairs, and use the existing scope's performance thresholds.
The distributed improvement gate needs physical distributed measurements.

Update feature tasks, validation receipts and affected operational/API docs with
actual results and remaining blockers. Deliver both repository revisions, commands
and results, the new recipe identity, compatibility impact and any still-blocked
selection paths. Never describe build/device-discovery success as completed inference
or complete Prima qualification.

Qualification presets must bind effective launch settings, exact rank/offload/output
placement, complete named assets and host/device/driver identity. Persist the admitted
context and recheck it during serving; global model/feature lists must not grant a
different preset's request features. Empty or revoked preset evidence remains closed.

Check baseline flag semantics before calling a comparison matched. The pinned
llama.cpp `7fe450e19305b828c199d602c23a8337aaa1f03b` counts output in every positive
GPU-layer count; Prima counts transformer layers separately. Use the shared assessment
translation and long-prefill/decode placement probe, including tied embedding output
weights when CPU projection is requested. Keep placement tracing outside timed runs.
Preserve historical mismatched receipts, label their limits, and repeat affected
acceptance without lowering its criteria.

For automatic-tool development, use the pinned baseline chat-runtime bridge rather
than a new format heuristic. Keep its license/provenance and API adaptations
visible. Wire serialized preparation, lazy triggers and the baseline parser through
actual generation before advertising an HTTP capability. Repeated parser elements
must consume input: optional calls plus nullable whitespace can create a grammar
cycle. Compute transitive nullability before recursion checks and reject such
cycles safely. Test generated grammars, split/mid-token triggers, Unicode, clone,
reset and vocabulary-backed sampler acceptance. Prima's initial model allocation
does not load its vocabulary; call its checked metadata loader and free that
loader when testing vocabulary-only assets. A parser/sampler contract does not
prove automatic/required HTTP calls or parallel streaming. Do not change the
recipe while a model lifecycle assessment is using the previous pin: restart
admission rechecks that immutable identity.

Automatic HTTP tools use the same baseline serialized preparation for generation,
apply-template and incremental parsing. Preserve plain/unmarked JSON as text;
compare an unexpected model choice with the pinned baseline before changing parser
semantics. Check explicit tagged activation and ordinary tool decline separately.
Required and parallel calls need actual schema-bound model output and continuations
from their emitted call IDs. Streams must keep text/reasoning/arguments separate,
retain stable per-call/item identities and once-only terminal/error records, with
length termination and owned disconnect settlement. Reserve internal preparation
parameters on raw completion/infill as well as chat routes. Invalid generated
GBNF must refuse instead of silently disabling grammar. Keep tools:auto,
parallel_tools and streaming_tools qualification distinct, and require the actual
native automatic/streaming capability markers. Library capabilities never replace
model/template/placement-specific evidence or the deferred performance gates.

Generated regular tool grammars must accept the template's existing generation
prefix before sampling, matching the pinned baseline. Tokenize it with special
tokens, skip only an inserted leading-space token, and reapply it after sampler
reset. Never prefill a user-provided grammar. Missing prefill forced a duplicate
assistant header and diverted reasoning into text. Check real-vocabulary repeated
reset plus thinking-on calls/streams across all three APIs, not only thinking-off
calls. Preserve failing published receipts and advance the immutable recipe when
this sampler behavior changes.


For explicit local devices, preserve native registry/device/buffer identity and
ordered selection. CPU-only none must bypass all GPU backend initialization;
selected Vulkan host buffers must not initialize an unselected adapter. Managed
UUID selectors require physical visibility masking followed by remapped native
--device names. Verify the actual binary flag and explicit_devices_v1 capability
before multi-device admission. Compare completed matrix weights with exact
float-narrowed cumulative layer proportions and physical UUID/driver/name mapping;
report only adapters with executed work, including zero assigned-work devices.
Check nonzero logical ordinals, ordered multi-adapter splits and no-visible-GPU
CPU execution with owned functional model processes. Registry/device selection
does not establish row/tensor/expert support, physical distributed acceptance
or performance qualification. Keep all deferred gates unpassed.

An empty Vulkan visibility mask does not reproduce driver-unavailable startup.
Check the latter separately in bounded owned subprocesses, retaining the failed
contract/CI receipt. MSVC /EHsc assumes exported C calls do not throw: keep
Vulkan initialization exceptions inside that boundary and expose an empty GPU
inventory on failure. CPU-only inference must still work, repeated discovery
must settle, and explicit unavailable GPU selection must refuse before model
loading. Add a driver-unavailable CI contract without claiming GPU acceptance.

For draft-simple decoding, clear target manifests/ranks/network setup in each
local draft context and suppress autonomous profiling. Respect independent K/V
cache types and explicit CPU selection; do not force mlock or inherit the target
GPU layout. Verify the actual modern flag aliases before managed preflight.
Resolve active draft files through the existing model resolver, stage complete
shards under verified basenames, and bind role/hash/size plus CPU placement and
context count in the signed scope. An inactive draft must not require its file.
Price every independent HTTP-slot draft context on the master; preserve baseline
shared-context estimation. Check bounded prefix batches, decode failure, sampler
grammar state, all accepted-token logprobs/usage, EOS/stop/output limits, invalid
request budgets and out-of-target-vocabulary proposals. Worker sequence IDs are
intentionally offset from the head's reserved sequence zero; audit the actual
metadata mapping before changing rollback IDs. Free samplers/contexts before
models, handle null speculators, and refuse failed slot initialization before
readiness. Capture accepted-token work counters, not performance claims. Check
real dense/MoE gateway paths and controlled zero-work-head native ranks, while
physical cluster/performance acceptance stays deferred and unqualified.

For prompt caching, reserve the entire configured host RAM ceiling on every
selected participant, including GPU workers and a zero-layer coordinator. Bind
the budget to signed settings and require prompt_cache_v1 before preparation.
Keep serialized KV state on its owning rank; use bounded all-rank transactions
and one eviction order for save/restore/remove. Remove partial saves everywhere;
invalidate partially restored sequences before recomputing their prompt. Missing
or foreign acknowledgements make the context unusable. Serialize actual owned
layers with absolute indices and handle zero-cell state safely; preserve existing
full-model state format. Clear snapshots when system prompts/adapters change.
Cache operation evidence establishes no throughput benefit. Defer cache contracts,
model processes and CI test execution with the rest of the test phase until the
implementation is finished. When pushing during this phase, skip CI tests and
record that they still require execution afterward.

For context checkpoints, keep a separate pool/ceiling from prompt-cache RAM and
independent histories per HTTP slot. Capture actual completed prefill positions,
including on a zero-layer head; optimistic n_past can include undecoded sub-batches.
Recurrent snapshots require an entire matching prefix and a remaining token to
rebuild logits. Rebuild sampling and draft prompt state after restoration. Freeze
count, token spacing and the planned byte ceiling in the execution contract and
negotiate context_checkpoints_v1. Price Prima's full sequence state rather than
the baseline's partial recurrent-only state, including metadata, on every selected
rank without charging the master twice. Clear histories on system/adapter changes
and explicit slot erase. Keep checkpoint contracts, model branches and failure/
cancellation checks deferred with the rest of the test phase until implementation
completion; compilation grants no acceptance or qualification.

For deployment template keyword defaults, merge request values over the owned
deployment object in the same renderer used by chat, Responses, Messages,
apply-template, input-token counting and automatic tool preparation. Negotiate
template_kwargs_defaults_v1 before preparation. Validate bounded finite JSON,
preserve dictionary insertion order, stage an owned content-addressed JSON file
on rank zero and verify existing bytes without following symlinks. Native startup
must match parsed defaults against the signed execution manifest. This file path
avoids Windows argv limits; workers consume rendered tokens. Named legacy aliases
must not silently ignore keyword defaults. Keep precedence, malformed input,
manifest/file drift and model/protocol checks in the deferred test phase until the
full implementation is finished.

For Windows native error handling, keep exceptions inside exported C entry
points: MSVC /EHsc assumes those calls cannot throw. Return nullptr/status from
model/context/backend/grammar/socket setup and refuse readiness on failure.
Backend setup borrows its context; callers free it once. Close only distinct owned
socket handles with zero linger, accounting for send/master aliases and partial
initialization. Refuse reinitialization without closing existing live handles.
Use checked sampler operations and thread-local error text for managed C++ callers;
keep nested chain callback failures visible at that boundary. A null grammar must
never become unconstrained generation. Preserve allocation ownership on grammar
reset/clone failures and bind cloned candidate pointers to copied storage. Fail
the owning HTTP request and clear its head/worker sequence on sampler failure,
while retaining other requests and valid snapshots. Keep invalid input, callback,
fully masked candidate, allocation/port/backend failure and model cleanup checks
in the deferred test phase until implementation completion. Compiler success or
removal of C4297 warnings is not runtime correctness or qualification evidence.

For arbitrary placement, carry immutable absolute layer ownership through model,
context, tensor loading, KV storage, graph callbacks and sequence snapshots.
Precompute local/segment indices; keep model/context identity and lifetimes exact,
clear target maps in local drafts and count explicit GPU layers in rank-local
ordering. Representation alone does not enable segmented execution. Keep current
window validation and do not advertise segmented_placement until the actual graph
and rank handoff executor can follow every approved visit. Keep row/tensor/expert
and architecture-specific graph support explicit. Test execution after full
implementation; compilation and map construction cannot qualify it.


For segmented execution, use only manifest-approved physical ring edges and bind
activation packets to the exact manifest, micro-batch wave, stage, row count and
model width. Preserve local graph visit order, handle zero-work relay ranks and
wait for a complete ring acknowledgement before another micro-batch or KV command.
Refuse malformed/foreign/expired waves and make failed contexts unusable. Reserve
all local visit schedulers and price each visit's host/device graph memory before
managed admission. The recipe47 executor groundwork does not enable the managed
segmented_placement capability: finish launch/admission/memory integration first.
Existing one-handoff-per-rank typed costs cannot score repeated visits or relays;
keep those candidates capacity-first and explicitly unmeasured until covered.
Unit/native/model checks and test-fix work start only after full implementation;
physical distributed and performance acceptance remain deferred to the live cluster.


Price retained Prima graph visits in the owning placement allocator, including
rank-zero input/output, worker host storage and additional per-device visits.
Keep exact absolute assignments; excess host/device/shared-pool capacity must
report overflow. Carry the internal prima_graphs reservation flag through typed
resolved settings and the immutable execution qualification scope; require it
before admitting a noncanonical layout from a segmented-capable runtime. A
conservative scratch estimate is not measured backend allocation or acceptance.
Local single-rank graphs retain their shared estimate; row/tensor/expert paths
need their own implemented pricing/execution before being enabled.


Recipe48 advertises segmented_placement for implemented Llama/Qwen2/Qwen3 layer
graphs after adding manifest admission. Freeze the planner's prima_graph_counts
alongside prima_graphs, and independently compare rank visit counts against exact
absolute ownership in both Kluster and native startup. Count head input/output
separately; merge adjacent ownership when counting local visits. Missing or altered
reservations must refuse noncanonical startup. Negotiate segmentation before using
absolute layer-count launch translation or adding candidate profile ring edges.
Keep unsupported architectures, row/tensor/expert work and one-handoff timing
limitations explicit. A capability report grants no qualification; execute the
correctness/test phase only after full implementation completion, and leave
physical distributed/performance acceptance to the live three-computer cluster.


For segmented planner costs, derive contiguous absolute ownership visits and
count completed GPU uploads/downloads per GPU-containing visit, keeping the head
output upload/logits download independent. Route each activation through actual
physical ring hops, including relays and repeated visits, and include full-ring
settlement plus the unchanged batch metadata chain. Bound native marker/JSON
payload sizes using the largest wave counter. Keep transport framing, buffering
and relay CPU work outside an explicitly labelled application-link proxy; these
estimates are not generation timings or performance acceptance. Require exact
planned graph counts and the current published segmented capability with fresh
signed model/source/settings/hardware/storage/link evidence. Keep cold logical
weight reads separate from resident decode and canonical legacy wire estimates.
Missing evidence must retain capacity-first selection; preview must not measure
or mutate assets/caches. Validate routes, visit costs and mixed candidate sets
only in the later test phase after full implementation completion.


For output-only GPU coordinators, retain ngl as the transformer count and allow
rank-zero output_gpu with zero transformer offload and one selected device.
Negotiate output_only_gpu_v1, preserve explicit device/visibility selection and
Vulkan signed hardware identity, and require the output backend at startup.
Reserve the output graph even when CUDA transformer offload is zero. In evidence,
never use assigned[-ngl:] when ngl is zero: keep the transformer GPU set empty and
require completed output projection in both prefill and decode on the selected
physical GPU. Price output upload/logits download separately from transformer
visits, including CPU-transformer local heads. Compilation does not establish
correctness or qualification; keep model/contracts in the later test phase and
physical distributed/performance acceptance deferred to the live cluster.


For managed LoRA, validate adapter metadata and complete A/B pairs on all ranks,
then allocate only the immutable owner's transformer tensors; head input/output
weights stay on rank zero. Unknown targets must fail their owning rank before the
readiness barrier. Keep metadata/device contexts/buffers/model registration under
exception-safe ownership and contain initializer failures at the exported C boundary.
Validate metadata types, finite alpha, positive rank and matching dense/expert
shapes. Keep target LoRA files out of independently loaded draft models.
Recipe50 provides this loading foundation only. Do not advertise managed LoRA until
verified adapter roles/transfers, ordered file/scale bindings, per-rank weight/scratch
pricing, owned launch paths and native rank-wide selection/scale synchronization are
implemented. Head-only HTTP adapter changes are insufficient: settle all ranks
before decode and preserve cache/checkpoint invalidation. Keep adapter and failure
checks in the later test phase after full implementation completion; compilation
and staging alone do not qualify adapter execution or distributed performance.


For managed LoRA assets, preserve ordered declarations and normalize finite F32
scales; allow template keyword defaults alongside adapters. Give each unique
regular complete local adapter an explicit verified asset role, retaining original
portable basenames without applying model-shard filename rules. Reject conflicting
names and symlinks. Freeze ordered basename/scale bindings and planned storage/
per-token scratch bounds through typed settings and the qualification scope.
Reserve storage on host/selected allocation pools and scratch for effective batches
and retained graph visits; full-file/all-pair bounds are conservative estimates,
not measured rank allocation. Before spawn, recheck staged size/SHA and metadata
bounds, then remap every adapter under the owned model layout on all ranks.
Do not advertise managed_lora_v1 until native binding checks, rank-wide selection/
scale settlement, worker asset-role negotiation, snapshot invalidation and completed
GPU LoRA/output evidence are implemented. Tests remain deferred until full
implementation completion; physical distributed/performance acceptance stays with
the user's live three-computer cluster.


For native LoRA selection, use model-local stable adapter identities and ordered
application. Validate/prepare a complete finite selection before mutation; managed
heads must bind replacement commands to exact manifest, monotonic revision and
physical ring hop order, then wait for all-rank acknowledgement before committing.
Bound selection/frame counts and I/O; foreign, stale, missing or failed replies
must make the context unusable. Do not allow worker-local managed changes.
Settle backend work before/after KV and snapshot invalidation. Check startup status
and retain exception-safe cleanup. HTTP updates must validate before enqueueing,
wait for idle slots, keep reported scales unchanged until success, clear draft KV
and rebuild system prefixes. Register result waiting before publishing the task.
Recipe51 implements synchronization code only; manifest/file/scale admission,
worker adapter-role negotiation and completed GPU LoRA/output evidence still precede
managed_lora_v1 enablement. Run synchronization/error/model checks only after full
implementation completion; compile success is not protocol or cluster acceptance.


For LoRA admission, independently check ordered native file basenames/scales and
approved size/SHA against the execution manifest, rejecting unreferenced assets and
changed initial selection. Stream hashes with bounded buffers and GGML's UTF-8 file
opener; retain legacy no-adapter manifest behavior. Negotiate worker adapter asset
roles before transfer and interpret master Windows/POSIX path separators consistently.
Freeze target metadata and recheck it from verified staged bytes. Tag actual LoRA
operation nodes with stable adapter/target/component/role identity, then report only
completed nonempty work with selection/revision context. Accumulate observations
per phase/epoch so partial batches do not hide later required work. Verification
must reject stale/foreign selections, wrong scales/targets/backends and incomplete
A/B execution. Do not enable managed_lora_v1 until input embedding and tied output
buffer paths are implemented. Native SHA vectors, file drift, role negotiation,
operation evidence and model checks run only after full implementation completion;
tracing/compilation is not performance or physical cluster acceptance.


For embedding LoRA, preserve the GGUF A(rank,vocabulary)/B(rank,embedding)
orientation and apply its delta to gathered token embeddings. Tied output needs
its own pair on the actual output buffer: decode/transpose B into F32 and retain A,
without rewriting supplied assets. Resolve input through the input pair even when
CPU tied weights share a tensor pointer. Preserve original component identities
in execution traces, including GET_ROWS for input A. Distinguish embedding ranks
in planner metadata. Load/apply adapters before graph reservation and include all
input/tied-output nodes; validate finite effective F32 scales before selection.
Do not reject Flash Attention merely because an adapter is selected when baseline
selection permits it. Recipe53 advertises managed_lora_v1 as an implementation
capability; it grants no qualification. Keep shape/transposition, quantized data,
GPU ownership, graph capacity, selection and Flash Attention checks in the deferred
test phase after the full implementation is finished. Physical distributed and
performance acceptance stays deferred to the live three-computer cluster.


For per-request LoRA, keep deployment defaults separate from slot selections.
Preserve empty-array baseline semantics and validate all IDs/scales before use.
Group only equal selections in a native batch and use bounded round-robin scheduling
so another configuration can progress. Settle batch weights on all ranks through
a manifest/revision/mode-bound acknowledged command before execution. A dedicated
batch-selection API may preserve sequence KV/snapshots only when the server tracks
their adapter identity; ordinary replacement must retain full invalidation. Bind
prompt caches/checkpoints to exact scales and charge that metadata to their budgets.
Clear changed slot/draft state and prepare system prefixes with each sequence's
weights, using independent prefix batches. Preserve selections in all public text
protocol translations and effective generation settings. Require request_lora_v1
and explicit request feature qualification; development capability is not acceptance.
Run concurrency, fairness, cache/prefix/draft, malformed selection and lost-reply
checks only after full implementation completion; cluster/performance gates remain
deferred to the live three-computer cluster. Activated LoRA invocation behavior is
a separate baseline requirement and cannot be inferred from ordinary LoRA support.


For activated LoRA, parse bounded UINT32 invocation metadata and check native token
IDs against the base vocabulary. Freeze count/hash identities from the supplied
adapter bytes, recheck owned staged metadata and negotiate activated_lora_v1 before
preparation. Find the final occurrence with a bounded linear-time scan that handles
overlapping sequences. Preserve the pinned baseline's activation boundary and
absent/multiple/mixed selection semantics. Select base and active weight phases
through the same acknowledged all-rank batch API before execution. System prefixes
must use the appropriate sequence weights without altering another slot's state.
Track neutral live-cache prefixes and neutral snapshot lineage. Cross-adapter reuse
may restore only captured base state within the new boundary; full recurrent state
after activation must never be relabelled as a neutral prefix. Capture at completed
positions, charge metadata to byte ceilings, and invalidate neutral reuse on shifts/
truncation while accounting for speculative accepted tokens. Compilation cannot
prove invocation/cache correctness or broad noncausal API/model behavior. Keep all
metadata, boundary, concurrency, recurrent/cache/draft and protocol checks deferred
until full required implementation completion; cluster/performance gates stay
unpassed until the user's live three-computer validation.


For reasoning controls, share the pinned baseline chat preparation/parser for plain,
named and automatic tool responses and structured schemas. Preserve effort/none,
thinking toggle, output format and protocol-specific history/delta semantics;
plaintext reasoning history is not vision or opaque stored state. Use the baseline
reasoning budget state machine with explicit model/token/sampler API adaptations and
license provenance. Enforce budgets in actual token selection, initialize from the
generation prefix, preserve UTF-8 completion/re-arming and suppress lazy grammar
while thinking; replay completed end tags before grammar resumes. Own grammar/chain/
budget allocation through failure and preserve reset/clone state. Bind deployment
controls to the manifest, honor request aliases/overrides, and require native
reasoning_controls_v1 plus explicit request/history qualification. An implemented
Jinja path does not prove legacy/manual/opaque-state parity. Test all parser, budget,
stream/history, sampler/draft and admission behavior only after full implementation
completion; physical cluster/performance gates stay deferred and unpassed.


For manual reasoning control, bind the server-generated returned completion ID to
its task before queue publication and reject caller-supplied internal fields. Use
the serialized inference loop to act only on the matching live, opted-in sampler;
slot numbers and later slot reuse cannot authorize a transition. Preserve baseline
success/message semantics outside active counting, and retain a successful control
across the initial prompt sampler reset. Expose the same authenticated inference
proxy without generation defaults or stream flags; separately qualify control and
custom delimiter features and negotiate their native capabilities. Legacy rendering
has the pinned baseline's pure-content/no-inferred-delimiter semantics. Compose
Jinja schemas with reasoning/tool grammar once; do not install duplicate schema or
fence constraints. Source/compile evidence does not prove prompt races, concurrency,
draft, auth or API parity. Run those checks only after full implementation finishes;
physical cluster and performance acceptance stays deferred and unpassed.


Derive baseline API requirements from the pinned implementation before adding a
new service. The current baseline Responses converter rejects nonempty previous
IDs and implements no persistence or opaque reasoning decryption; earlier notes
calling those missing integration features were inferences, now superseded. Match
its reasoning content array/type, empty summary/encrypted placeholders and actual
stream events. Replay from reasoning_text content and preserve assistant/tool
turn grouping. Messages thinking uses an empty signature and signature_delta before
block closure; keep all delta targets open until closure. Require reasoning_wire_v1
for the corrected protocol contract, separately from parser/control capabilities.
Source/compile evidence remains distinct from full API/model acceptance. Complete
all required implementation before tests or test fixes; physical cluster/performance
gates remain deferred to the user's live three-computer cluster and unpassed.

For model modernization, derive required tensors, graph operations and quantization
from the pinned baseline model implementation before adding an architecture name.
Attention sinks contribute one unscaled logit per query head to the softmax
normalizer without a value vector; regular softmax uses src[2] and Flash Attention
uses src[4]. Preserve per-head indexing across outer batches, optional operands,
masked/unmasked input, numerical stabilization and padded GPU dispatch bounds.
Advertise backend support only where the extra operand is actually computed;
unsupported accelerators must not silently ignore it. Optional graph-builder
wiring and production compilation are groundwork, not complete GPT-OSS/model
support. Keep loader, expert routing/biases/activation, sliding-window/RoPE,
quantization, exact placement and profiling requirements open until implemented.
Run numerical/native/model checks and fix tests only after the full required
implementation is complete; live cluster/performance gates remain deferred.

For expert biases, preserve the selected-expert/token ordering when gathering
rows. top_k IDs may be a padded/strided view: compact before flattening and use
an integer GPU copy, not floating-point reinterpretation or an unreported CPU
transfer. Match whether the baseline normalizes all logits or only selected
logits. A model-specific fused activation must register CPU work planning and
all GPU preparation/dispatch/shader dependencies, not just a source function.
Keep optional new graph behavior absent from existing callers unless required.
Compile evidence is not numerical/model acceptance; defer those checks until
full required implementation completion under the current user schedule.

When connecting a model loader to segmented execution, preserve absolute layer
metadata while storing only the rank's local tensors. Scalar/per-layer expert
widths and SWA patterns must match real GGUF metadata, including array types.
Use the same per-layer rotary frequencies in generation and cached K-shift;
KV defragmentation also maps owned absolute layers to rank-local tensors.
A zero-transformer coordinator has sequence positions without K tensors.
Fused QKV weights can have a combined bias or separate Q/K/V biases; make sliced
views contiguous before reshaping. Port tokenizer splitting and actual EOG
semantics with the graph: Harmony intermediate end markers must permit analysis
to continue into final text. Keep cached sequence isolation in noncausal masks.
Record compact SWA cache storage, quantization, profiler and accelerator gaps
explicitly; full-context KV plus masking does not prove the compact cache
contract. Registry/loader/graph compilation never establishes model acceptance.

When adding a quantized format, preserve its GGUF numeric type, packed block
size, exponent/nibble layout and actual stored bytes. Register type traits,
reference encode/decode, row validation, CPU dot-input type and every applicable
generic CPU dispatch; do not alias a neighboring quantization. GPU support must
include matrix/vector/indexed-expert/gather/decode shaders, F32 selection where
required, pipeline slots, generation dependencies and honest backend admission.
Copy reference behavior from the pinned primary source with license/provenance.
Check file-type policy and explicit overrides through their actual caller flow.
File-offset tensor-span accounting can already be format-neutral: inspect it
before adding fixed-byte estimates. Source/build evidence never replaces checks
against decoded original operands or full original model assets. Run those only
after full required implementation completion under the current schedule; physical
cluster/performance acceptance remains deferred and unpassed.

When extending full-model costs, derive roles and dimensions from the actual
loader/graph. Fused QKV must account for attention and rotary/KV work once; expert
bias arithmetic and active bytes use selected experts, while resident storage
includes all experts. Refuse missing/unknown roles and invalid/ambiguous shapes.
Keep non-matrix logical-work proxies labelled, and match KV costs to the actual
views/computation rather than a compact cache that has not been implemented.
Type-based matrix rates alone do not prove a model's GPU path: check required
sink/activation/other operations before publishing a requested-backend profile.
Existing typed decoders may already accept new format keys; inspect their rate/
byte recomputation before changing schema. Bind all new costs to a new immutable
source/recipe. Compilation is not measurement, generation quality or hardware
acceptance; run checks only after the full required implementation is finished
under the current schedule, with cluster/performance gates separately deferred.

For compact sliding-window KV, separate immutable owned-layer storage layout
from mutable sequence/cell state. Size the unified pool for each sequence's
window plus the incoming microbatch, with the pinned baseline's padding and
checked arithmetic. A simple window-only ring can overwrite history required
by the earliest token in a prefill batch. Activation must connect allocation,
separate cell reclaim/sequence operations, attention/mask views, shifts/defrag,
snapshot serialization and capacity/profile accounting together. Keep full-size
mode until those consumers are complete; do not advertise compact execution
from sizing metadata alone or score hypothetical memory/work savings. Exercise
shared prefixes, rewinds and zero-transformer ranks only in the post-implementation
test phase; physical cluster/performance gates remain separately deferred.


For compact sliding-window caches, keep sequence membership authoritative in the
full cache and map physical compact rows to those global cells. Prepare row
reservations without mutation before full-cache admission; commit only after
success. Bind tickets to owner identity and generation, validate restored maps
atomically, and preserve compact row bytes when global metadata is defragmented.
Shared sequences and earliest-token prefill history constrain reclamation. Keep
compact execution disabled until attention views/masks, sequence rewind/replay,
shifts, snapshots and managed budgets are wired together. Compile during this
phase; defer all unit/native/model tests and test fixes until full implementation.


Compact-cache views, attention masks and K shifts must share physical row order
and per-layer capacity. Defragment global metadata without copying compact row
bytes. Serialize compact layout/version/ownership/window and sparse row ordinals
alongside dense cell metadata; validate before map publication and clean up the
destination on failed/truncated restore. Allocate reservation commit storage
before full-cache admission. Prefix reuse must replay missing evicted history,
and distributed ranks must agree on that decision before mutating their caches.
Do not activate compact mode while managed budgets or replay coordination are
incomplete. Production compilation is source/build evidence only; all tests and
performance acceptance remain on the user's deferred schedule.


Distributed prefix rewind must check every physical rank before any cache
mutation, including a transformerless coordinator or a head without SWA layers.
Settle commit/abort with manifest, revision, sequence/range, phase, world and hop
identity; missing or foreign settlement makes the context unusable. Keep workers
from accepting decode or unrelated controls while a rewind preflight is pending.
Match resolved launch settings and KV/checkpoint estimates to actual storage:
while compact activation is unfinished, explicitly use/price --swa-full on every
rank. Do not treat this temporary full-layout accounting as finished compact
integration. Keep all tests deferred until full required implementation finishes;
physical distributed and performance acceptance stay pending for the live cluster.


For CLI session reuse, bind the declared local session-token prefix on each
transformer rank to a domain-separated digest of count and little-endian uint32
IDs. Also require a materialized predecessor at the earliest actual decode
boundary, including logits reconstruction. Empty relay ranks need no KV tensor
state. Replay when a worker session is missing/mismatched or required history was
evicted; invalidate declarations on checked mutation or actual decode. Ordinary
context-shift replay requires exact evaluated token history, even when session
saving is disabled. Positional add/divide preflight must simulate resultant
positions, signed delta bounds and window availability for every shared sequence;
RNN transforms operate on their tail state. Bind operands to all-rank settlement.
Keep compact activation disabled while server/self-extend replay or compact
managed budgets are unfinished. Preserve the user's deferred test schedule.


Server replay needs per-slot completed token history even when cache_prompt is
false. Populate it only after successful decode views, trim speculative futures
after accepted-prefix settlement, and exclude optimistic unevaluated entries
from later prefix reuse. Preserve the complete system prefix and aLoRA base/active
weight-phase identity. Replayed segments use their stored phase and restore the
live request selection afterward; preserve sampler/output state. Failed replay
clears the affected sequence through owned request recovery. Ordinary context
shift handling does not complete self-extend replay or compact managed budgets;
keep compact activation disabled until those consumers are complete. All tests
remain deferred until full required implementation finishes, with physical
cluster/performance acceptance pending for the user's live cluster.


Preflight grouped self-extend operations as one bounded transform program. Check
intermediate signed position/delta bounds, but evaluate required SWA history only
after the final composed result. Bind every operand and order to all-rank phase/
revision/manifest settlement. Replay needs actual evaluated positions as well as
token IDs; a session file without the transform schedule cannot safely be treated
as an ordinary reusable prefix. Do not accumulate transformer replay history for
recurrent self-extend. CLI replay does not complete server replay or compact
managed budgets; retain the activation and qualification guards until complete.
Continue honoring the user's implementation-first and live-cluster test schedule.


Server self-extend needs evaluated positions, weight phases and per-slot completed
counts. Rebase pending rows after the composed transform; advance counters only
for that slot's successfully evaluated rows. Division relative to a fixed system
origin preserves prefix coordinates; extend the program API without changing the
original step layout/wire contract. Evaluate system prefixes on real request
sequences rather than assuming head/worker sequence zero maps identically or RNN
state can be shared. Retry from complete history and restore the frozen batch
selection; refuse incomplete recurrent self-extend history. Keep compact mode
guarded until managed budgets and activation review finish. All tests remain on
the user's deferred implementation-first schedule; hardware cluster/performance
acceptance remains pending and unpassed.


Bind Prima's head KV ceiling (HTTP slots plus its reserved sequence) into immutable
settings, memory planning and qualification scopes. Price local drafts separately
with one sequence each. Mirror the actual unified pool while per-stream support is
unfinished, and preserve baseline wire mappings when Prima-specific fields are
absent. Count completed-history metadata, compact forward/reverse map copies and
snapshot row ordinals in addition to tensor storage. Quantized native KV estimates
must use block-aware storage and per-layer widths; include recurrent state cells
and checked products/sums. Estimates and successful production builds do not prove
model correctness or hardware allocation. Keep full mode active until the final
compact consumer/launch review completes. Run no unit/native/model tests or test
fixes until full required implementation finishes; defer physical distributed and
performance acceptance to the user's live three-computer cluster.


For compact cache activation, reserve graphs from immutable capacity rather than
live occupancy. Worst-case writes must fit the compact pool even after shifts or
defragmentation; thread the reservation flag through masks, storage and attention
without modifying live cache metadata. Bind explicit SWA mode and the head
sequence ceiling to native manifests and negotiate its advertised capability.
Account for the server's already-added reserved sequence during model startup,
while CLI workers and standalone manifest checks use the original slot count.
GPT-OSS compact storage is now an implemented default; other architectures'
compact paths and runtime acceptance remain open. Historical activation guards
above describe earlier prerequisites, not a current requirement to keep GPT-OSS
full storage active. Keep qualification off and follow the user's deferred test
and physical cluster/performance schedule.


When adding an architecture, follow the pinned baseline's metadata, loader and
graph together. Registry names alone do not establish support. Keep absolute
rank ownership, fused/separate QKV shapes, per-layer head counts, token versus raw
embedding scaling, head/post norms, activation and logit transforms explicit.
SWA pattern defaults and its RoPE frequency/scale are architecture-specific.
Extend compact masks, shifts, snapshots and managed/profiler byte estimates along
with the graph; negotiate the general compact storage capability before launch.
Price optional resident projections even when generation does not use them, and
retain explicit unfinished embedding/vision API contracts in the feature status.
Gemma3 generation/compact/profile paths are implemented development work; its
retrieval embedding-output and vision paths remain open. Source inventories and
production builds are not model/backend acceptance. Keep all tests deferred
until full required implementation finishes, and physical distributed/performance
gates pending for the user's live three-computer cluster.


Embedding projection integration must separate input/transformer width from output
vector width across metadata, graph pooling, allocation, reordering, C getters,
snapshots, server JSON and managed budgets. Aggregate pooled inputs before any
quantized projection; averaging projected microbatch means changes operation order.
Use sequence-ceiling columns for sparse/shared IDs and preserve complete token
output selection. A final head-only graph must use the assigned output backend,
settled worker wave and correct scheduler for evidence, with reset before metadata
cleanup on success or failure. Worker counts must use received batch metadata;
owned bounded vectors and complete shared-sequence/all-output flags avoid leaks
and graph-shape disagreement. Negotiate decode_metadata_v2 before distributed
preparation. Keep native matrix and OpenAI pooled response contracts distinct,
real token usage, normalization and encoding; source/build metadata is not API or
model acceptance. Continue the user's full-implementation-first test schedule and
live three-computer physical/performance deferral.


Inspect launch help by process role. The server owns embedding/pooling switches;
CLI workers receive selected output rows in batch metadata and must not be given
unsupported server flags. Preserve those options in immutable extras, validate
choices and capability, and distinguish embedding from chat qualification scopes.
Role-specific help is metadata evidence only, not a test or acceptance result.


Wire new native embedding features through authenticated route registration and
qualification as well as the binary. Resolve the served model ID; preserve native
input/format fields and keep generation defaults out of embedding requests.
Qualify encoding, requested dimensions and normalization explicitly. Parse frozen
mode extras by arity in native manifests so JSON/adapter values cannot become
flags; compare the head/worker role and reject environment/argv mode overrides.
Negotiate embedding_role_v1 before execution. Unsupported classifier pooling
must refuse before graph construction. Source wiring and capability/help/build
metadata do not prove API, model, backend or hardware acceptance. Keep the user's
implementation-first tests and live-cluster physical/performance deferrals.


For shared-expert MoE architectures, load only absolute manifest-owned layers and
use their local buffer/context indices. Keep routed and always-active shared FFN
branches separate until addition; preserve router normalization and per-layer
expert widths from the pinned baseline. Use direct sigmoid rather than silu(x)/x
for scalar gates. Profiler and planner costs must include full shared storage,
selected routed arithmetic and simultaneous branch scratch. Registration or
segmented capability metadata does not establish model or placement acceptance.


For fused SwiGLU models, price the combined up projection and its actual halves,
not an absent separate gate. Keep long/short RoPE factors on each owning rank's
layer buffers. Logical context slots must exclude head-only reserved sequence IDs
while KV/pooling metadata still reserves those IDs. Negotiate
logical_context_sequences_v1 before managed execution. Follow the pinned baseline
for attention semantics, including disabled Phi-3 sliding attention; do not infer
attention behavior solely from historical GGUF window metadata.


When enabling a sliding-attention architecture, update loading, graph admission,
owned dense/sliding masks, KV allocation, shifts, snapshots/replay and storage
estimates together. Default/pattern metadata and independent sliding RoPE must
match the pinned baseline. Gemma2 uses tied output weights, post-attention/FFN
norms, GELU, query scaling and attention/output soft-capping without Q/K head
norms. Its baseline scales supplied input vectors as well as token lookups;
Gemma3's token-only scaling must retain its own semantics. Price simultaneous
mask pools and soft-capping logical work without claiming measured rates.


When reusing decoder scaffolds, distinguish Gemma's tied-output/GELU path from
Gemma2/3 post norms and Granite's optional biases and scale controls. Preserve
Granite's rotary-disable switch in both attention and KV shifts; do not label
dense text groundwork as shared-expert or vision/deepstack support. LongRoPE
requires source enum/metadata parsing and CLI selection as well as factor loading.
Price factor storage on owning ranks and omit disabled rotary arithmetic from
logical costs. Follow the implementation-first test schedule and retain all
remaining architecture/backend/API/exact-placement requirements.


For Granite routed/shared experts, retain optional gate tensors: without a gate,
apply SILU to up and pass that result to down without multiplying by up again.
Keep selected full-router probabilities normalized and add the always-active
shared gated FFN without introducing Qwen2's sigmoid scalar gate. Preserve dense
Granite's rotary switch separately from Granite-MoE's rotary semantics. Load
expert/shared tensors only for manifest-owned layers; price all expert storage,
selected arithmetic, ungated activations, shared work and simultaneous scratch.
Optional gate callbacks must not report an absent gate matrix as executed work.
Source capability metadata does not qualify models, experts, hardware or timing.


Check the exact architecture before assigning new tensor semantics: Granite Switch
uses stacked token-selected LoRA and a persistent attention router; Granite SWA
uses fused routed gate/up and fused shared FFNs. Granite SWA needs explicit bounded
per-layer sliding/RoPE flags, actual selected-expert counts, sinks, and all compact
KV consumers. Slice fused routed outputs with their token/expert strides before
activation; price full storage and selected arithmetic once. Treat gate_up_exps
weights as expert storage in GGUF planning and reserve combined/contiguous halves
as well as simultaneous masks. Ambiguous fused/separate role sets must refuse.
Keep Switch, Hybrid and vision/deepstack work open until their own paths exist.


Preserve OLMo2's normalization axes and order: normalize projected Q/K columns
before head reshaping, omit attention/FFN pre-norms, and retain post-attention/FFN
norms. Sliding layers use plain rotary settings (frequency scale 1, extrapolation
0, attention factor 1) in both attention and KV shifts. Carry the optional
four-layer sliding pattern through compact allocation, masks, snapshots/replay,
native estimates and Kluster budgets. Typed norm costs must distinguish columns
from per-head normalization; source/build proof is not model or backend acceptance.


For the OLMo family, do not infer normalization from the model name alone. OLMo
uses unweighted LayerNorm, optional Q/K/V clamping and optional/tied output.
OLMoE uses learned pre-norms, projected-column Q/K RMS norms and unnormalized
selected full-router probabilities with required gated experts/output weights.
OLMo2 has no pre-norms and uses post norms with optional sliding attention.
Preserve these orders in the rank-owned graph; price unweighted norms, clamping
and selected routed work explicitly, classify OLMoE expert storage and reserve
its live scratch. Build/source metadata remains distinct from acceptance.


OpenELM varies head counts and FFN widths by absolute layer. Use each layer's
actual fused-QKV/output dimensions with head-width RMS norms, and keep tied
output on the assigned output buffer. Preserve those dimensions in typed costs
and KV estimates. Reserve wider projection/slice/norm scratch separately from
the embedding-width proxy. Missing key/value width metadata uses the native
first-layer head-width default, not a changing per-layer embedding/head ratio.
Treat registry/admission/build evidence separately from model/backend acceptance.


For bidirectional embedding models, load input embeddings/norms and classifier
weights only on the head, and transformer weights only on their absolute owners.
Preserve full-token attention within each batch; select raw output rows only after
the final attention layer and before its final FFN handoff. Optional projection
and classifier biases must stay optional. Head-only coordinators still initialize
position and pooling inputs; skip only unallocated transformer inputs. Keep
legacy input graph names compatible with the ring executor. Price materialized
F32 attention scores even when the requested FA flag does not change that graph;
retain allocated KV storage in budgets until allocation itself changes. Typed
costs must omit rotary work and a generative vocabulary head for BERT. Scalar
reranking requires the corresponding loaded classifier output; do not mistake
embedding paths for full multi-class classifier or embedding-architecture parity.
Build/source evidence remains separate from model/API/backend acceptance.


Classifier models may declare GGUF string output labels. Derive the class count
from that array, retaining the scalar default for an empty or absent array;
size the classifier output tensor and its bias to the count. Keep classifier
projection and bias optional independently, so a direct classifier projection
works without the intermediate tanh. Make the C API expose the count and labels
and carry the actual class-vector width through pooled rank extraction rather
than truncating it to one float. The rerank HTTP score uses its first element,
matching the pinned baseline, while the native embedding API retains all class
values. Reserve the full pooled class vector in Kluster's managed memory budget.
Do not mark classifier model/API acceptance from metadata, compilation or a
capability string alone.

For reranking, bind `--reranking`/`--rerank` to the head only and reject an
embedding-mode combination or non-rank pooling. Give reranking its own serving
qualification feature; an embedding-qualified or chat-qualified run does not
authorize rerank requests. Route all native rerank aliases through Kluster's
authenticated gateway, resolve the single served model ID and require the
native rerank API capability. Preserve the source document index when native
completion order varies, sort by descending relevance score, apply the bounded
`top_n` count and report evaluated token usage. A successful build or advertised
capability does not establish rerank model, API, backend or distributed parity.
Match the pinned baseline's `texts` request variant: return a TEI score array,
include source text only when `return_text` is true, and require a separate
native capability and request feature for that variant. Keep optional `top_n`
and returned text within request-scoped qualification rather than treating all
rerank calls as identical.

For tokenizer and template API work, compare the pinned llama.cpp request and
response contracts with Prima's handlers before exposing them through Kluster.
Bind `/tokenize`, `/detokenize` and `/apply-template` to the authenticated
gateway and the exact served model. Scope mixed token input, special-token
parsing, returned pieces and template tool/reasoning options separately where
they change behavior. Keep the selected template and defaults in the signed
run context; gateway exposure and native capability metadata are not model/API
acceptance. Follow the current user test deferral before any model-backed or
native contract check.

For `/v1/completions`, preserve the OpenAI text-completion contract separately
from the raw `/completion` and `/completions` endpoints. Bind the served model
through Kluster's authenticated gateway, emit indexed `choices[].text`, finish
reasons, usage and SSE `[DONE]`, and retain bounded multiple-prompt/choice and
logprob request semantics. Put source and request-scoped capability checks at
the gateway; compilation and JSON shape alone do not establish full baseline
API or model-backed streaming parity.

For `/infill`, require model FIM prefix/suffix/middle tokens and validate string
prefix/suffix, optional string prompt, and structured extra context before
task creation. The native task creator still requires a prompt field even when
the API makes it optional, so supply an empty default and keep FIM formatting
in the native tokenizer. Bind the route through Kluster's authenticated gateway
to the exact served model and require a distinct infill capability and request
feature. Keep literal special-token text literal in user-supplied FIM input.
Native compilation and advertised support do not establish FIM model/API
acceptance; follow the user's test deferral and live-cluster gate schedule.

For native `/completion` and `/completions`, preserve their raw response and
stream format separately from `/v1/completions`. Bind each alias through the
authenticated gateway to the exact served model. Require a separate raw
completion capability and request feature, validate prompt/stream/probability
options at the boundary, and convert native malformed requests to clear
errors. Source/build evidence alone does not qualify these API paths.

For `/embedding` and `/embeddings`, retain the native embedding response format
and `content` input behavior rather than converting them to OpenAI
`/v1/embeddings`. Route both aliases through the authenticated gateway with
the exact served-model check and a separate raw-embedding request feature.

For split GGUF and companion assets, collect every declared basename before
preparation, require the first shard as the selected native model, transfer
each signed digest, and verify the complete run-owned
basename layout before native launch. A digest-addressed cache file is not a
usable split-model layout by itself. Refuse missing shards, symlinks and
changed existing layout files and case-folding basename collisions; publish
new names through a temporary file so
an interrupted copy cannot look complete. Keep regression checks written for
these boundaries, but follow the user's current instruction to defer running
unit/native/model tests until the full implementation is finished.

For placement candidate generation, honor required nodes and explicit
priority, enumerate bounded optional-worker subsets with at least one worker
in a network-bound run, and include participant identity when deduplicating
capacity-equivalent plans. A common capacity shape can still have different
network edges and profiling costs. Keep candidate generation separate from
measured selection and do not infer a performance-gate pass from estimates.
