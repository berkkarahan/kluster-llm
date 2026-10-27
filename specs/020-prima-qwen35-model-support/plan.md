# Implementation Plan: Prima Qwen3.5 (`qwen35`) Support and Meaningful Model-Family Rejection

**Branch**: `020-prima-qwen35-model-support` | **Date**: 2026-10-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/020-prima-qwen35-model-support/spec.md`

## Summary

The pinned Prima fork has no `qwen35` architecture. Lading `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf`
into it aborts the process (Windows fast-fail `0xC0000409` in `ucrtbase.dll`) after the GGUF
metadata dump, with the real error text lost because Prima's stderr is buffered and the abort
skips the flush. The same file loads fine on the machine's installed llama.cpp runtime.

Two workstreams, one fork change and one Kluster change:

1. **Fork**: port the `qwen35` model family (hybrid SSM/attention + tolerated MTP tensors) from
   upstream llama.cpp into the fork's monolithic `src/llama.cpp`, including the CPU kernels and
   the Vulkan kernels it needs; replace the silent-abort path for unknown architectures with a
   readable, non-zero-exit refusal; advertise `architectures` and `qwen35_segmented_v1` in the
   existing capability payload. Publish by moving tag `0.0.1`.
2. **Kluster**: advance the recipe pin to the new commit and build recipe version 133, validate
   the new capability fields, refuse unsupported model families at plan time and pre-spawn with
   a distinct detail code, enrich launch failures with the rank-log tail, block the dashboard
   launch, and record the pin in the recipe ledger and docs.

## Technical Context

**Language/Version**: C/C++17 (ggml/llama.cpp layer of the Prima fork) + Python 3.12 (`src/kllm/`) + TypeScript/Svelte 5 (dashboard)

**Primary Dependencies**: Prima fork `berkkarahan/prima.cpp` (tag `0.0.1`, currently `be162f6fe`), ggml (CPU + Vulkan backends), llama.cpp upstream `v0.5.0` @ `7fe450e` as the porting reference, Kluster's `primacpp` module interactors, `uv`-managed Python environment

**Storage**: GGUF model files on disk; Kluster's managed-network SQLite for runtime/recipe persistence; rank logs on disk under the run directory

**Testing**: `uv run pytest` (Kluster unit/integration), the fork's existing `test-backend-ops`/build checks, plus manual end-to-end runs on this Windows/Vulkan machine with receipts written to `validation.md`

**Target Platform**: Windows (acceptance host, Vulkan backend, RX 9070 XT); Linux/macOS must keep building and replaying the same load/rejection behavior

**Project Type**: cross-repo native + application integration

**Performance Goals**: none claimed. This feature delivers correctness and diagnosability only; no latency/throughput target, no qualification-gate change.

**Constraints**: the fork must keep its current source layout and its existing supported families working; the release tag moves (`0.0.1`) rather than a new tag being cut; Kluster's refusal path must never spawn a process; a missing `architectures` capability must degrade to a warning.

**Scale/Scope**: one new model family (65 layers, ~8.35 GB IQ2_mix), one Windows/Vulkan node, one Kluster module (`primacpp`) plus one dashboard run-control touch point.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Owning-module patch surfaces**: new Kluster behavior lives in `src/kllm/modules/primacpp/interactors/` (a new `model_support.py` plus changes to `recipe.py`, `build.py`, `planner.py`, `runs.py`, `lifecycle.py`). No facade or re-export is added. PASS.
- **Cross-module gateways passed in as constructor arguments**: the model-support check is plain data + a pure function; no new gateway is introduced unless the pre-spawn check needs one, in which case it reuses the existing process/log gateway already passed to `runs.py`. PASS.
- **Tests assert observable behavior; bug fixes carry a regression check**: the silent-abort fix gets a regression check (readable message, non-zero exit, no abort code) and the unsupported-family refusal gets a negative test asserting no process spawn. PASS.
- **Tests are deterministic, isolated, no real GPUs/live clusters by default**: unit tests use synthetic capability payloads and the existing GGUF inventory stubs; the real GPU runs are validation receipts, explicitly marked as hardware-dependent. PASS.
- **Native trust boundaries explicit**: no new subprocess or elevation path. The fork change is in-process model loading. PASS.
- **Docs updated in the same change**: `docs/prima-integration.md` (recipe ledger + Recipe 133 section), `specs/017-prima-integration/validation.md` receipts, and the fork's `docs/fork/model-support.md` + README table. PASS.
- **No unmeasured performance claims**: the plan states explicitly that no performance gate is touched and no speedup is claimed. PASS.

Re-check after design: no new abstractions beyond one small `model_support.py` helper module (justified by two call sites: planner preview and run pre-spawn). PASS.

## Project Structure

### Documentation (this feature)

```text
specs/020-prima-qwen35-model-support/
├── spec.md          # Feature specification (this feature)
├── plan.md          # This file
├── tasks.md         # Task breakdown by milestone
├── memory.md        # Feature-local scratch context / recon findings
└── validation.md    # Observed evidence: commands, outputs, receipts
```

### Source Code (repositories touched)

```text
D:\dev\prima.cpp\                      # fork: berkkarahan/prima.cpp, tag 0.0.1
├── ggml/
│   ├── include/ggml.h                 # add GGML_OP_GATED_DELTA_NET op code
│   └── src/
│       ├── ggml.c                     # CPU kernels for the new family
│       └── ggml-vulkan.cpp            # Vulkan kernels for the new family
├── src/llama.cpp                      # arch enum/name table, KV metadata, tensor
│                                      # loading, hybrid cache, graph build,
│                                      # segmented execution, allowlist asserts
├── examples/server/server.cpp         # KLLM_PRIMA_CAPABILITIES= advertisement
└── docs/fork/model-support.md         # family documentation
    README.md                          # supported-family table

D:\dev\kluster-llm\                    # this repository
├── src/kllm/modules/primacpp/interactors/
│   ├── model_support.py               # NEW: pure architecture-support check
│   ├── recipe.py                      # pin + build recipe version 133
│   ├── build.py                       # capability payload validation
│   ├── planner.py                     # plan-time refusal
│   ├── runs.py                        # pre-spawn refusal, rank-log tail
│   └── lifecycle.py                   # rank start error enrichment
├── dashboard/src/lib/modules/run/     # block launch when unsupported
├── tests/                             # new regression/negative tests
├── docs/prima-integration.md          # Recipe 133 ledger + section
└── specs/017-prima-integration/validation.md   # receipts
```

**Structure Decision**: The fork keeps its monolith `src/llama.cpp`; upstream's file-per-architecture
layout is *not* adopted. The `qwen35` port therefore lands as additions to the fork's existing
architecture enum/name table, KV metadata table, tensor-creation switch, cache construction,
and graph-builder dispatch, plus new ggml ops in `ggml.c`/`ggml-vulkan.cpp`. Kluster changes stay
inside the existing `primacpp` module, with one new small interactor for the support check.

## Milestones

Each milestone produces evidence recorded in `validation.md`. Later milestones are gated by the
exit criteria of earlier ones.

### M0 — Specification and tracking

Author this spec set; create/update the workman item for spec 020. Exit: `spec.md`, `plan.md`,
`tasks.md`, `memory.md`, `validation.md` exist and the workman item reflects the real state.

### M1 — Fork reconnaissance: freeze the exact port list

Full clone of `ggml-org/llama.cpp` at the reference commit `7fe450e` (the local runtime source
under `.kllm/runtimes/…/source` is shallow and cannot be used for cherry-pick archaeology).
Enumerate, for the `qwen35` architecture only:

- architecture/name registration and the KV metadata keys the family reads;
- the tensor set it creates, including which entries are MTP-only;
- the hybrid (attention + recurrent SSM) cache construction and its state layout;
- tokenizer pre-type behavior (`LLAMA_VOCAB_PRE_TYPE_QWEN35`) and its custom regex split;
- the graph-builder chain (attention path, gated delta-net path, output head);
- the ggml ops involved, separating those that already exist in the fork from those that must
  be added on each backend;
- segmented-execution support (`qwen35_segmented_v1`) requirements;
- the fork's insertion points for each of the above (existing line anchors confirmed, not assumed).

Exit: a frozen, itemized port list recorded in `memory.md`, with each item marked CPU-required,
Vulkan-required, or tolerated-MTP. No code changes yet.

### M2 — Fork: meaningful rejection of unsupported model families

Locate the true abort origin for the unknown-architecture path in the pinned build (the existing
`throw` at the architecture dispatch is evidently not what terminates the process). Make an
unknown architecture produce a readable error naming it, a non-zero exit status, and no abort
fast-fail / WER event, and ensure the message reaches stderr before exit (flush or equivalent).

Acceptance: running the pinned-plus-fix build against a non-`qwen35` GGUF produces the readable
line, a non-zero exit, and zero `0xC0000409`/WER events. Verified on Windows.

### M3 — Fork: `qwen35` CPU correctness

Port architecture registration, KV metadata, tensor creation (MTP entries tolerated and reported),
hybrid cache, tokenizer pre-type, and the graph builder; add the missing ggml op(s) to the CPU
backend (`ggml.c`) with correct types and threading.

Acceptance: the Saluki GGUF loads with `-ngl 0`, prefills the prompt, and greedily decodes the
same token sequence as the reference llama.cpp runtime for at least two prompts (a short factual
one and a longer free-form one), with the outputs captured as a receipt.

### M4 — Fork: `qwen35` Vulkan kernels

Implement the Vulkan counterparts of the same ops (convolution + activation variants, the SSM
scan variants including the subgroup path, and the gated delta-net variants), wired into the
Vulkan backend's op dispatch and pipeline cache, with the same quantization-type coverage the
family actually uses.

Acceptance: the same model runs with the full 65-layer stack on Vulkan (`ngl 65`), reaches
readiness, generates tokens, produces GPU execution evidence (device-use logging / profiler
output), shows no silent CPU fallback, and produces no abort.

### M5 — Fork: segmented execution and capabilities

Implement the family's segmented graph build (multi-visit execution) and satisfy the fork's two
segmented-execution allowlist assertions so the family is not silently excluded. Extend the
capability advertisement in `examples/server/server.cpp` with `architectures` (derived from the
architecture/name table so it cannot drift) plus `qwen35_segmented_v1`.

Acceptance: `--list-devices` output contains both fields and lists `qwen35`; the allowlist
assertions hold for the new family; existing families' capability output is unchanged apart from
the new fields. Fork docs (`docs/fork/model-support.md`, README table, Windows/Vulkan doc) list
the new family and its limits (MTP tolerated, no draft decoding).

### M6 — End-to-end validation on this machine

Run the real deployment path on the single-machine cluster: prepare, plan, launch, generate, and
stop, twice — once CPU-only offload and once full Vulkan offload — capturing the API responses,
`rank.log` excerpts, GPU evidence, and generated text as receipts. Also run the negative paths:
an unsupported family through preview, through a forced launch attempt, and directly through the
server binary.

Exit: `validation.md` contains the positive and negative receipts, explicitly labelled with
hardware/OS context and what was *not* verified.

### M7 — Publish the fork

Commit on the fork's `main`, push, then force-move the `0.0.1` tag to the new commit and push it.
Verify with `git ls-remote` that the remote tag resolves to the intended commit.

Exit: remote tag and branch agree; the moved-release guard text in Kluster still exists (it is a
guard, not something this feature removes).

### M8 — Kluster: recipe 133 and the refusal contract

Advance `DEFAULT_RECIPE` (`source_commit`, `source_release_commit`, release identity,
`build_recipe_version` → 133) and refresh the recipe digest, then implement the contract:

- validate `architectures` / `qwen35_segmented_v1` in the capability payload parser (malformed =
  clear error; absent = warning);
- add the pure architecture-support check (new `model_support.py`) with the distinct detail code;
- call it from plan preview (refusal reported in the plan result) and from the pre-spawn path in
  `runs.py` (refusal before any process is created);
- enrich launch failures with the rank-log tail in `lifecycle.py`/`runs.py`;
- block the dashboard launch control when the plan reports the model unsupported, reusing the
  same detail code.

Acceptance: unit tests cover supported/unsupported/missing-capability cases, the negative
no-spawn assertion, payload validation, and pin consistency; the existing Prima suites pass.

### M9 — Documentation and receipts

Add a "Recipe 133" section to `docs/prima-integration.md`, extend the recipe ledger row, record
the new pin in the relevant feature memory, add receipts to
`specs/017-prima-integration/validation.md`, and re-stage the vendored `vendor/prima` tree if the
bootstrap path consumes it.

Exit: docs and ledger agree with the pin; receipts are present and labelled; no performance or
qualification claim is made anywhere.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| New `src/kllm/modules/primacpp/interactors/model_support.py` | The support check needs two call sites (plan preview and pre-spawn) and must stay pure/testable | Inlining the check in `planner.py` and re-importing it from `runs.py` creates an interactor-to-interactor import for a two-line rule and hides the detail code in two places |
| Two new capability fields in the Prima advertisement | Kluster can only refuse at plan time if the runtime publishes what it supports | Probing by launching the model is exactly the failure mode this feature removes, and it costs a full process spawn per attempt |
