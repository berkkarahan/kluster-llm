# Verification — `020-prima-qwen35-model-support`

Receipts are appended as milestones complete. Every receipt states the command, the observed
result, and the hardware/OS context. Nothing in this file may be inferred, fabricated, or
carried over from another feature's runs.

## Baseline reproduction (before any change)

| Check | Result |
| --- | --- |
| Pinned Prima (recipe 132, `be162f6`) loading `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf` | Failed. Windows Error Reporting events 1000/1001 for `llama-server.exe` faulting in `ucrtbase.dll`, exception `0xc0000409`, fast-fail code 7. |
| Same load with `-ngl 0` | Failed identically — not an offload or sizing problem. |
| `rank.log` content at failure | Ends after the GGUF tensor-type dump; the real error line is lost because Prima's stderr is buffered and the abort skips the flush. |
| Same file on the machine's llama.cpp runtime | Loaded and generated successfully. Used as the correctness oracle. |

Context: single Windows machine, Vulkan backend, RX 9070 XT. The model declares architecture
`qwen35` (65 layers); the pinned Prima has no such architecture.

## M2 — Meaningful rejection of unsupported families

Root cause: `common/common.cpp` called `llama_model_load(...)` without any guard, so an
exception thrown by the architecture dispatch escaped to `std::terminate` → `abort()`, which on
Windows is reported as `0xC0000409` (fast-fail 7) and drops the buffered error text. The
`llama_model_load_from_file` catch never applied to it, because in this fork that entry point only
sets up device layout — the real load happens in `llama_init_from_gpt_params`.

Change: guard the load at the composition root and report it (`common/common.cpp`), and make the
architecture rejection explicit (`src/llama.cpp`).

| Check | Result |
| --- | --- |
| Before (pinned recipe-132 build, `llama-cli`): `Kolibri-1-Q4_K_M.gguf` (arch `kolibri1`) | Exit `-1073740791` (`0xC0000409`); WER 1000/1001, `ucrtbase.dll`, fast-fail 7; log ends at the tensor-type dump with no reason. |
| Before (pinned build): `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf` (arch `qwen35`) | Same fast-fail signature. |
| After (`build-dev`, `-DBUILD_SHARED_LIBS=OFF -DGGML_VULKAN=ON`): `Kolibri-1-Q4_K_M.gguf` | `llama_init_from_gpt_params: cannot load model '…Kolibri-1-Q4_K_M.gguf': error loading model: error loading model architecture: unsupported model architecture: 'kolibri1' is not implemented by this build`, then `main: error: unable to load model`. Exit code **1**. |
| After: `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf` | Same shape, naming `'qwen35'`. Exit code **1**. |
| Windows Error Reporting | The last `llama*` WER event (Application log, id 1000/1001, `Exception code: 0xc0000409`) is at 14:39:24, i.e. the pre-fix run. The fixed runs at 14:48:26 produced **no** event. |

Context: single Windows machine, Vulkan backend, RX 9070 XT; build `D:\dev\prima.cpp@be162f6` plus
this change; binaries `build-dev\bin\Release\{llama-cli,llama-server}.exe`.

Note: the message is still reported through the existing `LOG_ERR` path, but the process now exits
cleanly, so the buffered text reaches the log — the loss of the message was a symptom of the abort,
not a separate flushing bug.

## M3 — `qwen35` CPU correctness

**Passed: real local model correctness, not a performance gate.**

Source: `D:\dev\prima.cpp`, published commit
`bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554` (M2 base `0972754`). Windows x64,
MSVC 17.14, RX 9070 XT. Initial development builds used static libraries,
Vulkan enabled and HiGHS disabled; the final source also built successfully with
HiGHS 1.9.0 enabled (`llama-cli`, `llama-server`, `profile-tool`, primitive/state
contracts). HiGHS's vendored C sources emitted existing unused-label warnings.

The architecture registry, hparams/tensor loader, attention/recurrent graph,
FFN/head and absolute ownership dispatch are connected. The loader excludes the
MTP block. A first incorrect generation was traced against the reference's
first-layer tensors: Prima's V cache writer expects `[all KV heads, tokens]`,
not upstream's `[head width, heads, tokens]`. Flattening that boundary fixed the
large first-attention-layer error. No assertion was removed to hide the issue.

Observed successful builds:

```powershell
cmake --build build-dev --config Release --target llama-cli llama-server -j 8
cmake -S . -B build-dev "-DLLAMA_BUILD_TESTS=ON" "-DBUILD_TESTING=ON"
cmake --build build-dev --config Release --target test-qwen35-primitives -j 8
```

The contract executable covers strided 4D cumsum, softplus extremes/in-place,
F32/F16 fill/in-place, four triangle variants, batched non-square-RHS triangular
solve, `repeat_4d`, and Unicode combining-mark/contraction/newline splitting.
It accepts an exact optional backend device and refuses unsupported operations
rather than silently scheduling them on CPU.

Observed CPU results (`--device none -ngl 0`, F16 attention KV):

| Check | Result |
| --- | --- |
| Short prompt, 5 prefill tokens | 16 greedy pieces matched reference: ` Paris.\nThe capital of France is Paris.\nThe capital of France is` |
| Longer sky/sunset prompt, 104 prefill tokens | 16 greedy pieces matched reference across two 64-token prefill chunks: `\n\n<think>\nThe user wants me to explain why the sky is blue during the` |
| Chat, `enable_thinking=false` | HTTP 200, `The sum of 2 and 2 is 4.` |
| Native primitive contracts | Passed on CPU, thread counts 1/4, including empty-input tokenizer handling |
| Model-backed state contract | CPU save/restore/copy preserved continuation logits; serialized sequence size 157,420,644 bytes |

Reference: managed upstream `7fe450e19`, build `05cf2709-c8c`, CPU, greedy,
seed 1, repetition penalty 1, 16 predicted tokens. Reports compared per-token
pieces as well as complete text, not just a common opening. Reference numeric
token IDs were retained; this older Prima response reports token pieces rather
than `return_tokens` IDs.

Model: `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf`, 8,349,689,920 bytes,
SHA-256 `98f6ebb527917a2541df8e667d51c3ec8a8bb135b44c606d40cc6ed924e89e52`.
The identity-bound local SHA sidecars agree with the model stat identity; managed
preparation independently verifies the asset before deployment.

Raw receipts under `C:\Users\karah\AppData\Local\Temp\opencode`:
`saluki-parity-{0,1}-{reference,prima}.json`, `saluki-prima-cpu-chat.json`,
`saluki-state-contract.log`, plus the model-trace diagnostics. These are
developmental artifacts, not manually restamped managed runtime manifests.

## M4 — `qwen35` Vulkan kernels

**Passed: local RX 9070 XT execution.** Vulkan0 identified the RX 9070 XT with
physical index 0, device identity `00000000030000000000000000000000`, raw driver
identity `8389014`.

The original op inventory missed several Vulkan-only gaps. In addition to
SOFTPLUS/FILL/TRI/CUMSUM/SOLVE_TRI, this graph needs SSM convolution and missing
EXP/NEG/SIGMOID/SUB/DIAG/SET paths. RMS-norm inputs are made contiguous at the
family graph boundary for this backend. Saluki also needs IQ1_S/M,
IQ2_XXS/XS/S, IQ3_XXS/S and IQ4_XS. New on-device row reduction/gather shaders
decode the original packed blocks using unchanged CPU codebooks; they do not
dequantize weights on CPU or substitute CPU matrix execution. SSM_SCAN and a
fused GDN opcode are not used and were not implemented.

```powershell
cmake --build build-dev --config Release --target test-qwen35-primitives test-qwen35-state llama-server llama-cli profile-tool -j 8
.\build-dev\bin\Release\test-qwen35-primitives.exe Vulkan0 <Saluki.gguf>
$env:KLLM_PRIMA_EXECUTION_EVIDENCE='1'
.\build-dev\bin\Release\test-qwen35-state.exe <Saluki.gguf> Vulkan0
.\build-dev\bin\Release\llama-server.exe -m <Saluki.gguf> --device Vulkan0 -ngl 64 --output-gpu -c 2048 -b 256 -ub 256 -t 8 -np 1 --host 127.0.0.1 --port 18192
```

| Check | Observed |
| --- | --- |
| Primitive and original-IQ-block contracts | Passed on Vulkan0 for all eight IQ formats, gather and batched matrix work versus CPU reference decoders, plus compositional ops and convolution |
| Full transformer/output stack | All 64 trunk blocks plus `output.weight` only on Vulkan0 in completed prefill **and** decode receipts. Embedding lookup explicitly stays on the head CPU by native design. The unused MTP block is not counted as an executed layer. |
| Short/long generation | Both 16-piece continuations matched the same reference text as CPU; prefill lengths 5 and 104 |
| Chat | HTTP 200: `The sum of 2 and 2 is 4.` |
| Sequence save/restore/copy | Passed on Vulkan, same 157,420,644-byte snapshot format |
| Fresh HiGHS-enabled development build | Server, CLI, profile tool and contracts built; original-IQ-block Vulkan contracts passed again |

Raw receipts: `saluki-state-vulkan.log` (identities deliberately labelled
`development-unpublished` / `development-qwen35-state-contract`),
`saluki-vulkan-completion-{0,1}.json`, `saluki-prima-vulkan-chat.json`, and
`saluki-prima-vulkan-server.log` in the same approved temporary directory.
Configured GPU counts alone were not treated as execution evidence. No
throughput/speedup claim or physical multi-node qualification is made.

## M5 — Segmented execution and capabilities

Implemented absolute-layer ownership and graph visit assembly, with both
segmented admission assertions accepting `qwen35`. Single-node graphs exercised;
physical distributed execution remains unverified.

The HiGHS-enabled `llama-server --list-devices` report contains 56 registry-derived
architecture names including `qwen35` and excluding `qwen35moe`, plus
`qwen35_segmented_v1` and `highs`. Existing features remain, and the model-family
list is derived through `llama_model_supported_architectures`, not duplicated in
the server. Native README/model-support/Windows-Vulkan docs record scope and the
backend-neutral `--output-gpu` flag (legacy CUDA-named alias retained).

## M6 — End-to-end validation

Fresh isolated managed source build `5b2fefc0-625c-40fe-8c79-72635a93e668`
completed preparation for runtime
`97baff6d71ecd9c3d73947a5b6bb2d51f9e8a0aab50d054371d5f9a63688a165`.
This build came from the published fork commit through the existing installer;
development binaries were not substituted. Current single-machine master during
managed acceptance is `049f6c77`, private address `10.10.10.3`.

The normal GPU plan reports `fits`, 64 trunk layers, rank `ngl=64`,
`output_gpu=true`, Vulkan0, `model_support.supported=true`, and no qualification
errors. The current source-controlled qualification policy is unchanged.

**Managed CPU boundary:** preparation job
`2fbb805a-884b-4048-9390-1a1f0a0dc91f` prepared the verified model. Normal
`POST /v2/inference/runs` refused CPU placement with HTTP 409,
`PRIMA_RUN_REFUSED: participant_platform_backend_not_qualified`. The existing
local policy requires Vulkan participants. This refusal was retained and the
owned CPU preparation was cancelled; no qualification override or assessment
bypass was used to manufacture a CPU managed pass. Standalone CPU correctness is
the separate M3 receipt. Raw refusal: `saluki-managed-cpu-refusal.json`.

**Managed Vulkan cycle passed:** preparation job
`cd939c18-eaff-4ae6-a761-77845ffb9ee3`, run
`a617a618-0bd8-4db2-9461-70ecd0cd663d` (PID 32060), successful chat, then normal
`POST /v2/inference/runs/stop` returned `ok=true`. A fresh final deployment was
started and left running as run `6cc40213-41bc-41d6-a069-7d08a43bb472`, PID 34232.
The served catalog `/v2/inference/models` and native `/v1/models` both identify
`Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf`; a request using that exact ID returned
`The sum of 2 and 2 is 4.` through the authenticated Kluster gateway.

Effective settings: context 2048, one HTTP slot, batch/microbatch 256, F16 K/V,
flash attention off, 8 threads, speculative decoding off, checkpoints/cache RAM
zero, thinking disabled. The manifest binds source runtime 97baff6d…, all 64
trunk layers to Vulkan0, GPU output projection, and the verified Saluki asset hash.
`KLLM_PRIMA_RANK_READY` reports manifest mode, 64 layers/64 GPU layers and
`server_request_loop`; its digest is
`421997225358171076559980895973284b196716870d43f9d3182aab0a4fda51`.
Managed per-layer execution tracing was not enabled in the agent environment;
the detailed physical prefill/decode matrix receipts are the separately labelled
M4 check of the same published source, not invented managed log entries.

Managed source checkout HEAD and build manifest both resolve to `bfef1ac…`.
Prepared artifact SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| `llama-server.exe` | `26649649550cd47e58fee601695b0ef062c3ffd42aacccb9a14ab4d95dfbbb33` |
| `llama-cli.exe` | `aed6864318d36251aa344c9f1397b48b823632c62729e5ece0245d0aaeef8387` |
| `profile-tool.exe` | `e70a8df580a59691d3c5194a5611802ab2d58e3407291bad4eabff932da19c60` |

Raw receipts: `saluki-managed-{gpu-plan,gpu-preparation,gpu-run,gpu-stop,
gpu-final-run,gpu-final-served-chat}.json`. Earlier chat requests used the first
entry from the inventory `/v1/models` list (Kolibri); that label was not used as
model-identity evidence. The final request was repeated with the actual served
Saluki ID, and native/active-config/manifest identity all agree.

**Negative family path:** live read-only Kolibri preview returns
`inference_supported=false` and `PRIMA_MODEL_UNSUPPORTED`, without changing the
running Saluki PID. Current native binary rejects Kolibri with exit 1 and the
readable `kolibri1` architecture message. The forced-start/no-spawn boundary is
covered by the isolated supervisor regression, not a destructive replacement of
the final live deployment. Receipt: `saluki-managed-negative-plan.json`.

## M7 — Publication

Published `bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554` to fork `main`, then moved
the existing `0.0.1` tag with an expected-old-tag force-with-lease. `git ls-remote`
verified both remote refs resolve to that SHA. No `0.0.2` tag was created.

Sequencing note: standalone CPU/Vulkan correctness completed before publication.
The fresh **managed** build/deployment follows publication because its isolated
installer must fetch and verify the exact public commit. Development binaries
were not copied or restamped into a managed runtime to simulate this boundary.

## M8 — Kluster recipe 133 and refusal contract

The refusal contract and recipe pin are implemented. Recipe 133 resolves release
`0.0.1` to `bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554`, digest
`27782ffb7cc01334f7305e78b1fd40550ed157d9bf8668aa3674917db40c26cf`.
Its isolated managed Vulkan build is prepared. The source-controlled
qualification flags/policy were not changed by this feature.

| Check | Result |
| --- | --- |
| Capability validation (`build.py`) | `architectures` is normalized to a sorted, unique list; a non-list, a non-string or an empty entry raises `Prima capability report contains an invalid model architecture list`. An absent field stays absent (the runtime cannot be judged) and never refuses a run. Malformed feature entries are rejected. |
| Support rule (`model_support.py`) | Advertised family → supported. Non-advertised family → `PRIMA_MODEL_UNSUPPORTED` naming both the model's architecture and the families the runtime does implement. No advertised families / unreadable GGUF → `PRIMA_MODEL_SUPPORT_UNKNOWN` warning only. |
| Plan-time refusal | `preview` reports the refusal and flips `inference_supported` to `false`, and additionally returns `qualification_warnings` + `model_support`. Covered by `tests/test_prima_integration.py::test_preview_refuses_a_family_the_runtime_does_not_implement`. |
| Pre-spawn refusal | `RankSupervisor.start` calls the rule before any reservation, directory, or process. `tests/test_prima_model_support.py::test_rank_start_refuses_an_unsupported_family_before_spawning` asserts the refusal and that the spawn callable was never invoked, that a supported family does spawn, and that an older pin without `architectures` still starts. |
| Launch diagnostics | `runs.rank_log_tail` / `runs._exit_before_readiness` attach the tail of `rank.log` to an "exited before readiness" failure, with CRLF normalized. Unit-covered. |
| Dashboard block | `planUnsupportedReason()` (run interactor) returns the API's first qualification error when a Prima plan reports `inference_supported: false`; it feeds `planRunBlockedReason()`, which disables Plan & Run and renders the reason. |
| Regression | Latest full Python suite, serial (`uv run python -m pytest -q -n 0`): **1741 passed, 19 skipped**, 1 existing Starlette deprecation warning. Earlier unchanged-dashboard checks: `svelte-check` 0 errors / 0 warnings; **64 module tests + 10 browser tests** passed. |
| New tests | `tests/test_prima_model_support.py` — 7 tests (support rule, GGUF architecture read incl. unreadable/absent files, capability-report validation, pre-spawn refusal, log tail). |
| Pin consistency | Source/release commit both `bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554`, recipe 133; remote branch/tag verified, managed source resolution/build completed. The new runtime advertises `architectures`; older entries retain warning behavior. |

Additional model-layout checks exclude the unused MTP block from active placement
without rewriting raw GGUF metadata. Focused memory-estimation suite: 48 passed,
1 skipped. Capability parsing also covers nested/unhashable feature entries and
an explicitly null (malformed, not absent) architecture list.

Parallel full-suite runs were not clean: one run during native compilation had
five host-probe timeout failures; a subsequent quiet parallel run had a Windows
two-writer SQLite `PermissionError` (1740 passed, 19 skipped, 1 failed). The
affected files passed isolated serial checks (usage 19 passed/7 skipped; SQLite
22 passed), then the complete serial suite passed. No unrelated probe/SQLite
implementation or test was changed to hide these outcomes.

Final dashboard `npm --prefix dashboard run check` passed with zero diagnostics;
the production build succeeded (existing adapter fallback-overwrite notice).
Concurrent dashboard/runtime refactoring edits in the checkout were preserved,
not reverted or folded into this feature's source changes.

Final focused Python run (`test_prima_model_support.py`, `test_memory_estimation.py`,
`test_prima_integration.py`): 101 passed, 3 skipped. Ruff passed on the changed
model-layout, capability-parser, recipe and focused test files.

Note on placement: the pre-spawn gate lives in `RankSupervisor.start` (lifecycle) rather than in
`runs.py`. That is the actual spawn boundary and the same code path that the preview's `plan()`
materialization feeds, so a bypassed preview still cannot create a rank process; putting it in
`runs.py` would have left the coordinator and direct rank starts unguarded.

## Limits

- No performance claim of any kind is made or will be made by this feature. No qualification gate
  is evaluated, enabled, or relaxed here.
- Only `qwen35` is added. `qwen35moe` and every other architecture remain unsupported and are
  expected to hit the rejection path.
- Multi-token prediction is tolerated, not executed: draft/speculative decoding stays disabled, so
  no draft-model speedup is claimed or measured.
- Single-machine acceptance only. No distributed, mixed-node, or multi-node run of the new family
  is claimed by this feature.
- Windows/Vulkan is the acceptance platform. Linux/macOS behavior is expected to be identical for
  the rejection path and is not separately exercised unless a milestone records a receipt for it.
