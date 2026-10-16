# 04 · GPU & Vulkan device selection

**Use when:** You work on Vulkan logical→physical device mapping, explicit local device selection and visibility masking, driver-unavailable startup behavior, or output-only GPU coordinators.

**Keywords:** Vulkan, logical, ordinal, physical, UUID, driver, adapter, visibility mask, explicit devices, explicit_devices_v1, output_only_gpu_v1, output_gpu, ngl, GPU inventory, discovery, vulkan_kv, vulkan_kv_cache_v1, vulkan_flash_attn_v1, strict_device_kv_v1, Flash Attention, KV cache.

**Related:** 03-windows-native.md, 10-profile-costs.md, 12-placement-qualification.md.

## Quick rules

- Map logical visible ordinals to physical identities explicitly; never assume device zero, use adapter display names as unique identities, or substitute CPU/Metal/CUDA.
- Execution is established only by completed prefill/decode GPU work and exact assigned layers/tensors — device enumeration and configured ranks do not establish it.
- Explicit local devices: preserve native registry/device/buffer identity and ordered selection; CPU-only none must bypass all GPU backend initialization; selected Vulkan host buffers must not initialize an unselected adapter; managed UUID selectors require physical visibility masking followed by remapped native `--device` names; verify the actual binary flag and `explicit_devices_v1` capability before multi-device admission; report only adapters with executed work, including zero assigned-work devices; check nonzero logical ordinals, ordered multi-adapter splits and no-visible-GPU CPU execution with owned functional model processes.
- An empty Vulkan visibility mask does not reproduce driver-unavailable startup — check that separately in bounded owned subprocesses and retain the failed contract/CI receipt; keep Vulkan initialization exceptions inside the `/EHsc` C boundary (see 03) and expose an empty GPU inventory on failure; CPU-only inference must still work, repeated discovery must settle, and explicit unavailable GPU selection must refuse before model loading; add a driver-unavailable CI contract without claiming GPU acceptance.
- Output-only GPU coordinators: retain `ngl` as the transformer count and allow rank-zero `output_gpu` with zero transformer offload on one selected device; negotiate `output_only_gpu_v1`, preserve explicit device/visibility selection and Vulkan signed hardware identity, and require the output backend at startup; reserve the output graph even when CUDA transformer offload is zero; in evidence never use `assigned[-ngl:]` when `ngl` is zero — keep the transformer GPU set empty and require completed output projection in both prefill and decode on the selected physical GPU; price output upload/logits download separately from transformer visits, including CPU-transformer local heads.
- Device-resident Vulkan KV cache & Flash Attention (`GPU -> RAM -> SSD` hierarchy): Prima's Vulkan backend supports all 9 Kluster KV precisions (`f32, f16, bf16, q8_0, q4_0, q4_1, q5_0, q5_1, iq4_nl`) for in-VRAM KV writes/dequant/repack (`GGML_OP_CPY` with arbitrary block-aligned view offsets), `GGML_OP_MUL_MAT` (all 9 for K, `f32/f16/bf16` for V), and `GGML_OP_FLASH_ATTN_EXT` (all 9 for K/V, head sizes `64, 80, 96, 112, 128, 192, 256`, GQA, ALiBi, softcap, SWA masks, and `src[4]` attention sinks), advertised via `vulkan_kv` and `vulkan_kv_cache_v1` / `vulkan_flash_attn_v1` / `strict_device_kv_v1`. Offloaded layers keep KV buffers strictly in VRAM; `--strict-device-kv` rejects any graph node touching a GPU KV tensor that cannot execute on the GPU backend so KV never silently spills or copies to host RAM.

## Full text (normative)

### Logical ↔ physical device mapping

- For Vulkan, map logical visible ordinals to physical identities explicitly.
  Never assume device zero, use adapter display names as unique identities, or
  substitute CPU/Metal/CUDA. Completed prefill/decode GPU work and exact assigned
  layers/tensors establish execution; device enumeration and configured ranks do not.


### Explicit local devices & visibility masking

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


### Output-only GPU coordinators

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


### Device-resident Vulkan KV cache, Flash Attention & `--strict-device-kv`

Prima's memory hierarchy is strictly `GPU -> RAM -> SSD`: transformer layers
assigned to a GPU (`ngl > 0`) keep their `cache_k_l*` and `cache_v_l*` buffers
allocated in Vulkan device memory, while CPU-assigned layers use host RAM/mmap.
Every operation that writes, shifts, defragments, or attends to a GPU-resident
KV tensor must execute on the GPU without copying the KV cache to CPU RAM:

- `GGML_OP_CPY`: `prima_kv_cpy.comp` supports `F32/F16 -> {BF16, Q8_0, Q4_0, Q4_1, Q5_0, Q5_1, IQ4_NL}`
  quantization, `{BF16, Q8_0, Q4_0, Q4_1, Q5_0, Q5_1, IQ4_NL} -> F32/F16` dequantization
  (used by `build_k_shift`), and same-type `{BF16, Q8_0, Q4_0, Q4_1, Q5_0, Q5_1, IQ4_NL}`
  repack (used by KV defrag), honoring arbitrary block-aligned `src0`/`src1`/`dst` byte offsets.
- `GGML_OP_MUL_MAT` (non-FA): supports non-zero tensor view offsets on `src0` (`K` cache in all
  9 types; `V` cache in `f32/f16/bf16`).
- `GGML_OP_FLASH_ATTN_EXT`: `prima_flash_attn.comp` and `prima_flash_attn_reduce.comp`
  support all 9 KV types (`f32, f16, bf16, q8_0, q4_0, q4_1, q5_0, q5_1, iq4_nl`), head
  dimensions `64, 80, 96, 112, 128, 192, 256`, GQA ratios, ALiBi (`max_bias`), logit
  softcap, F16/F32 causal/SWA masks, and `src[4]` attention sinks (`GGML_TYPE_F32`) with
  split-KV parallel reduction for decode/small-`N` batches.
- `--strict-device-kv` (`strict_device_kv_v1`): verifies before and after backend scheduler
  allocation that every graph node whose `src[]` or `dst` aliases a non-host KV buffer is
  supported by and assigned to that GPU backend, failing fast at context setup if any op
  would fall back to CPU.


---

**Related:** registry/device selection never establishes row/tensor/expert support, physical distributed acceptance or performance qualification — see 12-placement-qualification.md.
