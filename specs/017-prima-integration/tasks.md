# Implementation tasks

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
