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
