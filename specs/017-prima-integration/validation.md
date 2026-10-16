# Verification — 2026-10-10

## Spec 020 continuation — 2026-10-15

Recipe 133 pins fork `bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554`, release
`0.0.1`, digest `27782ffb7cc01334f7305e78b1fd40550ed157d9bf8668aa3674917db40c26cf`.
The dense `qwen35` port runs Saluki with 64 trunk blocks, separate recurrent state
and skipped MTP tensors. CPU/Vulkan greedy continuations matched the pinned
upstream reference; original IQ block checks and state replay/copy passed on the
Windows/RX 9070 XT host. No distributed/performance qualification is inferred.

A fresh isolated managed build (`5b2fefc0-625c-40fe-8c79-72635a93e668`)
prepared runtime `97baff6d71ecd9c3d73947a5b6bb2d51f9e8a0aab50d054371d5f9a63688a165`.
The normal managed Vulkan prepare/run/chat/stop cycle passed; the final Saluki
deployment was left running. The exact served ID is
`Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf`. Managed CPU launch remained refused by
the unchanged Vulkan-only participant policy, rather than bypassing it.

Latest full serial Python suite: 1741 passed, 19 skipped. Dashboard diagnostics
and production build passed. Full commands, artifact/model hashes, live readiness,
parallel-test caveats and remaining limits are recorded in
[spec 020 validation](../020-prima-qwen35-model-support/validation.md).

## Captured results

| Check | Result |
| --- | --- |
| `git merge-base --is-ancestor origin/main HEAD` | Passed; remote main d668eb7 merged as 1195bfe. |
| `uv run python -m pytest -q` | 1,216 passed, 14 skipped, 8 existing deprecation warnings; 25.06 s. |
| Latest `uv run python -m pytest -q` | 1,228 passed, 14 skipped, 8 existing deprecation warnings; 25.04 s. |
| `uv run python -m pytest -q tests/test_prima_integration.py` | 23 passed, including a real local controlled profiler subprocess. |
| Final worker/Prima boundary selection | 50 passed after adding refusal before worker firewall/process changes. |
| `npm --prefix dashboard test` | 63 module tests and 8 serial browser tests passed. |
| `npm --prefix dashboard run check` | 0 errors, 0 warnings. |
| `PYO3_PYTHON=<uv Python> cargo test --manifest-path crates/kllm-native/Cargo.toml` | 45 passed; no native source changes in this feature. |
| `uv run python scripts/build.py --skip-path-shim` | Passed; native extension/wheel and production dashboard rebuilt. |
| Final package rebuild with `--skip-ui --skip-path-shim` | Passed after the final worker gate change. |
| New and affected Python modules, `ruff check` | Passed. |
| Feature diff whitespace checks | Passed; original unrelated `src/kllm/__init__.py` trailing blank line was preserved and excluded. Literal unified patch context has scoped Git whitespace attributes. |

The final managed Prima installer was run with the pinned repository/commit, resolved
recipe digest, `--engine primacpp`, `--backend cpu`, `--from-source`, and isolated
`--runtime-dir /tmp/kllm-prima-managed-final`. It exited 0 and built/staged
`llama-server`, `llama-cli` and `profile-tool`. RuntimeRegistry verified those artifact
digests; the recorded recipe matched the recipe at that verification. This was a real macOS CPU
compile with downloaded, pinned ZeroMQ sources, not a mocked build.

The earlier Kluster wheel included all Prima Python modules, recipe JSON and source
patches. The native extension imports successfully. Existing uncommitted binding
stubs and unrelated whitespace were preserved; neither is used by the new feature.

## Limits

- No Vulkan model inference, Windows/Linux, mixed-node or distributed Prima
  generation result is claimed. The controlled subprocess test is not a real model
  benchmark.
- No before/after inference performance or model quality measurements were made.
- Current Prima help/source lacks baseline options including `--fit`, `--kv-unified`,
  `--ctx-checkpoints` and `--cache-ram`. Distributed execution assumptions also need
  modernization.
- The topology adapter and profiling/staging primitives do not constitute signed
  distributed rank execution or an integrated profile-driven planner. Those tasks
  remain open in tasks.md.
- The recipe's qualification gates remain false. No gate was marked successful
  based on a fixture, build, browser pass or fabricated benchmark.
- No live cluster network or inference process was changed. Kluster integration edits
  remain uncommitted; the Prima native changes were separately committed and published
  to the fork as recorded below.

## Mandatory Vulkan requirement

Vulkan GPU inference is now an independent mandatory qualification gate on Windows,
Linux and macOS (MoltenVK). The policy change changes the recipe identity, so previous
evaluation builds require a new resolution/build. Their CPU compile result remains
historical evidence and does not qualify Vulkan support.

Profiling now selects the matching accelerator metric instead of taking the faster
Metal/CUDA value. Missing, zero or nonfinite Vulkan measurements fail explicitly.
The pinned source now has an initial Vulkan Q4K/copy/bandwidth profiler path, but it
still needs model-backed profile runs, scheduler validation and real GPU offload and
distributed validation. Device enumeration alone is not an inference result.

The pinned source patch now builds a Vulkan-only Q4K kernel measurement, Vulkan
memory-copy delay and Vulkan read-bandwidth metric. The Vulkan kernel path verifies
the selected graph backend and returns no value when Vulkan cannot execute the graph;
it does not use CPU fallback. Profile output exposes these measurements, while
Kluster still requires a nonzero Vulkan Q4K result for Vulkan profiles.

Latest managed Vulkan source-build evidence (2026-10-10): recipe digest
`01a66905c394416f3db98b653aa08fbec017724e716e0271066b16c593e23f72` built the
pinned source and staged server, CLI, profile tool, Vulkan loader, MoltenVK library
and relative ICD manifest. The report included the `vulkan_profile` feature and
found `Vulkan0: Apple M1 Pro (MoltenVK)`. Running the staged binary with
`--world 2 --rank 0 --layer-window 0,8 --list-devices` exited 0, confirming the
zero-layer rank parser. A copy of the packaged runtime at a different path also
enumerated MoltenVK successfully. `otool -L` showed `@rpath/libvulkan.1.dylib`
instead of the build machine's absolute Homebrew path, and `codesign --verify`
passed for the staged server.

This verifies macOS build, packaging and device discovery only. No GGUF model was
run through Vulkan, and no Vulkan profile value, offloaded layer, distributed
generation or performance result was collected. Windows/Linux builds and device
checks, physical mixed-backend runs, API parity, rank lifecycle, authenticated
transfers and the performance gate remain open. All source-controlled qualification
gates remain false. No gate was closed from device enumeration or compilation.

## Published Prima fork and pin

Public fork: `https://github.com/berkkarahan/prima.cpp`, branch
`codex/kluster-integration`, commit
`a4a7e4d0fe11ef8275a17fa409974393c38f21fc`. The commit descends from upstream
`288df6a2cc2120387e12d6f40798f30cc525c9ea`; its 11-file diff matches the verified
temporary checkout byte for byte. Public Git access and remote/local SHA agreement
passed. The durable fork checkout at `/Users/berk/projects/prima.cpp` is clean and
tracks the published branch.

Kluster recipe schema 2 / build recipe 6 pins that fork commit, retaining the original
upstream source as provenance. Its patch list is empty; the superseded patch is removed.
Recipe digest: `17a4f918bb9447442cb94117d65b70f722dfa274c54279afb431bb6ad6b761bb`.

| Check | Result |
| --- | --- |
| `uv run python -m pytest -q tests/test_prima_integration.py tests/test_runtime_versions.py` | 100 passed, 1 skipped, 8 existing deprecation warnings; 3.65 s. Includes upstream source rejection, stale recipe rejection before preparation, retained stale records requiring rebuild, and fork metadata. |
| Affected Python modules and Prima tests, `ruff check` | Passed. |
| Managed installer from public fork, `--backend vulkan --from-source` | Passed in `/tmp/kllm-prima-fork-verification`; built Metal+Vulkan server, CLI and profiler from the exact SHA. Source checkout is clean; no local patches were applied. |
| Manifest and artifact verification | Source, upstream provenance, recipe digest and artifact hashes passed; relocated artifacts passed RuntimeRegistry capture. |
| Staged and relocated `--world 2 --rank 0 --layer-window 0,8 --list-devices` | Both exited 0 and reported the Vulkan profiling feature and Apple M1 Pro through MoltenVK. |
| Loader, ICD and signatures | Both locations use `@rpath/libvulkan.1.dylib` and a relative MoltenVK ICD; signatures passed for server, CLI, profiler, loader and driver. |
| `uv run python scripts/build.py --skip-ui --skip-path-shim` | Passed; wheel contains the fork pin and no superseded patch, and native extension loading passed. |
| Scoped whitespace check | Passed, excluding the preserved unrelated `src/kllm/__init__.py` blank line. |

All six qualification gates remain false. This receipt establishes fork publication,
reproducible macOS compilation, packaging and device discovery; Vulkan model inference,
Windows/Linux builds, distributed generation, API parity and performance remain open.

## Manifest/preparation and Windows assessment receipt — 2026-10-11

This section supersedes earlier source/build receipts for the current handoff. It does
not mark T012–T022 complete. The complete accepted compatibility/lifecycle/planner
plan remains unfinished: required native API/model/launch backports, arbitrary exact
placement, production coordinator/network admission/engine dispatch, and remote
end-to-end profiling/planner scoring are still implementation work.

Prima fork branch `codex/kluster-integration` now publishes
`aeb3afdd44b58c93e70e53d47ed4ac7e29452ff8`, descended from the requested starting
commit `a4a7e4d0fe11ef8275a17fa409974393c38f21fc`. Remote SHA agreement passed.
Kluster pins recipe schema 2 / build recipe 7 with digest
`084e6674c89d9e454e004df6e6f2a8b926cb285b1dca3b1679afbffd90c485bb` and no local patches.

| Check | Result |
| --- | --- |
| Native [CI run 38085012210](https://github.com/berkkarahan/prima.cpp/actions/runs/38085012210) | Passed Windows, Linux and macOS CPU compilation of server, CLI, profiler and SHA/manifest contract test; CTest passed on each. Vulkan is disabled in this CI build. |
| Managed installer from the published pin, macOS `--backend vulkan --from-source` | Passed in `/tmp/kllm-prima-assessment-final`; source/recipe and staged artifact hashes verified. |
| Staged and relocated device discovery | Both report Apple M1 Pro through MoltenVK and `execution_manifest_v1`; server/CLI/profiler signatures verify. Enumeration is not GPU execution. |
| `make test` | Passed: 1,244 Python tests, 15 skips; 63 dashboard module checks, 8 browser checks; 45 Rust tests; Svelte check 0 errors/0 warnings. |
| Final Python check after stricter preparation request validation | 1,245 passed, 15 skipped, 8 existing deprecation warnings. |
| Focused Prima/runtime checks after review fixes | 114 passed, 2 skipped without a native assessment directory. |
| Standalone lifecycle assessment with the staged native validator | 14 passed, no skips; controlled real children, signed WebSocket pushes, resume/integrity, ownership/readiness/lease checks and native manifest validation. |
| Standalone Vulkan assessment without supplied models | Exit 2, dense and MoE explicitly unavailable. No model execution result is claimed. |
| Dashboard production build | Passed. |
| Supported package rebuild and native loading | Passed; current fork pin, recipe, new modules and typed rank gateway verified. |
| Affected Ruff and scoped whitespace checks | Passed; unrelated `src/kllm/__init__.py` blank line and `src/kllm/primacpp/` stubs excluded. |

The versioned [compatibility inventory](compatibility.json) covers current proxy/control
routes, 46 parameters, template selections, recognized MoE architectures, launch
requirements and behavioral parity checks. Parser presence does not establish behavior.
The [machine-readable receipt](assessment-macos-controlled.json) records the current
source/recipe, baseline executable identity, OS/device observations, controlled results
and unavailable checks. It contains no model, physical distributed or performance pass.

Review fixes include process identity checks during readiness/renewal, serialized
controller construction, strict preparation envelopes, baseline binary identification,
and rejection of matching HTTP errors as successful observations. GPU execution proof
remains explicitly unavailable. The signed preparation implementation binds asset
chunks to sender, run and authority; its controlled transfer check launches no inference.

All six source-controlled qualification gates remain false. No existing live cluster,
adapter configuration, inference process or production runtime selection was changed.

Kluster implementation revision: `42be21fbc0df5e9e022635de86e879478bdceaa1`,
branch `feat/prima-integration`. The remote branch/main histories were rewritten
during the session. An isolated checkout merged the existing module refactor from
its equivalent published revision and reapplied the feature commit. Both the merge
result and the final implementation tree were verified byte-for-byte against the
locally validated trees. Publication uses a normal fast-forward push; no remote
history is overwritten. The original local history is retained in a private Git ref.
Only this handoff receipt is added after the implementation revision.

## Windows/Vulkan continuation — 2026-10-11

Native fork commit `91e586420eb357f45d80e33be0b31908fe4bc070` is published on
`codex/kluster-integration`; recipe 8 pins it. Recipe digest:
`21574abe7be8907595037677003ebee153e12bce4ad13a36837e96cda2c64711`.
Fresh managed Windows/Vulkan build passed. Fork CI run `38123362573` passed
Windows/Vulkan and the three portable CPU jobs. Local native CTest: 2 passed.

Kluster verification: Python 1252 passed / 19 skipped; Rust 42 passed;
network-helper feature 3 passed; dashboard tests/check and production build passed;
supported package rebuild and Prima native import passed. The rebuild required a
byte-wise SHA-256 hex formatter compatibility fix in the network helper.

Physical receipt `artifacts/prima-published-models.json` uses the published binaries
and pinned model bytes on RX 9070 XT. Dense sampled API behavior passes. Dense and
MoE completed prefill/decode Vulkan matrix ownership checks pass. MoE chat repeats
extra text and fails the requested answer check against the baseline. Receipt exit
status is failure; it is not qualification evidence for the failing behavior.

Both independent Windows local/distributed policies remain unqualified. Full gateway
API/lifecycle acceptance, arbitrary native placement, remote end-to-end profiling,
paired local performance and physical distributed execution remain open. Kluster
changes are in the shared working tree; only the native fork was pushed in this step.
The canonical kllm-prima skill was validated and installed for project discovery.

### Softmax correction and local continuation

Recipe 9 now pins `e4eb58b46cf2c7f2f320287d51ee26eb09070826`; digest
`c1c0a610e82376e2216a67f03f9f9ef115bab9b618a642c215513b5ffe1f884f`.
The fork fix guards absent-mask modulo/indexing in Vulkan softmax. Explicit Vulkan
SOFT_MAX, GET_ROWS, MUL_MAT_ID, DIV and SUM_ROWS operation runs pass; the test runner
now initializes Vulkan directly so device-registry skipping cannot masquerade as GPU
coverage. Native CTest: 2 passed. CI run 38125041981 and the fresh managed Windows/
Vulkan build passed. Published receipt artifacts/prima-softmax-models.json passes
sampled dense/MoE API checks and both completed Vulkan prefill/decode ownership checks.
This supersedes the earlier sampled MoE chat failure, but does not establish full API parity.

Local cancellation/authority/inventory fencing, launch rollback and request-feature
checks pass focused regressions. The dense alternating paired benchmark records
failure: token-count matching differs and decode throughput misses the threshold.
Its output bytes, request usage and measurements remain in
artifacts/prima-local-dense-benchmark.json. Local performance policy stays false.

Physical distributed cluster gates/validations will be tested later, as explicitly
requested by the user. They remain false. Later evidence must cover physical selected
workers/devices, private networking, exact placement, readiness, generation,
cancellation, partitions/recovery, cleanup and paired distributed performance.
This deferral does not waive the remaining local acceptance checks.

Final Python rerun for these changes: 1257 passed, 19 skipped (49.28s); Ruff and
whitespace checks passed. The MoE paired benchmark was still live at this handoff,
so its acceptance result is not yet claimed.

### Native protocols and model-backed profiling

Recipe 10 pins `f221716d9b45077a906621ca7e10d8b4ada485ed`; digest
`f177ecbaa7c8ac05c211b2824abe368a7511706ada0945537ddf1bb1b532bb31`.
Fresh managed Windows/Vulkan build passed. CI run 38127635068 passed Windows/Vulkan
and all three portable CPU jobs. Native CTest: 2 passed.

Published native protocol receipts: artifacts/prima-protocol-published-dense.json
and artifacts/prima-protocol-published-moe.json. Both pass native text/structured
Responses, Messages, forced nonstream tools, streaming lifecycle/usage/sequence,
input-token counts, output-limit and malformed-input checks, together with completed
GPU execution proof. Baseline conformance deviations remain explicitly recorded;
they are not counted as passes or copied into Prima behavior. Full gateway/tool-turn,
automatic/streamed tools, multimodal and stateful acceptance still remains open.

The MoE paired benchmark finished with failure in all four local workloads. Like
dense, generated token counts differ and decode throughput misses the threshold;
concurrency two is especially slow. Retain both failure receipts. No local or
distributed performance gate has been passed or relaxed.

Real model-backed ProfileJobs receipts: artifacts/prima-generation-dense.json and
artifacts/prima-generation-moe.json, both prepared. They measure warmed full-model
CPU/Vulkan generation at explicit offload, verify runtime/manifest/device/layer
ownership and record sample timings/RSS. These are profiling observations, not
performance acceptance. Partial/distributed layouts cannot extrapolate the measured
full-model decode cost. Focused regressions: 69 passed / 3 skipped; Ruff passed.

Full Python rerun: 1260 passed / 19 skipped (44.10s). Real profile cancellation
receipt artifacts/prima-generation-cancel.json passes: cancel after the Vulkan rank
acknowledges its manifest, retain cancelled status, stop the owned child and leave
no cached success. Native process enumeration after assessments showed no remaining
llama-server child. Local qualification remains open.

### Published normal gateway and measured planning

Recipe 11 pins `720da841239bc2ffef55b1fe08895562ade30bef`; digest
`2cb12d4546138bd34770c7994cada20b2ec30252f71febd61ba885bfce233ca1`.
Native source changes are published on `codex/kluster-integration`; remote SHA
agreement passed. Fresh managed Windows/Vulkan build succeeded in
artifacts/prima-windows-gateway. CI38131219709 passed Windows/Vulkan and CPU builds/
contracts on Windows/macOS/Linux. Local native CTest: 2 passed.

The native device report supplies total/free memory in Kluster's discovery format.
Manifests distinguish transformer layers from the planner's synthetic output stage
and explicitly place the output projection. Batch receipts include all actually
computed graphs after synchronization, including the separate output graph. The
verifier requires the planned output backend in both prefill/decode phases.

artifacts/prima-gateway-published-dense.json passes 27 checks through the actual
isolated agent, authenticated HTTP gateway, Rust launch and published native child.
They cover real generation profiling/cache use in normal planning, served model
identity, chat and streaming, Responses/Messages/structured/forced tools, input token
counts, limits/invalid input, concurrent queued requests, disconnect cancellation,
exact GPU layer/output proof, stop/restart and crash cleanup/restart. The relocated
artifact directory contains spaces. The harness uses a candidate qualification
policy only inside its isolated process; no committed/live policy is changed.

The profile API and planner now share a registry-owned profiler/cache. Matching
includes output projection placement; free-memory counters are excluded from stable
hardware identity. Earlier separate services caused cache invisibility and separate
reservations; the full-agent assessment reproduced that failure before the fix.

Focused Python checks: 139 passed / 4 skipped. Final full Python suite: 1270 passed /
19 skipped. The final expanded published MoE assessment passes all 27 checks. Native development
assessments passed 21 checks on both models before the additional profile/cancellation/
concurrency/crash cases. These observations do not pass paired performance, full API/
placement compatibility or distributed qualification. Distributed physical tests
remain deferred for later.

The first expanded published MoE runs passed profiling/cache use, protocols,
concurrency, disconnect cancellation and GPU ownership but failed crash cleanup.
Failure receipts are retained separately. The full-agent assessment exposed the
expiry monitor dereferencing an uninitialized Prima service and terminating before
later inference runs could be monitored. It now skips the absent service and keeps
running; late initialization has a regression test (focused 41 passed / 1 skipped).
The harness also waits for registry, process state and rank ownership to converge.
A rerun after the initialization guard still failed crash cleanup. Monitor task/stack
diagnostics showed a live expiry task with a cancelled run retaining rank ownership.
Failed cleanup previously prevented further monitor attempts. Cleanup now retains
ownership/error state and retries on subsequent ticks; a regression exercises a
failed stop followed by successful cleanup. The focused published MoE lifecycle rerun
passes 25 checks, and the final --profile suite passes all 27, including measured
planning, exact partial CPU/Vulkan placement and output projection, crash cleanup
and restart. This supersedes those failures without removing their receipts.
The supported package rebuild/import checks passed;
the initial rebuild was retried after the assessment released its Windows DLL lock.

Native telemetry correction is published as `5e3e2e13a4b0779d22b418d867088f86d8a3f8f1`
and pinned in recipe12. A set environment variable containing `0` previously enabled
execution tracing; now only `1` enables it. Native contract CTest: 2 passed. Fresh
managed build/CI and affected model/performance evidence are pending. The paired
runner explicitly rejects logs with execution receipts during timing. Current local
and distributed policy remains false; recipe11 receipts cannot qualify a different pin.

Recipe12's fresh Windows/Vulkan managed build and supported package rebuild/import
passed. Digest: `cba2d9f71c54c3234d9f653f7b71381fc2ca89b87bd8b82a85225248cc5abbc9`;
runtime ID `5fe63cd08d2d63c43a7de2dc4dcb1efeac9335245ba81a511f3d6bd96a6bd8ef`.
CI38132997745 and duplicate38133001176 passed all four build/contract jobs. Fresh
native protocol receipts prima-performance-protocol-dense.json and
prima-performance-protocol-moe.json both pass their sampled conformance and completed
Vulkan execution checks. Baseline deviations remain separate.

The corrected dense paired matrix completed five alternating pairs with explicit
sampler settings and no execution receipts during timing. It still fails: throughput
ratios are approximately 0.76/0.38/0.57/0.25 for short/long prompts at concurrency1/2.
Baseline generated71 tokens; Prima generated130 on short (sometimes67 with two
requests) and67 on long, despite matching output bytes. Retain
artifacts/prima-performance-dense-benchmark.json as failure evidence. MoE's recipe12
paired matrix is running. No performance threshold or output criterion was relaxed.
Focused checks after repinning:141passed/4skipped; Ruff/scoped whitespace pass.

The corrected MoE matrix also completed five alternating pairs with tracing disabled.
It fails all four workloads: throughput ratios approximately0.83/0.32/0.80/0.36.
Output bytes match, but token counts differ: baseline134/71 for short/long; Prima130,
sometimes68 in long concurrency2. Receipt: artifacts/prima-performance-moe-benchmark.json.
Both performance gates remain closed.

New tests/prima_token_assessment.py compares raw generation on the same baseline-
rendered prompt token IDs, with explicit samplers and real CPU/Vulkan references.
Dense observations in prima-raw-chat-dense.json: both engines produce130/67 tokens
on short/long Vulkan inputs, and67/67 on CPU inputs; output bytes and canonical
retokenization agree. Prima's chat counts match its raw generation, while baseline
chat reports71 on both prompts (including CPU). This isolates a chat conversion/
grammar-generation difference rather than proving a tokenizer defect. The baseline
Jinja chat path constructs a PEG-derived grammar/parser, whereas raw json_schema
completion uses the direct schema grammar. These differences require further native
template/grammar work. Raw Vulkan decode is also slower in Prima; its CPU reference
is comparable. Diagnostic timing is not paired performance acceptance. No gate is relaxed.

### Recipe14: connected local graph and structured chat framing

Published commit `501585d9116457f998d5372341c49b1ad093e6c3`, recipe digest
`5a40f12f8bf171de4ceceb3cc7c1eb67e50354b8602fa1195c7da11cd293ddd9`, runtime ID
`39e115c4c43b0f4731f7f4d2e5c32eaa79ff238ad4a882f3229e6424c686b477`.
Fresh managed Windows/Vulkan build in artifacts/prima-windows-json-framing passes;
server SHA256 `91c8dd98df998fc232b5f9fa0c89efe57d0100081e753a623a808e7a6d219876`.
CI38137945120 and CI38137941508 succeed. Supported package rebuild
`uv run python scripts/build.py --skip-ui --skip-path-shim` and native import pass.

Recipe13's connected single-rank graph avoids intermediate host handoffs and reports
one completed graph for both phases on dense/MoE diagnostics. Distributed graphs are
unchanged. Recipe14 adds optional JSON framing to response-format grammars and strips
the framing from API content, including incremental streams. Lexical grammar rewriting
and bytewise streaming contracts pass; generated usage counts are never adjusted.
Development protocol assessments pass on both models. Dense strict development
five-pair matrix matches output bytes and 71 actual generated tokens on all workloads,
but throughput ratios approximately 0.896/0.385/0.835/0.287 still fail the 0.95 gate.
Receipt artifacts/prima-json-framing-dense-benchmark.json is developmental evidence,
not final published-build performance acceptance.

The final published dense gateway --profile assessment passes all 27 checks in
artifacts/prima-framing-published-gateway-dense.json, including verified relocation,
profiling/cache use, protocols, cancellation, concurrency, GPU ownership, crash cleanup
and restart. The equivalent published MoE assessment also passes all 27 checks in
artifacts/prima-framing-published-gateway-moe.json. Fresh published native protocol
assessments pass on both models; baseline deviations remain separate. Full Python suite:
1270 passed / 19 skipped. Local and distributed qualification remain false; physical
distributed cluster gates and validations will be tested later.

### Recipe19: F32 Vulkan matrix precision

Published/remote-verified `146ccb624ad86ed7bdcd84e0bbeedd4b20b1b80e`; recipe digest
`fa9bd217ff771acd3db395effe645c3e9f34d24e772a8b69f0a588d1520f92d0`.
F32 matrix operands and explicit F32 precision requests now retain F32 shared tiles
and precision-preserving view copies. Attention KQ multiplication requests F32
across supported architectures, matching the pinned baseline. Tile dimensions fit
the device's actual shared-memory limit; independent CPU contracts cover16/32/64KiB
budgets and invalid requests. This native Vulkan correction does not alter CPU
matrix arithmetic or claim to fix CPU concurrent generation differences.

Receipt prima-f32-range-before.log reproduces15 infinity mismatches on finite inputs
against CPU. After extending tile coverage, prima-f32-range-final.log passes237
actual supported matrix comparisons, including39 finite-range cases;185 expert
comparisons pass in prima-f32-experts-final.log. Both native CTest contracts pass.
Generic1871/1871 summaries include filtered/unsupported cases. Initial reference
fixtures were corrected to avoid writing through intermediate views and to exclude
the F16 single-column CPU conversion that overflows the large input. Only the final
valid fixtures establish the before/after regression. A build command initially used
incorrect contract target names; the correct prima-*-contract-test targets then
built and both contracts passed. The source itself did not fail those checks.

Development protocol receipts prima-f32-protocol-{dense,moe}.json pass native API,
logprob and completed selected-device execution checks. MoE protocol offload is48
layers; the matched partial-offload performance matrix uses24, as before. Dense
five-pair development timing prima-f32-dense-benchmark.json matches bytes and actual
generated usage but fails overall: ratios approximately1.12/1.05/0.92/0.83 for
short1/short2/long1/long2. MoE development paired19481 is running. These development
receipts retain the recipe18 reference and explicit replacement-binary fingerprint;
they cannot qualify published19. CI38152704830/38152701173 are running. Fresh managed19
build has not started while paired timing uses CPU/GPU resources. All gates remain false.

The new --prefix-tokens2 diagnostic retains completed prefill/decode checks and
explicitly labels partial answers as diagnostic only. Recipe18 receipt
prima-prefix-cpu2-moe.json passes execution/transport checks for24 requests across
three two-request waves. Both engines show some initial logprob variation on
identical prompts; native long variation is larger. Distribution variation alone
does not prove a state defect. Continue batch/sequence and numerical investigation
with actual token pieces rather than assuming every difference is a bug. Physical
distributed cluster gates and validations will be tested later.

### Recipe15: small Vulkan decode batches and matrix correctness

Published and remote-verified `19adfe0800130e829544719136550257b21fd9f6`; recipe
digest `78a2744b0b168292959255e601ceba45f936ef3c785bb458c440a71b4df0afe5`.
Contiguous 1–8-column products dispatch independent vectors with correct column,
batch and broadcast offsets; larger/strided products retain matrix dispatch.
Short/odd floating-point vectors no longer read past the row, and all lanes enter
reduction barriers. The strided matrix path now selects F16 after copying F32
weights to F16. Failure receipts from the initial extension are retained separately.

Physical RX9070XT operation checks: 162 supported MUL_MAT and 185 supported
MUL_MAT_ID cases compare successfully against CPU. The generic runner's1796/1796
summary also includes filtered/unsupported cases; it is not1796 GPU comparisons.
Both native CTest contracts pass. Dense/MoE developmental protocol assessments pass.
The new views include padded weights/activations, with larger-batch fallback coverage.
Transposed test inputs were removed because this fork's CPU-reference multiplication
asserts against their layout; this is not evidence of transpose qualification.

Five alternating dense development pairs in artifacts/prima-small-batch-dense-benchmark.json
preserve identical bytes and actual generated counts. Throughput ratios in order
short/concurrency1, short/concurrency2, long/concurrency1, long/concurrency2 are
approximately0.91/0.93/0.84/0.53 (previously0.896/0.385/0.835/0.287). Every throughput
gate still fails; no threshold is relaxed. Focused Kluster tests141passed/4skipped.
Fresh managed recipe15 build and CI38139215714/38139213392 are in progress. No
qualification gate is enabled; physical distributed validations remain deferred.
Final recipe15 managed build/package/native import pass, with verified runtime ID
`006610d5a20c575131486f2fbe8f119320b3244b2e22cbb32678b2cc4a7fcde9`. Both CI runs
38139215714/38139213392 succeed. The first dense published gateway receipt fails
cancellation observation with an idle slot at9 decoded tokens. The harness discarded
its HTTP line iterator on break; httpcore catches GeneratorExit and closes the stream.
It now retains that iterator until after observing active native generation, and
captures rank logs on failures before cleanup. Published --profile rerun passes27
checks in artifacts/prima-small-batch-published-gateway-dense-retry.json. This changes
the observation lifetime, not the cancellation or throughput acceptance criteria.

Recipe15's published MoE --profile gateway suite also passes all27 checks in
artifacts/prima-small-batch-published-gateway-moe.json, including profiling/cache,
partial CPU/Vulkan placement and output proof, disconnect cancellation, crash cleanup
and restart. Both supplied models now pass the published15 gateway/lifecycle subset.

An unpublished extension uses existing contiguous copies of floating-point views
before small-batch vector dispatch, retaining larger-batch/outer-padding fallback.
Physical operation checks pass. First dense five-pair receipt
artifacts/prima-strided-vector-dense-benchmark.json matches bytes/counts and records
approximately0.90/0.97/0.86/0.75 throughput for short1/short2/long1/long2. Short2 passes
its workload gate but the complete matrix fails. The vector shader's previous loop
unroll hint is restored; operation/expert checks pass again and final paired timing
is in progress. No published-source qualification is inferred from these worktree results.

### Recipe16: Vulkan tensor views

Published and remote-verified `a024a7edc21d50c558f608878b7bb085bfde8aae`, recipe
digest `bf3ac467c3a55d83429e8205830ac029be2fccb3c502542bf87dd80099a8b7d7`.
General multiplication now checks packing across all active dimensions, including
padding between batches. Floating-point views use contiguous GPU copies before
small-batch vector dispatch. Specialized attention-vector kernels require their
actual supported shape/strides and do not ignore fourth-dimensional batches.
Strided quantized layouts lacking packing kernels are reported unsupported.

Failure receipt artifacts/prima-batch-layout-before-ops.log reproduces25 incorrect
padded/permuted products. After the fix,194 supported MUL_MAT GPU/CPU comparisons
and185 supported MUL_MAT_ID comparisons pass, plus both native CTest contracts.
The1828/1828 runner summary includes filtered/unsupported cases and must not be
reported as1828 actual GPU comparisons. Both dense/MoE developmental protocols pass.
Focused Kluster checks142passed/4skipped; Ruff and whitespace checks pass.

The earlier view-extension final dense matrix matches bytes/counts and records
approximately0.92/0.98/0.83/0.76 throughput for short1/short2/long1/long2; overall fails.
The corresponding MoE five-pair matrix completes with approximately0.85/0.52/0.86/0.55.
It fails throughput and long/concurrency2 generated counts: the last Prima pair
produces134/71 while all other long requests produce71. Output bytes match. Receipts
prima-strided-vector-final-{dense,moe}-benchmark.json predate the tensor-layout fix
and remain failure/development evidence. Repeat matched timing against published16;
no threshold or generated-usage criterion is relaxed.

Kluster worker argument translation retains parallel/cache/sampling settings and
omits only the HTTP alias from CLI ranks; rank-zero alias validation remains intact.
Regression passes and the real published15 CLI's help-based translation accepts
parallel2/f16 caches without alias. Full Python suite, recipe16 fresh managed build
and CI38141237726/38141235084 are in progress. All qualification remains closed;
physical distributed cluster gates and validations will be tested later.

The fresh recipe 19 MoE --profile --logprobs gateway suite also passed all 35 checks
in prima-f32-published-gateway-moe.json. Both supplied models now have published
recipe 19 gateway/profile/lifecycle evidence. Strict paired published-build timing
is running; all qualification gates remain closed.

Recipe16 fresh managed build93999 completes successfully with clean source and
runtime ID `e4f51bf577e1d0e42163a339c8d84c28c200fc2c4d13221d227df85f6b9392b7`.
Artifact capture and native import verify the pinned identity and hashes; supported
package rebuild passes. CI38141237726/38141235084 both succeed. The first full Python
run reports1failure/1270passed/19skipped: unchanged SQLite multiprocessing fence test
times out waiting for its child startup while native compilation is active. That
child remains alive holding the native DLL, blocking the first package replacement.
Ownership is verified against its exact failed-test temporary SQLite files and spawn
parent before stopping it; package replacement then succeeds. The unchanged isolated
test passes1/1 in2.61s. Full suite rerun without compilation is in progress. Retain
the first failure log; no SQLite production code or test timeout is changed.

The quiet full Python rerun passes1271 tests with19 skips in44.45s. This confirms
the changed worker argument behavior across the existing suite while retaining the
earlier timeout as evidence. No claim about physical distributed execution is made.

Published recipe16 dense --profile gateway assessment passes27 checks in
artifacts/prima-batch-layout-published-gateway-dense.json, including relocation,
profiling/cache use, protocols, concurrency/cancellation, GPU layer/output proof and
crash cleanup/restart. Equivalent MoE and final paired timing remain pending.

Published16 MoE --profile gateway11993 also passes27 checks in
artifacts/prima-batch-layout-published-gateway-moe.json. Both models' published
gateway/profile/lifecycle subset now passes, including GPU ownership and recovery.
Fresh published dense five-pair timing is running; MoE paired timing is next. These
gateway passes do not qualify throughput, broader features or physical distributed
execution. All qualification gates remain closed.

Published16 dense paired99185 completes five alternating pairs with tracing disabled,
identical output bytes and generated counts. Receipt
artifacts/prima-batch-layout-published-dense-benchmark.json reports throughput ratios
approximately0.93/0.98/0.85/0.73 for short1/short2/long1/long2. Only short2 passes; the
complete gate fails. Published16 MoE paired timing is in progress. No development
four-row kernel results exist yet; preserve these published16 results independently.

Published16 MoE paired61838 completes five pairs and fails: throughput ratios
approximately0.88/0.56/0.85/0.53 for short1/short2/long1/long2. Long2 Prima requests
in pairs0/2/4 include134 tokens instead of baseline71, while output bytes match.
Receipt artifacts/prima-batch-layout-published-moe-benchmark.json is failure evidence.

### Recipe17: quantized vector iteration and bounded grids

Published/remote-verified `622d5c4f9fdc1fa399c3f20e5632a67a2b63fdca`, recipe digest
`91d3a24c5c984cc74c6a93054cac75ff07561e37b7f688d06aa44e7b15bf2e17`.
Generic quantized vector loops process four values per iteration; floating-point
loops and output-row workgroup counts retain their prior behavior. A tested four-row
workgroup experiment was removed because dense paired throughput regressed to
approximately0.72/0.88/0.65/0.71 despite matching correctness. Retain its receipt
prima-row-group-dense-benchmark.json as a rejected experiment, not final evidence.

The retained quantized iteration passes198 supported matrix and185 expert GPU/CPU
comparisons, two native CTest contracts and both development model protocols. Its
five-pair dense receipt prima-quant-vec4-dense-benchmark.json matches bytes/generated
counts and reports approximately0.95/1.00/0.88/0.75: both short workloads pass but
the overall gate fails. These measurements are developmental, not published17 acceptance.

The grid helper rounds X/Z coverage, refuses invalid/oversized shapes and guards
partial row groups. CPU contracts check lower valid limits independently of hardware.
Four sparse final-row GPU cases pass on both published16 and the development backend;
vulkaninfoSDK reports RX9070XT X limit4294967295, so these cases do not enter the old
split branch. Do not claim reproduced hardware truncation. Source restoration after
the before-test completes; backups/receipts are retained. Cached GGML SIMD values are
off, but the effective MSVC project uses AdvancedVectorExtensions512; no CPU flag bug
is established and no CPU build-setting change is made.

Fresh managed17 build and CI38145088445/38145085072 are in progress. Qualification
remains false; physical distributed cluster gates and validations will be tested later.

Recipe17 managed build73560 and supported package rebuild/import pass. Capture
verifies clean source/artifacts and runtime ID
`10d6d390550578f1c4bfd68a9e2d040cdb09989fbf86a9dbc729b0329cde4031`.
CI38145088445/38145085072 both succeed. Focused checks142passed/4skipped.
Final published17 gateway and strict paired acceptance are still required.

tests/prima_token_assessment.py now supports --cpu-only, --concurrency1/2 and positive
--repetitions, with optional generated chat token pieces/top3 logprobs. Raw and chat
requests run in separate concurrent waves on identical baseline-rendered input IDs.
The long prompt now exactly matches the benchmark; older different-prompt diagnostics
remain historical. Dense CPU2 smoke receipt prima-concurrent-cpu-dense.json passes
semantic and CPU-execution checks: baseline raw67/67 short/long, Prima raw130/67,
both chat71. This establishes a raw CPU concurrent generation difference, not its
cause or a performance pass. MoE CPU2 three-round diagnostic is in progress; all
usage remains actual generated counts and qualification stays closed.

### Recipe18: native chat token logprobs

Published/remote-verified `83555d7b5a65a2dd84d3e49f27c237cabb98bbfd`; current recipe
digest `3557aa2522b2dfc51d71514c7b479a3a3a6b7c5749cea888c5e31dd9fe074e90`.
The server advertises `chat_logprobs_v1` and returns selected-token and alternative
logprobs in OpenAI choices and streaming deltas. Probabilities use full raw logits
before sampling; reporting does not change the sampler. Partial UTF-8 tokens retain
their unsigned byte arrays while JSON token strings use valid replacement text.
Validation rejects nonboolean logprobs, incompatible top_logprobs and bounds outside
0..20. Zero alternatives still reports the actual selected token.

Native CTest contracts:2passed. An initial test fixture incorrectly called the
model-dependent request parser with a null model and crashed; it was corrected to
test the independent logprob validation boundary. No production null-model behavior
was changed. Before receipt prima-logprob-before-protocol-dense.json reproduces all
eight new failures against published17. After receipts prima-logprob-protocol-dense-final.json
and prima-logprob-protocol-moe.json pass every new native case. Baseline deviations
remain separate; they do not weaken native validation or generated-usage checks.

Development gateway receipts prima-logprob-gateway-{dense,moe}.json both pass35
checks with --profile --logprobs, including logprobs, profiling/cache, real GPU
ownership, concurrent requests, cancellation, crash cleanup and restart. These
receipts use the explicitly recorded development binary, not the fresh published18
managed artifact. Managed build session43770 and CI38148640088/38148637306 are running.
Focused Kluster tests after repinning:207passed/4skipped. No policy gate is enabled.

The completed recipe17 CPU-only MoE diagnostic prima-concurrent-cpu-moe.json uses
three repeated two-request waves. Baseline chat counts remain134short/71long;
Prima long chat counts vary71/72/134. Token pieces identify zero/one/63 standalone
spaces respectively. Raw long native counts also vary130/68. This establishes a
CPU concurrency difference without identifying its cause; it is not performance
acceptance. Physical distributed cluster gates and validations will be tested later.

Recipe18 managed build43770 and supported package rebuild/import pass. Capture
verifies clean source, runtime ID
`1b106e3318a3d7dc377ec28e18fd293e45c5f22b91ff4032f64464c7d161703c`, and server SHA256
`fd781a99744ce053cb376736777a976c39d6945720b26c09b2ae93dd7927ca4c`.
CI38148640088 and CI38148637306 both succeed. Fresh published gateway receipts
prima-logprob-published-gateway-{dense,moe}.json each pass35 checks with --profile
--logprobs. The new protocol feature has published-build evidence for both models;
the complete API/model/placement and paired performance requirements remain open.

### Signed rank ownership and prepared startup

Rank ownership now persists the authenticated sender and master authority digest.
Signed start/status/renew/stop commands must match that owner and run ID. Local
supervision and lease expiry retain their internal cleanup path. Startup additionally
requires a completed, unexpired preparation from the same sender/authority and exact
runtime, containing the execution manifest and every declared model/companion asset.
The model layout is resolved from that approved preparation. New preparations refuse
nodes with an owned rank; cached bytes alone are not a startup authorization.

The real-child regression fails before owner binding and passes after it, verifying
sender/authority mismatch refusal without spawning or stopping the owned process.
The signed WebSocket regression reproduces unapproved cached-manifest startup before
the preparation check; after the fix it refuses that launch, accepts signed complete
preparation, starts one owned child, refuses status for another run and stops cleanly.
Receipts prima-rank-authority-{before,after}.log and prima-approved-start-{before,after}.log
retain the failures and passes. These use a controlled child and fixture qualification;
they do not establish native distributed readiness or physical cluster acceptance.

Full Python suite after signed ownership/preparation changes:1271passed/19skipped
in44.35s. Additional signed-start boundary checks pass2/2 after extending the
fixtures: expired/wrong-runtime/wrong-sender/wrong-authority/incomplete preparation
and active-rank preparation refusal. Ruff and scoped whitespace checks pass.
Published18 CPU-only repeated MoE token/logprob diagnosis is running (session65200);
it remains diagnostic evidence, not performance acceptance.

Signed preparation status/cancel commands now enforce the original operation ID,
sender and authority digest, matching asset-push ownership. The real WebSocket test
first reproduces a wrong-operation status disclosure, then verifies both status and
cancel refusal after the fix. Affected suite:142passed/4skipped; Ruff passes. The
full1271/19 run above predates this additional query fence; it is not represented as
a repeat of that later change. CPU diagnostic65200 remains live with baseline complete.

Final preparation query tests also verify unknown/non-string job IDs and a non-string
manifest digest fail as protocol errors while the signed channel stays usable. Asset
pushes reuse the same operation-owner check. Final affected suite:142passed/4skipped
in7.14s; Ruff and scoped whitespace pass.

Published18 CPU diagnostic65200 completes successfully in
prima-logprob-concurrent-cpu-moe.json. Three repeated two-request waves preserve
constrained output and CPU/connected-graph evidence. Baseline chat is consistently
134short/71long; Prima short is134 while long counts are134/71,71/72,71/72.
Native logprob entries equal actual generated usage for every request. Raw long
Prima counts vary130/68; baseline raw remains130. The same initial chat token
prefix has different raw logprobs across native requests before token sequences
diverge (for example the opening fence logprob ranges approximately-12.26..-9.34).
This motivates batch/sequence-state and numerical inspection as well as grammar
analysis; it does not yet establish a root cause. It is not paired performance
acceptance. No native job remains live and qualification remains false. Physical
distributed cluster gates and validations will be tested later.

### Published recipe 19 managed build and acceptance

Managed build 23845 completed successfully in artifacts/prima-windows-f32. The
supported package rebuild, native import, and artifact/source verification passed.
Runtime ID: `fa045bc61c3156e4ed100d444a64e70e3d5827dd98ef77a84aa1501a00cf752b`.
Server SHA256: `c1ff49c14d52dc508a21c14bf95459e017094a1c94ca32a19e0c619d18c9c1f9`.
Both CI38152704830/38152701173 succeeded. Focused Kluster tests: 142 passed / 4
skipped; Ruff and whitespace checks passed. The fresh published dense --profile
--logprobs gateway receipt passed all 35 checks; the corresponding MoE suite is running.

Development MoE paired 19481 completed five alternating pairs with tracing disabled.
All workloads matched returned bytes and actual generated counts, including every
long/concurrency2 request (71/71 in every pair). Throughput ratios were approximately
0.83/0.54/0.84/0.54, so the complete gate still fails. Receipt:
artifacts/prima-f32-moe-benchmark.json. These results establish the tested partial-
offload matrix's count improvement, not resolution of CPU-only variation, published
performance acceptance or broader compatibility. All qualification remains false;
physical distributed cluster gates and validations will be tested later.

Final published recipe 19 acceptance: both --profile --logprobs gateway suites
passed all 35 checks. The exact managed build then completed five alternating pairs
for each model, with tracing disabled, matching output bytes and actual generated
counts across every workload. Dense throughput ratios were approximately
0.97/1.01/0.89/0.77 (short1/short2/long1/long2); only the short workloads passed.
MoE ratios were approximately 0.82/0.55/0.84/0.54; every throughput gate failed.
All MoE long/concurrency2 requests generated 71/71 tokens in every pair. Receipts:
prima-f32-published-{dense,moe}-benchmark.json. Both complete matrices fail, so
qualification remains false. All assessment and build sessions are terminal; no
owned inference child remains. Broader compatibility, placement, distributed
implementation and local performance work remain required. Physical distributed
cluster gates and validations will be tested later.



### Recipe 20: direct batched strided attention

Published and remote-verified `857833e2102e8cf1402afe3442cf1ef616dadc41`; recipe digest
`7f1d59d770b3b04dec598514e04bccedca77763bc8225bb41fe14840ba1e7b22`.
Specialized F16/F32 attention now handles1..8 columns directly with explicit strides.
Two queries share each K/V read, including odd final columns. Descriptor offsets
are included in shader accesses; rows move to the X dispatch dimension with device
limits checked before selection. Four-dimensional batches retain the general path.

The focused before test passes24 cases. The final after test passes36, including odd
columns and padded/offset views. All273 supported matrix and185 expert comparisons
pass, plus both CTest contracts. The RX9070XT reports four-byte storage buffer
alignment, so F32 shader offsets are zero here and the old omission is not numerically
reproduced. Alignment changes are code/portability corrections, not another device's
qualification. The operation runner's new -f variable filter selects these cases.

Before/after two-column operation timing: values19.74→15.27us, keys58.27→40.34us.
Single-column measurements slightly regress9.50→9.85us and31.62→32.74us. Dense/MoE
development native protocol/logprob suites pass. Dense development five alternating
pairs pass all four local workloads with matching bytes and actual generated counts:
throughput ratios1.157/1.068/1.010/1.014. This is explicit development evidence in
prima-attention-dense-benchmark.json; it cannot qualify the published pin.
MoE paired69961 and CI38157856049/38157850903 are running. No managed20 build has
started during CPU/GPU timing. All qualification remains false; distributed tests later.

Release audit found another required boundary: qualification currently checks model,
features, layer mode and device/driver, but omits exact offload/output/context/batch/
cache/thread settings. Native defaults are2048/512 batching, while paired timing
uses512/128. Normal gateway acceptance must explicitly use the measured settings,
and source-controlled policy must bind them before any local scope is enabled.

MoE development paired 69961 completes with matched bytes/counts across every
workload, but fails throughput: ratios approximately 0.83/0.51/0.82/0.52. Receipt:
prima-attention-moe-benchmark.json. Both CI38157856049/38157850903 succeed. Fresh
managed build 26607 runs in artifacts/prima-windows-attention after timing completes.
Focused Kluster tests pass 142/4 skipped; Ruff passes. The gateway harness now explicitly
requests batch512/micro128/threads_batch8 and verifies the owned child's native arguments.
An optional --parallel-slots1/2 exercises real slots instead of only queued requests.
Fresh published model receipts and exact qualification setting/placement binding
remain required. All gates stay false; physical distributed validations later.

Recipe20's fresh managed build/package/native import and clean source/artifact
capture have completed. Runtime ID:
`ccad228ce2ad9b7ea8913836a6fb1f3ef8c817c93e8c72f1a607b85902f7b007`;
server SHA256:
`f2e8c12d4711f2766be5d31e83bbfa76fdc30621d06247f9fdcff925c1e1966b`.
The initial published dense gateway passed36 checks. After exact execution-scope
binding, prima-attention-scoped-gateway-dense.json passes38 checks, including actual
native argv, refusal of unmeasured batching, persisted active context and request-time
revocation while another preset remains approved. The corresponding MoE run is active;
two-slot gateway and published paired timing still need execution.

T047 implementation adds receipt-backed execution presets and per-preset API feature
checks. Ten changed setting cases and three changed offload cases are refused. Policy
revocation, cross-preset feature isolation and detached context tests pass. Focused
Python checks109passed/1skipped; full Python1287passed/19skipped47.38s; Ruff passes.
Controlled candidates never update the committed qualification policy. Distributed
physical cluster gates and validations will be tested later.

Final T047 qualification also binds named companions and host CPU/core/RAM/OS identity.
Final prima-attention-scoped-final-gateway-{dense,moe}.json pass38 checks each;
final two-slot equivalents both pass36. Full Python1293/19skipped47.82s; focused
Windows tests48pass (including the placement verifier); Ruff passes. These candidate
tests leave committed policy empty and all gates false.

Baseline placement audit: the verified pinned source 7fe450e counts output inside
every positive --gpu-layers count. Old MoE24/CPU-output comparisons therefore used
23 transformer GPU layers plus GPU output in the baseline, versus24 plus CPU output
in Prima. Their ratios are historical observations and cannot establish matched
performance. All baseline comparison tools now share explicit translation: ngl+1,
CPU output/tied embedding overrides where needed, and disabled autonomous host-op
offload. CPU-only runs keep0/none. Timing explicitly checks argv and disables both
native tracing and scheduler debug. Its new prerequisite placement probe uses a
completed1001-token prefill and four-token decode plus baseline scheduler assignments.

prima-attention-partial-placement-reproduced.json passes both engines at dense14
transformer GPU layers/CPU output. The first probe attempt failed because Prima lacks
--no-warmup and the tied baseline projection still used token_embd.weight on Vulkan;
those harness failures remain in prima-attention-partial-placement-before.json.
No native placement bug is established: final long-prefill ownership matches. Native
857833 remains clean and unchanged; no new fork push was needed. Corrected dense
published timing13196 is active; corrected MoE33/GPU-output timing follows. T048/T032/
T039 remain open; physical distributed gates and validations will be tested later.

Profile-cache correction T049: an isolated regression first fails because a4-thread
batch request reuses an8-thread measurement. The planner now rejects that reuse;
profiling explicitly launches batch threads equal to requested generation threads
and records fixed512/128 batching in its cache identity. Old records are retained
but unmatched. Focused103/3skipped pass; the fresh updated dense profiling/gateway
receipt prima-attention-batch-profile-gateway-dense.json passes38 checks. Final full
Python1294passed/19skipped43.66s, Ruff and whitespace checks pass. All test/model jobs
are terminal. Production presets stay empty and every gate remains false; native
source857833/recipe20 are unchanged. Kluster changes remain uncommitted. Physical
distributed cluster gates and validations will be tested later.

### Recipe21: owned rank readiness and transformerless coordination

Published and remote-verified `f11a4a20e7f34e2a67a6985164ac0d5c6c9b30e3`, digest
`6750661c6db4f05edf036ff7f384285db8a426400b37630293ea048d760c8801`.
The initial real two-process CLI worker aborts before manifest application because
world defaults to1. After startup ordering is corrected, controlled CPU14/14 ranks
pass readiness and generation. Zero-master CPU then reproduces an unallocated-input
assertion during warm-up; skipping unused transformer input writes fixes it.
Normal/zero-master CPU and Vulkan development regressions pass; Vulkan receipts prove
every assigned attention/FFN layer and normal master output. Both CTest contracts pass.
Files prima-readiness-controlled-cpu{,-v2}, prima-readiness-zero-master-cpu{,-v2},
prima-readiness-controlled-vulkan and prima-readiness-zero-master-vulkan retain results.

Both CI38165428009/38165425315 succeed. Fresh managed21 build34666, package rebuild,
native import and clean source/artifact capture pass. RuntimeID
`aed3280b3d7eb2f0a907528243bb73aceace26d17d6bc8dc0823847cdcb3c4f2`;
server SHA256`83f7337f77ec8ed77c070a3f741f49dff1db03fd96496fa8273529e9f9ed4ab4`;
CLI`742f655aee0b69ee414d8dc900e80ccadd548805cff388adaf5ecca35f2679bd`;
profiler`6a3ad9f777b4cedccc76a1d11bab2c9f0743d8deeab515b016cdbc78b7ae84ad`.
Focused Kluster171passed/3skipped, subsequent lifecycle/windows/integration108/3,
Ruff pass. Real signed-WebSocket/child regression proves configured-only stays
starting and owned post-init acknowledgement promotes ready. Layer/stage/identity/
type/partial/duplicate/log-limit negatives and dead-process status pass.
Fresh published dense gateway68182 is running; fresh published same-host cases remain
required. Production selection stays closed; performance20 receipts remain historical.

Two coordinator regressions fail before correction: cancelled pending remote stops
never retry, and failed admission release is persisted idle. The corrected monitor
retries cleanup without restarting; cleanup remains pending until release succeeds.
This does not compose the coordinator or qualify physical multi-machine operation.
Distributed cluster gates and validations will be tested later.

Final published21 controlled acceptance: dense68182 and MoE42336 gateway suites
both pass38 checks. All four fresh managed same-host rank cases23980 pass:
prima-readiness-published-cpu-normal/-cpu-zero/-vulkan-normal/-vulkan-zero.json.
The Vulkan cases include completed prefill/decode proofs for every assigned layer
and normal master output; zero-master mode has a declared CPU coordinator plus a
Vulkan worker, with no substitution for requested worker GPU work. These results
close T050's native/owned-readiness regression scope, not T014/T016 or physical
distributed qualification. Both CI runs have all four jobs successful. Full Python
65221 completes1299passed/19skipped53.15s; Ruff/whitespace checks pass. Production
scopes remain empty. All managed build/model/test sessions are terminal. Sourcef11
is clean/published, while Kluster additions remain uncommitted. These checks leave
T012–T022/T032/T039 open; physical distributed cluster gates and validations will
be tested later.



Final MoE two-slot gateway17756 also passes36 checks with33 transformer GPU layers
and GPU output. Both models now pass one-slot38/two-slot36 candidate suites. The
first corrected dense timing13196 aborts after detecting scheduler debug output:
old Prima enables GGML_SCHED_DEBUG by presence, including the string0. The harness
now removes that variable in child environments instead of setting0; the guard is
retained. Receipt prima-attention-matched-published-dense-benchmark.json records this
aborted timing, and its prerequisite placement probe passes both engines. Fresh
corrected timing93831 uses a distinct -v2 receipt and is active. No performance gate
is passed from an aborted or in-progress run.

Corrected published dense93831 completes all five alternating pairs with both
placement probes passing, debug/tracing disabled and exact output bytes/generated
counts matching. Short1/short2/long1/long2 throughput ratios are approximately
0.921/1.030/0.879/1.009. Only concurrency2 workloads pass; all TTFT ratios pass.
Receipt prima-attention-matched-published-dense-benchmark-v2.json proves the published
matrix still fails overall. Development all-pass timing is not substituted for it.
The corrected normal-layout MoE33/GPU-output matrix70287 is running. Remaining local
performance work, broad compatibility and distributed implementation stay required;
physical distributed gates and validations will be tested later.

Final corrected MoE70287 also completes five alternating pairs at the normal33
transformer GPU layers/GPU-output layout. Both placement probes pass and tracing/
debug remain disabled. Short1/short2/long1/long2 throughput ratios are approximately
0.728/0.409/0.745/0.495; all fail. Output bytes agree, while actual generated counts
differ for the long1/long2 workloads. All TTFT ratios pass and no OOM is recorded.
Receipt prima-attention-matched-published-moe-benchmark.json is current matching-
placement evidence. This supersedes the earlier invalid MoE comparisons, without
qualifying performance or claiming the count issue fixed. All model jobs are terminal.
T048/T032/T039 and broad implementation remain open. Distributed physical cluster
gates and validations will be tested later.

### Durable coordinator ownership and cancellation

The initial new coordinator regression suite reproduces9 failures: restart loses
the active manifest, start intent is absent before a potentially lost reply, and
seven mismatched rank identity/allocation variants are accepted. A separate
publication race also reproduces a serving result after Stop cancels startup.
Corrections now persist start intent/pending ranks/command authority/recovery
attempts, restore only cleanup, validate exact per-rank identity, and recheck
cancellation after publishing readiness. Failed cleanup or persistence retains
ownership and cancels automatic recovery; explicit new runs reset the bounded
three-attempt budget. Malformed/ambiguous durable records fail before commands.

`uv run python -m pytest -q tests/test_prima_coordinator.py tests/test_prima_lifecycle.py
tests/test_prima_windows.py tests/test_prima_integration.py` passes135/3skipped,
with4 existing Starlette deprecation warnings. This includes real owned child
processes surviving reconstructed rank/coordinator owners and then terminating
without touching an unrelated live child. It is a local process/filesystem
regression, not physical distributed generation or network acceptance. Ruff and
`git diff --check` pass. Full Python1326passed/19skipped49.83s also passes, with
4 existing Starlette deprecation warnings; output is retained in
artifacts/prima-coordinator-final-python.log. T016 production dispatch/private
admission remains incomplete; physical distributed cluster gates and validations
will be tested later. Nativef11/recipe21 and all closed qualification gates are
unchanged; Kluster changes remain uncommitted.

### Prepared distributed dispatch and private admission

The normal Prima dispatcher now selects local versus prepared multi-rank execution.
Preparation binds effective context/settings, master HTTP port, request settings
digest and signed master authority before transferring the immutable manifest.
Distributed startup requires the matching unexpired prepared plan; it cannot
authorize itself from cached model bytes. Network reservations add backward-compatible
engine/manifest/authority fields and keep their immutable endpoints/assignment set.
The Prima path verifies private assignments and signed diagnostic health without
starting RPC children or relays; the existing RPC path refuses Prima release.

`tests/test_prima_admission.py` passes7 cases, including actual signed three-agent
diagnostics with mapped sockets and simulated adapter changes. The coordinated
`tests/test_prima_dispatch.py` boundary passes real asset transfer, three owned fixture
children, worker-first startup, owned native-report parsing plus HTTP200, stale
preparation/authority refusal, concurrent Stop during validation and HTTP loading,
worker private-owner invalidation, refusal of RPC contention, and one complete
owned restart after terminating a worker. Production qualification is first verified
closed, then bypassed only in that isolated fixture. No model/GPU or physical cluster
qualification is inferred. The first run exposed CPU-rank qualification attempting
Vulkan device selection for an empty requested-device set; explicit CPU ranks now
skip GPU discovery while requested GPU ranks retain exact backend/device checks.

Wider focused Python175passed/3skipped13.47s with4 existing deprecation warnings.
Ruff and whitespace checks pass. Full Python1334passed/19skipped50.19s completes
successfully in artifacts/prima-dispatch-final-python.log; affected published local
gateway suites remain required. Nativef11/recipe21 stays clean and unchanged. Source-controlled
qualification scopes stay empty/false. Remaining signed participant hardware/scope
binding, native port admission, broad compatibility/placement/planning/performance
and final recovery acceptance are still open. Physical distributed cluster gates
and validations will be tested later.

The initial published local dense gateway rerun retains successful profiling,
planning, native startup and sampled APIs, then fails because the new dispatcher
does not expose its active manifest to assessment/log capture. This is recorded in
prima-dispatch-published-gateway-dense.json/log, not treated as a passed assessment.
The dispatcher now exposes the local or distributed active manifest; a distinct
prima-dispatch-published-gateway-dense-v2.json/log rerun is in progress. The committed
policy stays closed. This observation interface does not weaken execution checks.

Published local follow-up: dense59708 and MoE8095 complete successfully, each
passing38 checks through recipe21 with development_server=False. Receipts are
prima-dispatch-published-gateway-dense-v2.json and
prima-dispatch-published-gateway-moe.json, with their logs retained. This proves the
affected local gateway/profile/lifecycle subset after dispatch composition, not
physical multi-node operation or performance qualification.

A further controlled dispatch regression reproduces a degraded serving phase when
an already-ready master's trace log grows past the4MiB startup parser bound. Serving
monitoring now uses the persisted validated acknowledgement while continuing exact
PID/start-token checks and master HTTP health; starting ranks still require the
bounded native-report parser. The case passes after correction. Final focused
175passed/3skipped14.08s and full Python1334passed/19skipped46.21s pass, with4 existing
Starlette deprecation warnings. The latter is retained in
artifacts/prima-dispatch-final-python-v2.log. Ruff/whitespace checks pass. All
model/test handles are terminal; nativef11/recipe21 is unchanged and source scopes
remain empty/false. No native kernel/source/recipe changed. T053 is complete for
this controlled composition/local-regression scope, while the broad integration
requirements remain active and physical distributed acceptance is deferred.

### Signed participant hardware and distributed scope

The new signed prima_inventory action is read-only, bounded by the existing command
fence/deadline and observed on the receiving node. It returns typed host, platform,
source/recipe/build/artifact and selected physical Vulkan identities for that rank.
Preparation validates the node/source/recipe/allocation and binds every rank's
evidence into the immutable execution manifest. Startup collects fresh signed
observations before any child spawn; ownership launch and worker status/renewal
reject drift. Exact qualification scopes bind all hosts/builds/devices and distinguish
a zero-transformer CPU head from required Vulkan worker work. No inference proof
or gate is granted by inventory, and committed qualification remains empty/false.

Legacy/local manifests omit empty evidence during serialization; their canonical
digests are preserved, including reconstructed ownership records. Unit coverage
checks drift, backend substitution, malformed physical identities, source mismatch,
free-memory stability and temporary receipt-bound distributed qualification.
The real signed three-agent dispatch fixture now transfers all node evidence and
refuses a changed captured artifact set before any spawn; model execution/build
capture and adapters remain controlled. Focused159passed/3skipped16.07s, full
Python1348passed/19skipped46.10s with4 existing deprecation warnings, Ruff and
whitespace checks pass. Full log: artifacts/prima-participants-final-python.log.

tests/prima_rank_assessment.py --participant-evidence validates the added metadata
through the published immutable recipe21 source. Four fresh same-host dense CPU/
Vulkan normal/zero-master cases all pass: cpu-normal2/cpu-zero2/vulkan-normal4/
vulkan-zero3 checks. Receipts are prima-participants-published-{cpu,vulkan}-
{normal,zero}.json with separate master/worker logs, development_server=False,
native sourcef11 and observed evidence for both ranks. Vulkan checks retain completed
prefill/decode matrix ownership proofs; zero-master mode explicitly declares its
CPU head and Vulkan worker. These are same-host regressions, not physical cluster
qualification. Physical multi-machine validation is still scheduled for later.

The additive membership capability prima_inventory_version1 now prevents sending
new commands to older workers; their preparation error explicitly requests an agent
upgrade. Focused membership/auth/participant checks84passed/1skipped13.43s; final
full Python1348passed/19skipped45.71s completes in
artifacts/prima-participants-final-python-v2.log, with4 existing deprecation warnings.
Ruff and whitespace checks pass. Native source/recipe is unchanged and Kluster edits
remain uncommitted. All model/test handles are terminal. T054 is complete for signed
identity/context binding; native ports, broad compatibility/placement/planning,
local performance and remaining acceptance keep the full integration goal active.

### Native private listener foundation (T055)

The helper now verifies signed `prima_admit_ports` runtime commands against its
pinned key, current cluster/term/session/private scope and assignment revision.
The Python contract derives exact inbound data/signal ports and peer addresses
from the approved topology, deduplicates worker replies and excludes HTTP/RPC.
Worker handlers validate complete preparation, run/manifest identity, current
private assignments/hardware and qualification before privileged forwarding.
Inexact acknowledgement or revoked authority triggers owned local cleanup.

The protected helper records all rule intent before mutation, persists successful
CIM instance snapshots, rejects pre-existing/changed rules and retains partial
cleanup. Cleanup cannot release a still-owned rank; helper restart restores only
cleanup, never serving. Pending native rules block network restore/reprepare and
authorization revocation, including automatic network restoration on helper startup.
Creation verifies the newly returned instance identity; removal rechecks the full
snapshot in one fixed command and removes the captured CIM instance.

Verification on this Windows host:

- Focused Python admission/dispatch/participant/lifecycle/guided/network selection:
  `92 passed, 1 skipped in 6.99s`.
- Full `uv run python -m pytest -q`: `1371 passed, 19 skipped in 47.05s`, with the
  four existing Starlette deprecation warnings. Log: `artifacts/prima-ports-final-python.log`.
- Standalone helper `cargo test --manifest-path crates/kllm-native/Cargo.toml
  --no-default-features --features network-helper --bin kllm-network-helper`:
  `11 passed in 1.52s`; two dead-code warnings. Log: `artifacts/prima-ports-native-tests.log`.
  Tests cover signed authority/replay, durable intent/partial failure, listener
  collisions, cleanup narrowing and reconstruction. A shared fixture signed using
  the Python protocol is accepted by Rust and refused on replay. Windows PowerShell
  parses the real fixed snapshot/create/remove scripts without executing them.
- Supported `uv run python scripts/build.py --skip-ui --skip-path-shim` rebuild:
  exit0, staged helper and native extension import pass. Final package log:
  `artifacts/prima-ports-package-final.log`. Staged helper SHA256:
  `694a05bf3310d130494cb45e88d25344b81c531aa9a5a909e4282fbfb49a93cf`.
- Final skill/connection/port checks after installing the ownership-safe project
  Codex skill copy: `78 passed, 2 skipped in 3.17s`. Ruff and `git diff --check` pass.

These are source, fixture, filesystem and protocol-vector checks, plus OS syntax
parsing. No firewall mutation, privileged helper installation/IPC, physical private
connectivity or distributed model generation is claimed. Normal coordinator Run
does not yet invoke the port path. T056 retains production all-node admission,
partial/lost acknowledgements, stop-before-release, serving recheck/renewal/expiry,
capability negotiation, administrator/direct and Linux/macOS paths, protected IPC
and physical OS acceptance. Missing/changed native creation snapshots need local
review and are never adopted or deleted automatically. Older helper scope journals
without assignment revisions require owned-network cleanup and fresh preparation.

Native Prima source remains clean at f11a4a2/recipe21; no fork source change/push
was required. Kluster changes remain uncommitted. All production qualification
gates stay false. Physical distributed cluster gates and validations will be tested
later; T012–T022/T032/T039 and the full integration objective remain open.

### Coordinated native listener lifecycle — 2026-10-12 (T057)

Prepared distributed Run now checks strict `prima_ports_version1` membership and
signed read-only `prima_port_capabilities` before mutation. The updated protected
Windows helper must report both its wire support and an empty native owned-rule
ledger; other/missing backends or unreconciled ownership fail before admission.
All-node listener acknowledgement is a barrier before any rank spawn. Reservation
records retain attempted and admitted participants, and storage revalidates mutable
lists plus immutable admission requirements. Lost replies preserve the attempted
node. Cleanup drains all owned ranks before rules, narrows each successful native
release and continues attempting other nodes when one release fails.

PortSupervisor persists node-local intent before the helper call, including complete
manifest identity and signed command authority. The pre-spawn lease is bounded
to600s; launch and owned renewal switch it to60s. Its lock covers the validation/
spawn interval and expiry, preventing cleanup from racing a new child. Revocation
after spawn stops the owned child before rule release. Agent startup initializes
port-only records too; reconstruction resumes cleanup only. Pending listener
ownership blocks RPC startup, new asset preparation and profiling contention.

Verification:

- Final focused port/dispatch/admission/rank/RPC/skill checks: `146 passed, 3 skipped
  in 6.71s`, one existing Starlette deprecation warning.
- Full Python: `1381 passed, 19 skipped in 46.97s`, four existing deprecation
  warnings. Session70982 exited0; log `artifacts/prima-port-dispatch-final-python.log`.
- Standalone helper tests: `11 passed in 1.70s`, including the signed Python/Rust
  vector and non-executing Windows script parsing; two dead-code warnings. Log:
  `artifacts/prima-port-dispatch-native-tests.log`.
- Supported final package rebuild exits0; native extension and PortSupervisor imports
  pass. Log: `artifacts/prima-port-dispatch-package-final.log`. Staged helper SHA256:
  `2d1df2f64dee4373056d5d4011606565fc92f0af4b70d2be490d4282f3798b01`.
- Ruff and `git diff --check` pass; canonical kllm-prima is updated and its owned
  project Codex discovery copy is synchronized.

The signed three-agent fixture verifies the barrier before every spawn, partial
native admission with lost reply plus failed cleanup, complete retry, old capability
refusal before reservation/mutation, and existing cancellation/readiness/recovery.
Node-local tests reconstruct or expire real owned children, retain native-rule intent
when process stop fails, and preserve an unrelated live child. Adapters, native
rules/runtime capture and model execution are controlled; channels, asset files,
children, HTTP, process identity and persistence are real. These results do not prove
privileged-helper IPC/service mutation, physical Ethernet/firewall connectivity,
distributed model generation, GPU placement or performance.

T057 closes this controlled production composition/node-lease subset. T056 retains
direct administrator/Linux/macOS backends, helper-independent expiry while its agent
is absent, changed/lost native ownership review, protected IPC/service failure and
physical OS acceptance. Wider API/placement/planner/local performance/platform
requirements stay open. Physical distributed cluster gates and validations will be
tested later. Prima source remains clean at f11a4a2/recipe21; no fork change/push was
needed, Kluster edits remain uncommitted and all production qualification stays false.

T057 follow-up: a port-only owner now marks the dispatcher busy, blocks both local
and distributed Run/preparation, and is drained by Stop even without an active
coordinator/rank. The regression passes. Final focused147passed/3skipped7.22s and
full Python1382passed/19skipped48.34s pass; four existing full-suite warnings remain.
Full session76250 exits0; log `artifacts/prima-port-dispatch-final-python-v2.log`.
Supported package rebuild and Ruff/whitespace checks pass; package log:
`artifacts/prima-port-dispatch-package-final-v2.log`. No Prima source/recipe changed.

Initial published local gateway reruns both pass38 checks (dense13600/MoE30019,
development_server=False, immutablef11/recipe21). Receipts/logs:
`prima-port-dispatch-published-gateway-{dense,moe}.json/log`. The final follow-up
dense94645-v2 receipt is running after the port-only guard; MoE-v2 will follow.
These local checks do not establish physical firewall/cluster operation or performance.

Final T057 results: published dense94645-v2 and MoE12710-v2 both exit0 and each
record38 passed checks, development_server=False, immutable sourcef11/recipe21.
Receipts/logs are `prima-port-dispatch-published-gateway-{dense,moe}-v2.json/log`.
Both supplied models retain the affected local gateway/profile/lifecycle behavior;
this remains local acceptance of that subset, not performance or distributed qualification.

The new cancellation regression reproduces premature completion while an outstanding
native callback is still live. Admission/release now protect the executor future and
settle its outcome before propagating cancellation, including repeated cancellation.
Coordinator generation checks bind delayed admissions, preventing a stale callback
from entering another run. The regression passes after the fix. Focused148passed/
3skipped6.82s and final full Python1383passed/19skipped47.25s pass (four existing
full-suite warnings); session98834 exits0. Final full log:
`artifacts/prima-port-dispatch-final-python-v3.log`. Supported package-v3 rebuild,
native import, canonical skill synchronization, Ruff and whitespace checks pass;
package log `artifacts/prima-port-dispatch-package-final-v3.log`.

All model/test handles are terminal. Native Prima source/recipe and disabled
qualification are unchanged; Kluster edits remain uncommitted. T057 is complete
for the implemented composition/node-lease and controlled/local regression scope.
T056 and the wider integration requirements remain open. Physical distributed
cluster gates and validations will be tested later.

### Independent native rule watchdog — 2026-10-12 (T058)

The privileged helper now shares its serialized ownership state with a separate
timer thread; IPC connection/read waits do not hold that state lock. Its monotonic
deadlines are not restored after restart. Initial native admission is bounded to600s;
fresh signed exact-contract `prima_renew_ports` switches it to90s, beyond the agent's
60s rank lease. Renewals require current key/cluster/term/session/private assignment,
run/manifest and existing rule snapshots; replay, changed ownership, expired or
cleanup-pending admissions cannot revive a lease. Status reads do not renew.

The coordinator forwards new signed renewals immediately after rank startup and
during monitoring. Workers require a live owned rank and current private contract;
local rank renewal uses the same signed native helper boundary. Membership and
read-only port capability now report2; helper watchdog_version1 and an explicit
empty owned-rule ledger are required before mutation. Old version1 nodes/helpers
receive upgrade errors rather than entering an unsupported renewal path.

Expired/restored native rules drain independently of the agent. Failed or changed
rules stay in durable cleanup_pending while other expired runs are attempted. The
watchdog removes only verified owned rules; it neither kills processes nor proves
existing TCP traffic/generation stopped. Node/cluster process ownership and final
rank-stop acknowledgements remain separate. Watchdog shutdown joins its thread.

Verification:

- Standalone native helper: `15 passed in 1.65s`; log
  `artifacts/prima-watchdog-native-tests.log`, two dead-code warnings. New checks
  cover signed exact renewal/replay/non-revival, changed-rule retention, other-run
  cleanup, and real idle timer ticks with no agent/IPC activity using controlled
  rule operations. A current renewed lease survives those ticks. Existing native
  contract/vector, reconstruction and non-executing PowerShell checks still pass.
- Focused Python port/dispatch/admission/rank/RPC/skill boundaries:
  `162 passed, 3 skipped in 8.00s`, one existing deprecation warning. Real signed
  three-agent fixtures verify immediate and monitor renewals; version1 refusal
  occurs before reservation/rule mutation. Negative checks reject dead/idle ranks,
  changed manifest/ports/authority and missing/ill-typed watchdog availability.
- Full Python: `1397 passed, 19 skipped in 46.89s`, four existing deprecation
  warnings. Session71623 exits0; `artifacts/prima-watchdog-final-python.log`.
- Supported package rebuild exits0, native extension import passes; final log
  `artifacts/prima-watchdog-package-final.log`. Staged helper SHA256:
  `40ebd51072d9da9d37e1a5ef3c5564e9a17f88911a4882c3d5b3450794c813ed`.
  Rebuilding stages it without installing/restarting the protected helper service.
- Ruff/whitespace checks pass. kllm-prima and its owned project Codex copy are updated.

No physical firewall/private-interface mutation, privileged helper deployment/IPC,
service failure/reboot or distributed generation result is claimed. Protected OS
expiry/removal/traffic behavior, direct administrator/Linux/macOS paths and missing/
changed-rule review remain T056 work. All source qualification stays false. Physical
distributed cluster gates and validations will be tested later. Prima stays clean
at f11a4a2/recipe21 with no fork change/push required; Kluster edits are uncommitted.
The broad API/placement/assets/planner/performance integration objective remains active.

### Active asset/approval lease renewal — 2026-10-12 (T059)

Source grants now start just before each asset transfer and extend only after an
exact forward acknowledgement for the served range. Offset booleans, wrong/repeated
acknowledgements, changed source identity and expired grants fail without renewal.
Reads alone do not extend a grant. Source hashing checks cancellation/authority and
reports progress while preserving complete SHA256 and before/after file identity.

Successful authenticated worker chunks extend their600s approval. Signed
prima_prepare_renew binds safe-integer progress to job/operation/sender/authority;
expired/cancelled jobs and unchanged/replayed progress cannot revive approval.
The master periodically renews all enrolled workers from new preparation progress,
including already-complete participants, and performs final progress renewal before
publishing prepared. prima_asset_lease_version1 fences older workers. Cache checks
and chunk I/O run in worker threads; the command event loop remains responsive.

Focused transfer/rank/dispatch/network/RPC/skill checks pass179/3skipped7.39s with
one existing deprecation warning. Seventeen new checks use real files and controlled
clocks, including transfers beyond initial lifetime, absent/bad/foreign/replayed
acknowledgements, source drift/revocation, idle master renewal, job expiry/cancel/
authority mismatch and nonblocking cache verification. The real signed WebSocket
case verifies renewal and rejection of duplicate progress; three-agent preparation
and Run/Stop fixture regressions pass.

Initial full Python has one scheduling-sensitive monitor assertion (1413passed/
19skipped); its unchanged isolated test passes1/4.74s. The test now waits for actual
renewal acknowledgements within a bounded loop instead of assuming one nonblocking
monitor call acquires its lock. Final full Python passes1414/19skipped46.49s with
four existing deprecation warnings, session26188 exits0. Logs:
`artifacts/prima-transfer-leases-final-python.log` and distinct
`artifacts/prima-transfer-leases-final-python-v2.log`. Supported package rebuild,
Ruff and whitespace checks pass; package log `artifacts/prima-transfer-leases-package-final.log`.

The published local dense gateway8809 exits0, with its separate
`prima-transfer-leases-published-gateway-dense.json/log` receipt. Published MoE95011
is live now and must be observed to terminal before claiming its result. These are
affected local gateway/profile/lifecycle checks, not large physical multi-machine
transfer throughput or distributed qualification. No Prima source/recipe change or
fork push is required; sourcef11/recipe21 and closed qualification remain unchanged.
Physical distributed cluster gates and validations will be tested later. Wider
T015/T012–T022/T032/T039 requirements and the integration goal remain open.

T059 final follow-up: published dense8809 and MoE95011 both exit0 and each record38
passed checks, development_server=False, sourcef11/recipe21. Receipts/logs are
`prima-transfer-leases-published-gateway-{dense,moe}.json/log`. Supported final package
rebuild/native import, canonical skill synchronization and whitespace checks pass.
All model/test handles are terminal; no Prima source or qualification policy changed.
These are affected local gateway/profile/lifecycle results, not physical distributed
asset throughput or model compatibility/placement/performance acceptance. Those
broader requirements remain active; distributed cluster gates and validations will
be tested later.

### Local listener ownership recovery — 2026-10-12 (T060)

The missing local resolution path was reproduced with two failing PortSupervisor
regressions before implementation. The protected helper now offers local
prima_ports_review/resolve and optional recovery_version1. Review binds the full
durable owner, private adapter scope and current per-rule snapshots to a SHA256
digest; exact/changed/unknown/absent classifications expose lost creation receipts.
Active unexpired admission and foreign network/run identity are refused. A stale
snapshot or changed durable owner cannot authorize a decision.

Token-authenticated, same-origin/loopback-only GET/POST endpoints use the node's
PortSupervisor lock and require its matching owned rank to be stopped. restore_owned
retains original creation receipts and rechecks exact removal; it never adopts a
changed rule. Explicit keep_current preserves all current rules and relinquishes
local admission ownership only. Preserved rules require manual management. Failed
native or node persistence keeps ownership retryable; normal signed cleanup can
retry after a lost local resolution acknowledgement. Neither local recovery nor
helper status releases the cluster reservation or proves other ranks stopped.
Setup status surfaces listener-only recovery resources; older helpers receive an
upgrade error before a recovery request. Existing admission capability2 is unchanged.

Verification:

- Focused helper/API/ports/dispatch/coordinator/admission/network/skills suite:
  139 passed, 2 skipped in6.57s before the final helper-upgrade/status additions.
- Final full Python: 1431 passed, 19 skipped, four existing Starlette warnings
  in46.45s; session3901 exits0. Log artifacts/prima-listener-recovery-python.log.
- Standalone native helper: 20 passed in1.49s, two existing dead-code warnings.
  Log artifacts/prima-listener-recovery-native-tests.log. Five new native tests
  cover ownership classifications, stale/current-owner binding, preserved rules,
  exact restoration, persistence failure and unrelated-run preservation. Native
  rule operations are controlled; PowerShell parsing executes no firewall commands.
- Supported package rebuild exits0, native import returnsTrue. Log
  artifacts/prima-listener-recovery-package.log. Staged helper SHA256
  5fe3d6b3d490971f7d133a4725be091455e6eee05d5789de1056205844dcdd67.
- Ruff/whitespace checks pass. Canonical kllm-prima and its owned project Codex
  discovery copy are synchronized.

HTTP checks exercise the real ASGI router, authorization/schema/origin checks,
PortSupervisor persistence and HelperPlatform request boundary with controlled
helper responses; they are not protected named-pipe/service or physical OS proof.
No firewall/private-adapter mutation or privileged helper deployment occurred.
Prima checkout remains clean atf11a4a2/recipe21; no native Prima change or push was
needed. Kluster source edits remain uncommitted and qualification stays disabled.
T056 admin/direct/Linux/macOS, dashboard controls, protected IPC/service failure and
physical recovery acceptance remain open, as do the broader API/placement/assets/
profiling/planner/correctness/performance requirements. Physical distributed cluster
gates and validations will be tested later. The full integration goal remains active.

### Dashboard listener recovery — 2026-10-12 (T061)

The browser reproduced the missing Review Prima rules control before implementation
(prima-recovery-dashboard-before.log). The Private 10GbE network panel now lists
local pending Prima runs in master and worker views and shows exact ports/peers,
ownership labels and full snapshots. Source/manifest/run/digest and bounded rule
shape are checked before decisions appear. Changed/unknown rules disable removal;
keeping all current rules requires explicit manual-management acknowledgement.

Refresh/close/new review and every decision attempt invalidate the displayed review
and acknowledgement. Original abort signals and work revisions prevent stale read
results/errors/busy flags after close/reopen. Stale decisions, foreign review IDs,
local-only denial and invalid resolution acknowledgement cannot publish completion.
Local success instructs the user to retry Stop on the master; no cluster ownership
or inference state is optimistically released/restarted.

Verification:

- Full dashboard runner exits0, session72979: 63 non-browser plus8 browser tests
  pass, zero skips, phase durations27.42s/70.23s. Log
  artifacts/prima-recovery-dashboard-all.log. Production app source is unchanged
  since that run; subsequent fixture additions are verified by the focused run below.
- Final focused Chrome/Svelte/HTTP case passes1 in6.85s (runner7.56s), including
  master/worker-local recovery, real bearer-token forwarding, stale/foreign/unknown/
  absent/exact rule states, delayed read plus close/reopen, acknowledgement and both
  decisions, local-only refusal and invalid helper acknowledgement. No unhandled
  app or dev-client browser exceptions. Log
  artifacts/prima-recovery-dashboard-browser-final-v2.log.
- Final fixture loads production styles and viewport metadata and asserts innerWidth
  equals390/1280 CSS pixels. Panel/table/snapshot bounds fit both widths; screenshots
  artifacts/prima-listener-recovery-{390,1280}.png were visually inspected. The earlier
  scaled mobile fixture lacked viewport metadata and is not acceptance evidence.
- Svelte check: zero errors/warnings. Build exits0, session19068; existing fallback
  overwrite and NO_COLOR/FORCE_COLOR notices remain. Logs
  prima-recovery-dashboard-check-final.log and prima-recovery-dashboard-build.log.
- Supported package rebuild exits0; prima-recovery-dashboard-package.log. Native
  import/artifact follow-up is recorded below. Skill synchronization passes;
  affected skill tests26passed/2skipped2.84s. Whitespace checks pass.

The first final exception assertion exposed Vite8's unconditional CSS-client
WebSocket connection despite fixture HMR/ws disabling (browser-final.log). The
fixture retains the installed client's style helpers but removes that one connect
expression, guarded by an upgrade assertion; it neither changes production source
nor filters browser errors. The corrected focused case passes. An earlier UI test
read its checkbox before asynchronous review publication; it now waits for the
control (focused-v2/v3 logs). Original failures remain recorded separately.

Browser helper/native rules and cluster state are controlled HTTP fixtures. These
results prove the app interaction/layout boundary, not privileged Windows helper
deployment/IPC, physical firewall mutation, GPU execution or distributed acceptance.
No qualification, Prima source/recipe or live cluster/network state was changed.
T056 admin/direct/Linux/macOS, protected service/IPC and physical recovery acceptance,
plus broader API/placement/assets/profiling/planner/correctness/performance work remain
open. Physical distributed cluster gates and validations will be tested later.

T061 final artifact follow-up: supported native import returnsTrue, the built client
bundle contains the new recovery controls, canonical/discovery skill hashes match
(7d7ee413e8d5919f6a5f99173123d3d3203011ff54386a605efb404adf94c19a), and whitespace
checks pass. All test/build/browser handles are terminal. The broad integration goal
remains active; no physical distributed acceptance or qualification is inferred.

### Bounded Windows helper IPC — 2026-10-12 (T062)

An isolated Windows pipe reproduced the original client waiting beyond its test
deadline on a partial reply; the fixture eventually disconnects to avoid stranding
the test (prima-helper-ipc-before.log,1failed/6deselected3.62s). Helper requests now
use one monotonic deadline spanning their serialization queue, connection, framed
write and response: queries8s, mutations/recovery45s. Busy pipe opens retry within
that deadline. Windows cancellation owns an actual thread handle, retries across
call gaps and joins after I/O settles before the client lock opens.

The server uses the same retry principle and native FlushFileBuffers before
DisconnectNamedPipe so unread reply bytes are not discarded. Its45s timer includes
the flush; dispatch overruns do not leave a later flush waiting after a missed
one-shot cancellation. Timeout does not prove a server-side mutation stopped:
durable listener/cluster intents remain pending until the exact cleanup ACK. Wire
schemas and original signed-envelope bytes are unchanged. Malformed/oversized
responses refuse with HELPER_REFUSED. TIMEOUT is distinct from ADMIN_REQUIRED in
setup status/UI, blocks setup and avoids prompting for another grant.

Verification so far:

- Focused helper/ports/recovery/dispatch/admission/network checks107passed7.48s;
  final portable/Windows IPC checks21passed1.78s after delete/review/revoke deadline
  coverage. Windows pipe tests exercise partial header/body, blocked write, queue
  timeout, fragmented/invalid/oversized replies, actual protected-image rejection
  before bytes, cancelled Prima wait settlement and persisted timeout ownership
  followed by acknowledged cleanup. Successful fixture requests substitute only
  protected installation verification; helper mutation/policy replies are controlled.
- Standalone native helper24passed1.77s, two existing dead-code warnings. Four new
  real Windows pipe tests cover delayed consumption, partial input cancellation,
  dispatch-to-flush cancellation gaps and oversized input before policy dispatch.
  Log artifacts/prima-helper-ipc-native-tests-final.log.
- First full Python:1447passed/19skipped plus unrelated two-process SQLite startup
  PermissionError. Unchanged isolated test1passed2.57s. Second full:1448passed/
  19skipped plus native three-agent freshness expiry; unchanged isolated1passed9.84s.
  Original logs python.log/python-final.log and sqlite-isolated.log/network-isolated.log
  remain separate. No freshness threshold or either test was relaxed/edited.
- Final full Python1452passed/19skipped51.61s, four existing Starlette warnings;
  session44558 exits0. Log artifacts/prima-helper-ipc-python-final-v2.log.
- Focused browser case1passed7.23s verifies timeout guidance, no new authorization
  prompt and blocked setup, plus existing master/worker rule recovery. Log
  prima-helper-ipc-dashboard-focused.log. Full dashboard session50172 is still live;
  observe that handle before asserting its browser result. Check/build/final package
  verification are still required.
- Initial supported package/native import pass; staged helper SHA256
  12fa6c17384190ede38340a02caf188738398e9324a17f09b0619825df335db5.
  Final package follows the dashboard build. Ruff/whitespace and canonical/discovery
  skill synchronization pass (923439599a5c3166154eb8ef2e6d752ed4f3a560e14d0d5541bcf5f69299b11e).

These are real local pipe/thread/transport/file and controlled helper-policy tests.
They do not prove accepted protected service deployment, authorization ACLs, service
crash/restart, privileged firewall mutation, network adapter changes or distributed
generation. No protected helper was deployed and no Prima source/recipe changed.
T056 administrator/direct/Linux/macOS paths and protected service/physical OS
acceptance remain open, with broader API/placement/assets/profiling/planner/local
correctness/performance requirements. Physical distributed cluster gates and
validations will be tested later; qualification is disabled and the goal stays active.

T062 final follow-up: dashboard session50172 exits0 with71passed/0skipped
(63nonbrowser27.22s,8browser69.65s), log prima-helper-ipc-dashboard-all.log. Svelte
check finds0errors/0warnings and the dashboard build exits0; only existing fallback
overwrite and color-environment notices remain. Logs dashboard-check.log and
dashboard-build.log under artifacts/prima-helper-ipc-*. Final supported package
rebuild exits0 (package-final.log), native import returnsTrue, and the built client
contains timeout attention guidance. Staged helper SHA remains12fa6c17384190ede38340a02caf188738398e9324a17f09b0619825df335db5.
Whitespace and synchronized skill checks pass; all test/build/browser handles are
terminal. T062's bounded transport/API/UI subset is complete. No protected service
deployment, live firewall/adapter mutation, Prima source/recipe change or qualification
was performed. Wider T056 and engine API/placement/assets/planner/performance work
remain active; physical distributed cluster gates and validations will be tested later.

### Windows direct native policy backend and Stop cleanup — 2026-10-12 (T063/T064)

The direct backend composes the existing manual Windows adapter with an owned native
`--stdio-prima` process. Native administrator/mode/signature/replay/assignment checks,
exact listener ownership, independent expiry and recovery remain enforced. Direct
scope observation and stable signed reconciliation never mutate addresses/DHCP or
restore manual settings after restart. Remembered provider/grant ownership cannot
silently switch to an empty protected/direct ledger. Shutdown closes owned pipes,
waits for the child and retains failed/changed rules for recovery.

Private replies carry independent request UUIDs. Real subprocess-pipe tests cover
late admission replies, partial-frame timeout continuation, malformed envelopes,
scope/rule retention on grant changes and replacement mode negotiation. Six new
tests reproduced malformed/missing/foreign status retirement failures before the
fix (artifacts/prima-direct-grant-before.log,6failed1.96s). Retirement now requires
explicit typed empty ownership lists from the exact old grant, then probes the new
process before forwarding any original signed authorization.

The first full suite also exposed a Stop race: all owned ranks/listeners were stopped,
but the monitor could retire the coordinator before Stop cleared active_config.
Original artifacts/prima-direct-python.log records1465passed/19skipped plus1failure;
unchanged isolated dispatch passes1/4.80s. T064 retains the run ID through retirement,
clears matching state only after acknowledged Stop and preserves foreign state.
Two deterministic regressions cover both outcomes. The initial corrected full suite
passes1468/19skipped46.66s (prima-direct-python-final.log).

Final delivery evidence:

- Native helper28passed1.57s, artifacts/prima-direct-native-tests.log; shared signed
  policy vectors and direct-mode/restart/reconciliation contracts use controlled rules.
- Focused direct/Stop/dispatch/ports72passed6.40s, prima-direct-final-focused.log.
  Final owner/grant/guided/Stop36passed3.37s, prima-direct-final-owner-tests.log.
- Full Python1478passed/19skipped48.63s,4existing Starlette warnings;
  artifacts/prima-direct-delivery-python.log, session75084 exits0.
- Supported `uv run python scripts/build.py --skip-ui --skip-path-shim` exits0,
  prima-direct-delivery-package.log; `native_available()` returnsTrue. Staged helper
  SHA256f577dbd0daec43d61d5ac1ef70dcddd1180f2639a61981c416d1d3c9ee3c2421.
- Canonical kllm-prima and project Codex copy synchronized with
  `scripts/install_skills.py --agent codex --scope project --category development`;
  both SHA256ccc98d552ea6863de62cee22876b37ee906e938a70b7de0e991f473141710980.
  Ruff and whitespace checks pass. Three native dead-code warnings remain.
- Actual staged `--stdio-prima` invocation on this unelevated machine exits1 with
  empty stdout and Administrator authorization is required, before grant/journal read.
  Accepted elevated mutation is unavailable here and remains T056 physical acceptance.

Successful stdio policy replies are controlled Python-child fixtures; real native
signature/replay contracts do not prove privileged OS behavior. No protected service,
firewall/address mutation or live cluster was deployed. Dashboard application source
is unchanged since T062's71-pass/check/build receipts. Prima checkout remains clean
at published f11a4a20e7f34e2a67a6985164ac0d5c6c9b30e3, recipe21 unchanged; no fork push
is needed for this Kluster-only work. All test/build handles are terminal. T063/T064
source delivery is complete, while T012–T022/T032/T039/T056 remain open at their wider
boundaries. Physical distributed cluster gates and validations will be tested later;
all production qualification remains false and the full goal stays active.

### Published typed profiler matrices — 2026-10-12 (T065; T066 remains open)

Review of T017/T021 found native device_flops uploading raw float bytes into F16
and quantized tensor buffers. A completed-result check reproduced invalid F16,
Q4_K,Q6_K,Q8_0 computations while F32 passed (prima-profiler-contract-before.log).
The fix uses ggml_quantize_chunk, checks allocation/selected-backend operation and
compute status, refuses zero elapsed time, and validates every output against a
reference from decoded operands. It removes the redundant fallback scheduler and
its leaked backend ownership. CPU/GPU measurements cannot substitute for each other.

Expanded CPU checks initially rejected IQ constants compared to unquantized ones;
that invalid reference was corrected to the encoded values, not accepted as a pass.
Final CPU checks all12 existing profiler types pass. The initial all-format physical
Vulkan request records3 unavailable IQ formats (vulkan-final.json exits1); the
backend source explicitly lacks IQ2_XXS/IQ1_S/IQ1_M matrix kernels. Final contracts
require9supported numeric results and3explicit zero-measurement refusals. This is
not IQ GPU execution/parity. Physical RX9070XT UUID00000000030000000000000000000000,
driver8389003 is freshly enumerated and selected before each GPU run.

Publication and fresh consumption:

- Fork commit e7ee8f6ced718dca7187cfba4407f44b5c88884d is pushed and remote-verified
  on codex/kluster-integration. Only relevant source/test/CI/docs are committed.
- Recipe22 digest29419d3ca8cd75f4d04771887e2201d07b0b5635fe96b67fe5d045ae3d91a700
  consumes that full immutable SHA without patches. Fresh installer build exits0;
  artifacts/prima-profiler-published22/source is clean at that SHA and capture_runtime
  verifies every staged artifact hash. RuntimeIDb839c34accef13bb9bf239dd959e746a7f3fedea0ef4edba5490d92133bd8148.
  Server4e1004daf1034f1dd44d7fbf82db0c8813d01280ec597ab9cb3bcf29709f719e,
  CLI5695cba381ae7985ac1a4acc90b05d17c026466d4baa983f98d9ca09232c8b25,
  profiler6db4eb840820f0ea26c9c5298bc249e88bee81fe7ac69a66b0835f3db6418f37.
- Fresh published native CTest3/3pass12.58s (prima-profiler-published22-contract.log).
  The development cached build's unrestricted CTest initially included unbuilt legacy
  test targets and exited8; its relevant three contracts then pass. The fresh managed
  build has LLAMA_BUILD_TESTS=OFF and all its registered contracts are built/passing.
- Published physical GPU contract passes9numeric formats and3unsupported refusals;
  receipt prima-profiler-published22-vulkan.json contains exact selected-device evidence.
- CI38189617543 and38189620626 both complete success: CPU Ubuntu/macOS/Windows and
  Windows Vulkan compilation. All include the new CPU profiler contract; hosted
  Vulkan CI does not claim physical GPU execution.
- Supported Kluster package rebuild/native import returnsTrue; full Python1478passed/
  19skipped48.43s,4existing warnings (prima-profiler-python.log,98589terminal0).
  Canonical/project Codex skill SHA0801b7ec91e8b68b7551314dbd762a9cd8f5609386d605c0c812ee76e8d4b280
  matches after installer synchronization. Ruff and normal repository whitespace checks
  pass. A one-off core.autocrlf=false override incorrectly treated CRLF as whitespace;
  no files were changed for that check and the repository-configured check exits0.
- Fresh dense gateway/profile/logprobs38checks pass; receipt
  prima-profiler-published22-dense-gateway.json. Fresh MoE gateway/logprobs36checks
  also pass (prima-profiler-published22-moe-gateway.json,97553terminal0). The MoE
  follow-up does not repeat generation-mode profiling. Both gateway logs include
  taskkill process-not-found notices during already-stopped child cleanup; all
  owned lifecycle/restart assertions pass. No pending child remains from these runs.

Full-model kernel profiling remains a separate defect. Both unchanged published21
and the development fix exit1 after Uncaught tensor on Qwen3 dense, with logs
prima-profiler-kernel-{published21,development}.log. A production ProfileJobs request
using the fresh verified published22 runtime also settles failed, retains0 processes
and caches0 profiles (prima-profiler-published22-kernel-job.json/log). Its lifecycle
refusal is verified; usable full-model kernel profiling is not. T066 must repair cost
extraction/actual tensor-type measurements and the Vulkan transfer/bandwidth path
before T017 remote planner consumption. End-to-end generation profiling is distinct
and the dense gateway exercises it successfully. No paired performance gate is rerun
or passed from synthetic GFLOPS. Wider native compatibility/platform/placement and
local correctness/performance work remains open. No live helper/firewall/cluster
was changed. Physical distributed cluster gates and validations will be tested later.
Production scopes stay empty/false; the full goal remains active.

T065 final delivery is complete for the typed-matrix repair. Source/native/physical
matrix checks, publication, fresh build/artifact capture, CI, affected gateway checks,
supported package/import and documentation/skill synchronization all pass. All test/
build handles are terminal. T066 and the broader platform/API/placement/assets/remote
planner/local correctness/performance requirements remain open; no full integration
or qualification completion is claimed.

### Typed full-model kernel producer/cache repair — 2026-10-12 (T066 partial)

Inspection of the original job's fio_output_36948.log confirms the immediate exit1
was absent fio on Windows, alongside Uncaught tensor returning partial Qwen3 costs.
The previous Uncaught-tensor receipts remain valid observations, but that message
alone did not prove the exit's cause. The native repair uses complete GGUF metadata
without recreating/resetting live model layers. It validates required roles and
actual attention dimensions/head norms, counts only active MoE expert arithmetic,
retains full expert storage, and emits absolute per-layer/input/output typed costs.

Native schema2 is a typed_roofline_estimate, not completed end-to-end generation
timing. Required model tensor types run checked CPU and selected-backend matrix
probes. GPU memory reading uses a checked8192-square matrix-vector graph (five timed
iterations) instead of unsupported custom READ; KV copies use valid F16 operands
and compare completed bytes. The host RAM probe now gives threads disjoint slices.

The fio dependency is removed. Serial native unbuffered I/O owns an exclusive64MiB
temporary file beside the model on the same filesystem, reports queue-depth1 and
actual alignment/block sizes, checks read bytes and bounds work to30s per probe.
Windows uses the reported logical/physical alignment, aligned allocation and
NO_BUFFERING/WRITE_THROUGH/DELETE_ON_CLOSE. Linux uses O_DIRECT/O_SYNC; macOS uses
F_NOCACHE/flush. Unsupported/unwritable model storage refuses the measurement.
Hardware/controller caches are part of observed I/O, not guaranteed uncached media.
Native --io checks pass on Windows; an owned --hold-io-file child is killed/awaited
and its sole private file disappears. No model bytes, firewall or adapter changed.

Kluster validates native backend/context/history/threads, ordered complete layers,
finite positive per-type rates/storage and estimate consistency before caching.
CPU profiles cannot claim GPU costs. Typed capability cannot downgrade to schema1.
Cache identity includes typed settings plus observed OS/CPU/core/RAM/device identity;
the host and selected device are rechecked before publication. Absolute layer vectors,
input/output and measured disk/copy costs survive serialization. Corrupt/negative/
incomplete cache vectors refuse reads. Legacy fixture/API handling remains separate.

Evidence:

- Initial build error was the private prototype using an undefined LLAMA_API macro;
  corrected to the existing C++ profiler declaration convention. A first native run
  then exposed Prima's skeleton-model API; metadata initialization and borrowed GGUF
  descriptors now precede extraction and metadata ownership is explicitly released.
- Development native contracts3/3pass12.69s, real Windows I/O/death cleanup pass,
  physical RX9070XT9supported matrix comparisons plus3unsupported IQ refusals and
  checked GPU read probe pass. Logs prima-typed-profile-{contract,io} and vulkan.json.
  Native raw dense/MoE schema2 reports and production ProfileJobs development override
  prepare/cache28/48layers with no retained process; overrides are explicitly labelled.
- Focused Python105passed/2skipped3.54s, including20 new typed/cache/real owned-child
  checks; no hardware claim comes from controlled policy reports. Full1498passed/
  19skipped49.53s before publication, then1498/19skipped49.37s after recipe23 pin.
  Four existing Starlette warnings remain. Logs prima-typed-profile-python{,-final}.log.
- Fork72d4edaba7f0111322fe192027c166fe85985e33 is committed/pushed and remote-verified.
  Recipe23 digest0bc41d8bca717abaa221ac3bb1783dab00c3ccdc14593922f8fda4a591a5992a
  consumes it in a fresh managed Windows/Vulkan build; source is clean at that SHA,
  and capture_runtime validates all staged hashes. RuntimeID
  12d10ab8aae79ee51371a81566f030d4ea84f3cbd1334534154a15179c741a97.
  Server2b94db551e4252907d63cb5776a98e5dab3628170e404d23bfa9786728a5e4a3,
  CLIccf5b8f49f3e6c316491caf8cd1e77ae50459963a11f6a71267b7910e116cd08,
  profiler3295c7b6fecb63bfd4acad849686aad087bf7da2889be307f08a0c688e437da4.
- Fresh published native CTest3/3pass12.92s and selected physical GPU/read contracts
  pass (prima-typed-profile-published23-contract.log/vulkan.json). Actual published
  ProfileJobs prepare/cache both supplied dense/MoE28/48-layer profiles and retain0
  processes (prima-typed-profile-managed-published23.json/log,76851terminal0).
- CI38194600187/38194602916 both complete success across CPU Ubuntu/macOS/Windows
  and Windows Vulkan compilation. This proves portable build/contracts, not physical
  Linux/macOS GPU/storage or cluster acceptance.
- Supported final Kluster rebuild/import returnsTrue. Canonical/project Codex skill
  hashes match e97a2a9fc9a48ffd206a24cfd3fc89152eee96bf05132824b5bcc4e9b62cdedd;
  helper SHA remainsf577dbd0daec43d61d5ac1ef70dcddd1180f2639a61981c416d1d3c9ee3c2421.
  Ruff/whitespace checks pass. Published dense gateway/profile/logprobs38checks and
  MoE gateway/logprobs36checks pass (72330/47080terminal0). MoE generation profiling
  is not repeated in that gateway run; its typed kernel job is separately verified.
  Already-stopped child taskkill notices remain in the logs; all owned lifecycle/
  restart assertions pass and no child remains from either assessment.

T066 remains open for authoritative remote consumption and candidate scoring,
fresh matching link/disk context and resource reservation. Existing local-only
generation-cache planning does not become a distributed planner from these new
vectors. Broader native APIs/model/layout/platform and local correctness/performance
gates remain open; no policy threshold is relaxed and no scope is enabled. Physical
distributed cluster gates and validations will be tested later. Root changes remain
uncommitted, the Prima fork is published/clean, and the full goal stays active.

T066 producer/cache delivery follow-up: all local test/build/profile/gateway handles
are terminal. Published source/native/CPU/GPU/I/O/job/cache/CI/gateway/package evidence
proves the profiler repair, while the broader task stays unchecked for remote planner
consumption and reservations. No physical distributed acceptance or performance
qualification is inferred from these successful estimates and regression suites.

### Preparation-bound signed profile consumption — 2026-10-12 (T067)

The existing signed profile handlers accepted flat requests and exposed status/cancel
by job ID without the approved operation's owner. Worker prima_profile_version2 now
requires preparation_job_id, manifest_digest and ProfileRequest. Before starting
work it checks current complete/unexpired signed assets and exact manifest, model,
runtime, node/rank, backend and effective context/threads/device settings. Distributed
preparation requests typed kernel mode. CPU ranks explicitly choose CPU even when
their managed build is Vulkan; no requested GPU measurement is substituted.

Each profile persists its control_owner before native launch, refuses request-ID
adoption by another owner, and binds status/cancel to the same operation/sender/
authority. Native work/cache publication rechecks current authority and the600s
progress-renewed preparation approval; it does not extend the original wire deadline.
Model resolution uses that preparation's owned layout. Worker starts resolve/cache
I/O off the command loop. Cancellation and publication share the profile lock and
the final guard rechecks cancellation after ownership callbacks.

Master preparation requires profile protocol2 on selected workers before asset
transfer. Unselected members do not impose an unnecessary upgrade requirement.
It requests exact effective settings, compares returned ownership/node/model/runtime/
backend and complete typed cost vectors, then obtains fresh signed participant
evidence before import. Hardware hashes canonicalize the observed device fields so
native discovery and signed evidence match without logical-description/free-memory
noise. Older kernel cache hashes remain available but do not match the new identity.

Verification:

- Existing focused profile/dispatch/lease/heartbeat checks76passed/2skipped5.76s.
- Expanded real signed three-agent preparation/Run/Stop test passes both without
  profiling and with owned profile subprocesses. It verifies verified asset transfer,
  CPU backend selection, complete eight-layer typed reports for all three nodes,
  current hardware comparison and all three cache records on the master. Signed
  status/cancel from another operation are refused; the owner's original status
  remains prepared. Final two-case result2passed9.96s, prima-profile-signed-owner.log.
  Adapter/rules/model execution/rate reports are controlled fixtures; signed channels,
  owned child processes, HTTP, asset bytes, lease/persistence/cache behavior are real.
- Initial expanded fixture missed its os import; that test setup error is retained
  in prima-profile-signed-before.log (1failed/1passed6.84s) and corrected separately.
- Final focused dispatch/profile/lease/skill checks94passed/2skipped10.87s;
  artifacts/prima-profile-owner-final-focused.log. Full final1499passed/19skipped
  50.93s,4existing Starlette warnings (prima-profile-owner-python-final.log,
  13268terminal0). Earlier full1499/19skipped48.42s is retained separately.
- Supported package rebuild exits0, native_available returnsTrue; helper SHA
  remainsf577dbd0daec43d61d5ac1ef70dcddd1180f2639a61981c416d1d3c9ee3c2421.
  Canonical/project Codex skill hashes matchbc148d222428bf07ba1cef1e3762086c932ee8510a659af557b55669e825d8e2.
  Ruff/whitespace checks pass. Source Prima remains clean/published72d4edaba7f0111322fe192027c166fe85985e33,
  recipe23 unchanged. This is Kluster-only work; no new fork push is required.

All local test/build handles are terminal. T067's signed producer/import boundary
is complete; authoritative candidate scoring, measured exact private links/disk
context and concurrent resource reservations remain T066/T017/T021 requirements.
Normal previews still use matching local full-model generation cache or capacity
fallback; importing vectors does not itself enable distributed estimates. Physical
distributed cluster gates and validations will be tested later. No live helper/
firewall/adapter or cluster was changed and no qualification scope/gate is enabled.
Broader compatibility/layout/platform and local correctness/performance remain open;
root changes are uncommitted and the full goal remains active.


### Preparation-owned directed private stream probes � 2026-10-12 (T068)

Version2 diagnostics transfer3x1MiB with checked64KiB nonce/sample blocks and signed
ready/complete receipts on the existing private listener. Bulk work follows exact
signature/source/network/revision authentication, with2 simultaneous receivers,
10s client measurement/12s server bounds, nonce replay refusal and owned cancellation/
close. Latency includes application authentication/receipt overhead; throughput is
median acknowledged application payload. These are not raw NIC or inference claims.
Windows coarse monotonic timing initially produced zero-duration samples; switching
only measurement timing to perf_counter corrects that measured defect.

Temporary master-approved12s scopes bind probe/operation/manifest/network/authority,
source/target NodeIdentity and assignment revisions. Signed receive/send require
current complete owned worker preparation and exact manifest edges. Live callbacks
recheck owner/cancellation/expiry, accepted authority, pinned keys, exact local
address and source route before grant, transfer acknowledgements and publication.
Worker-to-worker keys remain ephemeral; ordinary pinned pairing is unchanged. Scope
close/expiry refuses reuse. Capability1 is checked on selected workers before assets.

Focused transport/channel/dispatch/network/lease/heartbeat checks64passed/2skipped
27.23s (artifacts/prima-link-final-focused.log). Real signed three-agent preparation/
Run/Stop collects all directed edges, including worker-to-worker, and refuses foreign
operations or expired preparation. Tests cover invalid signature/source/revision/
network, corrupt payload, changed authority/key, busy limit, nonce replay, cancellation,
closed/expired/revoked scope and noninteger geometry. Native model/rates/rules/adapters
and mapped assignments are fixtures; TCP/signatures/owned processes/assets/HTTP and
persistence are real. No physical networking/firewall or helper deployment is used.

First probe log3failed/8passed captures zero-duration timing, Windows reset cleanup
and replay-harness timing assumptions. Corrected12cases pass26.65s; final19 probe
cases include scoped authorization and strict geometry. The initial signed collector
used the master-only authority factory on a worker; its failure is retained and
corrected by checking the current accepted store authority and exact owner digest.
Final signed two-case preparation/Run/Stop passes9.69s before expanded denial checks;
expanded focused suite above passes. These corrections do not relax production gates.

T017/T021/T066 still need authoritative candidate scoring, matching disk/link context
and concurrent resource reservation. All qualification remains disabled. Physical
distributed cluster gates and validations will be tested later. Published native source
72d4edaba7f0111322fe192027c166fe85985e33/recipe23 remains clean and unchanged.


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

Published recipe32 runtime: 3707a5c1bbe05ff172d58dd76c3e74360378e52ba20fad4f19ae7f803e6c82bd
llama-cli.exe: b7256771e87c2d1ff81549a861a6bdd232460302830cac9d38a0525eafc48e9e
llama-server.exe: f7afc0ab12bacd01a91d302a26053613a9de19e5d9a020ee601b4a8217c55f13
profile-tool.exe: 6521546d2c23a9213942a746412c126e9d346acf23eb597b6836e9efa9a36413
Managed build receipt: artifacts/prima-vulkan-diagnostic-published32-build.log.

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

### Automatic-tool parser foundation - recipe36 (2026-10-12)

Published native source: ccc8c5947a0dd108f9c99f95af920ee5be9c160e.
Recipe digest: 2baf75ebf10ec12ad9bf6122a39bdec2d717cab41de2dadfb986080cdb1d5a67.
Clean managed Vulkan runtime:
8cb5137ad019f9a1e1cd1583ef3301725009e6eba5189f74301a11935f41be0d.
The baseline 7fe450 chat/template analysis, schema, PEG and specialized parsers
retain MIT provenance and use vintage Prima API adapters. The bridge serializes
preparation and parsed output. Native sampler grammar can remain unconstrained
until a literal/pattern/token trigger, including split or mid-token markers.
Clones retain partial trigger state; reset returns to dormant grammar. UTF-8
fragments, EOS masking, invalid configuration and nullable-cycle refusal are
checked. Repeated per-call parser elements now consume input rather than repeat
an optional call with nullable whitespace. This fixes the discovered generated
GBNF stack overflow without dropping schemas or replacing model execution.

Clean published native contracts pass5/5. Both supplied dense and MoE model
vocabularies pass Qwen-preset preparation, generated grammar parsing, actual
vocabulary token acceptance and reset checks. The vocabulary fixture must invoke
Prima's metadata loading after initial model allocation. An attempted nonexistent
MoE-specific preset filename refused; the corrected check explicitly uses the
bundled Qwen3 preset with the MoE vocabulary, not an invented model preset.
Published dense and MoE gateway/lifecycle checks each pass49/49. CI runs
38259661919 and38259669671 each pass Windows/Linux/macOS CPU and Windows Vulkan
build/contracts. All owned local build/test/model handles are terminal.
Python passes1606, skips19 (64.60s); package rebuild and
owned development skill synchronization pass.

Preserve the earlier development MoE failed receipt:47 checks passed before
restart refused because this agent changed the root recipe while assessment was
active. Restart correctly enforces the source identity. It is not a model/API
failure or a passing complete lifecycle. Repeat against the settled recipe36 pin.

Receipts: artifacts/prima-auto-parser-published36-manifest-check.json,
artifacts/prima-auto-parser-published36-contracts.log,
artifacts/prima-auto-parser-published36-dense-vocab.log,
artifacts/prima-auto-parser-published36-moe-vocab-qwen-preset.log,
artifacts/prima-auto-parser-published36-dense-gateway.json,
artifacts/prima-auto-parser-python-tests.log and artifacts/prima-auto-parser-package.log.

T083 is scoped parser/sampler infrastructure. Automatic/required HTTP generation,
parallel streaming and protocol admission remain T084; the server still refuses
and does not advertise automatic tools. Broader API/model/placement/platform work
remains open. Qualification flags are false and execution scopes are empty.
Performance and physical distributed acceptance will be tested later on the
user's live cluster of three computers. No local performance gate or diagnostic
extension was run; existing thresholds remain unchanged.

### Automatic/parallel HTTP tools and generated-prefix repair (recipes37/38)
Native673cd92 implements automatic/required tools and parallel streams across all
three protocols, using the baseline bridge for actual generation and parsing.
Invalid grammar refuses instead of silently disabling constraints; internal parser
parameters are reserved on raw completion/infill as well as chat routes. Single-
call generation no longer repeats calls when parallel is disabled. Kluster checks
native automatic/streaming markers after exact execution feature evidence, and
parallel_tools requires its own evidence rather than borrowing tools:auto.

Its clean dense100 suite found six thinking-on channel failures. Regular generated
grammars had not consumed the assistant prefix already placed in the prompt, so
they forced a duplicate header and reasoning became ordinary text. Native37828feb
matches the baseline prefill behavior: accept generation-prefix tokens only for
regular generated tool grammars, preserving special tokens and skipping an inserted
leading space, then reapply after reset. Raw/user grammars remain unprefilled.
Actual Qwen vocabulary checks parse reasoning/calls and exercise repeated reset.
A diagnostic fixture first omitted the opening think tag; its corrected input
contains the actual generated tag. Preserve diagnostic and published37 failures.

Recipe38 source:37828feb156ab14082ce3569b2c8f6aa9d3012c6.
Digest:41a925c516f108d0fd19adae1cfb43d9b781ab2c68224e420069ea93753631b5.
Runtime:848714589332ebe83df1272d9c6b8dffcf403c2e3997c6778cf478bb4e296098.
Clean source/artifact and ordinary Vulkan build verification pass. Development
native5/5, dense100/MoE100 and actual vocabulary prefill/reset checks pass.
Python1617/19skip passes68.37s; package rebuild, Ruff and skill synchronization pass.
The simple automatic echo prompt produces unmarked JSON in both Prima and the
pinned baseline. Preserve text semantics; do not reinterpret that as a tool call.
Explicit tagged activation and ordinary decline are distinct checks. Schemas bind
required/parallel output, IDs survive follow-up histories, all three streams retain
reasoning channels and explicit truncation, owned disconnects settle native slots,
and automatic Chat logprob entries match actual generated usage.
Final clean published native5/5 and dense100/MoE100 gateway/lifecycle checks pass.
Both model vocabularies pass generated-prefix acceptance, reasoning/call parsing
and repeated sampler reset with the bundled Qwen preset. CI runs38269490124 and
38269494774 each pass Windows/Linux/macOS CPU and Windows Vulkan build/contracts.
All owned local build/test/model handles are terminal.

Source qualification remains false with empty execution scopes. T084 does not
close broader T013/T018/model/launch/placement/platform requirements. No performance
acceptance or local timing diagnostic extension is run. Performance and physical
distributed cluster gates/validations will be tested later on the user's live
cluster of three computers, retaining existing criteria.


Final recipe38 receipts:
artifacts/prima-auto-tools-published38-manifest-check.json,
artifacts/prima-auto-tools-published38-contracts.log,
artifacts/prima-auto-tools-published38-dense-vocab.log,
artifacts/prima-auto-tools-published38-moe-vocab.log,
artifacts/prima-auto-tools-published38-dense-gateway.json,
artifacts/prima-auto-tools-published38-moe-gateway.json,
artifacts/prima-auto-tools-published38-ci.json,
artifacts/prima-auto-tools-prefill-final-python-tests.log and
artifacts/prima-auto-tools-prefill-package.log.
T084 is complete for the tested text/template paths. Full integration still has
broader API/model/launch/placement/platform requirements open. Qualification remains
false/empty, with performance and physical distributed validation deferred to the
user's live cluster of three computers. No local performance gate was run or passed.


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

Recipe 92 source/build receipt:
`artifacts/prima-infill-published92-final-manifest-check.json` records exact
published source, recipe digest, clean checkout, three staged executable
hashes and `infill_api_v1` metadata. Kluster syntax and packaging succeeded.
No unit/native/model tests or test fixes ran; they remain deferred until the
full implementation is finished. FIM model/API/backend acceptance is false.
No inference, profiling measurements, distributed or performance validation
ran. Physical distributed and performance checks await the user's live
three-computer cluster. Every qualification gate remains false and execution
scopes remain empty.

Recipe 93 source/build receipt:
`artifacts/prima-raw-completion-published93-manifest-check.json` verifies the
published commit, recipe digest, clean source, three staged executable hashes
and raw_completion_v1 metadata. Native request handling compiled on
Windows/Vulkan and Kluster syntax/packaging checks completed. No unit/native/
model tests, test fixes, inference, profiling measurements or performance
checks ran. Raw completion model/API/backend acceptance is false. Every
qualification gate remains false and execution scopes empty. Physical
distributed and performance acceptance await the user's live three-computer
cluster.

Recipe 93 embedding-alias source/build record: the clean pinned manifest reports embedding_output_v1 and Kluster routes `/embedding` and `/embeddings` through exact served-model checks. No embedding model/API/backend test or acceptance ran. Unit/native/model tests remain deferred until full implementation; physical distributed and performance gates remain deferred to the live three-computer cluster, with qualification false.

T015 layout source/packaging record: staged model/shard/companion paths now require a complete verified basename layout before native use, with failure on altered or missing assets. Python syntax and Kluster packaging passed. A focused regression check exists but was not run; all unit/native/model tests remain deferred until full implementation finishes. No native split-GGUF/companion execution or physical transfer validation ran. Qualification gates remain false and execution scopes empty; distributed and performance checks await the live three-computer cluster.

T015 Windows portability follow-up: asset basenames are now unique under Unicode case folding in both collected files and execution manifests, preventing case-insensitive shard/companion collisions before staging. A focused regression case was added but not run under the deferred test schedule; qualification and physical cluster gates remain unpassed.

T015 native loader compatibility: selected split GGUF models and companions must start with shard 00001, because the pinned Prima loader rejects a later shard as the entrypoint. Asset collection now refuses later selected shards before transfer or launch. A regression case was written but not run; split-GGUF model execution acceptance remains deferred with other unit/native/model tests until implementation finishes.

T017 source progress: the Prima planner now enumerates bounded combinations of optional worker removals while honoring required nodes and explicit priority, retaining at least one native worker for network-bound candidates. It keys distinct candidates by participant set as well as capacity placement so equal-looking CPU plans with different network edges are not collapsed. A regression check was written but not run under the deferred unit/native/model test schedule. This is source and packaging evidence only, not measured planning or performance acceptance; T017 and all qualification gates remain open. Physical distributed and performance checks await the live three-computer cluster.

T016 listener admission source follow-up (2026-10-13): initial journal-save failure now checks the durable record before releasing a pre-helper resource claim. A definitely absent owner releases the claim; persisted or uncertain ownership stays fenced for cleanup. Regression cases were written but not run under the deferred unit/native/model test schedule. Python syntax, git diff check and Kluster packaging passed. No physical distributed or performance acceptance was attempted; qualification remains disabled.
T016 restart follow-up: restored listener journals now reacquire a node reservation before other starts can proceed; a focused restart regression was written but not run. Distributed lifecycle acceptance remains open.
T016 run-reservation follow-up: local Prima runs now retain their node reservation while serving and release it only after rank/listener/coordinator cleanup; restored rank ownership also reclaims a reservation. Focused cases were written but not run under the deferred test schedule.
T016 rank cleanup follow-up: a failed final rank-journal write retains in-memory cleanup ownership so the serving-run reservation cannot be retired early. A regression case was written but not run.
T016 rank ownership source follow-up: RankSupervisor now owns a node reservation through rank stop and restart recovery, including direct signed starts; failure to publish the first owner journal stops the spawned child. Focused regression cases were written but not executed under the user-deferred test schedule.
T016 native spawn handoff follow-up: if process-registry registration fails after a known native child launches, the rank supervisor now journals and drains that PID/token rather than losing the cleanup handle. Regression source was written but not run; no physical or performance acceptance was attempted.
T016 native launcher follow-up: Kluster child registry lock acquisition now precedes model/log-sink spawn, so a poisoned lock cannot orphan a just-created child; a missing log-sink stdin drains the helper. Rust build/source checks only; no unit/native/model tests or physical/performance validation ran.
T014 segmented preview source follow-up: preview and execution capability checks now use the same absolute rank visit count; unreserved noncontiguous plans are refused in preview. Regression cases were written but not run under the deferred test schedule. Native row/tensor/remote-expert and physical distributed/performance acceptance remain open.
T014 candidate source follow-up: candidate-link approval and measured selection now share the preview/native exact segmented graph-visit rule. A regression case was written but not run under the implementation-first test deferral. Kluster packaging passed. Row/tensor/remote-expert, physical distributed and performance acceptance remain open; qualification stays disabled.
T014 device mapping source follow-up: RPC GPU indices now resolve via named RPC inventory, including preceding CPU devices, and interleaved GPU-device layers are rejected. Multi-device split weights encode exact whole-layer counts. Regression source was written but not run under the implementation-first test schedule. Physical selected-device/distributed/performance gates remain deferred and unpassed.
T022 recipe 94 source/build record: Prima fork a9d109972532b3d90891f7d34aee0110da7bab74 and recipe digest 58b23b1bcfb0f5c8b3ddc88b807f696566c24e5388849ff324aee2ec890d4e9c built cleanly as Windows/Vulkan server, CLI and profiler in artifacts/prima-explicit-device94/manifest.json. The native contract target compiled. Kluster now requires explicit selected-device capability for any GPU rank and the native manifest refuses GPU work without device selection. No unit/native/model tests or performance validation ran. Qualification gates and execution scopes remain disabled; physical distributed/performance acceptance awaits the live three-computer cluster.

T032 gateway source record: the Responses input-token route now passes the active Prima configuration to the shared feature/scope check. A regression case was written but not run under the implementation-first schedule. Python syntax and git diff checks passed. API/model and distributed/performance acceptance remain unpassed; qualification stays disabled.
T032 token-count continuation: apply the same deployment defaults as Responses generation before feature/scope admission, so prompt-affecting defaults cannot bypass qualification. Regression source covers missing scope and an unqualified default; tests remain deferred.
T032 capability continuation: token counting now requires response_input_tokens_v1 metadata on the active Prima runtime. Regression source covers a qualified Responses scope with missing native token-count capability; execution remains deferred.
T032 served-model continuation: both Prima Responses routes now require one served model matching the active deployment, reject a different requested model and forward the canonical ID. Regression source covers mismatch and accepted token-count forwarding; tests remain deferred.
T016 distributed monitor ownership record: after coordinator retirement, state cleanup and failure reporting now require the active Prima configuration to match the retired run ID. An unrelated active engine/run is preserved. Regression source was added but not executed under the deferred test schedule; physical distributed acceptance remains unpassed.
T016 local monitor ownership continuation: after an owned rank is drained, only a matching active Prima run ID may clear global process state or replace the run error. Regression source was added but not executed under the user-deferred test schedule.
T016 monitor replacement follow-up: post-stop cleanup rechecks the active configuration, so a replacement published during rank/coordinator cleanup is not cleared or given the retired run error. Regression source was added without execution.
T016 runtime-registry ownership record: local and distributed Prima cleanup now uses a locked engine/run-ID conditional clear, preserving a replacement running record; a failed journal restores the prior in-memory owner. Master rank stop also checks active run identity before clearing a matching PID. Regression source was added but not executed. Distributed process and performance acceptance remain deferred and unpassed.
T016 serving-monitor ownership record: a distributed Prima monitor stops its own stale coordinator before publishing over an unrelated active run, rechecking after serving refresh. Cleanup errors are scoped to the owned run; the local monitor also avoids reporting pending cleanup over a replacement configuration. Regression source was added but not executed. Qualification and physical gates remain unpassed.
T016 runtime-registry claim continuation: Prima local launch and distributed serving now claim the running registry slot only if empty or already owned by that exact engine/run. A different engine/run cannot be overwritten by a stale monitor, and a failed claim journal retains the previous in-memory record. Regression source was added without execution.
T021 recipe 95 source/build record: Prima fork 35d9aad5b38f98d576ba541432e0f0b05f9efc14 publishes the visible Vulkan profiler physical UUID/driver/index and advertises vulkan_profile_device_identity_v1. Kluster requires an exact selected-device match before caching. Clean managed Windows/Vulkan source, recipe digest 43d8d1b456dae00ee1db6f5efb4c5eec3faac99c2d6859ef2b0bba0453afee76 and three matching executable hashes are in artifacts/prima-profiler-device95/manifest.json. No unit/native/model/profile or performance execution ran; qualification and physical cluster gates remain unpassed.

Recipe 95 generation-profile source follow-up: after CPU/Vulkan model-backed measurements, Kluster rediscovers the selected physical Vulkan device and compares stable node/device inventory before caching a profile. Python syntax, Kluster package build and git diff whitespace checks passed. No unit/native/model tests, inference, profiling measurements or performance gates ran under the implementation-first schedule. All qualification gates remain unpassed; physical distributed and performance acceptance are deferred to the live three-computer cluster.

T016 signed-command source follow-up: worker rank start, fresh profile-context lookup and native listener admission offload expensive asset/device/helper work from the async command loop. Start/context use the existing 60-second signed window. Python syntax, Kluster package build and git diff whitespace checks passed. No unit/native/model tests or physical distributed validation ran under the implementation-first schedule. Qualification remains disabled; distributed and performance acceptance await the live three-computer cluster.

T016 signed-command continuation: worker rank start, fresh profile-context lookup, private-listener admission/renewal, rank status/renewal and stop now run potentially blocking asset, device, native helper and process work off the async command loop. Start/context/status/renewal use the signed protocol maximum 60-second request window. Python syntax, Kluster package build and git diff whitespace checks passed. No unit/native/model tests, test-fix work, inference, physical distributed or performance gates ran; qualification remains disabled.

T017/T021 distributed profile identity source follow-up: master cache retention and candidate scoring now require typed backend and Vulkan UUID/driver to match the signed participant inventory; CPU profiles cannot claim GPU identity. Python syntax, Kluster package build and diff whitespace checks passed. No unit/native/model tests, profiling measurements, physical cluster validation or performance gates ran. Qualification remains disabled; physical distributed and performance acceptance are deferred to the live three-computer cluster.

T015 asset-batch source follow-up: preparation rejects case-folded basename collisions, conflicting sizes for one SHA-256 and missing/oversized execution manifests. The master advances acknowledged offsets by digest across duplicate-content basenames. Workers validate an exact staged asset set against the execution manifest before approval, including runtime and participant identity; launch approval rechecks it. Python syntax, Kluster package build and diff whitespace checks passed. No unit/native/model tests, physical transfer, inference or performance gates ran. Qualification remains disabled; physical distributed/performance acceptance is deferred to the live three-computer cluster.

Recipe 96 GPT-2 source/build evidence (2026-10-13): fork PR #1 commit ab3eea95ab6f0b18aa080f0769f5fd5eaca0e5ce is pinned by normalized recipe digest b40ae1f567981061dedd2e6c1f570396b4016bef3d1e2b4681759fa2075e976b. Development and clean managed Windows/Vulkan server, CLI and profiler compilation passed. artifacts/prima-gpt2-segmented96/manifest.json has matching clean source, gpt2_segmented_v1 metadata, disabled qualification and verified staged artifact SHA-256 hashes. Kluster package build passed. No unit/native/model tests, GPT-2 execution, profiler measurement, physical distributed or performance gate ran. All acceptance/qualification gates stay unpassed; physical distributed and performance checks are scheduled for the user's live three-computer cluster.

Dashboard continuation receipt (2026-10-14): Prima engine settings UI (primaRuntimes.svelte),
the engine-mode swap (status `engine` from `kllm start/join --engine`, worker cluster-target
fallback), `GET /v2/node/prima-devices` device-identity probe and manifest `capabilities`
exposure carry source/test evidence only: ruff, python -m compileall, focused pytest
(test_config.py, test_dashboard_api.py, test_prima_integration.py, test_runtime_versions.py),
svelte-check, vite build and the dashboard suite including the prima-settings browser
fixture test all passed. No Prima model execution, profiling measurement, distributed
lifecycle or performance validation was attempted; every qualification gate remains false
and execution scopes stay empty. API/model/backend, distributed and performance acceptance
remain deferred with the integration's test phase to the live three-computer cluster.

Recipe 129 release-pin receipt (2026-10-14): recipe.json 129 pins the fork's initial
release `0.0.1` at `cb0da060105c18cc702b5d1927eb2e60ec2c1e22` (digest
`8f92b6a3d9b59d8fb46212ec89b688788187e1f1794e62882081d8c8921f4e72`; parser fixes from
3107200 retained upstream). Contract validation passed at the ancestor with
CI-identical flags and the clean managed Windows/Vulkan build in
artifacts/prima-initial-release-published129 verified commit/recipe_digest, three
staged executables, exactly six unpassed qualification gates, the 111-feature probe
with the node's physical Vulkan identity, and a verified set of artifact hashes.
Dual-source resolution (pinned commit or pinned release resolving to the same
identity) carries focused resolver tests, engine_sources release publication and
dashboard prefill/shortcut/browser-test evidence. Packaging passed
(compileall + scripts/build.py, native_available True). No unit/native/model test
phase nor any Prima inference/profiling/distributed/performance validation ran; all
qualification gates and execution scopes remain closed as before, and the deferred
physical distributed/performance acceptance still waits for the live three-computer
cluster.
