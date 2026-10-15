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
- The fork already contains a `throw ... "unknown model architecture: '...'"` on the architecture
  dispatch path, yet the process still fast-fails — so the abort origin is **not yet located**.
  Finding it is T009.

## Environment facts

- Fork checkout: `D:\dev\prima.cpp`, branch `main` at `be162f6` (clean), tag `0.0.1` = `be162f6`.
- Fork source layout: monolithic `src/llama.cpp` (~31.6k lines) with an architecture enum, a
  name table, KV/metadata tables, a clean unknown-arch `throw`, two segmented-execution allowlist
  assertions, a `build_mamba_segmented` precedent, and a graph dispatch table.
- Fork Vulkan backend `ggml/src/ggml-vulkan.cpp` currently has **no** SSM/gated-delta kernels, and
  `ggml/include/ggml.h` lacks `GGML_OP_GATED_DELTA_NET` entirely — both must be added.
- Reference upstream source available locally: `C:\Users\karah\.kllm\runtimes\9ca5f9df41e27be6\05cf2709-c8c\source`
  = llama.cpp `v0.5.0` @ `7fe450e`. It uses per-architecture files (`src/models/qwen35.cpp`,
  `qwen35moe.cpp`), a gated delta-net build base (`llm_build_delta_net_base`), vocab pre-type 46
  (`LLAMA_VOCAB_PRE_TYPE_QWEN35`) with `unicode_regex_split_custom_qwen35`, and asserts
  `n_layer_nextn == 1` for MTP. It is a **shallow** clone — a full clone is required before any
  cherry-pick/archaeology work (T003).
- The fork has no `qwen3next`/`nextn`/`hybrid` code, so the port is additive rather than a merge.
- Kluster-side touch points: `primacpp/interactors/{recipe,build,runs,lifecycle,planner}.py`,
  `modules/inference/router_http.py` (409 codes), `modules/runtimes/interactors/source.py`
  (moved-release guard), `dashboard/src/lib/modules/run/interactor.svelte.ts` (once blocked only
  on `fit_status: overflow`), `docs/prima-integration.md` (recipe ledger),
  `specs/017-prima-integration/validation.md` (receipt conventions).
- Current pin: Prima target `3ccb9038…`, build `6285acda-3fb`, recipe 132, digest `4169b81e…`.
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
  `ffn_{gate,down,up}`. Full-attention layers add the Qwen3Next-style fused
  `wqkv` (`{n_embd, n_embd_head_k*n_head*2}`) + `wo` + `attn_q_norm`/`attn_k_norm`.
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
`rope_multi`, attention, `pad`, `diag`, `permute`, `add`, `sub`, `mul`, `view`, `cont`,
`transpose`, `scale`, `exp`, `neg`, `reshape`, `sum_rows`, `repeat`, `set_inplace`.

Missing — **must add on CPU and Vulkan**:

| Item | Kind | Notes |
| --- | --- | --- |
| `GGML_UNARY_OP_SOFTPLUS` + `ggml_softplus(_inplace)` | unary op | Used by the gated delta-net decay pathway |
| `GGML_OP_FILL` + `ggml_fill(_inplace)` | op | Chunked delta-net masks |
| `GGML_OP_TRI` + `ggml_tri` (+ `enum ggml_tri_type`) | op | Causal triangular mask |
| `GGML_OP_CUMSUM` + `ggml_cumsum` (+ `ggml_vec_cumsum_f32`) | op | Prefix sums over the gate |
| `GGML_OP_SOLVE_TRI` + `ggml_solve_tri` | op | Forward substitution in chunked attention |
| `ggml_repeat_4d` | helper only | Trivial: `GGML_OP_REPEAT` with an explicit target shape (the fork's REPEAT kernel derives the target from `dst->ne`, so no kernel work) |

The chunked delta-net path is *compositional* — it needs no fused `GGML_OP_GATED_DELTA_NET`.
The fork has no `fused_gdn_*` parameters, so the fused op and its Vulkan kernel are **not**
needed for this feature (recorded as a deliberate scope decision, not an oversight).

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

## Milestone state

| Milestone | State |
|-----------|-------|
| M0 spec + tracking | done (spec set authored; workman unavailable — see T002) |
| M1 recon | **done** — frozen port list at the top of this file |
| M2 rejection fix | **done and verified** — `common/common.cpp` + `src/llama.cpp`; receipts in `validation.md` |
| M3 CPU correctness | not started (port list frozen; hybrid cache identified as the blocking design) |
| M4 Vulkan kernels | not started (depends on M3) |
| M5 segmented + capabilities | not started (allowlist anchors recorded) |
| M6 end-to-end | not started |
| M7 publish 0.0.1 | not started |
| M8 recipe 133 + gate | Kluster half **implemented and tested**; pin bump blocked on M7 |
| M9 docs/receipts | pending (partial receipts written) |

### Fork working state (not yet committed)

`D:\dev\prima.cpp` at `be162f6` + local changes:

- `common/common.cpp`: `llama_init_from_gpt_params` is now a guarded wrapper around
  `llama_init_from_gpt_params_impl` (catches, logs, returns an empty result).
- `src/llama.cpp`: the unknown-architecture message now reads
  `unsupported model architecture: '<name>' is not implemented by this build`.
- Dev build tree `build-dev/` (untracked): `-DBUILD_SHARED_LIBS=OFF -DGGML_VULKAN=ON
  -DUSE_HIGHS=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5`. **Note for reproducibility:**
  `cmake` defaults `BUILD_SHARED_LIBS=ON` here, which fails to link because
  `gpt_log_verbosity_thold` is not exported from the DLL; the recipe's
  `-DBUILD_SHARED_LIBS=OFF` is required and matches the shipped configuration.

### Kluster working state (not yet committed)

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

Full Python suite: 1737 passed, 19 skipped. Dashboard: `svelte-check` 0 errors/0 warnings,
64 module tests + 10 browser tests passed.
