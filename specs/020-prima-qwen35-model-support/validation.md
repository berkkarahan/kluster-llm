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

Pending.

## M4 — `qwen35` Vulkan kernels

Pending.

## M5 — Segmented execution and capabilities

Pending.

## M6 — End-to-end validation

Pending.

## M7 — Publication

Pending.

## M8 — Kluster recipe 133 and refusal contract

Two halves. The refusal contract (model-family support rule, capability validation, plan-time and
pre-spawn refusal, dashboard block, log-tail diagnostics) is implemented and tested now. The recipe
pin advance is blocked on M7, because the pin has to name a commit that does not exist yet.

| Check | Result |
| --- | --- |
| Capability validation (`build.py`) | `architectures` is normalized to a sorted, unique list; a non-list, a non-string or an empty entry raises `Prima capability report contains an invalid model architecture list`. An absent field stays absent (the runtime cannot be judged) and never refuses a run. Malformed feature entries are rejected. |
| Support rule (`model_support.py`) | Advertised family → supported. Non-advertised family → `PRIMA_MODEL_UNSUPPORTED` naming both the model's architecture and the families the runtime does implement. No advertised families / unreadable GGUF → `PRIMA_MODEL_SUPPORT_UNKNOWN` warning only. |
| Plan-time refusal | `preview` reports the refusal and flips `inference_supported` to `false`, and additionally returns `qualification_warnings` + `model_support`. Covered by `tests/test_prima_integration.py::test_preview_refuses_a_family_the_runtime_does_not_implement`. |
| Pre-spawn refusal | `RankSupervisor.start` calls the rule before any reservation, directory, or process. `tests/test_prima_model_support.py::test_rank_start_refuses_an_unsupported_family_before_spawning` asserts the refusal and that the spawn callable was never invoked, that a supported family does spawn, and that an older pin without `architectures` still starts. |
| Launch diagnostics | `runs.rank_log_tail` / `runs._exit_before_readiness` attach the tail of `rank.log` to an "exited before readiness" failure, with CRLF normalized. Unit-covered. |
| Dashboard block | `planUnsupportedReason()` (run interactor) returns the API's first qualification error when a Prima plan reports `inference_supported: false`; it feeds `planRunBlockedReason()`, which disables Plan & Run and renders the reason. |
| Regression | Full Python suite: **1737 passed, 19 skipped**. Dashboard: `svelte-check` 0 errors / 0 warnings; **64 module tests + 10 browser tests** passed. |
| New tests | `tests/test_prima_model_support.py` — 7 tests (support rule, GGUF architecture read incl. unreadable/absent files, capability-report validation, pre-spawn refusal, log tail). |
| Not verified yet | The recipe 133 pin (`source_commit`, `source_release_commit`, release identity, `build_recipe_version`, digest) and the resulting prepared-runtime expectation remain unset until the fork is published. Consequently no runtime in this machine's library advertises `architectures` yet, so every family check currently takes the warning path — which is the intended pre-publication behavior. |

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
