# Feature-local context: `020-prima-qwen35-model-support`

Scratch context for this feature only. Durable knowledge belongs in `docs/memory/` with an
`INDEX.md` entry, and only with user authorization.

## Locked decisions (2026-10-15)

- Publish by **moving** tag `0.0.1` on `berkkarahan/prima.cpp`, matching the established pattern
  (the tag has moved across recipes 11 → 29). No `0.0.2`.
- Unknown-family rejection is **Prima-only**; llama.cpp runtimes are not gated by this feature.
- **Both** CPU and Vulkan kernels are in scope. CPU correctness gates Vulkan acceptance; the tag
  moves only after both pass.
- Capability contract: the fork advertises `architectures` (derived from its arch/name table) and
  `qwen35_segmented_v1`. Kluster refuses at plan time **and** pre-spawn with a distinct detail
  code; a missing `architectures` field is a **warning**, not a refusal; launch failures gain the
  rank-log tail as a catch-all diagnostic.
- New spec directory (`specs/020-prima-qwen35-model-support/`), not appended to 017.
- Non-goals: `qwen35moe`, MTP/speculative decode execution (tensors tolerated/skipped, drafts stay
  disabled), any qualification-gate or performance-claim change.
- Acceptance environment: single machine, master `66cbaf05`, Windows, Vulkan, RX 9070 XT.

## Observed failure (reproduced before this feature)

- Model: `Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf`, 8.35 GB, arch `qwen35`, 65 layers.
- Pinned Prima (recipe 132, `be162f6`) has **no** `qwen35` architecture.
- Symptom: Windows Error Reporting event 1000/1001 for `llama-server.exe` faulting in
  `ucrtbase.dll`, exception `0xc0000409` (STATUS_STACK_BUFFER_OVERRUN / fast-fail), fast-fail
  code 7. The real error text is lost because Prima's stderr is buffered and the abort skips the
  flush; `rank.log` ends after the GGUF tensor-type dump.
- Reproduces with `-ngl 0`, so it is not an offload/sizing problem.
- The same file loads and generates correctly on this machine's llama.cpp runtime.
- M2 located and fixed the abort origin: the architecture exception escaped the
  unguarded `llama_init_from_gpt_params` composition root. Fork commit `0972754`
  now logs the exception and returns an empty init result; see the M2 receipt.

## Environment facts

- Fork checkout: `D:\dev\prima.cpp`, branch `main` and remote tag `0.0.1` at
  `bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554` (published after local CPU/Vulkan acceptance).
- Fork source layout: monolithic `src/llama.cpp` (~31.6k lines) with an architecture enum, a
  name table, KV/metadata tables, a clean unknown-arch `throw`, two segmented-execution allowlist
  assertions, a `build_mamba_segmented` precedent, and a graph dispatch table.
- The M1 baseline Vulkan backend `ggml/src/ggml-vulkan.cpp` had no SSM/GDN kernels.
  Qwen3.5 needs SSM convolution and the compositional delta-net primitives;
  neither `SSM_SCAN` nor a fused `GATED_DELTA_NET` is used by this graph.
- Reference upstream source available locally: `C:\Users\karah\.kllm\runtimes\9ca5f9df41e27be6\05cf2709-c8c\source`
  = llama.cpp `v0.5.0` @ `7fe450e`. It uses per-architecture files (`src/models/qwen35.cpp`,
  `qwen35moe.cpp`), a gated delta-net build base (`llm_build_delta_net_base`), vocab pre-type 46
  (`LLAMA_VOCAB_PRE_TYPE_QWEN35`) with `unicode_regex_split_custom_qwen35`, and asserts
  `n_layer_nextn == 1` for MTP. It is a **shallow** clone — a full clone is required before any
  cherry-pick/archaeology work (T003).
- The M1 baseline had no `qwen3next`/`nextn`/`hybrid` code, so the port was additive rather than a merge.
- Kluster-side touch points: `primacpp/interactors/{recipe,build,runs,lifecycle,planner}.py`,
  `modules/inference/router_http.py` (409 codes), `modules/runtimes/interactors/source.py`
  (moved-release guard), `dashboard/src/lib/modules/run/interactor.svelte.ts` (once blocked only
  on `fit_status: overflow`), `docs/prima-integration.md` (recipe ledger),
  `specs/017-prima-integration/validation.md` (receipt conventions).
- Pre-feature pin: Prima target `3ccb9038…`, build `6285acda-3fb`, recipe 132, digest `4169b81e…`.
  llama.cpp runtime `9ca5f9df…` = upstream `v0.5.0` `7fe450e`, build `05cf2709-c8c`, loads the
  model today and is therefore the correctness oracle.
- `vendor/prima.cpp` inside this repository is stale (tag `0.0.1` = `6d0f7c4` there).
- Recipe digest covers the recipe JSON **and** the text of `build.py`, so any `build.py` edit
  invalidates prepared runtimes — folded into the same 132 → 133 bump.
- `tests/test_prima_integration.py` asserts `release_commit == source_release_commit ==
  source_commit`, so all three must move together.
- Moved-release guard text (kept, not removed): "Prima release '0.0.1' moved; the recipe pin
  stands at <sha>. Use it, or advance the recipe".
- Host note: `PYTHONIOENCODING=utf-8` is required for `kllm` CLI output on this machine (rich's
  `→` crashes the default cp1252 console encoding).
- Useful negative-test models already local: `kolibri1`, `gpt-oss` (120b, 63 GB).

## Frozen port list (M1)

Reference: upstream `ggml-org/llama.cpp` `v0.5.0` @ `7fe450e19` (full clone at
`D:\dev\llama.cpp-ref`, blobless but complete history — sufficient for archaeology).
Fork anchors verified by reading `D:\dev\prima.cpp` at `be162f6`.

### A. Model facts to port (upstream `src/models/qwen35.cpp`, `src/llama-arch.cpp`)

- Registry: arch string `qwen35` (`llama-arch.cpp:41`), enum `LLM_ARCH_QWEN35`
  (`llama-arch.h:46`), factory (`llama-model.cpp:325`).
- hparams: RMS eps; `{arch}.rope.dimension_sections` (arr[4]); `ssm.conv_kernel`;
  `ssm.inner_size`; `ssm.state_size`; `ssm.time_step_rank`; `ssm.group_count`;
  recurrent-layer map from `{arch}.attention.recurrent_layers` (arr) else
  `{arch}.full_attention_interval` (default 4); `n_layer_all` = trunk + MTP blocks.
- Derived dims: `head_k_dim = head_v_dim = ssm_d_state`; `n_k_heads = ssm_n_group`;
  `n_v_heads = ssm_dt_rank`; `key_dim = d_state*n_group`; `value_dim = d_state*dt_rank`;
  `conv_dim = 2*key_dim + value_dim`.
- Tensors — every layer: `attn_norm`, `attn_post_norm` (`blk.%d.post_attention_norm`),
  `ffn_{gate,down,up}`. Full-attention layers add a Q projection containing
  interleaved query/gate heads (`{n_embd, n_embd_head_k*n_head*2}`), separate K/V
  projections (or a combined QKV tensor), `wo`, and `attn_q_norm`/`attn_k_norm`.
  The supplied file uses separate Q/K/V tensors, not a combined QKV tensor here.
  Linear-attention (recurrent) layers add `wqkv` `{n_embd, key_dim*2+value_dim}`,
  `attn_gate` `{n_embd, value_dim}`, `ssm_conv1d` `{d_conv, conv_dim}`,
  `ssm_dt`(bias) `{dt_rank}`, `ssm_a` (`blk.%d.ssm_a`), `ssm_beta`/`ssm_alpha`
  `{n_embd, n_v_heads}`, `ssm_norm` `{head_v_dim}`, `ssm_out` `{value_dim, n_embd}`.
- MTP block (index ≥ `n_layer`): attention-only block plus `nextn.{eh_proj,enorm,hnorm,
  embed_tokens,shared_head_head,shared_head_norm}`. Created with `TENSOR_SKIP` when
  `load_mtp` is false — **tolerated, never executed** (out of scope).
- Graph: full-attention layer = fused q+gate projection → per-head q/k RMS norm →
  MRoPE → attention → sigmoid gate → `attn_post_norm`; linear layer = `wqkv` →
  `ggml_ssm_conv` (+`silu`) → `build_gdn_l2_norm` (rms_norm scaled by `eps/n` then
  `1/sqrt(n)`) → delta-net (`build_recurrent_attn`) → `ssm_norm` → gated output →
  `ssm_out`. Output head is the usual `output_norm` + `output` (tied fallback).
- State: hybrid. Attention layers use the KV cache; linear layers use
  conv state `n_embd_r()` = `(d_conv-1) * (ssm_d_inner + 2*ssm_n_group*ssm_d_state)`
  and recurrent state `n_embd_s()` = `ssm_d_state * ssm_d_inner` (upstream
  `llama-hparams.cpp:208/236`).
- Tokenizer: `tokenizer.ggml.pre == "qwen35"` → pre-type 46
  (`LLAMA_VOCAB_PRE_TYPE_QWEN35`, upstream `llama-vocab.h:57`), BPE split
  `unicode_regex_split_custom_qwen35` (upstream `unicode.cpp:610`).

### B. ggml ops the family needs

Already in the fork (no work): `ssm_conv`, `silu`, `sigmoid`, `mul_mat`, `rms_norm`,
ordinary/NeoX RoPE, attention, `pad`, `diag`, `permute`, `add`, `sub`, `mul`, `view`, `cont`,
`transpose`, `scale`, `exp`, `neg`, `reshape`, `sum_rows`, `repeat`, `set_inplace`.

Missing — **must add on CPU and Vulkan**:

| Item | Kind | Notes |
| --- | --- | --- |
| `GGML_UNARY_OP_SOFTPLUS` + `ggml_softplus(_inplace)` | unary op | Used by the gated delta-net decay pathway |
| `GGML_OP_FILL` + `ggml_fill(_inplace)` | op | Chunked delta-net masks |
| `GGML_OP_TRI` + `ggml_tri` (+ `enum ggml_tri_type`) | op | Causal triangular mask |
| `GGML_OP_CUMSUM` + `ggml_cumsum` | op | Prefix sums over the gate; the scalar prefix loop is inlined in the CPU kernel |
| `GGML_OP_SOLVE_TRI` + `ggml_solve_tri` | op | Forward substitution in chunked attention |
| `ggml_repeat_4d` | helper only | Trivial: `GGML_OP_REPEAT` with an explicit target shape (the fork's REPEAT kernel derives the target from `dst->ne`, so no kernel work) |

The chunked delta-net path is *compositional* — it needs no fused `GGML_OP_GATED_DELTA_NET`.
The fork has no `fused_gdn_*` parameters, so the fused op and its Vulkan kernel are **not**
needed for this feature (recorded as a deliberate scope decision, not an oversight).

**M3 inventory correction:** `ggml_rope_multi` is not present in this fork. The
earlier list incorrectly classified it as existing. Upstream text positions are
`[p,p,p,0]` (`llama-graph.cpp::llm_graph_input_pos::set_input`), and its
interleaved-MRoPE cache advances all four theta streams at every pair
(`ggml-cpu/ops.cpp::ggml_mrope_cache_init`). For this file's `[11,11,10,0]`
sections and 64 rotary dimensions, every rotated pair selects one of the first
three streams, so the text-only rotation reduces to ordinary partial NeoX RoPE.
The model builder must validate that reduction, or implement the missing
multi-position operation; do not silently apply it to other section layouts or
claim image/video support. Numerical parity is still unverified.

### C. Fork insertion points (verified line anchors at `be162f6`)

| Area | Anchor |
| --- | --- |
| Arch enum / name table | `src/llama.cpp:233` region, name table `:310` |
| KV metadata tables | `:411` (enum), `:524` (names) |
| Unknown-arch rejection | `llm_load_arch` `:6266`, message `:6269` (fixed in M2) |
| Segmented allowlists | `:10634` (hparams/ownership guard), `:22281` (`GGML_ASSERT`) |
| Recurrent model predicate | `llama_model_is_recurrent` `:29300` |
| Cache | `llama_kv_cache` struct `:3023`, `cache.recurrent =` `:4222`, recurrent slot find `:4348` |
| State row sizes | `n_embd_r()` `:2787`, `n_embd_s()` `:2797` — model-global today |
| Mamba precedent | `llm_build_mamba` `:11380`, `build_mamba` `:18091`, `build_mamba_segmented` `:18145` |
| Graph dispatch | `:22384` region (also `:6892`, `:9685`, `:28276`) |
| hparams fields | `:2642` region (`ssm_d_inner`, `ssm_d_state`, …) |
| ggml op enum / names | `ggml/include/ggml.h:506`, op names `ggml/src/ggml.c:3010`, `:3106` |
| ggml constructors | `ggml_ssm_scan` `ggml/src/ggml.c:7274`, `ggml_rwkv_wkv` `:7440` |
| CPU vec helpers | `ggml/src/ggml.c:2101` (`ggml_vec_set_f32` neighbourhood) |
| CPU op kernels | `ggml_compute_forward_ssm_conv_f32` `:16207`, `_ssm_scan_f32` `:16277` |
| CPU dispatch switches | `:16552` (unary), `:17431` (main), `:18394` (grad), `:19265`/`:19363` (`ggml_get_n_tasks`) |
| Vulkan backend | `ggml/src/ggml-vulkan.cpp` — no SSM/delta kernels at all today |

### D. Blocking design decision recorded for implementation

The fork's cache is single-mode (`cache.recurrent` is model-global) and its state row sizes are
model-global. `qwen35` needs attention KV for some layers and per-layer conv/SSM state for
others, with recurrent dimensions taken from the linear layers only. The port therefore has to
teach the fork's cache a hybrid mode (attention slots + per-layer recurrent state) — this is the
largest structural change in M3 and must be prototyped before the graph work.

Resolved in the published port with `kv_self.recurrent_state`: attention token
rows and F32 recurrent sequence rows remain independent, including sequence
operations and versioned paired snapshots. CPU/Vulkan save/restore/copy checks
preserve continuation logits.

## Milestone state

| Milestone | State |
|-----------|-------|
| M0 spec + tracking | done (spec set authored; workman unavailable — see T002) |
| M1 recon | **done** — frozen port list at the top of this file |
| M2 rejection fix | **done and verified** — `common/common.cpp` + `src/llama.cpp`; receipts in `validation.md` |
| M3 CPU correctness | **passed** — 5/104-token prompts, 16 matching greedy pieces each versus upstream; chat and state replay/copy pass |
| M4 Vulkan kernels | **passed locally** — original IQ blocks, compositional ops, generation/chat, state replay/copy and complete trunk/output matrix evidence on RX 9070 XT |
| M5 segmented + capabilities | implemented; 56 registry-derived architectures, `qwen35_segmented_v1`, `highs` verified; physical distributed execution unverified |
| M6 end-to-end | **managed Vulkan passed and left running**; managed CPU correctly refused by unchanged Vulkan-only participant policy (standalone CPU passed) |
| M7 publish 0.0.1 | **done** — remote `main` / tag both `bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554` |
| M8 recipe 133 + gate | **pinned and tested** — digest `27782ffb7cc01334f7305e78b1fd40550ed157d9bf8668aa3674917db40c26cf`, no qualification-policy change |
| M9 docs/receipts | updated with published pin, managed artifacts, generation/stop/readiness and explicit limits |

### Fork working state

`D:\dev\prima.cpp` at published `bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554`
(includes M2 `0972754`):

- `common/common.cpp`: `llama_init_from_gpt_params` is now a guarded wrapper around
  `llama_init_from_gpt_params_impl` (catches, logs, returns an empty result).
- `src/llama.cpp`: the unknown-architecture message now reads
  `unsupported model architecture: '<name>' is not implemented by this build`.
- `ggml/include/ggml.h`, `ggml/src/ggml.c`: append four op IDs and one unary ID
  without renumbering existing ops; constructors, names/symbols, CPU dispatch,
  thread planning, F32 softplus/cumsum/tri/solve, F32/F16 fill, and `repeat_4d`.
  Cumsum follows row strides; fill/tri/solve require contiguous operands as in
  upstream. Softplus matches upstream's `x > 20 ? x : logf(1 + expf(x))`.
- `src/llama.cpp`: hybrid hparams and per-layer storage-width helpers;
  `kv_self` keeps token-addressed attention rows, `recurrent_state` owns a
  separate F32 sequence-addressed conv/SSM store. Recurrent metadata admission
  is prepared on a shadow before attention admission. Equal-length batching,
  state-copy/mask inputs, clear/copy/keep/remove/position transforms,
  attention-only defrag/shift, memory sizing, and versioned paired snapshot
  save/restore are wired. Snapshot restore checks matching terminal positions
  and clears both stores on failure. The loader activates this layout; CPU and
  Vulkan model-backed state checks passed.
- `llm_build_context::build_qwen35_delta_net`: single-token autoregressive and
  64-token compositional chunk paths ported from the pinned upstream reference.
  `build_qwen35_linear`: input projections, sigmoid beta/softplus decay,
  convolution history, L2-normalized/repeated Q/K heads, delta-net state update,
  gated RMS norm and output projection. Full-attention and model/segmented
  dispatch are connected. The V projection is flattened to Prima's cache-writer
  contract before storage; RMS-norm views are made contiguous for Vulkan.
- `include/llama.h`, `src/llama-vocab.cpp`, `src/unicode.cpp`, vocab loading:
  pre-type 46, exact Qwen3.5 split expression and custom combining-mark-aware
  splitting, with `tokenizer_clean_spaces=false` matching the reference.
- `tests/test-qwen35-primitives.cpp`, `tests/CMakeLists.txt`: deterministic
  primitive/tokenizer contracts, CPU thread counts 1/4, and optional exact
  backend-device selection with no CPU fallback. Passed CPU and Vulkan; optional
  real-GGUF tests check all eight IQ decoders/gather/matrix paths against decoded
  original operands. `test-qwen35-state` passes CPU/Vulkan replay/copy.
- `prima_gdn.comp`, `prima_iq.comp`, Vulkan dispatch/pipelines: depthwise
  convolution, compositional primitives and IQ1_S/M, IQ2_XXS/XS/S, IQ3_XXS/S,
  IQ4_XS original-block F32 row reductions/gather. No CPU weight dequantization
  or model-performance claim; actual embedding lookup stays CPU by native design.
- `llama_model_supported_architectures` derives capability names from the registry.
  `--output-gpu` exposes the existing GPU-output selection on Vulkan as well as
  CUDA/Metal/HIP, retaining the old CUDA-named alias.
- Dev build tree `build-dev/` (untracked): `-DBUILD_SHARED_LIBS=OFF -DGGML_VULKAN=ON
  -DUSE_HIGHS=ON -DCMAKE_POLICY_VERSION_MINIMUM=3.5` (initial development used
  HiGHS OFF). **Note for reproducibility:**
  `cmake` defaults `BUILD_SHARED_LIBS=ON` here, which fails to link because
  `gpt_log_verbosity_thold` is not exported from the DLL; the recipe's
  `-DBUILD_SHARED_LIBS=OFF` is required and matches the shipped configuration.
- Tests were enabled in the existing dev CMake tree with
  `cmake -S . -B build-dev -DLLAMA_BUILD_TESTS=ON -DBUILD_TESTING=ON`.
  Latest successful build targets: `llama-cli llama-server profile-tool
  test-qwen35-primitives test-qwen35-state`.

### Supplied GGUF inventory (read directly during M3; no inference run)

`Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf` reports:

| Field | Observed |
| --- | --- |
| `block_count` / `nextn_predict_layers` | 65 / 1 — execute 64 trunk layers; skip `blk.64.*` MTP |
| Embedding / FFN widths | 5120 / 17408 |
| Attention heads / KV heads / head width | 24 / 4 / 256 |
| Rotary dimensions / sections / base | 64 / `[11,11,10,0]` / 10000000 |
| SSM conv / state / groups / value heads / inner | 4 / 128 / 16 / 48 / 6144 |
| Full attention interval / RMS epsilon | 4 / approximately `1e-6` |
| Linear `attn_qkv` / `attn_gate` | `[5120,10240]` / `[5120,6144]`, IQ3_S |
| Linear conv / alpha+beta / out | `[4,10240]` F32 / `[5120,48]` Q8_0 / `[6144,5120]` IQ4_XS |
| Full-attention Q / K / V / out | `[5120,12288]` IQ2_XXS / `[5120,1024]` IQ2_XXS / `[5120,1024]` IQ2_S / `[6144,5120]` IQ1_S |

The loader subtracts MTP before ownership/cache/graph sizing. Kluster's model
layout excludes predictor tensors from active placement while preserving the
65-block metadata; the managed topology now reports 64 executed trunk layers.

### Published recipe / managed runtime

- Recipe 133 source: `bfef1ac1277dd5d260cfc7d3397ac23b9cf1c554`, release `0.0.1`.
- Digest: `27782ffb7cc01334f7305e78b1fd40550ed157d9bf8668aa3674917db40c26cf`.
- Runtime ID: `97baff6d71ecd9c3d73947a5b6bb2d51f9e8a0aab50d054371d5f9a63688a165`.
- Fresh isolated managed build: `5b2fefc0-625c-40fe-8c79-72635a93e668`, prepared.
- Current live single-node master during managed acceptance: `049f6c77`,
  `10.10.10.3`, RX 9070 XT. Earlier scratch IDs (`66cbaf05`, later `278bcac8`)
  belonged to earlier local agent sessions. The idle local agent was restarted
  to load this Python code, retaining its current key and private interface.
- Model SHA-256: `98f6ebb527917a2541df8e667d51c3ec8a8bb135b44c606d40cc6ed924e89e52`.
- Managed GPU acceptance: preparation `cd939c18-eaff-4ae6-a761-77845ffb9ee3`,
  run `a617a618-0bd8-4db2-9461-70ecd0cd663d` generated and stopped normally.
- Final live deployment: run `6cc40213-41bc-41d6-a069-7d08a43bb472`, PID 34232,
  serving the exact Saluki ID via the Kluster gateway on port 18765 (native 8080),
  context 2048 / one slot / F16 K/V / 64 GPU trunk layers plus GPU output.
- Managed CPU attempt was refused with `participant_platform_backend_not_qualified`;
  no qualification policy was weakened to bypass it. CPU preparation was cancelled.
- `bootstrap_prima_build` rematerializes from the recipe when explicitly invoked;
  no tracked vendored Prima source exists to restage here. The existing ignored
  vendor cache was left untouched; this deployment uses the isolated managed source.

### Kluster implementation (committed as `79ed916`)

| File | Change |
| --- | --- |
| `src/kllm/modules/primacpp/interactors/model_support.py` | NEW — pure support rule, `PRIMA_MODEL_UNSUPPORTED` / `PRIMA_MODEL_SUPPORT_UNKNOWN` detail codes |
| `planner.py` | preview adds the support error (flips `inference_supported`) plus `qualification_warnings` + `model_support` |
| `lifecycle.py` | `require_model_support(model, capabilities)` runs immediately after the execution-capability gate, before the reservation and `spawn` |
| `runs.py` | `rank_log_tail` / `_exit_before_readiness` attach the rank-log tail to readiness failures |
| `build.py` | `parse_device_report` validates/normalizes `architectures` and rejects malformed feature entries; absence stays a warning |
| `dashboard/.../run/interactor.svelte.ts` | `planUnsupportedReason()` blocks Plan & Run on `inference_supported === false`, surfacing the API's first qualification error |
| `tests/test_prima_model_support.py` | NEW — 7 tests (support rule, GGUF arch read, report validation, pre-spawn no-spawn refusal, log tail) |
| `tests/test_prima_integration.py` | plan-time refusal test |

Latest full Python suite: **1741 passed, 19 skipped**, serial (`-n 0`). Parallel
runs encountered Windows probe timeouts under native compilation and a SQLite
two-writer PermissionError; isolated affected files passed (19/7 skipped and
22 passed), and the complete serial run passed without changing unrelated code.
Prior unchanged-dashboard checks: `svelte-check` 0 errors/0 warnings, 64 module
tests + 10 browser tests passed.
