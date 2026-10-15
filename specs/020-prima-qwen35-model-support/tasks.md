# Tasks: Prima Qwen3.5 (`qwen35`) Support and Meaningful Model-Family Rejection

**Input**: Design documents from `/specs/020-prima-qwen35-model-support/`
**Prerequisites**: [spec.md](./spec.md), [plan.md](./plan.md)

**Tests**: Included where the change is in this repository's Python/dashboard surface (constitution: bug fixes carry a regression check; boundary contracts need checks). The fork-side native work is verified by build + real process/model runs, recorded as receipts rather than as pytest cases.

**Organization**: Tasks are grouped by milestone (M0–M9 from `plan.md`), which map onto user stories: M2/M3/M4 deliver US1 and US2's fork half, M5/M8 deliver US3, M6/M7/M9 close out evidence and publication.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: `[US1]` run a qwen35 model, `[US2]` fail loudly, `[US3]` publish capabilities

---

## Phase 1: M0 — Specification and tracking

- [x] T001 Author `specs/020-prima-qwen35-model-support/{spec.md,plan.md,tasks.md,memory.md,validation.md}` from the locked plan
- [ ] T002 Create the workman item for spec 020 and keep its status in step with this file — **blocked**: `workman` is not installed on this machine (no binary on `PATH`, no `~/.local/bin/workman`, no `~/.config/workman`, no workman MCP server, no source checkout). Status is tracked in `memory.md` instead.

---

## Phase 2: M1 — Fork reconnaissance (blocking for all fork work)

- [x] T003 Full clone `ggml-org/llama.cpp` at reference commit `7fe450e` into the working area (the runtime source under `.kllm/runtimes/…/source` is shallow and cannot be used for archaeology) — `D:\dev\llama.cpp-ref`, blobless but full history, checked out at `7fe450e19`
- [x] T004 Enumerate the `qwen35` architecture surface in the reference tree: registration/name, KV metadata keys, tensor creation (marking MTP-only entries), hybrid cache construction, tokenizer pre-type + custom split, graph-builder chain, segmented-execution path — recorded in `memory.md` § A
- [x] T005 [P] Enumerate the ggml ops the family uses and classify each as already-in-fork vs must-add, and as CPU-required / Vulkan-required / MTP-tolerated — recorded in `memory.md` § B
- [x] T006 Confirm every fork insertion point by reading the fork (arch enum, name table, KV tables, tensor switch, cache build, graph dispatch, allowlist asserts) — record exact anchors in `memory.md` — recorded in `memory.md` § C
- [x] T007 Freeze the itemized port list in `memory.md` and stop: no code before the list is frozen — frozen; § D records the hybrid-cache design decision that gates M3

---

## Phase 3: M2 — Meaningful rejection of unsupported families (US2)

- [x] T008 [US2] Reproduce the pinned build's failure on a non-`qwen35` GGUF and capture the exact evidence (exit code, WER event, buffered-stderr loss, last `rank.log` line) — exit `-1073740791` (`0xC0000409`), WER 1000/1001 `ucrtbase.dll` fast-fail 7, log ends at the tensor-type dump
- [x] T009 [US2] Locate the real abort origin for the unknown-architecture path (the existing `throw` in the architecture dispatch is not what terminates the process) — `common/common.cpp:1695` called `llama_model_load` unwrapped; the throw escaped to `std::terminate`
- [x] T010 [US2] Make an unknown architecture produce a readable error naming the architecture, flush it before exit, and exit non-zero with no abort fast-fail — guarded wrapper at the composition root; message now names the architecture
- [x] T011 [US2] Regression receipt: readable line present, non-zero exit, zero `0xC0000409`/WER events, for at least two different unsupported architectures — `kolibri1` and `qwen35`, both exit 1, no WER event after the fix (see `validation.md`)

---

## Phase 4: M3 — `qwen35` CPU correctness (US1)

- [ ] T012 [US1] Register the architecture (enum value, name string, table entries) so the family is no longer "unknown"
- [ ] T013 [P] [US1] Add the KV metadata the family requires, including hybrid/SSM parameters and the layer count the file declares
- [ ] T014 [P] [US1] Add tensor creation for the family's tensor set, including MTP-only entries tolerated with a stated reason (no draft path enabled)
- [ ] T015 [US1] Implement the hybrid cache construction (attention + recurrent SSM state) with correct per-sequence state sizing
- [ ] T016 [P] [US1] Add the tokenizer pre-type and the family's custom text splitting
- [ ] T017 [US1] Add the graph builder: attention path, recurrent/gated delta-net path, output head, and the multi-visit/segmented form
- [ ] T018 [US1] Implement the missing ggml op(s) in the CPU backend (`ggml/src/ggml.c`) with correct type support and threading
- [ ] T019 [US1] CPU acceptance: `ngl 0` load + prefill + greedy decode parity against the reference llama.cpp runtime on two prompts; capture as a receipt

---

## Phase 5: M4 — `qwen35` Vulkan kernels (US1)

- [ ] T020 [US1] Implement the Vulkan convolution + activation kernel variants the family needs
- [ ] T021 [P] [US1] Implement the Vulkan SSM scan kernels (`d128`/`d256` + subgroup path) required by the family
- [ ] T022 [P] [US1] Implement the Vulkan gated delta-net variants required by the family
- [ ] T023 [US1] Wire all new kernels into the Vulkan op dispatch and pipeline cache with the quantized types the file actually uses
- [ ] T024 [US1] Full-offload acceptance: 65/65 layers on Vulkan, readiness, generation, GPU evidence, no silent CPU fallback, no abort; capture as a receipt

---

## Phase 6: M5 — Segmented execution and capability advertisement (US3)

- [ ] T025 [US3] Implement the family's segmented graph build and satisfy the fork's two segmented-execution allowlist assertions
- [ ] T026 [US3] Derive `architectures` from the architecture/name table and add it plus `qwen35_segmented_v1` to the capability payload in `examples/server/server.cpp`
- [ ] T027 [US3] Assert via `--list-devices` that both fields appear, `qwen35` is listed, and prior families/capabilities are otherwise unchanged
- [ ] T028 [P] [US3] Document the family in `docs/fork/model-support.md`, the README family table, and the Windows/Vulkan doc, including the MTP-tolerated / no-draft limitation

---

## Phase 7: M6 — End-to-end validation on this machine

- [ ] T029 [US1] Prepare → plan → launch → generate → stop with CPU-only offload on the single-machine cluster; capture API responses and rank logs
- [ ] T030 [US1] Repeat the same cycle with full Vulkan offload; capture GPU evidence
- [ ] T031 [US2] Negative path: unsupported family through plan preview (distinct detail code), forced launch attempt (refused, no rank process), and direct binary run (readable error)
- [ ] T032 Record all receipts in `validation.md` with explicit hardware/OS context and an explicit list of what was not verified

---

## Phase 8: M7 — Publish the fork

- [ ] T033 Commit the fork work on `main` and push
- [ ] T034 Force-move tag `0.0.1` to the new commit and push it
- [ ] T035 Verify with `git ls-remote` that the remote tag and branch resolve to the intended commit

---

## Phase 9: M8 — Kluster recipe 133 and the refusal contract (US2, US3)

- [ ] T036 [US3] Advance `DEFAULT_RECIPE` in `src/kllm/modules/primacpp/interactors/recipe.py` (`source_commit`, `source_release_commit`, release identity, `build_recipe_version` → 133) and refresh the digest — **blocked on T033–T035** (the SHA does not exist until the fork is published)
- [x] T037 [US3] Validate `architectures` / `qwen35_segmented_v1` in `build.py`'s capability parser: malformed → clear error, absent → warning (no refusal) — `parse_device_report` now validates and normalizes `architectures` (absent stays absent → warning downstream) and rejects malformed feature entries
- [x] T038 [US2] Add the pure architecture-support check in a new `src/kllm/modules/primacpp/interactors/model_support.py` with the distinct detail code — `PRIMA_MODEL_UNSUPPORTED` (refusal) and `PRIMA_MODEL_SUPPORT_UNKNOWN` (warning)
- [x] T039 [US2] Call the check from `planner.py`'s plan preview and report the refusal in the plan result — preview reports the error (flips `inference_supported`) plus `qualification_warnings` and `model_support`
- [x] T040 [US2] Call the check pre-spawn in `runs.py` so a bypassed preview cannot start a rank process — implemented at the actual spawn boundary, `RankSupervisor.start` in `lifecycle.py`, immediately after the execution-capability gate and before any reservation or spawn (the preview and the launch share this one gate)
- [x] T041 [US2] Enrich launch failures with the rank-log tail (`runs.py` readiness failure, `lifecycle.py` rank start) — `runs.rank_log_tail` / `runs._exit_before_readiness`; the supervisor's existing `ready` diagnostics carry the same log
- [x] T042 [US2] Block the dashboard launch control in `dashboard/src/lib/modules/run/` when the plan reports the model unsupported, reusing the same detail code — `planUnsupportedReason()` feeds `planRunBlockedReason()` (disables Plan & Run and shows the API's qualification text verbatim)
- [x] T043 [US3] Tests: payload validation (valid/malformed/absent), support check (supported/unsupported/unknown-capability), plan-time refusal, negative no-spawn assertion, pin+digest consistency — new `tests/test_prima_model_support.py` (7 tests) + plan-time refusal test in `tests/test_prima_integration.py`; the pin+digest consistency assertion is the existing `tests/test_prima_integration.py:224` check, which only becomes meaningful with T036
- [x] T044 Run the existing Prima test suites and confirm no regression (FR-015) — full Python suite 1737 passed / 19 skipped; dashboard `svelte-check` 0 errors/0 warnings, 64 module + 10 browser tests passed

---

## Phase 10: M9 — Documentation, ledger, receipts

- [ ] T045 Append the "Recipe 133" section and ledger row to `docs/prima-integration.md`
- [ ] T046 Record the new pin in the relevant feature memory and re-stage `vendor/prima` if the bootstrap path consumes it
- [ ] T047 Add the cross-referenced receipts to `specs/017-prima-integration/validation.md`
- [ ] T048 Final pass: confirm docs/ledger/pin/version all agree and that no performance or qualification claim was introduced

---

## Dependencies

- T003–T007 (M1) block all fork implementation tasks.
- T010 (M2) is independent of the family port and can land first — it is the diagnosability fix.
- T019 (CPU correctness) gates T024 (Vulkan acceptance); the tag moves (T034) only after both.
- T025–T028 (M5) gate T026's consumer work in T037–T039.
- T036 (pin advance) must follow T035 (published commit SHA).
- T045–T048 (M9) close the feature; nothing else may be re-opened by them.
