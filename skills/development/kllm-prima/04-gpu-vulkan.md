# 04 · GPU & Vulkan device selection

**Use when:** You work on Vulkan logical→physical device mapping, explicit local device selection and visibility masking, driver-unavailable startup behavior, or output-only GPU coordinators.

**Keywords:** Vulkan, logical, ordinal, physical, UUID, driver, adapter, visibility mask, explicit devices, explicit_devices_v1, output_only_gpu_v1, output_gpu, ngl, GPU inventory, discovery.

**Related:** 03-windows-native.md, 10-profile-costs.md, 12-placement-qualification.md.

## Quick rules

- Map logical visible ordinals to physical identities explicitly; never assume device zero, use adapter display names as unique identities, or substitute CPU/Metal/CUDA.
- Execution is established only by completed prefill/decode GPU work and exact assigned layers/tensors — device enumeration and configured ranks do not establish it.
- Explicit local devices: preserve native registry/device/buffer identity and ordered selection; CPU-only none must bypass all GPU backend initialization; selected Vulkan host buffers must not initialize an unselected adapter; managed UUID selectors require physical visibility masking followed by remapped native `--device` names; verify the actual binary flag and `explicit_devices_v1` capability before multi-device admission; report only adapters with executed work, including zero assigned-work devices; check nonzero logical ordinals, ordered multi-adapter splits and no-visible-GPU CPU execution with owned functional model processes.
- An empty Vulkan visibility mask does not reproduce driver-unavailable startup — check that separately in bounded owned subprocesses and retain the failed contract/CI receipt; keep Vulkan initialization exceptions inside the `/EHsc` C boundary (see 03) and expose an empty GPU inventory on failure; CPU-only inference must still work, repeated discovery must settle, and explicit unavailable GPU selection must refuse before model loading; add a driver-unavailable CI contract without claiming GPU acceptance.
- Output-only GPU coordinators: retain `ngl` as the transformer count and allow rank-zero `output_gpu` with zero transformer offload on one selected device; negotiate `output_only_gpu_v1`, preserve explicit device/visibility selection and Vulkan signed hardware identity, and require the output backend at startup; reserve the output graph even when CUDA transformer offload is zero; in evidence never use `assigned[-ngl:]` when `ngl` is zero — keep the transformer GPU set empty and require completed output projection in both prefill and decode on the selected physical GPU; price output upload/logits download separately from transformer visits, including CPU-transformer local heads.

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


---

**Related:** registry/device selection never establishes row/tensor/expert support, physical distributed acceptance or performance qualification — see 12-placement-qualification.md.
