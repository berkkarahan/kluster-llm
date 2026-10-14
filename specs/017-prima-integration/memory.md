# Feature-local context

- Remote main d668eb763d7bbc5b077a456fbc899761d64b7979 was merged as 1195bfe.
- Existing src/kllm/__init__.py whitespace and untracked src/kllm/primacpp binding
  stubs were preserved. New feature code uses modules/primacpp; no import-time library load.
- Current architecture governance uses owning-module patch surfaces, superseding
  historical facade-patch guidance. Do not reintroduce the old agent/services layout.
- Prima source pin: berkkarahan/prima.cpp aeb3afdd44b58c93e70e53d47ed4ac7e29452ff8
  on codex/kluster-integration. Upstream base/provenance remains OpenCPIL/prima.cpp
  288df6a2cc2120387e12d6f40798f30cc525c9ea. Recipe schema 2 / build recipe 7
  applies no local source patches; the native changes are committed in the fork.
- Its layer traversal begins at rank 1, with rank zero last in a window cycle.
  The fork's argument parser accepts zero window entries, verified with device
  enumeration. Core code has further zero-rank assumptions. Contract previews may represent zero-work rank zero but it cannot
  be launched until source changes and real-process validation cover that case.
- Cross-node layer windows differ from per-node GPU tensor proportions. Existing
  Kluster CPU-prefix/master-GPU-suffix layouts are not generally one-cycle windows.
- Upstream CMake omitted profiler/network sources, distributed dependencies and
  a build-info generator. The first patch addresses these and requires concrete binds.
- Kernel profiling output is a Q4K estimate, not measured end-to-end generation speed.
- Vulkan GPU computation is an explicit must-have. The initial profiler exposes
  Metal/CUDA measurements but no Vulkan kernel measurement; missing Vulkan metrics
  must fail profiling rather than label another backend's performance as Vulkan.
- Existing gateways preserve public inference contracts; the old Prima server lacks
  current launch/API features. Do not silently omit those options to obtain a launch.

## Continuation scratch state (2026-10-11)

- Active goal: finish integration; physical distributed cluster gates/validations are
  explicitly deferred by the user and must remain false (test later). Local work is
  not deferred. No root commit/push authorization; native fork publication is authorized.
- Current recipe 9 pin: `e4eb58b46cf2c7f2f320287d51ee26eb09070826`; digest
  `c1c0a610e82376e2216a67f03f9f9ef115bab9b618a642c215513b5ffe1f884f`.
  Clean native checkout: artifacts/prima-windows-initial/source, branch
  codex/kluster-integration. Fresh published managed build: artifacts/prima-windows-softmax.
  Fork CI 38125041981 passed Windows/Vulkan + portable CPU jobs.
- MoE defect was Vulkan only (CPU observation passed). Explicit backend operation tests
  previously skipped Vulkan through newer ggml registry; test-backend-ops now directly
  initializes `-b Vulkan0`. Unmasked softmax failed NMSE ~0.2; masked passed.
  Guarding modulo-zero and conditional mask loads fixes it; SOFT_MAX/GET_ROWS/MUL_MAT_ID/
  DIV/SUM_ROWS operation runs pass. Added 128-expert/top-8 matmul and softmax cases.
- artifacts/prima-softmax-models.json: published dense+MoE sampled chat/structured/forced
  named nonstream tool/stream checks observed; both completed prefill/decode GPU proofs pass.
- Local run changes fence authority/inventory, retain Stop during validation instead of
  clearing cancellation late, pin captured runtime from operation snapshot, and roll back
  an owned child if final persistence fails. Request-time protocol/tools/vision qualification
  is enforced separately. Auto checkpoints/cache RAM resolve zero; explicit requests retained.
  Auto Flash Attention resolves off only for unquantized V; q8 V still needs on and is not
  part of the tested configuration. Normal f16 K/V settings are required for acceptance.
- New tests/prima_local_benchmark.py: five alternating engine pairs, isolated sequential
  process loads, warm-up, short/long prompts, concurrency 1/2, context4096/cache f16/FA off.
  Trace disabled; constrained JSON string with 64 hello words; sampled process RSS, GPU
  memory separately. Dense receipt artifacts/prima-local-dense-benchmark.json fails:
  output bytes/token counts differ, throughput ratios ~0.62/0.32/0.51/0.21 (short/long x1/2).
  Native short generated130 tokens vs baseline71; native long67 vs71, same JSON semantics.
  Keep failure; investigate sampling/grammar/token accounting and native Vulkan decode
  kernels before any performance-policy relaxation. Native TTFT is much better, insufficient.
- Pending broader work: actual isolated full-agent gateway/lifecycle assessment, native
  Responses/Messages + tool/template backports, end-to-end profiler/planner integration,
  exact native arbitrary placement/zero-work control tests and performance optimization.
  Native modern baseline source for comparison is artifacts/prima-baseline-source (v0.5.0).
- In this turn focused tests: 59passed/1skip before defaults, 47passed for defaults/run
  parameter checks, 40passed/1skip request gates + lifecycle + inference overrides. Ruff and
  whitespace pass. Full Python run and MoE paired benchmark may still be running:
  inspect their authoritative process handles and artifacts/prima-continuation-python-tests.log
  and artifacts/prima-local-moe-benchmark.log; never restart only because a wait yielded.

## Latest continuation state

- 2026-10-14 continuation: root integration published as `d30ecdc` on
  `feat/prima-integration` (the entire Prima managed-runtime integration, engine mode,
  Prima dashboard settings UI and its browser test; commit 1 pins recipe 128
  `31c1e22cf1ff330e5454166d0434a892fd54b77a`). Recipe 129 pins the fork's initial
  release `0.0.1` — `source_release`/`source_release_commit`
  `cb0da060105c18cc702b5d1927eb2e60ec2c1e22` (the post-integration `main`; the old
  `codex/kluster-integration` was merged there) — digest
  `8f92b6a3d9b59d8fb46212ec89b688788187e1f1794e62882081d8c8921f4e72`. Contract
  validation passed at ancestor 3107200 (prima-chat-parser-contract-test exit 0 with
  CI-identical flags, incl. JSON-native tool/content regressions). Clean managed
  build: artifacts/prima-initial-release-published129 with verified
  commit/digest/staged executables/111-feature capabilities/RX 9070 XT Vulkan
  identity/6 unpassed gates. Dual-source resolution (pinned commit or pinned
  release tag collapsing to the same identity) is implemented and tested; the
  dashboard prefills the release, offers "Install 0.0.1 and set target" and shows
  the dual identity. An installer exit-code read of 1 through PowerShell pipeline
  captures was a capture artifact; successes verify via the manifest. Commit 2
  (recipe 129 + receipts + docs) and the push are the remaining publication step.
  The dashboard swap precedence: status `engine` (kllm start/join --engine) →
  master's flag wins; worker follows the cluster target engine → active-run config
  → running runtime. Compute panel reports the build's own device probe
  (`GET /v2/node/prima-devices`); selection stays on Hardware gpu_ids + the Deploy
  plan.
- Current pin (pre-129): `f221716d9b45077a906621ca7e10d8b4ada485ed`, recipe 10, digest
  `f177ecbaa7c8ac05c211b2824abe368a7511706ada0945537ddf1bb1b532bb31`.
  Published/clean native checkout remains artifacts/prima-windows-initial/source.
  Fresh managed runtime: artifacts/prima-windows-protocols. CI38127635068 passed all jobs.
- Native examples/server/protocols.hpp + existing scheduler now implement stateless
  text Responses/Messages/input-token count, streams, usage, output limits and forced
  named nonstream tools. Stored/background Responses, vision, automatic/stream tools
  and tool-turn templates still refused. Invalid input returns400. Truncated Messages
  tools yield partial argument text/max_tokens rather than a bogus complete tool action.
- tests/prima_protocol_assessment.py against published binaries: dense/MoE native
  conformance and GPU proofs observed. Baseline failures are separate (no Responses
  sequence numbers, completed rather than incomplete length termination, reserved fields
  accepted). Messages needs common chat_template_kwargs.enable_thinking=false in addition
  to thinking.disabled to suppress baseline thoughts; Responses schema supplies both
  standard text.format and baseline's response_format extension with identical schema.
- MoE benchmark completed (old recipe9). All four workloads fail: throughput ratios
  ~0.80/0.31/0.76/0.27 short/long x concurrency1/2, token counts differ. Do not relax gates.
- Generation profiling added to ProfileJobs via mode=generation + explicit gpu_layers,
  new generation_profile.py (real CPU/full-selected-Vulkan processes, warmup+3 samples,
  readiness/log identity, manifest/proof validation, cancellation tracking). Real dense
  and MoE jobs prepared against recipe10. Counters in artifacts/prima-generation-*.json.
  Manifest writes MUST use json.dumps(model_dump(),sort_keys=True,separators=(',',':'));
  model_dump_json() gives a different native digest (ordering), found/fixed in profiling
  and benchmark writer. Existing supervisor/assessment sorted writers already correct.
- DeviceProfile has total_layers/gpu_layers/cpu_decode_ms. Full model + same ngl can
  score actual decode_ms; partial/distributed layouts return unmeasured. Profiling/start
  reservations added (runs.starting, busy callback); authority failures release reservation.
  Still need normal plan/run cache lookup (T035), actual full-agent/gateway lifecycle,
  native automatic/streamed tools + templates, arbitrary native placement/zero-work,
  remaining API/settings backports and performance fixes. Do not mark goal complete.
- Focused Python69passed/3skip, Ruff/diff pass. Full Python rerun may still be live under
  exec session37370; inspect artifacts/prima-protocol-python-tests.log before resuming.
  Native model/benchmark/profile sessions were terminal except final full Python check.
  No root commit/push. Native fork pushed, Kluster worktree changes remain local.

- Final full Python1260passed/19skip; session37370 terminal0. Generation sessions52897
  terminal0; no live llama-server observed. New cancellation receipt
  artifacts/prima-generation-cancel.json passed: actual Vulkan acknowledgement then
  cancel, statuscancelled, child_stoppedtrue. All current native/build/model/profile
  jobs terminal. Next work is T035 normal planner cache integration and real isolated
  full-agent gateway/lifecycle acceptance, plus native tools/templates/performance work.

## Recipe 11 normal gateway continuation

- Published native `720da841239bc2ffef55b1fe08895562ade30bef`, branch
  codex/kluster-integration, remote agreement verified. Native changes: device memory
  report compatible with shared parser; manifest output_gpu placement (legacy generic
  keep_out_in_cuda flag); synchronized completed batch receipts across all subgraphs,
  including output; C++ manifest contracts. Local CTest2pass. CI38131219709 and
  duplicate38131216967 all jobs passed.
- Root recipe11 digest2cb12d4546138bd34770c7994cada20b2ec30252f71febd61ba885bfce233ca1,
  fresh clean managed artifacts/prima-windows-gateway, runtime_id
  53f0b669d10ae5e434f2bfd137ce5f9c2705fbb119cea84a7f2bdaebfc507269.
- T035 implemented and dense real full-agent measured plan proven. Cache-only scorer
  requires every feasible candidate match runtime/model/hardware/driver/settings/NGL/
  total layers/output_gpu; unmeasured candidates retain capacity-first. Canonical
  generation_settings includes output_gpu and stable hardware strips free_bytes.
  RankAllocation/DeviceProfile/ProfileRequest output_gpu defaults false. Both old
  scorers reject output placement mismatch. Preparation preserves planned output.
- Fixed profile API vs planner cache invisibility: get_profile_jobs creates one
  registry-owned service at root/prima-profiles; PrimaControl attaches prepared/model
  resolver and busy callback. HTTP/preparation/planner now share jobs/cache/reservation.
  Root lifecycle backend matching case-insensitive; inventory ignores volatile free.
  Synthetic output is separate from transformer counts; exact ownership proof required.
- tests/prima_gateway_assessment.py uses isolated temporary KLLM_HOME on D, verified
  copied artifacts in directory with spaces and in-memory candidate policy only.
  --profile starts actual HTTP generation job and proves normal cached plan. Published
  dense receipt prima-gateway-published-dense.json27pass covers chat/protocols/forced
  tools/limits/invalid input, concurrency queue, stream disconnect cancellation,
  exact transformer+output Vulkan proof, stop/restart/crash cleanup/restart.
  Original development dense/MoE receipts21pass. Published MoE first run25pass but
  observer crash barrier failed; retained prima-gateway-published-moe-crash-failure.json.
  Harness now waits for registry+state+rank ownership allclear (previous loop watched
  only registry). Full MoE rerun --profile live session70663; inspect authoritative
  process/log before restarting. Original run1594 terminal1, dense86675terminal0.
- Focused139pass4skip; fullPython1268pass19skip session18059terminal0. Ruff/diff pass.
  Package rebuild compiledwheel but failed replacing loaded Windows .pyd (assessment
  owns it); rerun uv run python scripts/build.py --skip-ui --skip-path-shim after MoE
  child/assessment exits. No native Rust change in this turn. Native fork committed/
  pushed only; root work remains local.
- T035/T036 closed, T037 pending finalMoE result. T032/performance/full API+placement/
  distributed implementation stillopen; physical distributed acceptance explicitly
  deferred. Policy allgatesfalse. Next native performance work must check env telemetry
  semantics: current getenv treats string0 as enabled, despite benchmark runner setting0.
  Prior token segmentation/throughput failures remain evidence, not qualification.

- MoE second assessment remainedfailed: crashmonitor retained registry+pid+rankowner.
  Cause was production service._expire_worker_rpc checking hasattr(_prima) while
  _prima was None, so task died before slow MoE plan initialized PrimaControl. Fixed
  getattr(...,None) guard and async regression covering a second tick after actual
  late initialization. Focused41pass1skip. Retain monitor-failure receipt too.
  New expanded published MoE rerun session98848 live; fullPython session44465 live.
  Earlier session70663terminal1, full18059terminal0. New monitor fix onlyPython.
- Package rebuild retried after secondMoE exited: successful uv run python
  scripts/build.py --skip-ui --skip-path-shim; native_available true, start_prima_rank
  present, recipe11commitcorrect. RootHEAD e8e0e7f9e3e89ffb9173f35903f19695a9fec3cc,
  nativefork720da84 pushed; rootchanges remainuncommitted. Update receipts/T037 if
  finalmodelassessmentpasses; otherwisepreservefailure andfixrootcause.

- Final fullPython1269passed19skip, session44465terminal0 (47.31s); Ruff/scoped
  whitespace pass. ExpandedMoE session98848 remainslive at latestpoll. Only tests/
  purePython/doc edits after packaging; no change to recipe11identity.

- Third MoE full --profile run98848 terminal1: still25pass thencrashcleanup failure,
  registry+STATEpid+rankowner retained. Initializationguard fixesrealNone dereference,
  but isnotsolecause. Retain prima-gateway-published-moe-expiry-guard-failure.json.
  Added worker_expiry_task done/cancelled/exception/stack diagnostics and runs cancel/
  manifest status to crash-failure envelope. New no--profile diagnostic assessment
  session53949 live at handoff: artifacts/prima-gateway-moe-monitor-diagnostic.log/json.
  It still runs same gateway/protocol/concurrency/cancellation/GPU/stop/restart/crash
  checks, skipping alreadyvalidated profile stage to focus nextaction. Inspect its
  authoritative handle before restarting; nextreadfailuremonitor.task info ifterminal.
  Goalremainsactive, noqualificationgatesenabled. All docs nowexplicitMoEcrashfailure.

## Cleanup retry and recipe12

- Diagnostic session53949terminal1. Receipt monitor state: expiry tasklive at sleep,
  run_cancelledtrue while manifest/readyrankowner retained. Fixed LocalRuns.monitor_once
  to reconcile retained ownership despite cancel flag; failed stop keeps manifest/
  ownership, publishes apply_error and retries nexttick. Regression failedstop->success
  verifies no lost ownership/registry and later clearedstate. Focused42pass1skip;
  fullPython1270pass19skip (session99755terminal0). Ruff/scopedwhitespacepass.
- Published recipe11 focused MoE lifecycle retry session60587terminal0:25checkspass.
  Final --profile session62363terminal0:27checkspass including measuredplan cost
 20.253375ms, partialCPU/Vulkanlayers33of48 andoutputGPU proof, crashcleanup/restart.
  Bothmodels now27pass on720da84, T037closed. Preserve earlierfailingreceipts ashistory.
- Native telemetry getenv0bug fixed bycommon/prima-env.h helper explicit1-only optin;
  zero/unset/false casescontracttested. NativeCTest2pass. Published andremotematched
  5e3e2e13a4b0779d22b418d867088f86d8a3f8f1. CI38132997745success all4jobs; duplicate
  38133001176wasinprogress latestpoll. Recipe12digest
  cba2d9f71c54c3234d9f653f7b71381fc2ca89b87bd8b82a85225248cc5abbc9.
  Fresh clean artifacts/prima-windows-performance managedbuild4069terminal0.
  Supportedpackagebuild/prima-performance-package-build.log succeeds afterbuild,
  no livecluster modifications. Fresh dense native protocol receipt
  prima-performance-protocol-dense.jsonobserved, withGPUprefill/decodeproof.
- tests/prima_local_benchmark.py nowrefusesGPUexecutionreceipts intiminglogs and
  explicitlysetsallsamplers greedy/no penalties/no top-k/p/min-p cutoff onbothengines.
  Same outputbyte/token/TTFT/throughput/memory criteria; no gate relaxed. Five alternating
  dense pairs onrecipe12 LIVE session7735; outputprima-performance-dense-benchmark.json/
  log. Do notstart competing GPU/profile/tests/builds duringtiming. Captureterminal
  acceptance beforeMoE12protocol/perf orfurthernativechanges. No MoE12 modelresult yet.
  T038pendingfresh telemetry/performance checks, T032/fullerAPI/placement workopen.
  Policyallfalse; distributedphysicalvalidationdeferred asrequested. Rootuncommitted.

- Recipe12 runtimeID5fe63cd08d2d63c43a7de2dc4dcb1efeac9335245ba81a511f3d6bd96a6bd8ef.
  NativeCI both38132997745/38133001176success. Dense/MoE fresh sampledprotocols+GPU
  receipts observed. Packagebuildsucceeded beforephysicaltiming. Focused141pass4skip.
- Densecorrectedbenchmark7735terminal1:5pairs, noGPUreceipts intemporarystaginglogs,
  strictsamebytes/token criterionstillfails. Ratios~.76/.38/.57/.25 (short/long x1/2),
  baseline71generatedtokens vsnative130short67long; concurrentnative short67or130.
  SAMPLING explicitlygreedy/alltopfiltersdisabled/no penalties. Allgatesstillfalse.
- MoE12protocol86503terminal0. MoE12pairedbenchmark LIVE session45475, logs
  artifacts/prima-performance-moe-benchmark.log/json. No competing profiling/tests/
  native compilation whiletiming; currentnativecheckout5e3e2e1clean (no fusionpatch).
  Futurepairedreceipts include runtime_id/recipe_digest/source_commit/server_sha256/
  baseline identity at top; currentrunningreceipt predates that cosmeticprovenance
  enhancement, but workload identities/captureverified source12 remain valid.
- Next performanceinvestigation: local build_llama/build_qwen2 creates3graphs even
  n_world1, copying embeddings viaCPU betweeninput/transformer/output indecode loop.
  map_layer_to_subgf_id givestransformer scheduler1; localfusion wouldneedmap0,
  retaininputtensorthroughalllayers, connectoutputdirectly, and permitinput+output
  activation ontheonegraph. No implementationyet. Compareidentical renderedprompt/
  inputtokens acrossengines (possiblyraw /completion) toseparatetemplate/sampling from
  numerical/backend/graph differences. Do not relax deterministic/performance gates.

- MoE12benchmark45475terminal1,5pairs. Throughputratios~.83/.32/.80/.36, bytesmatch,
  generatedbaseline134short71long versusnative130(allshort)/130or68longx2. Tracing0
  actualverified bylogguard; gatefailuresretained. T038closed(executed acceptance,
  notpassedperformance); T039open(chatgrammar/token+Vulkandecodeperformance).
- Newtests/prima_token_assessment.py realCPUVulkan input-ID diagnostic. Allsessions
  terminal:35625initialraw,41264fullresponseformatrender,21243raw+chat; allobserved.
  Receiptsprima-raw-{tokens,full-request,chat}-dense.json. Baseline-renderedfullrequest
  Qwen3shortprompt22tokens includes<think>\n\n</think>\n\n; long1025tokens. Feedexact
  arrayssamebothengines. Vulkanrawboth130short67long; CPUrawboth67/67; canonicaloutput
 66tokens andexactbytesallmatch. Baselinechat(always71usage+timings.predicted_n)differs
  fromrawgeneration evenCPU, nativechatcounts matchraw. No modeltokenizerdefect proven.
  Baseline common/chat.cpp Jinja path usesPEGgrammar/parser; oldnativejsonschema direct
  grammar doesnotinclude thatchatformat. Nextnativeworkshouldportchattemplate/grammar/
  parser handling(auto/streamtools/toolturns neededT013 too) andcomparetruegenerated
  tokens, neverfudgeusage. Directnative Vulkandecode6.1/7.2ms vsbaseline3.9/4.5ms,
  CPU15.5/18.3 vs16.8/18.0 roughlycomparable. Diagnosticsnotreleasebenchmark.
- No activejobs latest21243terminal0, no modelchildren. All nativechangespublished5e,
  checkoutclean. RootPython/doc/test editsuncommitted. Currentrecipe12identity unchanged.
  Supportedpackagebuildpassed beforelatestpurePy monitor/testdoc changes. Full1270
  pass19skip priorrepin; focused141pass4skip after12. Ruff andscopedwhitespacepass.

- Recipe13 connected local input/transformer/output graph, published dbb8de4; fresh
  build and all CI jobs pass. Development dense/MoE GPU receipts report one graph
  per phase. Recipe13 dense --profile failed cancellation observation; no-profile
  retry passed25 checks. Preserve failure receipt, do not claim27 passed on13.
- Recipe14 pins published501585d9116457f998d5372341c49b1ad093e6c3, optional JSON
  framing grammar/parser with bytewise streaming contracts. NativeCTest2passed;
  development dense/MoE protocols pass. Dense strict five-pair development benchmark
  bytes/counts match71tokens; throughput .896/.385/.835/.287 fails. No gate relaxed.
- Fresh recipe14 managedbuild5298 handle nowmissing; completed manifest exists with
  digest5a40f12f8bf171de4ceceb3cc7c1eb67e50354b8602fa1195c7da11cd293ddd9,
  runtimeID39e115c4c43b0f4731f7f4d2e5c32eaa79ff238ad4a882f3229e6424c686b477.
  CI38137945120/38137941508 allsuccess. Packagebuild/native_available true passes.
  Published dense --profile gateway83061terminal0,27checks pass. MoE equivalent
  live87556. Development small-batch vector extension build44066live; shader maps
  column and batch separately, only contiguous multicolumn inputs <=8 enter newpath;
  tests include N2/4/8/9, F32/F16/Q8/Q4K and two-dimension broadcasting. Notpublished.
- Ignored nativecheckout .venv was created by task UV command; deletion auto-review
  rejected (blocked by policy), left in place. Task-created uv.lock removed via patch.
  Root edits uncommitted; all qualification gates false; distributed physical testslater.

- Published14 MoE gateway87556terminal0,27passed includingprofile/cache/crash/cancel.
  Published14 dense/MoE nativeprotocols terminal0; nativepassed, baseline deviations
  separate. Smallbatch vector tests initiallyfailed existingK64 dotproduct onRX64lane
  subgroup (rowbounds). Fixed all-lane barriers/boundedpairloads, addedoddKtailtests.
  Newstridedfallbacktests exposed F32weightscopytof16 whileselectingF32matrixpipeline;
  fixedpipelinechoice toconvertedtype. TransposedA testinvalidggmlinvariant; corrected
  testtotransposedB. Build57876live; all code remainsdevelopmental, unqualified.

- Finalsmallbatchbuild57876terminal0. StridedF32matrixpipelinefixpasses. Both
  transposeA/B cannotserveCPUreference inoldggml (assertions), removedinvalidtests;
  paddedA/B/both viewcases retained. Operationrunner1796total includesfiltered/skips;
  actual162MUL_MAT and185MUL_MAT_ID supported GPU-to-CPU checks pass. CTest2pass.
  Dense/MoE developmentprotocols pass,9243terminal0. Dense5pair55277terminal1:
  strictbytes/countsmatch, short1 .91/short2 .93/long1 .84/long2 .53 throughputfails.
  Prior14 short2 was.385, long1 .835; avoidswapping workloadlabels.
- Published remoteverified19adfe0800130e829544719136550257b21fd9f6, recipe15digest
  78a2744b0b168292959255e601ceba45f936ef3c785bb458c440a71b4df0afe5.
  Freshmanagedbuild80825live artifacts/prima-windows-small-batch,
  artifacts/prima-small-batch-managed-build.log. CI38139215714/38139213392live.
  FocusedPython141passed4skipped. Package15notyetbuilt; rebuildbeforenative-assessments
  toavoidWindowsDLLlock. Rootuncommitted; nativeclean; allgatesfalse, distributedtestslater.

- Recipe15 freshbuild80825terminal0, sourceclean, capture_runtime verifies
  runtimeID006610d5a20c575131486f2fbe8f119320b3244b2e22cbb32678b2cc4a7fcde9.
  Package/native import true pass. CI38139215714/38139213392 allsuccess.
  Densepublishedgateway11098terminal1: cancelobserver slotidledecoded9has_nexttrue.
  Retaining response.iter_lines() iterator throughobservation fixesharness premature
  disconnect (httpcorePoolByteStream catchesGeneratorExit thenclose). Dense --profile
  retry60548terminal0 passes27. Failurecapturer nowcopiesownedranklogbeforcleanup.
- Native developmental stridedvector extension allowsfloat views already requiring
  contiguouscopy, keepsouter-onlypaddingfallback. Build38667terminal0; fullMULMAT
  checkpasses162 supported cases. Densefivepair LIVE90766 receipt
  artifacts/prima-strided-vector-dense-benchmark.json. No competingGPU/tests/builds.
  Notpublished; native2filesmodified on19adfe, recipe15 remainscurrent.

- MoEpublished15gateway1064terminal0,27passed. Both published15 gateway/profile
  suites pass now; currentpolicyallfalse. Stridedcandidate90766terminal1,bytes/counts
  match, ratios short1.90 short2.97 long1.86 long2.75; onlyshort2passed overallfails.
  Restored vectorloop [[unroll]] hint (prior sourcehadit), rebuilt14046terminal0,
  bothmatrix/expertchecks pass. Native3filesmodified on19adfe (CPP/test/shader).
  Finalcandidate densefivepair LIVE58568, output/log
  artifacts/prima-strided-vector-final-dense-benchmark.json/log. No competingGPU,
  builds orCPUtests whiletiming. NextMoEpairedcandidate, thenpublish/repinifvalid.

- Finaldense58568terminal1; bytes/countsmatch, ratios short1.92 short2.98 long1.83
  long2.76. Onlyshort2passes, overallgatefails. FinalcandidateMoEpaired LIVE29812,
  artifacts/prima-strided-vector-final-moe-benchmark.json/log, ngl24outputCPU matched
  baseline; repeated5pairs. Do notcompile/test/profile competing CPU/GPU whiletiming.
- IndependentCodeGraph review remoteT016: LocalPrimaRuns refusesnetwork_id and
  len(ranks)!=1; PrimaCoordinator is notcomposed inPrimaControl. Signed prima_start
  startsRankSupervisor but no automaticworker readiness transition; prima_status
  returnsrank.status and expire onlyleasecleanup/localmonitor. Native common.cpp1924
  emitsCONFIGURED aftertensors/contextbackendsetup beforelaterwarmup/controlvectors.
  Globaldistributedready mustincludemasterHTTPhealth afterworkerparticipation, not
  configuration receipts alone. Wirecoordinator/admission/commands/readyloop into
  authoritativeproductiondispatch, bind workerowner tosender/authority/session/run,
  retainpersistentcleanup/fencing andprivateedgeleases. Do notsimplyenablepolicy.
- Potential follow-upVulkanlayout issue: ggml_vk_dim01_contiguous ignoresnb[2]
  padding whiledispatch usesne00*ne01 batchstride. Outer-onlypadded floatviews currently
  stayoldmatrixpath, which needs explicit correctness cases before claiming support.
  Add N1/2/8/9 paddedouterA/B cases; considercopy predicate/pipeline handling and
  explicitquantview support/refusal. Not yet tested; currentqualifiedscope remainsclosed.

- Added outer-batch padding regression inputs (views4/5/6) to native test_mul_mat:
  N1/2/8/9, F32/F16 weights, bothpadding sources, bs2,3/nr2,1. Not built orrunyet
  becausepairedMoE29812 stilllive. CPP stillusesold dim01 predicate lackingnb2;
  reproduce withthese tests aftertimingbeforefixing. Candidatebinary/hash currently
  beingtimed predates onlythese newtests; CPP/viewoptimization+unroll isas58568.
  Nextsafeaction wait29812terminal, capture strictresult, build/runnewtests torecord
  originalfailures, fixpackedbatchstride checks andspecializedvec dim3limits asneeded,
  rerunaffectedmatrix/modelchecks, publishnative changesandrepinfreshsource.

- MoEpaired29812terminal1. Fivepairs ratios short1.85 short2.52 long1.86 long2.55;
  onlylong2validationerror: finalPrima pair tokens134,71 vsbaseline71,71 (allotherlong
  pairs71);bytesmatch. Receiptpredatesbatch-layoutfix; allperformancegatesclosed.
- Addedview7 attention-permutation cases (CPUvalid unlikeoldtransposedtests). Native
  before-ops reproduces25 failures (1803/1828 totals includesfiltered/skips). Fixed
  packedcheck=>ggml_is_contiguous(allactive dims), specializedvec guards ne3=1/F32B/
  actualsupportedstrides, floatviewgeneralcopy, unsupportedquantviews gracefulfalse.
  After194supportedMUL_MAT/185MUL_MAT_ID comparisons pass; CTest2pass. Bothdevelopment
  protocolspass21964terminal0. Published/remoteverifieda024a7edc21d50c558f608878b7bb085bfde8aae.
- Rootrank_arguments nowtakesrank default0, onlyHTTPaliasomittedforworkers; spawn
  passesrank.rank. CLI15realhelptranslationpar2/f16passes, fixture43pass1skip, focused
  142pass4skip, Ruff/whitespacepass. Native sourceclean afterpublication; rootuncommitted.
- Recipe16digestbf3ac467c3a55d83429e8205830ac029be2fccb3c502542bf87dd80099a8b7d7.
  Freshmanagedbuild LIVE93999 artifacts/prima-windows-batch-layout,
  artifacts/prima-batch-layout-managed-build.log. FullPython LIVE30970 output
  artifacts/prima-batch-layout-full-python-tests.log. CI38141237726/38141235084pending.
  No model/benchjobs live; package16notyetrebuilt. Rebuildpackagebeforenativechecks.
  Nextcaptureclean16/source/artifact, publishedgatewaybothmodels +strictpairedtiming,
  continueperformance/fullAPI/placement/remoteimplementation; physicalcluster testslater.

- Recipe16managed93999terminal0 sourceclean. RuntimeID
  e4f51bf577e1d0e42163a339c8d84c28c200fc2c4d13221d227df85f6b9392b7 capture/importtrue.
  CI38141237726/38141235084 allsuccess. Initialpackagefailed WindowsDLLlocked.
- FullPython30970terminal1:1failed1270passed19skipped, SQLite multiprocessingfence
  childstart timeout10sec whilecompilationactive. PID34112 spawn(parent25068) orphan,
  psutil.open_files exact pytest-991/popen-gw3/test_two_process_network_mutat0 DBfiles
  provesourtestownership; nativeDLLloaded. Revalidatedparent/argv/files, terminated
  only34112. OtherPython25608/41316 unrelated untouched (no nativeDLL loaded).
  Package16retrysucceeds. UnchangedisolatedSQLitetest1passed2.61s. NoSQLitecode/timeout
  edits. FullPythonquietretry LIVE21311 artifacts/prima-batch-layout-full-python-retry.log.
  Nextwaitterminal, thenpublished16gatewaydense/MoE and strictpairedmatrix sequential.

- QuietfullPython21311terminal0:1271passed19skipped44.45s. Published16 dense
  --profile gateway started (toolhandle fromlatestexec), receipt
  artifacts/prima-batch-layout-published-gateway-dense.json/log. Package16already
  rebuilt/importverified beforeassessment, noDLLreplacementwhilemodelharnessrunning.

- Densepublished16gateway7353terminal0,27passed. MoE equivalent LIVE11993,
  receipt artifacts/prima-batch-layout-published-gateway-moe.json/log. Nextwaitthat
  exacthandleterminal, capture result, thenfreshpublished16dense paired (ngl28,
  outputGPU) andMoEpaired (ngl24outputCPU). Noothernativechangespending; sourcea024clean.

- Published16MoEgateway11993terminal0,27checksallpassed. Both published16--profile
  gateway suites pass. Freshpublished16 dense5-pair startedlatesttoolcall receipt
  artifacts/prima-batch-layout-published-dense-benchmark.json/log. KeepCPU/GPUquiet.
  Afterterminalcollectstrictresult thenfresh16MoEbenchmarkngl24outputCPU. FullPython
  already1271passed19skipped; no needrepeatunlessnewedits/failures. Nativea024clean,
  rootuncommitted. Qualificationallfalse, distributedphysicalvalidationslater.

- Current livebenchmark handle99185 (fresh published16dense), confirmedrunning via
  tool; no development-server override. Nextpoll99185 andartifactlog, do notrestart
  onobservationtimeout. Allmodel/gateway/test/buildjobs otherthan99185 areterminal.

- Published16 densepaired99185terminal1, bytes/countsmatch, throughput short1.93
  short2.98 long1.85 long2.73; onlyshort2passed overallfails. Published16MoEpaired
  LIVE61838, output/log artifacts/prima-batch-layout-published-moe-benchmark.json/log,
  ngl24outputCPU; do notcompile/test/profile whiletiming. Existing16build/CI/package
  andboth27casegateway suites pass; fullPython1271pass19skip; allgatesfalse.
- Native development on a024 adds4-row-per-workgroup generic GEMV to reuseBloads,
  K-quant/expertkernels keepone row. Hostworkgroupceil helpercoverspartialrowgroups;
  K-shaders haveuniformrowbounds guard. Addedsparse last-row-only tests F32/F16/Q8
  m262145,K32 andQ4K m65537,K256, solegacygrid truncation producesNMSE1 insteadof
  hidingonewrongrow inwholematrixmean. Notbuiltor tested yet. Source8filesmodified.
- AfterMoE61838terminal: run uv run python artifacts/prima_row_tail_regression.py.
  This taskhelper preserves7editedbackendfiles underartifacts/prima-row-tail-source-backup,
  swapsbackendtemporarilytoHEADa024 whilekeepingnewtests, builds/runsoldops then
  restoresallsavedsource(infinally, withchangecheck). Expectedfailureexit1 and
  artifacts/prima-row-tail-before-{build,ops}.log evidence. Do notinterrupt helper.
  Thenbuildcandidate llama-server/test-backend-ops, rerunMULMAT/ID/CTest +models,
  strictpaired candidate timing beforepublication/repinning. No policyrelaxation.

- Published16MoE61838terminal1: ratios short1.88 short2.56 long1.85 long2.53;
  long2generatedcounts stilldiffer (pairs0,2,4 containonePrima134 vsbaseline71 each;
  bytesmatch). Preserve failure. Four-row WG candidate58341terminal1, bytes/counts
  match butdenseperf REGRESSES .72/.88/.65/.71. Removed4-row workgroup path.
- Before-regression helper73154terminal0: originala024 passesallfourtail cases;
  source restoredcorrectly. vulkaninfoSDK.exe showsRX9070XT andiGPUmaxWGX4294967295,
  sooldsplitbranchisnotreached bythose sizes. Do notclaim reproducedhardwaretruncation.
  Addedpureggml/src/ggml-vulkan-dispatch.h grid function andCPUcontractcoverage for
  smaller validmaxX64/65535, coverage/limits andinvalidoversized shapes. Hardware
  tailcases givecurrentdevicecorrectness only. Portablebounded-grid fix +K rowguards
  retained; WG allone row again.
- Newquant-only4-elements/iteration genericshader (floatrowsremain2) candidatebuilt
  96289terminal0. 198supportedMULMAT/185MULMATID comparisons pass; CTest2pass includes
  gridcontracts. Denseprotocol passes. Dense5-pair LIVE80833 output/log
  artifacts/prima-quant-vec4-dense-benchmark.json/log. Do notCPU/GPUtest/build whiletiming.
  Native9trackedfiles+newdispatchheader modified ona024; no published17yet. Noother
  jobs live. Nextcapture80833 result; retainquantlooponlyifmeasureduseful, thenMoE
  protocolchecks, publishappropriatechangesandrepinfreshrecipe17/build/validation.

- Quant4candidate80833terminal1: counts/bytesmatch, throughput short1.95 short2~1.00
  long1.88 long2.75; bothshortpass overallfails. MoEprotocol70710terminal0 nativepassed.
  EffectiveVSggml.vcxproj ReleaseEnableEnhancedInstructionSet=AdvancedVectorExtensions512;
  cacheGGML_AVX*OFF misleadingnoncacheFindSIMDoverrides. CPU7800X3D, noflagchangesneeded.
- Published/remoteverified622d5c4f9fdc1fa399c3f20e5632a67a2b63fdca, nativeclean.
  Recipe17digest91d3a24c5c984cc74c6a93054cac75ff07561e37b7f688d06aa44e7b15bf2e17.
  Freshmanaged LIVE73560 artifacts/prima-windows-quant-vec4 +
  artifacts/prima-quant-vec4-managed-build.log. FocusedPy terminal0 log
  artifacts/prima-quant-vec4-python-tests.log (readcount). CI38145088445/38145085072pending.
  Afterbuild/CI verifycapture/source/artifacts, rebuildpackagebeforenativeassessments,
  finalpublishedchecks/modelperformance anddiagnoseMoE2 tokenvariance. Nootherjobs live.
  Rootuncommitted, allqualificationfalse, physicaldistributed testsdeferred.

- Fresh17managed73560terminal0, sourceclean, package/import/capturepass. RuntimeID
  10d6d390550578f1c4bfd68a9e2d040cdb09989fbf86a9dbc729b0329cde4031. CI38145088445/
  38145085072bothsuccess. Focused142passed4skipped; full1271/19stilllastappsuite.
- Extendedtests/prima_token_assessment.py concurrency1/2,repetitionspositive,cpu-only;
  ThreadPool raw+chatrequestwaves; receiptsperrequest/repetition, actualtokens/native
  body/logprobs top3 optional. Longpromptnowexactbenchmark'Context for this test:'
  (olderdiagnosticsdifferentprompt remainhistorical). CPU/vkproof invariants retained.
  Ruff/helpCLIpass. DenseCPU2single-round validation startedlatesttoolcall, receipt
  artifacts/prima-concurrent-cpu-dense.json/log. AfterpassrunMoE CPU2repeat3 toseparate
  CPU/templatevsGPU numeric variance; thenVulkan2sameinputs ifneeded. Diagnosticsnotgates.

- DenseCPU2 smoke87242terminal0 observed allsemantics/CPUproofpass; baseline raw67
  bothprompts, Prima raw130short/67long, chatboth71. Receiptprima-concurrent-cpu-dense.
  MoECPU2diag LIVE39635, repeat3 chatlogprobstop3 singlegraphproof, output/log
  artifacts/prima-concurrent-cpu-moe.json/log. Nootherjobs live, no realbenchmarklive.
  Afterterminalcompareperrequest counts/tokenpieces CPUbaseline/native; runVulkan2
  sameinputs tolocalizevariation. NewscriptRuff/help passed andrealCPU2harnessworks.
  Source17clean/published, rootuncommitted. Package17alreadybuiltbeforediagnostic;
  don'treplacepyd while39635 holdsit. Qualificationallfalse, distributedphysicaltestslater.

- Recipe18 published/remoteverified83555d7b5a65a2dd84d3e49f27c237cabb98bbfd,
  digest3557aa2522b2dfc51d71514c7b479a3a3a6b7c5749cea888c5e31dd9fe074e90.
  Raw-logit OpenAI choice/stream tokenlogprobs, strict0..20 top validation, selected
  token even0 alternatives, exactbytes/validUTF8 text, no sampler modification.
  Native2contracts pass; previous nullmodel parser fixture corrected to independent
  validation helper. Dense before8casesfail; dense/MoE devafter pass8newcases, and
  both dev gateway --profile --logprobs35pass. Baseline deviations remain separate.
- Managed18 43770terminal0 atartifacts/prima-windows-logprobs, sourceclean/package/
  nativeimport/capturepass. RuntimeID1b106e3318a3d7dc377ec28e18fd293e45c5f22b91ff4032f64464c7d161703c.
  Serverfd781a99744ce053cb376736777a976c39d6945720b26c09b2ae93dd7927ca4c.
  CI38148640088/38148637306bothsuccess; focused207passed4skipped.
  Published gateways dense48270/MoE41962terminal0 each35pass; receipts
  prima-logprob-published-gateway-{dense,moe}.json/log. Qualifiedscope stillfalse.
- Root signed rank ownership now persists sender/authoritydigest; remote start/
  status/renew/stop check exactowner+run. Wirestart requires current complete signed
  preparedbundle sameauthority/runtime; cached bytes alone refused. Scoped layout
  comes fromapprovedjob; newprep refusesownedrank. Realchild ownerbeforefailafterpass,
  realWebSocket cachedmanifestbeforelaunchafterrefuse/approve/start/status/stop passes.
  Additional sixbadpreparedrecords +busyrank refusal pass; nativeGPU notfixturetested.
  Full1271passed19skipped44.35s; extrafocused2pass; Ruffpass. T044done boundary only;
  distributednative readiness/coordinator/admission/remoteplanner stillunimplemented.
- Recipe17 CPU2 MoE39635terminal0 varieslongchat71/72/134 andraw130/68 across3rounds.
  Standalone spaces0/1/63 explainchatcounts; byteoutputsame. CPUdifference proven,
  causeunresolved. Newpublished18 --cpu-only --concurrency2 --repetitions3
  --chat-logprobs --expect-single-graph LIVE65200, output/log
  artifacts/prima-logprob-concurrent-cpu-moe.json/log. No build/benchmarklive.
  Package18alreadybuilt; don'treplaceDLL while65200loads it. Rootuncommitted;
  physicaldistributedvalidationlater, allgatesfalse. Next inspectactualtokenmargins
  after65200terminal and continue native/readiness/API/performance implementation.

- Follow-up signed preparation query fence: status/cancel now require original
  operation/sender/authority, matching assetpush. RealWebSocketwrongoperationstatus
  beforefails (returnedprepared), afterstatus/cancelrefuse. Focused17048terminal0
  142passed4skipped11.55s/Ruffpass. Full1271/19abovepredatesqueryfence. BaselineCPU
  half65200complete; nativeCPUhalfprocessinglongprompts, livehandleconfirmed.

- 65200terminal0, receiptprima-logprob-concurrent-cpu-moe.json statusobserved.
  Baselinechat134short/71longallrounds. Native134short, long3rounds134/71,71/72,71/72;
  eachlogprobentrycountmatchesactualusage. Rawlongnative130/68, baseline130stable.
  Initialnativefence/quote logprobs varybeforedifferenttokenchoice, despiteidentical
  requests: fence-12.259/-9.343/-10.639/-10.527. Nextisolatebatch/sequence numerical
  statealongsidegrammar, usingcurrent18exactinputs andactualtokenpieces. No rootcause
  claimed. Diagnosticnotperfqualification. Alljobsdone, rootuncommitted/nativeclean.
- Finalqueryfence malformedunknown/nonstringjob andmanifestdigest errors stayprotocol
  failures; signedWebSocketcontinueswithapprovedstart/stop. Assetpushusescommon
  ownershiphelper. Final142passed4skipped7.14s/Ruff/whitespacepass, log
  artifacts/prima-preparation-owner-final.log. Full1271/19 predatesqueryfenceonly.
  Readinessreview: configured marker common.cpp1924 precedes controlvector/LoRA/
  warmup; CLI threadpools and worker receive loop initialize later. Do not treatthat
  marker as worker-ready. HTTPhealth stillprovidescurrentlocalserverbarrier. Native
  post-init worker marker and productioncoordinator composition remainrequired.
  Single-rank sendsocketsnull, so mismatchedlegacyremoteKVseq offsets do not explain
  currentCPU-local variation; inspectactualremote batchsequence mappingbeforefix.

- NativeF32 precisionfixpublished146ccb624ad86ed7bdcd84e0bbeedd4b20b1b80e,
  remoteverified/sourceclean. Recipe19digestfa9bd217ff771acd3db395effe645c3e9f34d24e772a8b69f0a588d1520f92d0.
  F32weights/default andexplicitF32requests useF32sharedtiles/viewcopies; KQattention
  alwaysrequestsF32likebaseline. Resourcehelper choosesgeometrywithinactualdevice
  sharedbudget; CPUcontracts16/32/64KiB+invalidpass. Nativefinalbuild66924terminal0.
  Beforeprima-f32-range-before.log15inffailures; final48161terminal0 actual237supported
  matrixcasesincluding39range,185experts,CTest2pass. Earlierbadfixturewroteviews and
  F16CPU1colquery70000overflows; correctedreferencebeforevalid15failreceipt. Initial
  targetprima-contractwrong, correctedprima-contract-test passed. Generic1871counts
  includefiltered/unsupported, don'tclaim1871GPUcomparisons.
- Dense/MoEdevnativeprotocols+logprobs passed; MoE48GPUlayers proofvalid butnotpaired
  placement. Dense5pairs7012terminal1 byte/countsmatch; ratios1.124/1.048/.917/.833,
  overallfailslong. Noqualifiedpolicyenabled. Densebenchmarkpredatesresourcehelper;
  actual9070floatgeometriesunchanged, quantF32large changedbutnotmodelgraph used.
- Newprefix_tokensoptional2..256 inprima_token_assessment, defaultcompleteunchanged.
  81958terminal0 prima-prefix-cpu2-moe.json24requests. Bothenginesinitiallogpvaries,
  baseline~0.4 onlong/native~3.4; don'tcalldistributionvariationaloneaCPUstatebug.
  ActualnativeCPU71/72/134completecountsremainunresolved. Prefixmodeexplicitdiagnostic.
- MoEdevelopmentpaired LIVE19481 withngl24/outputCPU, matchingprioracceptedmatrix
  settings; output/logartifacts/prima-f32-moe-benchmark.json/log. Don'tbuild/test CPU/
  GPU whiletiming. Source19published, rootrecipe19repinnedAFTERbenchmarkcaptured18;
  receiptretainsoldreference+devbinaryhash, notpublished19qualification. CI38152704830/
  38152701173pending. Managed19NOTstarted; after19481terminal startfresh
  artifacts/prima-windows-f32 withexactSHA/digest/backendvulkan, verifyCI/capture/
  cleanartifacts, packageBEFOREnewharnessloadsPyd, freshpublishedgateway35bothmodels
  andaffectedstrictpairedacceptance. Rootuncommitted; allqualfalse; distributedtests later.

- MoE development paired 19481 terminal1: output bytes/actual generated counts match
  all workloads, including long2 every pair71/71. Ratios .834/.537/.837/.536 still
  fail throughput. Recipe19 source CPU arithmetic unchanged; don't claim CPU-only
  issue fixed. Receipt prima-f32-moe-benchmark.json remains development evidence.
- Fresh19 managed23845 terminal0, supportedpackage/import/capture/sourceclean pass.
  RuntimeIDfa045bc61c3156e4ed100d444a64e70e3d5827dd98ef77a84aa1501a00cf752b.
  Serverc1ff49c14d52dc508a21c14bf95459e017094a1c94ca32a19e0c619d18c9c1f9.
  CI38152704830/38152701173bothsuccess; focused142pass4skip7.65s/Ruffpass.
  Freshpublisheddensegateway34363terminal0 35pass. MoEpublished--profile--logprobs
  suite startedlatesttoolcall; receiptprima-f32-published-gateway-moe.json/log.
  No build/benchmark live. Keep pyd untouched while gateway holdsit. AfterMoEdone
  readactual35case outcomes, updateallrelateddocs, and run matched published19
  pairedacceptance before localqualification. Rootuncommitted/allgatesfalse.

- MoE published19 gateway72683 terminal0 35passed, receipt
  prima-f32-published-gateway-moe.json/log. Both freshpublished19 gateways35pass.
  Published19 dense5pairbenchmark started latesttoolcall with ngl28/outputGPU true;
  output/log prima-f32-published-dense-benchmark.json/log. Do not build or run CPU/GPU
  jobs during timing. After dense terminal run matching MoE ngl24/outputCPU five pairs.
  Source19 clean/published; root uncommitted; allqualificationfalse. No otherjobs live.

- Published19 dense paired38007 terminal1: bytes/counts match, ratios
  .969/1.006/.890/.773, only short workloads pass. Published19 MoE paired22957
  terminal1: bytes/counts match all workloads, ratios .817/.549/.836/.538; all fail.
  Long2 native71/71 every pair. Receipts prima-f32-published-{dense,moe}-benchmark.json/log.
  Both fresh19 gateways35passed, CI/build/package/capture/sourceclean already passed.
  All sessions terminal, no owned llama child remains. Root uncommitted, native146
  clean/published, all gates false; distributed physical tests later. Goal remains active.
- Next performance investigation: specialized Vulkan strided attention vector kernels
  accept only one activation column; concurrency2 falls back to input copies and
  independent vector dispatches. Inspect mul_mat_vec_{nc,p021}.comp plus corresponding
  cpp selectors/push constants, extend exact small-column handling with real matrix/
  CPU-reference regressions if profiling supports it, then paired dense timing.
  CPU MoE MUL_MAT_ID and baseline Q4_K dots both currently use nrc1, so don't claim
  a missing dual-column routine as established cause. Compare CPU repack/dispatch
  and model-level costs before selecting that optimization. Worker post-initialization
  readiness/coordinator/admission and remaining API/model/settings compatibility
  also remain required; do not redefine completion around this precision milestone.

- Nativeattention smallcolumn path implementedandpublished857833e2102e8cf1402afe3442cf1ef616dadc41.
  Sourceclean, recipe20digest7f1d59d770b3b04dec598514e04bccedca77763bc8225bb41fe14840ba1e7b22.
  Pairedqueries shareK/Vreads, explicitstrides/descriptoroffsets, ncols1..8 (odd
  supported), rowsXaxis/device-limits, retain4Dgeneral. Native5filescommit.
  Beforeattention24pass (offsetomissionnotreproducedGPUminAlignment4); after36pass.
  All273matrix/185expert comparisons +CTest2pass; native dense/MoEprotocolspass.
  Opsbefore/after values2 19.74→15.27us; keys2 58.27→40.34us. Single1 slightregress
 9.50→9.85,31.62→32.74; before/after/artifactsprima-attention-* retained.
- Dense dev5paired96912terminal0 allworkloadsPASS, bytes/counts match,
  ratios1.157/1.068/1.010/1.014. Don'tenablepolicyfromdevreceipts. MoEdevpairedLIVE69961
  ngl24/outputCPU; receiptprima-attention-moe-benchmark.json/log. Don'tbuild/testwhile
  timing. CI38157856049/38157850903pending. Fresh20managedNOTstarted; package19current.
- Releaseaudit: qualification.manifest_context omits effective execution settings;
  qualification_status checks model/features/layer-mode/devicedriver but not exact
  GPUngl/output/context/batch/cache/threads. T047open, needboundexecution presets before
  localpolicyenable. Nativecommon.h n_batch2048/n_ubatch512. Currentgateway harness
  configuration omits batching, benchmarkexplicit512/128. Addbatch_size512/micro_batch128/
  threads_batch8 to normal gateway body and test profile/plan/run exactscope. Root
  policy allfalse; no live deploymentswitch. Do not narrow broader API/placement/
  distributed implementation goal to dense qualification. Native146CPUarithmetic
  unchanged; CPU-onlycountvariation stillopen. Rootuncommitted; distributedtests later.

- MoE devpaired69961 terminal1, ratios .833/.507/.816/.517; bytes/counts match all,
  throughput fails. Both CI38157856049/38157850903 pass all4jobs. Managed20 LIVE26607
  artifacts/prima-windows-attention +prima-attention-managed-build.log. Focused142/4
  pass7.92s, Ruffpass. No otherjobs/modelbench live. Afterbuild terminal package
  scripts/build.py --skip-ui --skip-path-shim BEFORE native-loaded assessments,
  verifycapture/sourceclean/hash/import; freshgateway --profile --logprobs bothmodels
  now36checks (addedactualargv) and dense --parallel-slots2 withoutprofile34checks,
  then published paired dense28/outGPU +MoE24/outCPU. Never qualify devresults.
- Gatewayharness now batch_size512/micro_batch128/threads_batch8, records launch
  settings and checks actual owned psutilcmdline flag uniqueness/values inclcache,
  context/threads/parallel. Rust prima_from_pydict verified uses long --ctx-size/
  --threads flags; extra_args providesbatch/micro/threadbatch. No appqualification
  guard changes yet; T047 remainsopen. APIserving eligibility also needs persisted
  exactmanifest context for policy revocation, not generic runtime-only check. Current
  qualification.status lacks execution settings scope, all sourcegatesfalse.

- T047 implemented receipt-backed `execution_scopes`, exact typed JSON equality for
  settings/assets/ranks/segments/placement/device-driver-capacity, detached manifest
  context, per-preset request features, launch-context return and active-config
  persistence. No production preset/gate enabled. Ten setting and three offload
  negative cases plus revocation/isolation/detachment pass. Focused109/1; full1287/19
  47.38s; Ruff and diffcheck pass. Native857 source remains clean; no new native push.
- Fresh20 build26607/package/import/capture passed. Runtime ccad228ce2ad9b7ea8913836a6fb1f3ef8c817c93e8c72f1a607b85902f7b007,
  server f2e8c12d4711f2766be5d31e83bbfa76fdc30621d06247f9fdcff925c1e1966b.
  Initial50295dense36passed. Scoped73564dense38passed (argv/unmeasuredlaunch/persisted
  context/revocation). MoE scoped8814 LIVE, log/receipt prima-attention-scoped-gateway-moe.
  Two-slot gateway and published paired dense28/outputGPU and MoE24/outputCPU next.
  Benchmark now explicitly threads_batch8 and checks actual args for both children;
  identity launch_settings captures context/batch/cache/thread/parallel scope.
  Don't rebuild pyd while gateway runs; don't run CPU/GPU jobs during paired timing.
  T047 acceptance pending MoE/two-slot receipts. Broader T012–T022/T032/T039 remain
  open; physical distributed gates/validations will be tested later. Root uncommitted.

- T047 final execution presets also bind Windows registry CPU model, OS build,
  physical/logical cores, RAM and full named assets; positive library selection
  requires at least one receipt-backed preset. Full1293/19 47.82s; focused48/Ruff pass.
  Final96555dense38passed,10775MoE38passed,31786dense2slots36passed,17756MoE2slots36passed.
  Receipts prima-attention-scoped-final-gateway-{dense,moe}{,-two-slots}.json.
  T047done, all production scopes/gates remainempty/false. Skill updated canonical
  and ownership-safe Codex project discovery install; no root commit/native changes.
- Important baseline correction: source artifacts/prima-baseline-source HEAD verified
  7fe450e19305b828c199d602c23a8337aaa1f03b. n_gpu_layers always includes output for
  positive counts; historical MoE24/outputCPU timing actually compared baseline23
  transformers+GPUoutput. Do not quote old MoE ratios as matched performance.
  Shared prima_assessment.baseline_placement uses ngl+1, forceCPU explicit+tied output
  regex ifneeded, --no-op-offload; CPU0/none unchanged. Benchmark and rawdiagnostic plus
  protocolmodelassessment share it. Paired nativebatchthread8 argvverified; debug0.
- New tests/prima_placement_assessment.py longfirstprefill1001+decode4 source20 native
 14GPU/outputCPU passes realCPU/GPUownership; baseline corrected also passes scheduler
  matrixassignments. Initial prima-attention-partial-placement-before.json failures
  were unsupportednative--no-warmup and baseline tiedprojectionstillGPU. Fixed harness
  only, final receipt prima-attention-partial-placement-reproduced.json; no native
  defect inferred. Headerlogicautooffload looked suspicious but nativecallbacks force
  layer CPU/GPU assignments (llama.cpp17063/17076), so do NOT add a native setter fix.
- CURRENT livejob13196 corrected published dense28/outputGPU paired timing with
  mandatory diagnosticplacementprobe first; output/log
  prima-attention-matched-published-dense-benchmark.json/log, separate -placement.json
  plus retainedPrima/baselinelogs. Don'tbuild/testCPU/GPU whiletiming. Next benchmark
  MoE normal33GPU/outputGPU (both finalgateway1/2slots choose33, earlierbench24 differs).
  Also corrected24/outputCPU comparison useful separately, no qualificationfromold.
  T048open; rootuncommitted, native857cleanpublishedrecipe20; broadscope stillactive.

- Timing13196 terminal1, notthroughputfailure: baselinepair0slots1/2 completed, first
  Prima refusedbytracingguard because GGML_SCHED_DEBUG="0" enablesoldnativepresence
  debug. Nativebackend2202confirms. Correctedserver child-env builder filters explicit
  None values; timedenv schedulerdebug=None (unset), receipttrace0 remains valid.
  guard rejects ## SPLIT logs andnativeGPUreceiptlogs. No nativechange needed.
  Fresh93831 LIVE with prima-attention-matched-published-dense-benchmark-v2.json/log;
  oldfailedreceipt retained, prerequisite28/outputGPU longplacement passed both.
  MoE2slot17756 terminal0 all36checks, actual33GPU/outputGPU. After93831terminal run
  correctedMoE33/outputGPU paired acceptance (mandatoryfirstlongplacement) in distinct
  prima-attention-matched-published-moe-benchmark.json/log. Don'trunotherCPU/GPUjobs
  whiletiming. Native20/source857 remainsclean/published. No fullgoalcompletion.

- Dense93831 terminal1: corrected published five-pair throughput ratios
  .921/1.030/.879/1.009 (short1/short2/long1/long2), onlyconcurrency2passes. AllTTFT,
  bytes/counts/noOOM pass; allplacements/debugguard pass. Receipt ...dense-benchmark-v2.
  Freshmatched MoE70287 LIVE ngl33/outputGPU, normalgatewayplacement (both1/2slots).
  Mandatoryplacementprobefirst passedbothengines. Output/log
  prima-attention-matched-published-moe-benchmark.json/log. NootherCPU/GPUjobs whiletiming.
  Native857clean; no newnativepush/rootcommit; allsourcegatesfalse. T048performance
  open, no substitutionofdevelopmentallpass. Next after70287terminal readactualmatrix
  andupdateallrelateddocs. BroaderT013/T014/T016/T017implementationstillrequired.

- MoE70287 terminal1, corrected33GPU/outputGPU paired5matrix allthroughputfail
  .728/.409/.745/.495; TTFTallpass/noOOM/outputbytesagree, long1/2actualcountsmismatch.
  Exactplacementprobe passesbothengines; debug/tracingdisabled. Noothermodeljobs live.
  Retain oldinvalid24CPUoutputresults separately. Current counts recurrence means
  T039notfixed globally; API/policy candidate suites stillpass theirsubset. All
  productiongatesfalse/source20unchanged/rootuncommitted/distributedphysicaltests later.
  T047done, T048localperformance acceptance open. Further remote read: PrimaCoordinator
  lifecycle.py212 starts workers thenmaster/statusbarrier but notcomposed Control;
  monitor263 shortcircuitscancelled andcannotretrycleanup_pending; readiness worker
  stillneedsnativepost-init acknowledgement, admission/dispatch/recovery stillopen.

- T048checkboxdone for executingcorrectedplacement+pairedmatrices, NOTpassinggate;
  T032/T039stillopen. T049batch-threadcachefix reproducedassertion before: requested4
  wronglyprofiledusing8. Aftercacheguard/profileexplicitthreads_batch+identity512/128/
  batchthreads regressionpasses, focused103/3. Oldcachekeysretainedbutunmatched.
  Fresh15322densegateway --profile--logprobs38passed; newreceipt
  prima-attention-batch-profile-gateway-dense.json/log. All modeljobs terminal.
- FullfinalPython80493 LIVE, output artifacts/prima-execution-scope-final-python.log;
  RuffallchangedPython passes. Package/nativecode unchangedthisturn, no newnativepush.
  Rootuncommitted, canonicalPrimaSkillupdated+Codexprojectinstallownershipsafe passed.
  Distributedphysicaltestsremainlater; broadgoalactivewithremainingAPI/placement/
  coordinator/admission/remoteplanner/performance requirements, notcompleted.

- Fullfinal80493 terminal0:1294passed19skipped43.66s. All test/model sessions terminal;
  no build live. Whitespace/Ruffpass, nativecheckout cleanat857833 recipe20 unchanged.
  Assertedsourcequalification allfalse/emptyexecutionpresets. Freshprofiledense38
  passed afterbatch-threadcachefix; priorfinalMoE38/dense2+MoE2both36 holdsubsetonly.
  Task T047/T048/T049done (T048 executed FAILED matrices, neverqualified). Broad
  T012–T022/T032/T039 remainopen, goalactive. Rootchangesuncommitted. Physical
  distributedcluster gates/validations explicitlylater in relateddocs.

- New nativeReady sourcef11a4a20e7f34e2a67a6985164ac0d5c6c9b30e3 published/remoteverified
  normalpushcodex/kluster-integration, 6files100insertions. Recipe21 digest
 6750661c6db4f05edf036ff7f384285db8a426400b37630293ea048d760c8801. No mathkernel change.
  CLI appliesmanifestbeforeworld1assert, skipslegacyzero-rank assertion; native helper
  reportsphaseReady/layers/ngl/id/digest/stage afteractualmodel/warm/threadpool/control
  setup (workers) andafterservercallbacks (rank0). Capability rank_readiness_v1.
  ZeroMasterRootwarmup formerly writes unallocatedinp_pos/mask; afteractivationinputs
  set llama_set_inputs returnsforrank0window0, preserving positionswirebatchmemory.
- Dev controlledsamehost script tests/prima_rank_assessment.py: initialCLIassertfail
  prima-readiness-controlled-cpu; fixed -v2 passesnormalCPU14/14. ZeroCPUinitialfail
  prima-readiness-zero-master-cpu, fixed-v2 passes. Vulknormal14/14 andzeroMaster0/28
  bothpass exactGPUproofs+hello generation. Initialfailuresretained. Nativebuild54441/
  55201and2CTestpassed. No physicalclusterqualificationclaimed, deferredlater.
- PythonRankSupervisor nowowner.layers, bounded4MiB complete-line trustedchildReady
  report type/stage/allocation/identity/check-owner/deadPIDvalidation. Partiallineswait,
  configuredaloneNone. readyvalidateslayers. SignedWorkerstatus promotesreport; rank0
  normal LocalPrimaRuns waitsReady+HTTP. Tests69pass1skip thenfocused171/3 afterrepin,
  later108/3. Signedfakechild emitsConfigured thentriggeredReady realtransporttest.
  Newcoordinator testsreproduce2fails beforefix. monitorcancelledactive retriesstop,
  persistedcleanup_pendingincludespendingnodes untilrelease succeeds; noautorestart.
  T016stillnotcomposed/admitted/fullyrecovered; leave broadtasksopen.
- Managed21 34666 terminal0 artifacts/prima-windows-readiness. Package/import/capture/
  cleansource passed. Runtimeaed3280b3d7eb2f0a907528243bb73aceace26d17d6bc8dc0823847cdcb3c4f2.
  Server83f7337f77ec8ed77c070a3f741f49dff1db03fd96496fa8273529e9f9ed4ab4,
  CLI742f655aee0b69ee414d8dc900e80ccadd548805cff388adaf5ecca35f2679bd,
  profile6a3ad9f777b4cedccc76a1d11bab2c9f0743d8deeab515b016cdbc78b7ae84ad.
  CI38165428009/38165425315all4jobssuccess. CURRENT68182 densegatewayLIVE --profile
  --logprobs outputprima-readiness-published-gateway-dense.json/log. Don'trebuildpyd.
  AfterterminalrunMoEgateway, freshpublishednormal/zeroCPU/Vulk cases(scriptremove
  development_bin). No newGPUtiming21yet; 20perf historicalfailednotqualified.
  Rootuncommitted, sourcef11cleanpublished, allgatesfalse, T050open/T051done/broadgoalactive.

- Published21dense68182 terminal0 38passed; MoE42336 terminal0 38passed. Allfresh
  managedcontrolledcases23980terminal0 CPUnormal/CPUzero/Vulknormal/Vulkzero pass
  readiness/hello andGPUproofwhereGPUdeclared. Receipts
  prima-readiness-published-{cpu,vulkan}-{normal,zero}.json/log plus.master/.workerlogs.
  No modeljobs live. T050donefornative readiness/zeroRoot regressions, T051cleanupdone;
  T014arbitraryplacement/T016composition/admission/recovery remainopen. Noqualification.
- CURRENT fullPython65221 LIVE, logartifacts/prima-readiness-final-python.log. Ruffpass,
  diffcheckpass, bothCIall4jobs successful. Canonicalkllm-prima skillReady/cleanup
  process updated+projectCodexdiscoveryownedinstallpassed. Rootuncommitted, forkf11
  cleanpublishedrecipe21. No benchmarks21run,20failedperf historicalnotqualified.
  After65221terminalreadcounts/updatecurrentdocs; nextsubstantiveimplementation
  productioncoordinator/admission/recovery orremainingnativeAPI/placement asfullgoal.
  Physicaldistributedcluster gatesandvalidationswillbetestedlater.

- T052 coordinator persistence/regressions implemented. Beforefix9newtestsfail:
  restoredactiveNone, launchintentnotpersisted,7identity/allocationvariantsaccepted.
  Additionalreadinesspublicationrace failsbeforefinalgenerationcheck. Newrun.json
  schema1journalsmanifest/digest/pendingnodes/commandowner/recoveryattempts before
  eachstart andaftereachstopack. Restoreonlycleanup; legacywithoutpendingnodes
  retainsallranks. Invalidrecordsfailclosed. Lock+generation/event fencesStop,
  monitor skipsstartupownedlock, recoverybounded3 andexplicitnewrunresetsbudget.
  Cleanup/persistencefailurecancelsrecovery; revokedcheckstopsinsteadofrelaunch.
  Realchildrestarttestreconstructsranksandcoord thenstopsowned2, preservesunrelated1.
  FirstrealchildfixturefailedbecausestopcallbackreturnedNone; correctedTrue andpasses.
  Focused135passed/3skipped; Ruff/diffcheckpass. Nativef11cleanrecipe21unchanged;
  allqualificationgatesfalse/presetsempty. FullPython48788currentlyLIVE, log
  artifacts/prima-coordinator-final-python.log. Canonicalskillupdated andprojectCodex
  owneddiscoverycopyinstalled. No rootcommit/push. T016productioncomposition,
  Prima-specificnetworkadmissionstillopen; Physicaldistributedvalidationslater.

- Final coordinator Python run48788 completed successfully:1326passed/19skipped,
  4 existing deprecation warnings,49.83s. T052 complete for durable lifecycle
  primitives; the full integration goal remains active. All source qualification
  gates are false and execution presets empty. Native checkout remains clean at
  published f11a4a20e7f34e2a67a6985164ac0d5c6c9b30e3; no native change this turn.
  Kluster changes remain uncommitted. Physical distributed validations are later.
  Next implementation: compose the coordinator into normal Prima run dispatch and
  signed commands; create Prima-specific network admission using private assignment
  addresses, signed diagnostic health and durable reservations. Existing RPC
  admission launches RPC children/relays and must not be reused as Prima admission.
  Also fix the distributed qualification context's unknown participant platforms
  before any future receipt can grant that scope. T012–T022/T032/T039 remain open.

- FullPython65221 terminal0 1299passed19skipped53.15s. All model/build/test sessions
  terminal, no expectedownednativechild remaining. Fresh21 CPU/Vulk normal/zero
  receiptsdevelopment=False actualchecks2/2/4/3 pass. Bothpublishedgateways38pass.
  T050/T051donefornativeReady/startup/zeroRoot andretrycleanup primitives; fullT016
  productioncoordination/admission/recovery stillopen, arbitraryT014/baselineT013/
  remoteT017 andlocalperformanceT032/T039 remainrequired. ALLproductiongatesfalse/
  presetsEmpty. Nativef11cleanpublishedrecipe21, rootuncommitted. Goal remainsactive;
  physicaldistributedgates/validationslater. Don'tmarkwholegoalcompletefromthesechecks.

- T053 production dispatch and private admission are implemented and being validated.
  PrimaRuns selects local versus prepared multi-rank execution. DistributedPrimaRuns
  connects the owned coordinator to signed rank commands, master HTTP readiness,
  private admission and recovery. Preparation binds effective settings/HTTP port,
  request settings digest, master authority and expiry before immutable transfer.
  Prima reservations persist immutable engine/manifest/authority/assignment/edges,
  use exact private addresses and signed diagnostics, and start no RPC children or
  relays. RPC start/release cannot take over an owned Prima run. Worker status/renew
  recheck its manifest/private owner. Startup initializes durable owners for cleanup;
  a rotated authority can settle old ownership only through a fresh signed idle proof.
- Admission tests7pass including real signed3agent diagnostics/mapped sockets with
  simulated adapters. Dispatch fixture uses real signed transfers,3owned children,
  headHTTP and persistence, covering worker-first barrier, stale preparation,
  cancellation during validation/loading, private-owner invalidation, RPC contention,
  Stop and one complete restart after worker crash. Model/native execution is fixture,
  not physical cluster qualification. Initial dispatcher failed CPU head's empty
  Vulkan selection; explicit CPU ranks now skip discovery while GPU checks stay exact.
  Wider175pass3skip13.47s. FullPython45967 terminal0:1334passed19skipped50.19s.
  Ruff/diffpass. Nativef11 clean/recipe21 unchanged/rootuncommitted/gatesfalse.
  Actual published dense gateway5229 terminal1: receipt/log
  prima-dispatch-published-gateway-dense.json/log, passedFalse; inspect actual failure
  and correct before rerunning dense then MoE. No model handles known live now.
  T053pendingactualgateway reruns; T016open for signedparticipant hardware/scope,
  nativeportadmission/final acceptance. Physicaldistributedtests later, goalactive.

- Dense59708 terminal0 passes38checks, MoE8095 terminal0 also38; both published
  recipe21 development=False. Firstdense5229 failure was missing PrimaRuns.manifest
  observation; getter now exposes local/distributed active manifest, actualreruns
  pass. Receipt/log prima-dispatch-published-gateway-dense-v2 and-moe retained.
  Controlled3rank extendedtest reproduces serving->degraded when headtrace log>4MiB.
  Fix parsesnativeReady onlywhileStarting; Ready usespersistedack plusownedPIDtoken
  andHTTPhealth. Test passesafterfix. Fixturefinally nowstopsranks beforeclosing
  channels, preservingprimaryassertion onfailure. Focused70012 terminal check;
  finalfullPython-v2 runningnext. No nativechanges/rootcommit/gateenable.

- Final T053 follow-up complete. Focused70012 terminal0:175passed3skipped14.08s.
  Full17485 terminal0:1334passed19skipped46.21s,4existingwarnings, log
  artifacts/prima-dispatch-final-python-v2.log. Both physical published local
  gateways pass38 (dense59708-v2 andMoE8095, development=False). All model/test
  handles terminal. Trace-volume controlledfailure is reproduced beforefix and
  passes; serving trustsits previouslyvalidatedack butstillchecksPIDtoken+HTTP.
  T053done forcomposed dispatch/admission andcontrolled/localregression scope,
  NOTfullT016 orphysicalcluster qualification. Rootuncommitted/nativef11unchanged
  recipe21/allsourcegatesfalse. Canonicalskillupdated; reinstallownedCodexcopy.
  Next substantive work: signed per-node platform/device/driver/host evidence and
  distributed qualification context (currently remote platforms unknown), native
  rank-port admission, and large active-transfer approval/grant renewal. T012–T022/
  T032/T039 remainopen. Physicaldistributedtests later; goal remainsactive.

- T054 signed participant evidence implemented. New participants.py typed host,
  device and observed-hardware records bind node/runtime/source/recipe/build/artifact,
  exact allocation and selected Vulkan UUID/driver/ordinal/capacity. New signed
  prima_inventory is read-only; receiving node rechecks deadline/authority after
  native discovery. Prep collects every node before immutable asset transfer.
  Fresh signed observations fence changes before ANY spawn; owned native startup
  and worker status/renew revalidate. Distributed scopes bind all normalized ranks'
  hosts/builds/devices instead of remote platform=None; zero-work CPU head permitted
  only alongside required Vulkan workers in that scope. Gates remainfalse.
  Optional empty metadata omitted by model_serializer so legacy/local raw canonical
  bytes/digests preserved; test reconstructs oldmanifest. Different participant
  source/recipe/allocation and malformed/duplicate devices fail. Linux/mac CPU-name
  fallback added, no hardware/accelerator qualification claim from it.
- Real signed3agent fixture now captures/transfers observations and refuseschanged
  artifactset before spawn. Added14 participant tests; focused159/3skip16.07s.
  Legacy signedownership fixture upgraded its fake capture/evidence; no bypass of
  hardware checks. Workers advertise additive prima_inventory_version1; oldnodes
  refused before unsupportedcommands. Extra membership/authfocused84pass1skip13.43s
  had a teardown live scheduler RuntimeError; final full1348pass19skip45.71s succeeds
  (78070 terminal0),4existingdeprecationwarnings. Priorfull64979 also1348/19/46.10s.
  Native published metadata matrix command terminal0: cpu-normal2/cpu-zero2/
  vulkan-normal4/vulkan-zero3 pass actual readiness/generation and requested GPUproof,
  bothranks observed, development=False. Receipts prima-participants-published-*.json
  logs retained; samehost, notphysicalcluster. Nativef11clean recipe21unchanged,
  rootuncommitted; noCpppush thisturn, allsourcegatesfalse. T054done/fullgoalactive.
  Next native rank-port admission, large active-transfer lease/grant renewal, plus
  remaining broad API/placement/remote planner/local performance. Physical distributed
  cluster gates and validations will be tested later.

- T055 listener foundation implemented: strict manifest-derived inbound data/signal
  rules and exact private peers; signed worker admit/release handlers validate
  preparation, manifest/run, private assignments, hardware and qualification.
  HelperPlatform forwards original signed bytes. Native helper pins key/term/session/
  assignment and durable sequence fences; journals rule intents before host calls,
  persists CIM snapshots, rejects foreign/pre-existing/changed rules, retains partial
  cleanup and reconstructs cleanup only. Pending rules block private network
  restoration/reprepare/revocation, including helper startup's automatic restoration.
  Windows create checks returned instance identity; remove rechecks full snapshot
  and removes captured CIM instance. No physical firewall mutation or helper install.
- Focused Python92/1skip6.99s; full1371/19skip47.05s,4existingwarnings, terminal0
  session82399/log artifacts/prima-ports-final-python.log. Native helper11/0failed
  1.52s in artifacts/prima-ports-native-tests.log: cross-language signed fixture and
  Windows OS parser checks included; firewall scripts parsed only, never executed.
  Supported final package rebuild/native import succeeds, staged helperSHA
  694a05bf3310d130494cb45e88d25344b81c531aa9a5a909e4282fbfb49a93cf,
  log prima-ports-package-final.log. Final skill/connect/port78/2skip3.17s; canonical
  skill updated and ownership-safe Codex project copy synced. Ruff/diff checks pass.
- T056 is open: compose all-node native port admission before any spawn, persist
  every attempted participant for lost acknowledgements, drain ranks before rules,
  bind serving recheck/renewal/expiry and restart cleanup, negotiate capabilities,
  implement direct administrator/Linux/macOS paths and validate protected helper IPC
  plus physical OS behavior. Current normal Run does not yet call new port handlers.
  Review path for lost/changed CIM ownership remains required; unknown rules are
  retained safely. Legacy helper assignment_revision0 needs owned cleanup/reprepare.
  Large-transfer grant renewal, broader APIs/placement/profiling/planner, local
  performance and platform acceptance remain required. Nativef11 clean/recipe21
  unchanged/root uncommitted/allgatesfalse. Physical distributed gates and validations
  will be tested later. Full integration goal stays active; T055 does not close T016.

- T057 production listener composition implemented (2026-10-12). Root Run checks
  strict prima_ports_version1 and signed prima_port_capabilities before mutation;
  updated helper reports supported version plus explicit empty owned-runs list.
  Local signed admission uses service-level channel sequence so reconstructing a
  controller does not replay sequence1. Native-required reservation flags immutable,
  attempted/admitted node lists durable and storage revalidated; every node intent
  written before its command, every listener acknowledged before any child starts.
  Port release after all rank stop acknowledgements narrows per successful node and
  continues past failures. Failed/lost replies stay cleanup_pending for retry.
- New PortSupervisor stores exact manifest/authority intent before helper mutation.
  Initial admission600s; owned launch/renew60s. Lock spans validation-to-spawn and
  expiry; post-spawn revoke stops owned child before native cleanup. Restored records
  always cleanup only. Port-only journals initialize on agent startup; port ownership
  blocks RPC, asset preparation and profile contention. Worker startup/status/renew
  checks native rules; root checks local rules too. Protected helper has no independent
  watchdog yet; agent leases do not prove cleanup while the agent is absent.
- Signed3-agent fixture uses verified original envelopes and controlled native rules,
  covers all-node barrier, lost apply reply+failed release with no spawn, retained
  worker ownership, retry, old capability refusal without reservation, original
  readiness/cancellation/crash recovery. Node tests expire/reconstruct real children,
  stop failure retains rule intent, unrelated child survives. No physical firewall,
  private NIC changes or privileged helper install/IPC; no distributed model proof.
- Focused final146/3skip6.71s; full1381/19skip46.97s4existingwarnings, session70982
  terminal0, artifacts/prima-port-dispatch-final-python.log. Standalonehelper11pass
  1.70s in prima-port-dispatch-native-tests.log. Final supportedpackage/import pass,
  log prima-port-dispatch-package-final.log; stagedhelperSHA
  2d1df2f64dee4373056d5d4011606565fc92f0af4b70d2be490d4282f3798b01.
  Ruff/diffcheck pass; canonicalskill updated and ownership-safe projectCodex synced.
  Nativef11 clean/recipe21unchanged/rootuncommitted/allgatesfalse.
- T056 remains direct administrator/Linux/macOS paths, helper-independent expiry,
  missing/changed CIM review, protected IPC/service failures and physical OS acceptance.
  T012–T022/T032/T039 broad scope remains active, including large-transfer grant
  renewal/API/placement/remote planner/local performance. Physical distributed cluster
  gates and validations will be tested later. Published local gateway regression
  dense13600 is live now (owned handle), then MoE next, distinct receipts/logs
  prima-port-dispatch-published-gateway-{dense,moe}; do not restate passes until terminal.

- T057 final port-only guard: PrimaRuns.busy includes node listener owner, launch
  refuses it before either local/distributed dispatch, Stop drains it after owned
  rank stop even if no coordinator record. Constructor initializes port owner before
  facade. New observable busy/refusal/Stop regression passes. Focused147/3skip7.22s;
  full1382/19skip48.34s4existingwarnings,76250terminal0, log
  prima-port-dispatch-final-python-v2.log. Supportedpackage-v2/Ruff/diff pass.
  Initial published dense13600 andMoE30019 terminal0 each38passed, sourcef11,
  recipe21 development=False, distinct prima-port-dispatch-published-gateway-*.json/log.
  Final dense94645-v2 is live after last guard; MoE-v2 next. Do not claim final reruns
  passed until handles terminal. Still no physical network/FW/helper IPC acceptance.

- Final T057 receipts dense94645-v2/MoE12710-v2 terminal0, each38passed, f11/recipe21
  development=False. Logs/receipts prima-port-dispatch-published-gateway-*-v2.
  These cover affected published local gateway/profile/lifecycle, not distributed
  networking/generation or paired performance. No production scopes enabled.
- Cancellation regression reproduces future.done=True while native executor callback
  still blocked. New _port_call uses a shielded executor future, preserves context and
  settles the call before propagating cancellation (including repeats). Both apply
  and release use it; pending participants stay durable on failure/cancellation.
  Root admission closures bind coordinator generation for delayed local/remote calls.
  Test passes after fix; no late admission after release acknowledgement is accepted.
- Final focused148/3skip6.82s; full1383/19skip47.25s4existingwarnings,98834terminal0,
  log prima-port-dispatch-final-python-v3.log. Package-v3 supportedbuild/import pass;
  canonicalskill/ownedCodex copy synced. Ruff/diffcheck pass; nativehelper11pass remains
  valid (only status-owned-runs field changed this turn). All model/test handles terminal.
  Nativef11 clean/recipe21unchanged/rootuncommitted/allgatesfalse. T057done for composed
  node-leased listener lifecycle/control/local regressions, NOTfullT016/T056/integration.
  Next T056 directadmin/Linux/macOS native backends, helper-independent expiry while
  agent absent, missing/changed rule review, protected IPC/service and OS acceptance.
  Broad API/placement/remote profiler/planner/assets grant renewal/local performance
  remain open; physical distributed cluster gates and validations will be tested later.

- T058 implemented independent native rule watchdog (2026-10-12). Helper state now
  Arc<Mutex<Helper>>, timer Weak ref and bounded shutdown join. IPC blocks before
  state acquisition, so missing/idle agent cannot stop timer ticks. Native monotonic
  deadlines initial600s, fresh signed exact-contract renewal90s; node/rank lease60s.
  Restart restores pending cleanup only, no persisted clock lease. Changed/failed
  rules retained; other expired runs still attempted. No arbitrary PID operations.
- Port protocol now2 (heartbeat + strict schema max2 + signed capability). Native
  status requires watchdog_version1 + explicit empty owned-run list before availability;
  old/missing/ill-typed/owned helper fails. New prima_renew_ports carries exact contract,
  verifies sender/signature/assignment/term/session/replay/native snapshots and rejects
  expired or cleanup-pending revival. Status alone never renews. Coordinator renews
  immediately after native spawn ack and on monitor; receiver requires live owned
  rank/current contract and preserves original signed frame to helper. Native helper
  expiry only narrows rules, not process/traffic/generation or final cluster release.
- Native15passed1.65s (real timer thread with controlled rule removal; no agent/IPC
  activity), artifacts/prima-watchdog-native-tests.log. Focused162/3skip8.00s;
  full1397/19skip46.89s4existingwarnings,71623terminal0, watchdog-final-python.log.
  Supported finalpackage/nativeimport/Ruff/diff pass; stagedhelperSHA
  40ebd51072d9da9d37e1a5ef3c5564e9a17f88911a4882c3d5b3450794c813ed,
  package log prima-watchdog-package-final.log. Canonicalskill/ownedCodex synced.
- No protected helper installation/restart or physical FW/private NIC mutation.
  T056 physical/service watchdog acceptance, directadmin/Linux/macOS, unknown/changed
  CIM review and protected IPC/service failure remain. Broad API/placement/assets
  grant renewal/remote planner/profiling/local performance remain required. Allhandles
  terminal/nativef11clean recipe21unchanged/rootuncommitted/allgatesfalse/fullgoalactive.
  Physical distributed cluster gates and validations will be tested later.







- T059 progress-bound asset leases implemented. Source grant JIT per asset; validates
  complete hash/fingerprint with cancel/authority/progress checks. Exact served-range
  acknowledgement renews bounded source TTL; bool/duplicate/wrong/foreign/expired
  ack or source drift fail, reads do not renew. Future assets do not expire during
  earlier copying. Worker successful signed chunks extend600s approval; expired jobs
  rejected before and after chunk write. New signed prima_prepare_renew requires
  owned job/operation/sender/authority, new safe-integer progress and valid phase/expiry.
  Root PreparationLeases renews enrolled complete/current workers every30s with new
  hashing/acknowledgement/tracked profile progress and finalizes all approvals together.
  Strict heartbeat prima_asset_lease_version1 fences old workers. Worker cache and
  chunk I/O off event loop; prima_prepare signedTTL60. No expired/cancelled revival.
- Seventeen realfile/controlledclock tests added; real signedWebSocket renewal+duplicate
  refusal and3agent fixture pass. Focused179/3skip7.39s; firstfull one monitor-renew
  contention assertion failed (1413/19). Unchangedisolated1pass4.74s; test nowbounded
  waits actual nonblocking monitorrenewal. Finalfull1414/19skip46.49s4oldwarnings,
 26188terminal0, transfer-leases-final-python-v2.log. Ruff/diff+supportedpackage pass.
  Canonicalskill/ownedCodex updated. NativeRust/pin/recipe untouched this turn.
- Published local dense8809terminal0 passes; receipt/log
  prima-transfer-leases-published-gateway-dense. MoE95011live, distinct-moe receipt/log;
  observe same handle to terminal before asserting result. No physical network/FW/
  distributed large transfer/throughput acceptance. Physical distributed cluster gates
  and validations will be tested later. Rootuncommitted/nativef11clean recipe21/allgates
  false/fullgoalactive. Wider companion/shard native execution, platform/admin/helper
  recovery, API/placement/remote planner/profiling and local performance remain open.

- T059 final receipts dense8809 andMoE95011 terminal0 each38passed, developmentFalse,
  f11/recipe21; prima-transfer-leases-published-gateway-*.json/log. Final supported
  package/nativeimport/skill-sync/Ruff/diff pass. All model/test handles terminal.
  Rootuncommitted/nativef11clean recipe21unchanged/allgatesfalse/fullgoalactive.
  T059 done for progress lease / command event-loop / controlled signed transfer
  continuation, notallT015 orfullintegration. Physical distributed cluster gates and
  validations will be tested later. Next platform/admin/helper recovery or broader
  native API/placement, remote profiler/planner and strict local performance work.

- T060 local listener recovery API/helper source path implemented. Reproduced missing
  PortSupervisor.resolve with two failures, then added protected helper review/resolve
  with recovery_version1. Snapshot SHA binds full owner/scope/current observations;
  stale/live/foreign recovery denied. restore_owned never adopts changed/unknown
  rules; explicit keep_current preserves OS rules and forgets local ownership only.
  Local loopback/token/origin routes use node port lock and require matching rank stop.
  Setup status surfaces listener-only pending resources. Old helper recovery refuses
  before request; persistence failure retains retry, normal cleanup settles lost ACK.
- T060 focused139/2skip6.57s; native20pass1.49s; supported rebuild/nativeimportTrue;
  final full1431/19skip46.45s4oldwarnings, session3901terminal0. Logs
  prima-listener-recovery-{native-tests,package,python}.log. Staged helper SHA
  5fe3d6b3d490971f7d133a4725be091455e6eee05d5789de1056205844dcdd67.
  Controlled native rules/ASGI/helper responses; no protected service/named-pipe or
  physical firewall/adapter acceptance. Canonical skill/Codex copy synchronized.
  Root edits uncommitted; nativePrima cleanf11 recipe21 unchanged/no push needed.
  All qualification false, full goal active. Next T056 admin/direct/platform paths,
  dashboard recovery controls, protected service/IPC and physical Windows acceptance;
  remaining API/placement/assets/remote profiler/planner/localperformance still open.
  Physical distributed cluster gates and validations will be tested later.

- T061 master/worker local Prima listener recovery wired into network dashboard.
  Typed resource/review identity and bounded rule/digest checks; exact/absent permit
  restoration, changed/unknown refuse. Explicit keep-current manual acknowledgement;
  refresh/close/attempt clears review/ack. Captured abort/work revision fences prevent
  late reads/errors/busy state after close/reopen. Local-only and invalid ACK refusal;
  completion tells user to retry Stop on master, never releases cluster itself.
- Dashboard full71pass0skip (63nonbrowser/8browser,72979terminal0); final focused
  browser1pass6.85s withtrue390/1280 CSSwidths and production styles/screenshots.
  Viewport metadata omission corrected; old scaledmobile notproof. Vite8 client
  unconditional fixture WS caught bynewzeroexceptionassert; guarded removal ofonly
  itsconnect keepsrealCSShelpers/noerrorsfilter, final browser-final-v2.log pass.
  Check0errors0warnings; build19068terminal0; supportedpackageterminal0; skill
  tests26/2skip2.84s. Docs/skill updated, Rootuncommitted/nativePrimaf11clean recipe21
  unchanged/allqualificationfalse/fullgoalactive. No livehelper/FW/cluster mutation.
  Next admin/direct/platform paths and protected IPC/service physicalacceptance;
  native API/placement/assets/remoteprofiling/planner/localcorrectness/perf stillopen.
  Physical distributed cluster gates and validations will be tested later.

- T062 bounded Windows helper IPC implemented. Original partialreplyclient exceeds
  testdeadline, captured helper-ipc-before.log (forcedfixture disconnect avoidshang).
  Shared8squery/45smutation+recovery deadline coversqueue/open/write/read; busy pipe
  retrybounded. Ownedthreadhandle CancelSynchronousIo retriesgaps andjoinsbeforelock
  release. Native45s timer repeatscancellation incldispatch-to-flush gap; native
  FlushFileBuffers preventsunreadreplydiscard beforeDisconnectNamedPipe. Invalid/
  oversizedresponse HelperRefused; actualprotectedimagecheck rejectsfixturebefore
  bytes. Timeout/errorcodes survivehelperstatusGuided/UI; no regrantprompt and
  setupblockeduntilhelperresponds. Timeout!=mutation/rankstopproof; ownedrecords
  persistpendinguntilcleanupAck. Signedwireunchanged andnodeownerretentiontested.
- T062 Windows10namedpipe tests+portable11deadline/status cases pass21/1.78s; focused
  broader107/7.48s beforefinal3actioncases. Native24/1.77s realpipe4newtests, other
  rulescontrolled. FirstfullSQLitePermissionError, unchangedisolated1/2.57s; second
  networkFreshness expiry, unchangedisolated1/9.84s. Logsretained/tests+limitsunchanged.
  Finalfull1452/19skip51.61s4oldwarnings,44558terminal0. Dashboard71/0skip,50172
  terminal0; check0/0, build0, supportedfinalpackage0/nativeTrue. StagedhelperSHA
  12fa6c17384190ede38340a02caf188738398e9324a17f09b0619825df335db5. Skill/Codex
  copy923439599a5c3166154eb8ef2e6d752ed4f3a560e14d0d5541bcf5f69299b11e.
  Logsprima-helper-ipc-*; allhandles terminal. Successfulpipefixtures substitute
  protectedinstallationverification, notproductionpolicy. Realimage rejectiondoes
  notproveacceptedprotectedservice/ACL/deployment/restart/FW/hardware. None mutated.
  Rootuncommitted/nativePrimaf11clean recipe21unchanged/allqualfalse/fullgoalactive.
  Next admin/direct/platform sourcepaths andprotectedservice/physicalOS acceptance;
  broadernativeAPI/placement/assets/remoteprofiler/planner/localperformance stillopen.
  Physical distributed cluster gates and validations will be tested later.

- T063 direct elevated-Windows source path delivered: owned private-stdio native
  child, native administrator/mode refusal, shared signed scopes/rules, manual address/
  DHCP ownership, stable reconciliation, persistent provider/grant fences and shutdown.
  Partial replies survive read timeouts; malformed/partial requests abandon the pipe
  and settle the owner. Six malformed/foreign grant-status failures reproduced then
  fixed: explicit matching empty rule/scope lists and fresh replacement probe required.
  Controlled real-child pipes remain distinct from accepted elevated firewall behavior.
- T064 Stop race from first full suite reproduced after monitor retirement; retain
  run ID through acknowledged cleanup, clear own active_config and preserve foreign.
  Deterministic2checks and final full1478/19skip48.63s pass,75084terminal0;
  native28/1.57s, focused72/6.40s and finalowner36/3.37s. Supported finalpackage0,
  nativeimportTrue; helperf577dbd0daec43d61d5ac1ef70dcddd1180f2639a61981c416d1d3c9ee3c2421.
  Skill/Codex copyccc98d552ea6863de62cee22876b37ee906e938a70b7de0e991f473141710980.
  Logs prima-direct-*; allhandles terminal. Actual nonadmin stagedexe refuses before
  config read. No live service/FW/address mutation; elevated physical T056 remains open.
  Root uncommitted; Prima f11clean recipe21unchanged/no push. All qualificationfalse.
  Next authoritative remote profiling/planner and local correctness/performance,
  platform/native compatibility remain. Physical distributed validations later.

- T065 native matrix-profiler defect reproduced: raw floats copied into F16/quantized
  tensors,4wrong computations in CPU checker. Proper encoding plus decoded-operand
  reference, allocation/status/time/output checks and direct chosen-backend execution
  fix it; no fallback scheduler/extra backend retained. Expanded IQ-reference mismatch
  corrected; CPU12formats pass. Vulkan9supported comparisons pass,3unsupported IQ
  formats explicitly refuse and remain unqualified (initial all-format failure retained).
- Published e7ee8f6ced718dca7187cfba4407f44b5c88884d, remote verified; recipe22 digest
  29419d3ca8cd75f4d04771887e2201d07b0b5635fe96b67fe5d045ae3d91a700.
  Fresh managed source clean; capture verifies staged hashes; runtimeb839c34accef13bb9bf239dd959e746a7f3fedea0ef4edba5490d92133bd8148.
  Published native3/3pass12.58s; physical UUID00000000030000000000000000000000 driver8389003
  receipt prima-profiler-published22-vulkan.json. CI38189617543/38189620626 all4jobs success.
  Supported Kluster package/nativeTrue; full1478/19skip48.43s terminal98589.
  Skill/Codex0801b7ec91e8b68b7551314dbd762a9cd8f5609386d605c0c812ee76e8d4b280.
  Fresh dense gateway/profile/logprobs38pass; MoE gateway/logprobs36pass,97553terminal0.
  MoE generation profiling not repeated. Taskkill already-stopped notices in both logs;
  all owned lifecycle/restart assertions pass. All test/build handles terminal.
- T066 full-model kernel-profile failure is pre-existing on published21 and remains
  on22: Uncaught tensor in Qwen3 cost extraction. Production ProfileJobs published22
  settles failed,0processes/0cachedprofiles; artifact prima-profiler-published22-kernel-job.
  Synthetic matrices do not solve complete typed model costs or Vulkan bandwidth;
  continue that native repair and authoritative remote profiling/planner integration.
  No native performance qualification from GFLOPS, root uncommitted, all gatesfalse.
  Physical distributed cluster gates and validations will be tested later.

- T065 matrix repair/delivery closed with published22 source/native/CPU/GPU/CI and
  fresh gateway/package verification. T066 legacy full-model kernel failure remains
  the next implementation gap, before remote planner costs can be trusted. Root
  changes uncommitted, native fork e7ee8f6 published/clean, qualification stillfalse.

- T066 native producer/cache repair published72d4edaba7f0111322fe192027c166fe85985e33;
  recipe23 digest0bc41d8bca717abaa221ac3bb1783dab00c3ccdc14593922f8fda4a591a5992a.
  Original exit1 confirmed missing fio plus partial Uncaught tensor costs. Complete
  GGUF metadata now counted read-only; true Q/K/V shapes/head norms, active MoE
  arithmetic/full storage, absolute layer/output costs. Native schema2 typed roofline
  estimates with checked GPU reads/F16 copies; exclusive aligned unbuffered64MiB I/O
  beside model replaces fio. Real Win I/O and owned-child death cleanup pass; other
  OS physical storage/GPU not inferred from CI compilation.
- Kernel decoder validates backend/settings/complete ordered vectors/rates/estimates;
  source capability downgrade rejected, host/device rechecked, cache binds observed
  OS/CPU/cores/RAM plus device and typed settings. Cached negative/incomplete vectors
  refuse reads; generation timing remains distinct. Focused105/2skip3.54s includes20
  newcases. Full1498/19skip49.37s afterpin; supportedpackage/nativeTrue. Skill/Codex
  e97a2a9fc9a48ffd206a24cfd3fc89152eee96bf05132824b5bcc4e9b62cdedd synchronized.
- Fresh managed23 source clean, hashes verified, runtime12d10ab8aae79ee51371a81566f030d4ea84f3cbd1334534154a15179c741a97.
  Native3/3pass12.92s; physical9GPU/3unavailable/refusal plus GPU read proof pass;
  CPU12formats and cost semantics pass. Published production ProfileJobs dense/MoE
  prepare/cache28/48layers,0processes (76851terminal0). CI38194600187/38194602916 all4jobs success.
  Dense gateway/profile/logprobs38pass,72330terminal0; MoE gateway/logprobs36pass,
  47080terminal0. MoE generation profiling not repeated in gateway; typed kernel job
  separately verified. Already-stopped taskkill notices, all lifecycle checks pass.
  All local test/build/profile/gateway handles terminal. Logs prima-typed-profile-*.
- T066/T017 still require signed remote profile consumption, feasible candidate
  comparison with complete host/device/runtime/model/settings matching, link/disk
  context and concurrent resource reservation. measured_local_candidates is still
  local full-model generation only; do not extrapolate it to partial ranks. Native
  compatibility/placement/platform and local correctness/performance remain open.
  No live network/helper changes, root uncommitted, fork72d4clean, allgatesfalse.
  Physical distributed cluster gates and validations will be tested later.

- T067 signed profile control/import delivered. Worker profile protocol2 binds
  complete current prep, manifest/model/runtime/rank/backend/effective settings and
  persists operation/sender/authority owner. Owner callbacks recheck auth+600s
  approval through native work/cache; status/cancel/request-ID adoption foreign
  refusal. Async starts resolve approved layout offloop. CPU backend explicit on
  Vulkan builds. Cancel/cache publication serialized and callback cancellation rechecked.
- Master checks selected worker profile2 before transfers, imports only complete
  matching typed data after fresh signed participant evidence. Canonical device
  hardware hash omits description/free noise, matching native and signed observation;
  old kernel cache hashes unmatched. Controlled real three-agent+profile subprocess
  two-case suite2pass9.96s; foreign signed status/cancel refused, originalowner preserved.
  Fixture model/rates/adapters/rules are not physical acceptance. First missing-os
  fixture error retained; final focused94/2skip10.87s, full1499/19skip50.93s pass.
  Supportedpackage/nativeTrue; skill/Codexbc148d222428bf07ba1cef1e3762086c932ee8510a659af557b55669e825d8e2.
  Logs prima-profile-owner-*, allhandles terminal. Native fork72d4clean recipe23
  unchanged/no push needed; root uncommitted; qualification stillfalse.
- T066/T017/T021 still need authoritative typed candidate scoring with exact fresh
  private-link/disk context and concurrent resource reservations. Existing plan
  path remains measured_local_candidates (local full-model generation only), capacity
  fallback otherwise. Next implement that remaining pipeline; do not call vector
  import a complete distributed planner. Broader APIs/placements/platforms/local
  correctness/performance stay open. Physical distributed validations later.


### T068: preparation-owned directed private link evidence (2026-10-12)

DedicatedDiagnostics now supports bounded signed version2 stream probes alongside
unchanged echo1. Each probe transfers3x1MiB in64KiB chunks, checks exact nonce/sample
bytes and signed acknowledgements, caps simultaneous receivers at2, binds the
private source and rechecks authority/identity. Windows monotonic quantization
reproduced zero elapsed samples; measurement timing uses perf_counter. Existing
monotonic ownership/expiry deadlines remain unchanged.

Signed prima_link_profile binds the original preparation operation, complete current
manifest/assets, exact directed edge, private assignments/revisions, master authority,
source/target keys and current observed source address/route. Ephemeral12s scopes
permit worker-to-worker authentication without durable pairing, carry live ownership
callbacks, reject expiry/replacement/foreign local owner and are cleared on close.
Selected workers must advertise link profile1 before asset transfer. Master collection
is sequential to avoid probe contention and retains receipts on the preparation job.

Focused64passed/2skipped27.23s (prima-link-final-focused.log); real signed three-agent
prepare/Run/Stop now collects every directed edge, including worker-to-worker while
no worker peer is pinned. Transport is real local TCP; virtual adapters, mapped
assignments, model/rules and rates remain explicit fixtures. First tests exposed the
coarse Windows timer and reset-versus-EOF cleanup behavior; original failed logs are
retained. Worker authority validation uses its accepted store authority, not the
master-only authority factory. Full suite/package results follow in validation.md.

Native published72d4edab/recipe23 unchanged; no new Prima push is needed. Normal
preview scoring still lacks authoritative typed distributed estimates and resource
reservations. T017/T021/T066 and broad integration remain open. Physical distributed
cluster gates and validations will be tested later; all policy scopes/gates stay false.


T068 final delivery: full Python suite1518passed/19skipped56.03s,4existing Starlette
warnings (artifacts/prima-link-python-final.log; process35335terminal0). Supported
package rebuild exits0; native_availableTrue and installed link collector import
succeed (artifacts/prima-link-package-final.log). Canonical/project Codex skill
SHA256 both7edf8cdb2fe7f40cd98446123c6d5abd38bedc75a1364729c8cfe50905d83a04;
installation updates only the owned Prima copy. Ruff/whitespace checks pass. Native
source remains clean at72d4edaba7f0111322fe192027c166fe85985e33/recipe23. All test/
build handles are terminal. Root changes remain uncommitted. Candidate scoring,
disk-context integration, resource reservations, broader compatibility/layout/platform
and local performance remain open. Physical distributed cluster gates and validations
will be tested later; no qualification scope is enabled and the full goal stays active.


### Completed GPU handoffs and typed candidate comparison � 2026-10-12 (T069)

Prima commit cac12883afb9dd91b247deb61cb8245806bea4fd is published and remote-verified
on codex/kluster-integration. It adds completed_f32_embedding_v1 host uploads/downloads
at actual model embedding width. Every one-warmup/five-timed sample changes input,
checks all returned bytes, synchronizes the selected GPU backend and uses a steady
high-resolution clock. CPU/invalid widths/unavailable backends refuse substitution.
Native schema2 retains exact bytes plus both sample arrays; host_transfer_profile_v1
requires valid GPU evidence in Kluster's consumer. GPU-device read/KV-copy costs are
kept separate, and embedding handoffs never stand in for logits-width downloads.

Development native contracts3/3pass12.90s; selected RX9070XT contracts verify GPU
matrix formats plus4/4096/8192/16388-byte handoffs. Development dense/MoE ProfileJobs
both complete28/48-layer reports. These development probes overlapped the native
contract process; their rates are boundary evidence, not performance acceptance.
Fresh published24 jobs below run after those processes settle. The initial build
command named the nonexistent profile_tool target, then corrected to profile-tool
and prima-profiler-contract-test; no source failure was hidden by that command typo.

Recipe24 digest a4f1d9fb1cb431a4be83f5679c26dfa965c4ba8e2f18a77fe5ee0dd9786610f5
consumes the exact published source in artifacts/prima-host-transfer-published24.
Source is clean at that SHA; capture_runtime verifies all staged artifact hashes.
RuntimeID a389f41c2483cffa29bd2f39e0bdd560fd8e26cb8c86434b38c8c86e266c4fd7.
CLI3ddfde82f715a092229b679d7e3d5c9a2cde5ecee82b73dd5bdb47f7e148ce3a,
server2e8f2695ba878d9ffd4cb4ffa9d6c96f46d8b814206e0b2b204811c957b3176d,
profiler49c92faf866ceaf3da9a9d6f5b2fa606109c83572015e7a7868639ddc15a177b.
CI38199767142/38199764093 both succeed across Windows/Linux/macOS CPU contracts
and Windows Vulkan compilation. They do not prove physical cross-platform GPU use.

Final published production ProfileJobs both prepare/cache28/48-layer vectors,
4096/8192-byte GPU handoffs and model filesystem identity, retaining0 processes
(artifacts/prima-host-transfer-managed-published24-final.json/log;63921terminal0).
Storage records the OS filesystem device identifier and owned directory; it does
not assert physical media identity or uncached hardware speed. The earlier published
receipt predates the consumer's storage field and remains separate historical evidence.

prima_profile_context_version1 adds preparation-owned read-only signed fresh hardware/
filesystem context. It refuses foreign operations, expired/incomplete preparation,
wrong profile/model/node, changed participant identity and missing layouts. The master
rechecks complete signed context before typed candidate comparison. Normal previews
consume prepared records and obtain fresh observations without profiling, transferring,
restaging removed layouts or publishing caches. Explicit profiling negotiates context1
alongside profile2/link1 before asset transfer. Older participants need an upgrade.

The comparator uses absolute assigned CPU/GPU indices, exact effective context/threads,
selected device and matching GPU embedding handoffs, plus rank-zero input/CPU output.
It validates current private addresses/revisions and link receipt geometry/samples;
all candidate evidence must match or selection stays capacity-first. Published native
F32 embedding/shape/position payloads and one-token metadata determine transfer bytes.
Authentication/receipt latency is a labelled application proxy. Logical weight-read
cost uses measured sequential filesystem throughput and stays separate from decode,
serving as a tie breaker after decode cost. Scope is resident one-token/slot f16 K/V,
Flash Attention off and representable contiguous layer windows. GPU output needs
its own completed logits download; no embedding-size extrapolation is allowed.

Verification: initial scorer45-case run has one invalid fixture public-key fingerprint;
it is corrected to SHA256 of its32 decoded bytes. First corrected45passed10.59s.
Final focused64passed10.60s covers GPU host transfers, absolute layer/zero-master costs,
link-driven worker exclusion, missing/stale/model/context/hardware/storage refusal,
malformed link rates, current owned context and signed three-agent boundaries
(artifacts/prima-typed-planning-final-focused.log). Three-agent tests collect signed
fresh filesystem/hardware context and refuse a foreign owner; models/rates/adapters/
rules/mapped assignments remain controlled fixtures, not physical cluster acceptance.
Full final1543passed/19skipped55.42s,4existing Starlette warnings
(artifacts/prima-typed-planning-python-final.log;53709terminal0). Supported package
rebuild exits0; native_availableTrue, planner import and recipe digest verify.
Skill/connect55passed/2skipped2.87s, canonical/project Codex skill hash0d608c2935e3b5ddc1657f3c033a9dec82bf12070e4f1a4576aeaa48ef93e9a2.
Ruff and whitespace checks pass. All local build/test/profile handles are terminal.

T017/T021/T066 remain open for concurrent resource reservations, profiling additional
feasible candidate contracts/edges, GPU-output logits downloads and remaining effective
settings/layouts. Broad API/model/assets/platform/layout and local correctness/performance
acceptance remain open. No qualification gate or scope is enabled. Physical distributed
cluster gates and validations will be tested later. Root changes remain uncommitted;
Prima source is published/clean and the full integration goal remains active.


### Shared node work reservations � 2026-10-12 (T070)

The registry now owns an atomic NodeResources guard. Exact engine/operation or run,
authority and immutable contract scopes permit an owned preparation's profile/link
child while refusing foreign work and overlapping measurements. Profile reservation
precedes model resolution; master preparation remains claimed through worker-future
settlement. Worker preparation retains its claim across the entire authenticated
asset transfer, releasing only after verified bytes, cancellation or expiry settle.
Native listener admission retains its claim until durable owned release/recovery.
Rank startup joins only the same run/manifest/authority and rechecks current prepared
approval immediately before spawn. Master dispatch binds its startup intent to the
resolved execution contract before admission. All selected ranks therefore reserve
before any spawn; failed partial admission preserves unrelated owners and drains
only attempted listener ownership. Resource capability1 is required before transfer/
admission; older workers receive an upgrade refusal.

Normal llama.cpp master startup uses the same node guard (409NODE_RESOURCE_RESERVED)
and RPC startup returnsRPC_BUSY before firewall/spawn on conflict. Existing durable
rank/listener/preparation/coordinator records and process tokens remain authoritative
on restart; the in-flight guard is not a new persistent authorization or expiry lease.
It conservatively excludes competing node work rather than estimating concurrent
CPU/GPU/disk sharing. No extra remote reservation or arbitrary process endpoint exists.

Private stream grants retain claims through one owned transfer or their real12s
expiry timer. Expiry/close cancels and settles receiver/source tasks before callback
release, and an old callback cannot retire a newer token. Unused grants release on
timer rather than requiring another command. Profiling futures retain claims across
cancellation until completion and retain live child ownership when cleanup fails.
A real owned Python child reproduces generation failure plus failed cleanup; it
remains alive/tracked with cleanup_pending and a busy guard until explicit successful
Cancel retry. The old generation finally path discarded this tracking entry. Prepared
profile/master jobs briefly report settling until final worker work completes.
Preparation Cancel now revokes completed approval as well as unfinished work; running
rank Run/Stop ownership remains separate.

Verification: first resource-focused90passed10.82s; shared link/profile/dispatch49passed
28.11s. Initial cross-engine130case run has2fixture failures (missing explicit local
master authority and an older busy-message assertion). The local fixture now supplies
its real typed authority and busy wording is preserved. Corrected52passed/1skipped
10.77s. Initial expanded suite has9failures because a __new__ worker fixture omitted
new resource/future fields; fixture initialization is corrected rather than bypassing
production guards. Later worker transfer/resource/dispatch/lifecycle50passed/1skipped
10.81s. New atomic/child/queued-cancel/persistence/startup checks and real signed barrier
20passed10.85s. Final focused112passed/1skipped40.42s
(artifacts/prima-resource-final-focused.log). Modified transfer assertions prove claims
remain through each partial chunk and retire on full verified completion. Signed
three-agent Run refuses a busy worker before any spawn, cleans partial native-rule
fixtures, preserves the foreign claim and refuses resource-capability0 before mutation.
Tests separately prove grant timer/close settlement, stale callback preservation,
listener cleanup failure retention, pre-resolution reservation and llama/RPC conflicts.
Models/rates/adapters/rules/mapped network assignments remain controlled fixtures;
signatures/TCP/files/owned child processes/persistence are real. No physical cluster
or privileged OS firewall/service acceptance is inferred.

Full final1555passed/19skipped60.87s,4existing Starlette warnings
(artifacts/prima-resource-python-final.log;49553terminal0). Supported package rebuild
exits0; native_availableTrue and installed resource module import pass. Skill/connect
55passed/2skipped2.90s (prima-resource-skills-final.log). Canonical/project Codex skill
SHA256 868537e6c934a2afaf531d07e3ce12092e5e6ae8a48351e19702a0294e31dac2. Ruff/whitespace checks pass. All local test/build handles are
terminal. No native source changes: published cac12883afb9dd91b247deb61cb8245806bea4fd,
recipe24/digesta4f1d9fb1cb431a4be83f5679c26dfa965c4ba8e2f18a77fe5ee0dd9786610f5
remain unchanged, so no additional fork push is needed. Root changes remain uncommitted.

T017/T021/T066 remain open for additional feasible candidate profile/contracts/edges,
GPU-output logits measurements and remaining settings/layouts. Broad API/model/assets/
platform/layout and local correctness/performance acceptance remain open. Physical
distributed cluster gates and validations will be tested later. All qualification
scopes/gates stay disabled and the full integration goal remains active.


### Vocabulary-width GPU output costs - 2026-10-12 (T071)

Published Prima fbed5cbb3beacd2d68284182ab89fc00ba1fdca1 is consumed by recipe25,
digest bac8b1c00aae9917a66535322f7f832e9499ed1a496f7c4078f0c51d34d17074.
Fresh artifacts/prima-logits-published25 source is clean; capture verifies staged
hashes. RuntimeID816885e7731ceaa614ba9a33c1808eb02ce60be6f74238559d4e63b1631b8bef.
logits_transfer_profile_v1 reports completed_f32_logits_v1 F32 downloads at actual
vocabulary width, retaining five checked positive samples. The consumer refuses
partial/downgraded evidence and checks exact bytes against GGUF embedding dimensions.
It distinguishes connected local and split distributed output graph handoffs.

Native contracts3/3 pass12.52s (prima-logits-native-contract.log); physical selected
RX9070XT checks 524292/607744-byte downloads plus existing embedding widths
(prima-logits-vulkan-contract.log). Fresh published managed dense/MoE jobs prepare
28/48-layer costs and607744-byte logits downloads, retaining0 processes
(prima-logits-managed-published25.json/log). CI38204746334/38204744313 both completed
successfully across Windows/Linux/macOS CPU and Windows Vulkan compilation.
Those jobs do not establish other-platform physical GPU acceptance.

Before candidate-edge changes: focused69pass11.48s; full1567passed/19skipped61.63s
(prima-logits-python-final.log); supported package rebuild completes/native_availableTrue
and recipe import verifies; skill/connect55pass/2skipped2.97s. Canonical/project skill
copies then matched b8ba9bca414a74986b307cdd9d979b4754a7d6bf1801c1089f67b16969fa6e86.
Profiler receipts do not qualify generation performance, broad compatibility or
placement. T017/T021/T066 retain candidate/settings/layout work. Physical distributed
cluster gates and validations will be tested later. All qualification remains closed.
Root changes remain uncommitted; published native source remains clean.


### Feasible candidate private-link coverage - 2026-10-12 (T072)

Explicit distributed include_profile preparation now derives additional directed
profile_edges from the real capacity candidates, honoring node_priority/required_nodes.
Only representable same-model candidates among already prepared participants at
unchanged private addresses can add pairs. A bounded typed field binds the approval
to the immutable execution manifest; unique/non-self/known endpoints are required.
Empty metadata is omitted, preserving legacy canonical bytes/digests. No execution
rank/layer/port/transfer edge or native firewall rule is widened. Private diagnostic
collection measures the union once per directed pair using the existing signed,
bounded, leased and resource-owned protocol. Heartbeats advertise link version2;
older agents refuse before assets if extras are needed, while execution-only version1
preparations stay compatible. Preview rejects duplicate/unapproved receipts and
retains capacity-first selection for incomplete evidence, without profile/cache writes.

The new four-agent signed preparation measures worker-a -> worker-c after excluding
worker-b from a feasible ring. All four candidates receive estimates. Removing only
that extra receipt restores the original capacity-first plan. Version1 refusal occurs
before remote assets/profiles. Pairing stays master-pinned; scoped peer delegation
creates no permanent worker-worker pairing. Unit coverage proves private-address drift,
unprepared participants, user constraints, duplicate/self/foreign endpoints and
legacy serialization refusal/preservation. Fixture models/rates/adapters/assignments
remain controlled; signatures, actual TCP, files and owned profile processes are real.
Physical distributed cluster gates and validations will be tested later.

Initial focused checks exposed fixture omissions: absent execution-edge declarations,
missing registry.close and worker_rpc wiring, and a default parallel setting outside
the typed one-slot scope. Fixtures now provide those actual contracts; production
refusals were preserved. Corrected final focused135passed/3skipped39.84s
(prima-candidates-final-focused.log). Full1580passed/19skipped61.89s,4existing Starlette
warnings (prima-candidates-python-final.log). Supported package rebuild exits0,
native_availableTrue and ProfileEdge/current recipe imports pass. Skill/connect
55passed/2skipped2.83s (prima-candidates-skills-final.log). Ruff/whitespace pass;
canonical/project skill SHA25697b42b121909c6200b9c89d4eac234ca0d7ab0e91e5c4d803ce60482765a1ea7.
All local test/build handles are terminal. Prima source stays published/clean at
fbed5cbb3beacd2d68284182ab89fc00ba1fdca1; recipe25 is unchanged and no new fork push
is required. Root changes remain uncommitted.

T017/T021/T066 remain open for candidate participants/devices not covered by the
selected preparation and remaining settings/layout/profile coverage. Broad API,
model/assets/platform/arbitrary placement and local correctness/performance acceptance
remain required. This completes candidate-edge coverage among prepared participants,
not the full integration or any physical cluster gate. Qualification scopes/gates
remain disabled and physical distributed acceptance will be tested later.


### Published MoE indexed-kernel continuation - 2026-10-12 (T073, validation in progress)

Prima b7d101e85af5450e6138c969aacfc7675cc9aa94 is committed/pushed and remote-verified
on codex/kluster-integration. Kluster recipe26 digest
7e16cf32c4ce985aff4f0dacba3457bb3a7f5479dcae5565a00ece5c862a69fb consumes that pin.
Vulkan selected-expert vector execution now covers one..eight decoding tokens and
actual padded ID row strides, with distinct token/input/output offsets for input
and down projections. Larger batches retain grouped matrix execution. Its odd final
row tile now refuses out-of-range stores; vectorized selected-row reads are bounded.
The original general path's35-row nine-token cases show approximately6..8 absolute
errors from overwriting following expert rows. The corrected path passes.

Expanded contracts compare decoded stored operands, exact CPU-Q8-representable signed
inputs, padded ID rows and repeated selections across tokens. General Vulkan quantized
matrix F16 decoding uses an explicit L1-based rounding bound; small vectors retain
F32 tolerances. All13 supported indexed formats pass208cases on CPU and the selected
physical RX9070XT (one/two/eight/nine tokens, one/eight experts, input/down projections).
Native CTest3/3 passes13.97s; actual GPU operation/embedding/logits contracts exit0
(prima-moe-smallbatch-verified-cpu.log/prima-moe-smallbatch-verified-vulkan.log).
Initial fractional CPU fixtures did not account for internal dot-input quantization;
inputs were corrected rather than changing production arithmetic. Later all-format
fixtures make existing general-matrix F16 rounding explicit. Both failures and final
logs are retained. After the all-format run, only explicit include headers/comments/
documentation changed; the fresh published build will recompile the exact commit.

The unchanged published25 model token diagnostic completes and proves matching raw
short130/long68 generated counts but chat long134baseline/71Prima, with identical
output text and different actual generated token pieces
(prima-recipe25-moe-token-diagnostic.json/log). This remains a correctness failure;
no usage retokenization or threshold relaxation is allowed.

The developmental strict five-pair MoE benchmark is live (session62374). It uses the
pinned25 capture with the explicitly labelled development binary; it cannot qualify
published26. CI38207372718/38207368689 are live at the published SHA. Fresh managed26
build, source/artifact capture, model-backed gateway and final paired acceptance are
pending. Do not treat those handles as terminal or restart on observation timeout.
All qualification stays disabled. Physical distributed cluster gates and validations
will be tested later. Root changes remain uncommitted and the full goal remains active.


### Recipe26 managed verification - 2026-10-12 (T073 follow-up)

Fresh artifacts/prima-moe-smallbatch-published26 source is clean at published
b7d101e85af5450e6138c969aacfc7675cc9aa94. Capture verifies staged binaries and source;
runtimeID1f2fba8ffb795a76fdd7564169e210e7d2cc949cf218efc7f9d0c9397a1a2d94,
recipe26/digest7e16cf32c4ce985aff4f0dacba3457bb3a7f5479dcae5565a00ece5c862a69fb.
CLI693e6542d7cbe571c737b42fa5f2c96906d24201eaf48c84375c4250f74d3614;
server08970d9ee6fe59d3ef36150d848ffc128f486be42cbf5ea087878230c21b89d1;
profiler87a174eb0585c5223964d5cde0154303044bd27cee13133b675d936452410421.
Native published CTest3/3 passes14.16s; all208 indexed GPU cases pass on the selected
RX9070XT with0 failed comparisons, alongside prior matrix/embedding/logits tests
(prima-moe-smallbatch-published26-{cpu,vulkan}-contract.log). No source patch or
artifact restamping is applied. Both CI38207372718/38207368689 complete all4jobs.

Fresh production managed dense/MoE typed jobs prepare28/48-layer reports and retain
0 processes (prima-moe-smallbatch-managed-published26.json/log). Published dense
normal authenticated gateway/profile/logprobs passes38checks; published two-slot
MoE gateway/logprobs passes36checks. These isolated candidate-policy exercises
never change committed qualification or the user's cluster/home.
Full Python1580passed/19skipped63.75s,4existing Starlette warnings
(prima-moe-smallbatch-python-final.log). Supported package rebuild exits0,
native_availableTrue/current recipe digest import verifies; skills55pass/2skipped4.01s.
Canonical/project skill SHA25673158af6e39f8b56822de0b9b16a85e0472052bf941709f376ff29aa68e0b1a1.
Root/native whitespace checks pass. Both Prima checkouts are clean; root work stays
uncommitted. Completed build/test/profile/gateway handles are terminal.

The strict DEVELOPMENT five-pair MoE matrix completed (62374terminal1). Short1/2
throughput ratios0.743/0.749 and long1/2 ratios0.798/0.908 are below0.95; all cases
fail acceptance. Long generated counts still differ. Its placement probe passes;
telemetry remains disabled, output bytes agree and no OOM is observed. Compared
with prior matched recipe20 concurrency2 ratios0.409/0.495, these are improved
observations, not qualification or proof of matched long-token quality.
The final PUBLISHED26 five-pair benchmark is live, session45701, writing
prima-moe-smallbatch-published26-benchmark.json/log. Retain/poll this exact handle;
no terminal status or performance pass is inferred from the development result.
T073 delivery validation remains open until this receipt settles. T032/T039 and
broader compatibility/settings/placement/platform/profile work remain open. All
qualification gates/scopes remain disabled. Physical distributed cluster gates
and validations will be tested later. The full integration goal remains active.


### Recipe26 final paired receipt - 2026-10-12

The exact published26 benchmark finished (45701terminal1), with matching placement
probes, disabled telemetry and five alternating pairs. Short1/2 throughput ratios
0.746/0.741; long1/2 ratios0.804/0.906. TTFT ratios0.187/0.507/0.691/0.754 pass their
threshold, but all workloads fail local acceptance because throughput is below0.95
and long generated-token counts differ. Model output bytes agree and no OOM is
observed. See prima-moe-smallbatch-published26-benchmark.json/log and placement.json.
T073's implementation/delivery/measurement work is complete; T032/T039 acceptance
remains open. No gate or scope is enabled. All recipe26 handles are now terminal.
Physical distributed cluster gates and validations will be tested later.

A further MoE expert-reduction change is being developed in the Prima worktree.
It replaces the eight-expert add chain with a packed reduction and adds bounded
SUM_ROWS row dispatch/canary contracts. It is not published or consumed by recipe26;
its operation/model/performance checks remain pending. The recipe26 receipt remains
an immutable result from the previously clean published source.


### Device-memory expert aggregation - 2026-10-12 (T074 in progress)

Prima semantic commit fd24c9d325ac19122d91773237cac7bf47360ced and documentation-only
follow-up3e8ee697854e624d4485c552406205f9f9926eeb are published/remote-verified on
codex/kluster-integration. Recipe27 digest
04fb9c274b89bca124110811612c86e4a528467520b0b1fcc704345f9b338ad9 pins the latter.
The new native llama_sum_experts packs the expert axis and reduces device-memory
weights with at least4 selected experts. Host weights retain the original add chain;
one..three experts retain their path. This preserves CPU prefixes in partial offload.
SUM_ROWS explicitly binds/bounds output row count before surplus workgroups access
storage. The neighboring-tensor canary also passed the unguarded run on this robust
GPU driver; no reproduced corruption claim is inferred from that run.

54 signed/odd-width/multi-token reduction cases and existing208 indexed matrix cases
pass on CPU and selected RX9070XT. Native CTest3/3 passes14.40s. Completed five-pair
operation samples show CPU serial0.0052ms versus packed0.0485ms; GPU serial0.0699ms
versus packed0.0641ms. Those observations motivated host-weight path preservation,
not a model-level performance claim. Both the initial packed-all and corrected
host-preserving development MoE two-slot gateways pass36checks. Initial development
benchmark25475 was explicitly stopped due to the measured CPU regression, terminal15;
owned descendants settled and authoritative inventory finds0 matching native/benchmark
processes (prima-moe-reduction-aborted-benchmark.json). The first cleanup pass found
its second matching interpreter already gone; final read-only inspection records
settlement. No benchmark receipt or pass is claimed for that aborted run.

Both exact-pin CI38211686599/38211684084 succeed across all4CPU/Windows-Vulkan jobs.
Full Python1580passed/19skipped71.80s,4existing Starlette warnings
(prima-moe-reduction-python-final.log). Fresh managed27 installer is live, session77113;
published source/artifact capture, native/GPU/model jobs, package and final matched
performance acceptance remain pending. Do not restart the installer on observation
timeout. No qualification gate/scope is enabled. Physical distributed cluster gates
and validations will be tested later; broad compatibility/settings/platform/placement/
profile and local correctness/performance work remain open. Root work is uncommitted.


### Recipe27 managed verification - 2026-10-12

Fresh artifacts/prima-moe-reduction-published27 is clean at published
3e8ee697854e624d4485c552406205f9f9926eeb. Capture verifies staged source/artifacts;
runtimeID1936a1d21b1d2225e6782b1c28d93da3fc1b66804312a87aff97660651c3b37b,
recipe27 digest04fb9c274b89bca124110811612c86e4a528467520b0b1fcc704345f9b338ad9.
CLIcd9f10cb9c47dc3c70fc6ab0efc608d0e5c236dc55a68b6cf983ef70da09cd12;
server76f3edc7712b40c84ed49ba8268ba595b99f70bf5eea131597bfd982fda78e90;
profiler7846c70916bbbd92ecb3f3904378ac884b6b2fc5522e6429ed46d56737ec49ef.
Actual published CTest3/3 passes17.38s,54 reduction and208 indexed GPU comparisons
pass with0 failures (prima-moe-reduction-published27-native/vulkan-contract.log).
Published GPU operation timing is serial0.0737ms/packed0.083ms, reversing development
samples. These short samples are variable and establish no performance gain. The
full matched model benchmark must determine retention, rather than assuming fewer
nodes imply faster execution. All input/bounds/numerical checks remain distinct.

Both supplied production ProfileJobs prepare28/48-layer costs and retain0 children
(prima-moe-reduction-managed-published27.json/log). Published dense authenticated
gateway/profile/logprobs passes38checks. Published two-slot MoE gateway is live,
session68261. Final strict paired benchmark remains pending. Package rebuild exits0,
native_availableTrue/current digest import verifies; skills55pass/2skipped4.43s.
Canonical/project skill SHA256377821fa3ece42a01815b159506b59c150f244077c7912c010af822ae577e0f1.
Full Python1580pass/19skipped71.80s and exact-pin CI38211686599/38211684084 succeed.
Root/native whitespace checks pass. No source patches or artifact restamping apply
on the managed path. All qualification remains closed. Physical distributed cluster
gates and validations will be tested later. T074 final model/timing validation and
broader local correctness/performance/compatibility/settings/platform/placement/
profile work remain open. Root changes are uncommitted; the full goal remains active.


### Recipe27 published gateway completion / paired assessment live

Published27 dense gateway/profile/logprobs passes38checks; published27 MoE two-slot
gateway/logprobs passes36checks. Both runs are terminal0 and retain their actual
candidate-policy boundaries. No committed qualification/home/cluster state changed.
The final matched five-pair benchmark is live, session86076, writing
prima-moe-reduction-published27-benchmark.json/log. Poll this exact handle rather
than restarting on timeout. It must determine whether the variable operation timing
supports retaining the packed reduction. T074 remains open until the final receipt
settles. All other build/test/profile/gateway handles are terminal. Root/native
whitespace checks pass. Both native checkouts are clean at the published pin.
Physical distributed cluster gates and validations will be tested later. Local
correctness/performance, broader compatibility/settings/platform/placement/profile
requirements and full integration remain open; qualification scopes/gates stay disabled.


### Recipe27 final paired failure - 2026-10-12

Exact published27 final benchmark completes,86076terminal1. Five alternating pairs
and placement probes retain identical settings/model/device and disabled telemetry.
Short1/2 throughput ratios0.723/0.722; long1/2 ratios0.722/0.829. TTFT ratios
0.240/0.655/0.728/0.729 pass, but all cases fail throughput and long generated counts
still differ. Output bytes agree; no OOM is observed. See
prima-moe-reduction-published27-benchmark.json/log and placement receipt.
The packed candidate regresses against published26 ratios0.746/0.741/0.804/0.906,
so short operation timing cannot support retaining it as a speed improvement.
T074's delivery/measurement is complete, not local acceptance or retention approval.
All27 handles are terminal. Qualification stays disabled. Physical distributed cluster
gates and validations will be tested later. The Prima development checkout is now
working on a direct-stride Vulkan reduction that avoids packing and preserves the
legacy serial addition order; its checks remain pending and are not recipe27 evidence.


### Direct-stride Vulkan reduction - 2026-10-12 (T075 in progress)

Published/remote-verified Prima f13088f3c8bc02f5f72c2e1cb6c09de632bbe202 is pinned by
recipe28 digest a8c119b84ac5491a90edf20b4461c18ebe79b420e72dce3b60e67d424e728e7a.
Vulkan4..32-expert device weights now reduce directly from the permuted F32 view,
with one lane per embedding/token output and legacy serial additions. Host paths
remain unchanged; other device paths retain their existing behavior. The bounded
strided shader checks dimensions/stride widths/input view alignment, binds exact
byte ranges and tiles dispatch within device limits. Vulkan-buffer interface identity
selects this path. Unsupported strided widths refuse GPU support.

54 reduction and208 indexed cases pass on CPU and selected RX9070XT, including
unaligned source views, odd widths, multiple experts/tokens, canaries and cancellation
values. The selected/legacy result1 versus packed result4 demonstrates observable
rounding preservation. Final native CTest3/3 passes14.38s; completed alternating
operation samples report serial0.0858ms/packed0.0739ms/direct0.0756ms
(prima-moe-strided-reduction-checked-native/vulkan.log). Earlier rounds vary; no
model-level speed or qualification is inferred. Development two-slot MoE normal
gateway/logprobs passes36checks. Incremental shader addition first failed because
old generated declarations lacked the new symbols; CMake now tracks both .comp
additions and the generator executable, and ordinary reconfiguration/build succeeds.
No generated artifact is manually restamped or consumed as a published pin.

Fresh managed28 build is live,62407; full Python is live,95393; exact-pin
CI38222199242/38222194254 are live. Published source/artifact/model/contracts/gateway/
paired validation remain pending. Poll these exact handles. Qualification remains
closed. Physical distributed cluster gates and validations will be tested later.
The complete integration and broad compatibility/settings/platform/placement/profile/
local correctness/performance work remain open. Root work is uncommitted.


### Recipe28 managed verification - 2026-10-12

Fresh artifacts/prima-moe-strided-published28 source is clean at published
f13088f3c8bc02f5f72c2e1cb6c09de632bbe202. Source/artifact capture verifies;
runtimeIDe59467f6bb4bfba2be597e36d87a64dec8e234639058bbd67ad7c3a3c3751f49,
recipe28 digest a8c119b84ac5491a90edf20b4461c18ebe79b420e72dce3b60e67d424e728e7a.
CLI5c1317219f68bb2dd8f04028a96937121127286fe251b7ea89eb1678b09b7240;
server7a4b0b6ce9d003500368b6dc47cfa5ce26dbf7ce043f0f6cca3e1ab4a41c41ea;
profiler3324fadbf4b90d3b759d86e23d94150107f1422a33cd217d0f185f6dbb3a4d5d.
Published CTest3/3 passes14.45s; selected RX9070XT54 reduction/208 indexed cases pass
with0 failures. Cancellation-sensitive direct/serial result1 versus packed4 verifies
legacy F32 order. Short completed rotation medians0.089ms serial/0.0812ms packed/
0.0748ms direct remain operation observations, not model qualification
(prima-moe-strided-published28-native/vulkan-contract.log).

Both production supplied-model jobs prepare28/48-layer typed costs and retain0
children (prima-moe-strided-managed-published28.json/log). Dense published normal
authenticated gateway/profile/logprobs passes38checks. Two-slot published MoE gateway
is pending. Exact-pin CI38222199242/38222194254 both pass all4CPU/Windows-Vulkan jobs.
Full Python1580pass/19skipped66.66s; package rebuild exits0/native_availableTrue and
current digest import verifies; skills55pass/2skipped4.44s. Canonical/project skill
SHA2564959463002c89855fdd215cbc92ae4e434b59653ab1612479a94bd91de5cfae9.
No hidden source patch/restamping applies. T075 published model/gateway/paired checks
remain open. Physical distributed cluster gates and validations will be tested later.
Qualification scopes/gates remain disabled; broad implementation/local acceptance
work and the full goal remain active. Root work stays uncommitted.


### Recipe28 published gateway completion / paired assessment live

Published28 dense gateway/profile/logprobs passes38checks; published28 two-slot
MoE gateway/logprobs passes36checks. Both runs are terminal0, with isolated candidate
policy and no committed qualification/home/cluster changes. Native/GPU/source/artifact/
profile/package/Python/skill/CI checks are complete as recorded above.
The exact published28 five-pair matched benchmark is live,81172, writing
prima-moe-strided-published28-benchmark.json/log. Retain/poll this handle; no final
performance pass is inferred from earlier operation observations. All other local
handles are terminal. T075 final paired validation remains open; broader API/model/
assets/settings/platform/arbitrary placement/profile/local correctness/performance
requirements still remain. Both Prima checkouts are clean at the published pin and
root/native whitespace checks pass. Qualification scopes/gates remain disabled.
Physical distributed cluster gates and validations will be tested later. Root work
is uncommitted and the full integration goal remains active.


### Recipe28 final paired failure - 2026-10-12

Exact published28 matched five-pair benchmark completes,81172terminal1. Short1/2
throughput ratios0.722/0.724 and long1/2 ratios0.754/0.841 remain below0.95. TTFT
ratios0.196/0.309/0.685/0.754 pass, but all4cases fail; long generated counts still
differ. Output bytes agree, placement probes pass, telemetry stays disabled and no
OOM is observed (prima-moe-strided-published28-benchmark.json/log). Direct operation
microtiming cannot support default model selection. T075 delivery/measurement is
complete; local acceptance remains open. All28 handles are terminal. The development
fork now restores original model expert-add aggregation while retaining separately
validated strided-operation support, and adds a shared generation/template-rendering
endpoint plus exact per-engine prompt-token diagnostics. Those next changes remain
unpublished/unqualified. Physical distributed cluster gates and validations will be
tested later. All qualification remains closed and the complete goal remains active.


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

### Optional Vulkan diagnostic correctness repair - 2026-10-12

Published Prima commit 9f548d2fc88d7ab556f67f9324e5c3dd6e8e1529 repairs the
optional GGML_VULKAN_PERF path: timestamp queries retain normal batching, check
support/availability/wrap/units, have bounded per-backend allocation, and report
unavailable GPU work explicitly. Diagnostic servers advertise a dedicated
capability; assessment refuses them before acceptance model/placement startup.
Development capability probes are bounded and hash-checked, and owned server
logs survive failures and cleanup. This is a correctness and evidence-integrity
repair, not performance acceptance.

The prior instrumented path failed nine reduction checks and printed empty timing
tables. The repaired instrumented path passes 54 reduction and 208 indexed checks.
The ordinary non-instrumented build also passes native contracts and selected
GPU checks; its diagnostic capability and timing records are absent. Python:
1588 passed, 19 skipped; focused diagnostic/skill checks: 63 passed, 2 skipped.
Ruff passes, supported package build succeeds and kllm._native imports.

Recipe32 pins the published commit with digest
b13b3e5029466a413865b387a92b2f992a8e9d62628314572ead8fe6f92d2f87.
Fresh managed build verification passes: clean published source, exact artifact
hashes, correct recipe identity and no diagnostic timing capability. No local performance acceptance
or new performance-gate diagnostic experiment is run. Performance and physical
distributed validation remain deferred to the user's three-computer live cluster.
All qualification flags remain false and execution scopes remain empty.

Receipts: artifacts/prima-vulkan-timing-before.log,
artifacts/prima-vulkan-timing-final-contract.log,
artifacts/prima-vulkan-diagnostic-normal-contract.log,
artifacts/prima-vulkan-diagnostic-normal-capabilities.json,
artifacts/prima-diagnostic-python-tests.log,
artifacts/prima-diagnostic-focused.log and artifacts/prima-diagnostic-package.log.

Recipe32 managed gateway correctness: dense: 36 gateway correctness checks pass; moe: 36 gateway correctness checks pass.
The MoE run uses two parallel slots. No paired performance run is executed.
Published PR1 head is verified as 9f548d2fc88d7ab556f67f9324e5c3dd6e8e1529.
Portable CI runs38242934671/38242929739 were still in progress at handoff;
no CI pass is claimed. All local build/test/model processes are settled.

### Full Jinja template implementation - 2026-10-12

T080 completes the native server Jinja rendering path and Kluster preset/custom
translation. The parser/runtime/builtins and JSON/unicode helpers are vendored
from pinned baseline7fe450e19305b828c199d602c23a8337aaa1f03b with its MIT license
and explicit provenance. The server defaults to full embedded Jinja, supports
inline/file sources, bounds templates to64KiB, validates before readiness, shares
apply-template/generation rendering, normalizes typed text, supplies template
kwargs/token/tools context, and isolates cached programs from request contexts.
Interactive native CLI Jinja, input-origin token marking and full automatic
native tool parsing remain separate work, not passes from this change.

Kluster freezes bundled preset bytes into signed manifests and binds the source
to exact qualification settings. Only rank zero renders; workers consume its
resulting token stream. Native jinja_template_v1 negotiation precedes execution
preparation. Existing legacy/default selections are retained.

Development native contracts4/4 pass, including256 concurrent template contexts;
all six bundled presets match Python Jinja in24 cases. Four actual-model default
prompt/token cases match the pinned baseline exactly. Dense preset/default gateway
checks pass36 each. Python1592 pass/19skip; focused template/Windows checks52 pass.
MoE custom-template correctness, publication and fresh managed consumption are
in progress. No performance acceptance or performance-gate diagnostic is run.
Physical distributed and performance validation remains deferred to the user's
live three-computer cluster; all qualification remains unpassed.

### Published Jinja source and managed consumption

Prima feature commit: cfca7ee1f7a36001680f0ab7bf3098793ab7dd85.
CI-only follow-up / PR1 head: ae9ee59caaa6e8b974bd5ff0cf1b1d47a9d3a8c7.
The follow-up adds the template executable to explicit CI build targets and
changes no native runtime source. Recipe33 intentionally consumes the immutable
feature commit, with no hidden patches. Digest:
7e70746e8805c2ba56b257251bdceb4655bf442a578c6df5d6b5934edaa829b1.
Runtime ID: b5c2a7171100ac9566c1137c589cfa83e1ef281d57a6b88e5fac3a5d57551900.
llama-cli.exe: 18571df3a33ade2d7f469fc4260a00679e6cb23dccdabe3d6f526a5570626eb5
llama-server.exe: 9a491f1b21644a4a807dd00bba9ef072d8cfd73bfc852f57cb856e179fad44f1
profile-tool.exe: b11e5ae5f1578b67e7d9bd1d62319f97a281f1e2405217d090b4933a6af47433
Fresh source cleanliness, hashes and actual Jinja/non-diagnostic capabilities
are verified. Native contracts4/4 pass; 24 preset reference cases pass; published
large custom dense gateway37/37 passes, including owned file/props checks.
The Windows launch path uses an exclusively created content-named template file
inside the owned run directory, keeping full64KiB source off the command line.
Changed existing files are refused without overwrite. Final Python1593 pass/19skip,
focused template/lifecycle/Windows76 pass/1skip, owned skills60 pass/2skip. Supported
package rebuild/native import and Ruff pass. Source-controlled qualification
flags remain false and execution scopes remain empty. CI and the final default
MoE gateway are in progress; no performance gate is run or claimed.

### T080 final published correctness receipts

Recipe33 / cfca7ee managed build: four native contracts pass; six bundled presets
match independent Python Jinja in24 cases; four actual-model default prompt/token
cases match the pinned baseline. Published dense custom large-template gateway:
37/37 checks pass. Published MoE default embedded-template gateway with two slots:
36/36 checks pass. This completes the scoped server/preset/custom implementation
and immutable consumption step, not the broader T013/T018/T032 release scope.

Receipts: artifacts/prima-jinja-published33-contracts.log,
artifacts/prima-jinja-published33-presets.json,
artifacts/prima-jinja-published33-default-prompt-check.json,
artifacts/prima-jinja-published33-large-dense-gateway.json,
artifacts/prima-jinja-published33-default-moe-gateway.json,
artifacts/prima-jinja-final-python-tests.log and artifacts/prima-jinja-package.log.
All local owned build/test/model processes are terminal. CI-only headae9ee59 runs
38246808567/38246800110 are still live at handoff and are not counted as passes.
Prior recipe32 CI runs38242934671/38242929739 both completed successfully.

Current launch inventory (parser presence, not behavioral qualification):
artifacts/prima-jinja-published33-compatibility.json. Seventeen baseline switches
remain absent, covering cache/checkpoint controls, speculative draft controls,
KV/SWA modes, device/tensor/MoE placement and reasoning controls. Full automatic
tool parsing/input-origin token marking and broader architecture/platform paths
also remain open. Continue these implementation tasks without performance-gate
validation. Physical distributed and performance acceptance will be tested later
on the user's live three-computer cluster; thresholds/gates remain unchanged.

### Named tool streaming continuation - 2026-10-12

T080 template CI runs38246808567/38246800110 completed successfully on all four
configured variants. T081 implements named forced-tool streaming for native Chat,
Responses and Messages: stable call/item IDs, argument deltas in the correct
channels, terminal usage, incomplete/length termination and error settlement.
Native contracts exercise fragmented escaped arguments, genuinely incomplete
JSON, empty terminal output and errors after stream start. Automatic tool choice,
parallel automatic calls and history conversion remain broader T013/API work.

An initial dense correctness run passes normal/limited streams but catches a
missing logprob when a scored token carries no argument bytes. The corrected
stream preserves that receipt, including framing/UTF-8 buffering. This is a
correctness fix, not a performance measurement. Rechecking model correctness,
publication and fresh immutable consumption are in progress.
Performance and physical distributed validation remain deferred to the user's
live three-computer cluster; no qualification is granted by this work.

### Named function streams: corrected development checks

Dense gateway correctness passes43/43 checks, including success/partial-length
streams for Chat, Responses and Messages plus exact native Chat logprob accounting.
Native contract coverage passes, including escaped/fragmented and incomplete
arguments, empty completion, error settlement and scored tokens with empty deltas.
Python1600 pass/19skip; focused stream/diagnostic/Windows checks63 pass. Ruff passes.
The MoE two-slot correctness run is active. Publication/managed consumption is
pending; no performance gate is run or granted. Physical distributed validation
and performance acceptance will be tested later on the three-computer cluster.

### Published named-tool stream implementation

Prima commit3e41d4772ecfaff8c87ac5c140be456e42f4efa3 is pushed to PR1's
codex/kluster-integration branch. Recipe34 pins that exact commit with digest
2475fef22a8b0021acdeeda6a3c9a0253a8b0d01fba5ebd92d95118853d702a6.
Development dense/MoE gateways each pass43/43 checks, including seven new
named-tool stream checks across all three protocols and Chat logprobs. Native
regression checks pass; Python1600 pass/19skip; focused63 pass; skill checks62
pass/2skip; supported package rebuild, native import and Ruff pass.
Fresh managed build and immutable artifact consumption are in progress.
No performance gate is run or granted. All physical distributed and performance
acceptance remains deferred to the user's live three-computer cluster.

Recipe34 fresh managed correctness: native contracts4/4 and dense gateway43/43 pass.
Runtime ID: 4ba79a64335ea0244c0136a5f8f6c8a469316cd47f97aa20127c90613d09d5f1
llama-cli.exe: 8029d220297100a2b77da96fde084bac87da2917e242227331f42caa61bb23a3
llama-server.exe: ba56252bf4e3cdfd2f60ef888ddae1a92898df70525c0b34ffb227b6c64ada81
profile-tool.exe: c5d21bba121d8b871b0f870c5af36140916b34c5d852127eef5032e9fa9f0831
Source cleanliness, artifact hashes, actual named-stream capability and ordinary
non-diagnostic build are checked in prima-tool-stream-published34-manifest-check.json.
Fresh MoE gateway and PR CI are still in progress. No performance gate is tested.

### T081 final published correctness receipts

Recipe34 / Prima3e41d4772ecfaff8c87ac5c140be456e42f4efa3 is freshly built and
consumed. Immutable source/hash/capability checks pass. Native contracts4/4 pass;
published dense/MoE gateway suites each pass43/43 checks, including all three
named tool-stream protocols, genuinely partial termination and native Chat
logprob accounting. The MoE suite uses two parallel slots. Python1600 pass/19skip,
focused63 pass and skill checks62 pass/2skip; package/native import and Ruff pass.
CI runs38249549031/38249542832 both completed successfully on the configured
portable CPU and Windows/Vulkan compilation variants. All local owned build,
test and model handles are terminal; no performance gate is run or claimed.

Receipts: artifacts/prima-tool-stream-published34-manifest-check.json,
artifacts/prima-tool-stream-published34-contracts.log,
artifacts/prima-tool-stream-published34-dense-gateway.json,
artifacts/prima-tool-stream-published34-moe-gateway.json,
artifacts/prima-tool-stream-python-tests.log,
artifacts/prima-tool-stream-focused.log and artifacts/prima-tool-stream-package.log.

T081 completes named forced-tool streaming, not automatic tool choice, parallel
automatic tool parsing or tool-result/history conversions. T013/T018 and broader
model/launch/settings/placement/platform/profile work remain open. Performance and
physical distributed acceptance will be tested later on the user's live cluster
of three computers. Thresholds remain unchanged, all qualification flags remain
false, and all execution scopes remain empty.

### Text tool-history continuation - 2026-10-12

T082 maps Chat tool-call/result messages, Responses function_call/function_call_output
items and Messages tool_use/tool_result blocks into a shared checked history.
Call IDs and consecutive/parallel historical call groups are preserved. Typed text
results use baseline newline joining; missing/null assistant content is normalized.
Jinja argument representation follows detected template capabilities. Templates
that cannot render calls and unsupported non-text content refuse rather than
silently losing history. Stored response state and new automatic tool choice remain
separate implementation work.

Kluster now classifies Responses input-item history and Messages tool blocks as
tool_turns, preserves nested vision requirements, and handles Anthropic none choice
without claiming automatic tools. An initial model suite passes all six new
history-continuation cases but catches an HTTP422/400 invalid-input boundary change;
that regression is corrected. Full regression/model checks are in progress.
Native history contracts and54 focused Python checks pass. Performance and physical
distributed validation remain deferred to the user's live three-computer cluster.

### Published text tool-history implementation

Prima3707d0adbce5e8772b622777601a2856ca836e0e is pushed to PR1. Recipe35 pins
that exact source, digest e07b55484ec4c6c012cfd3cdf7e34f310486852c64cc006446a14806da189faa.
Development dense/MoE gateways pass49/49 checks each, including real generated
call IDs reused in text and streaming continuations across all three protocols.
Three actual-model history prompt/token cases match the pinned baseline, including
null assistant content, typed results and parallel historical calls with Unicode
arguments. Native contracts pass. Python1606 pass/19skip, focused54 pass, skills61
pass/2skip, package/native import and Ruff pass. Fresh managed consumption is in
progress. Automatic tool choice, multimodal history, stored response state and
broader model/launch/placement/platform work remain open. No performance gate is
run or granted; physical/performance acceptance remains deferred to the live cluster.

Recipe35 fresh managed source/hash/capability checks, native contracts4/4 and
published dense gateway49/49 pass. Runtime ID: 0bd3e076b2006ff6a2d56a818fd3d3e17d42876542dcdebd631860a19366b45d
llama-cli.exe: 47930ab9f7d10c4a604827b2e1150ed7dd201b405c9c2d605332300103b3d106
llama-server.exe: 9274704927557652000b40577fbfba11b837bdf580cc89a6b31d224ea5bcd28b
profile-tool.exe: 795e92618a0829c3335efc55040635f6a2d28529b11c51b4b2505879eebaf20e
Fresh MoE continuation and published baseline prompt checks are still pending.

### T082 final published correctness receipts

Recipe35 / Prima3707d0adbce5e8772b622777601a2856ca836e0e is freshly consumed.
Source/hash/capability checks pass; native contracts4/4 pass; published dense/MoE
gateway suites each pass49/49 checks. The MoE run uses two slots. Three published
history prompt/token cases match the exact pinned baseline. Python1606 pass/19skip,
focused54 pass, owned skills61 pass/2skip, package/native import and Ruff pass.
CI38252725117/38252719776 both pass all configured CPU and Windows/Vulkan compile
variants. All local owned build/test/model handles are terminal.

Receipts: artifacts/prima-tool-history-published35-manifest-check.json,
artifacts/prima-tool-history-published35-contracts.log,
artifacts/prima-tool-history-published35-dense-gateway.json,
artifacts/prima-tool-history-published35-moe-gateway.json,
artifacts/prima-tool-history-published35-prompt-check.json,
artifacts/prima-tool-history-final-python-tests.log and artifacts/prima-tool-history-package.log.

This closes supplied text tool history for the tested tool-capable Jinja paths;
it does not close automatic tool choice/parallel generation, multimodal history,
stored Responses state or broad T013/T018/model/launch/placement/platform work.
Source-controlled qualification remains false with empty execution scopes. No
performance gate is run or passed. Physical distributed and performance acceptance
will be tested later on the user's live three-computer cluster, with existing
thresholds retained.

### Automatic parser foundation (recipe36, in progress)
Native ccc8c5947a0dd108f9c99f95af920ee5be9c160e imports the baseline
7fe450 chat/template analysis, PEG, schema and specialized format parsers with
MIT provenance and vintage API adapters. Local bridge returns serialized parser
preparation/output; native character grammar gains lazy triggers and sampler
clone/reset. A generated nullable repetition overflowed the old grammar: repeated
per-call parsers now consume a marker and transitive nullability rejects unsafe
cycles. Development native5/5 and dense vocabulary acceptance pass. Automatic
HTTP serving is still refused and unadvertised. Recipe36 digest is
2baf75ebf10ec12ad9bf6122a39bdec2d717cab41de2dadfb986080cdb1d5a67.
Fresh immutable build is pending. The development MoE suite passed47 requests/
checks, then its restart admission refused after this agent changed the recipe
while the assessment was active. Preserve that failed receipt; repeat the full
lifecycle against the settled published pin. No performance acceptance was run.
All physical distributed/performance validation remains deferred to three PCs.

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

### T084 development progress
Native automatic/required tools now prepare prompts, lazy/regular grammar,
preserved markers and baseline parsing through all three HTTP APIs. Incremental
streams cache per-call IDs, separate reasoning/text/arguments, use dynamic output
indices, and retain terminal usage, partial length/error settlement. Transitive
nullable grammar checks remain in place; single-call preparation no longer repeats
per-call markers when parallel is disabled. Explicit grammar parse failure refuses
instead of silently disabling constraints. Raw completion/infill now reject reserved
internal parameters; nonstream parser failures return typed native server errors.
Kluster requires separate parallel_tools evidence and native automatic/streaming
capabilities after checking exact execution qualification. Initial dense/MoE85/85
checks pass with real generated tool-history continuations. New actual automatic
truncation, disconnect and reserved-parameter checks are pending. The first simple
automatic invocation prompt produces unmarked JSON on Prima and the exact baseline;
retain that model behavior as text, not a heuristic function call. Explicit tagged
requests check lazy activation; separate auto requests decline tools normally.
Published recipe remains36 until model checks settle. Performance/distributed gates
remain deferred to three computers and all qualification remains false/empty.

### Generated grammar prefill repair (native37828feb)
Published recipe37 source673cd92 initially passes native5/5 and portable CI, but
its clean dense100-case gateway suite exposes six thinking-on channel failures:
tool calls complete while a duplicate assistant header/chain appears as ordinary
content. Baseline sampling pre-accepts the existing generation prompt into regular
generated tool grammars. Prima's bridge omitted this step. Native37828feb156ab14082ce3569b2c8f6aa9d3012c6 adds an explicit generated-only token
prefill list, applies it once on initialization and again after reset, and leaves
raw/user grammars unchanged. A real Qwen vocabulary contract checks reasoning,
call parsing and repeated reset; development native5/5 and dense100/100 pass,
including automatic token logprobs and reasoning channels in every protocol.
MoE100-case development run remains active; root recipe stays37 until it settles.
The first diagnostic vocabulary fixture omitted the generated opening think tag;
its corrected fixture includes the actual tag and passes. Preserve both failed
receipts. Source qualification remains false/empty. No local performance gate is
run; performance and physical distributed acceptance remain deferred to three PCs.

### T084 final published receipts
Root now consumes recipe38/native37828feb156ab14082ce3569b2c8f6aa9d3012c6,
digest41a925c516f108d0fd19adae1cfb43d9b781ab2c68224e420069ea93753631b5,
runtime848714589332ebe83df1272d9c6b8dffcf403c2e3997c6778cf478bb4e296098.
Clean source/binary manifests, native5/5, dense100/MoE100, both vocabulary prefill/
reasoning/reset checks, Python1617/19skip (68.37s), package, Ruff and installed
skill sync pass. CI38269490124/38269494774 both pass all four build variants.
All owned local build/test/model handles are terminal. T084 is complete; broader
API/model/launch/placement/platform tasks remain open. Qualification is false with
empty execution scopes. Performance and physical distributed gates will be tested
later on the user's live cluster of three computers; no local performance gate
or timing-diagnostic extension was run.


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


T086 context: existing native draft loading clones the target execution manifest
back into the draft, overwriting requested draft GPU layers and rank isolation.
Recipe40 owned MoE-target/dense-draft reproduction exits before readiness and
logs the requested CPU draft on Vulkan0. Root launch currently refuses all
speculative settings. Native draft token acceptance also batches usage before
stop processing, omits per-token logprobs and needs KV rollback audited against the head-to-worker
sequence-ID offset (the existing slot.id worker mapping is intentional). Fix actual execution and managed asset translation, not parser
presence alone. Keep performance/physical cluster acceptance deferred.


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
