---
name: kllm-prima-delta-audit
description: "Audit the berkkarahan/prima.cpp fork delta vs upstream and the kluster-llm integration boundary across recipe digests, placement, and qualification. On-demand only; requires explicit human user permission before launching."
disable-model-invocation: true
---

# Audit Prima Fork Delta & Kluster Integration Boundary

Guide for conducting an on-demand, non-destructive audit of the `berkkarahan/prima.cpp` fork additions/modifications relative to upstream `OpenCPIL/prima.cpp` and the `kluster-llm` $\leftrightarrow$ `prima.cpp` integration boundary.

Covers `src/kllm/modules/primacpp/` (recipe pins, qualification, placement, scheduler, lifecycle), native process supervisors in `crates/kllm-native/`, and fork delta changes in the `prima.cpp` checkout (commits `288df6a..origin/main`, Vulkan memory/device management, ZeroMQ control rings, and C ABI boundaries).

This skill is strictly **on-demand** and must **never** be invoked automatically.

---

## 1. Human Permission Required Before Audit Launch

**MANDATORY FIRST STEP**: Before inspecting any code, running git commands, or gathering audit notes, you MUST prompt the human user and receive explicit, unambiguous authorization to proceed.

1. State clearly to the user:
   - Target scope: `berkkarahan/prima.cpp` fork delta (`288df6a..origin/main`) and `kluster-llm` integration boundary (`src/kllm/modules/primacpp/`, `crates/kllm-native/src/process.rs`).
   - Audit dimensions: Recipe/digest verification, zero-trust qualification gates, HiGHS MILP scheduler fallback complexity, process supervisor lifecycle, Vulkan device enumeration/memory detection, C ABI exception safety, and ZeroMQ IPC schemas.
   - Non-destructive guarantee: All operations are strictly read-only; no code, build configurations, or git checkouts will be modified.
   - Output destination: Review artifacts will be stored in a separate designated audit workspace (e.g. `~/teamwork_projects/kluster_prima_review/`).
2. Ask: *"Do you give permission to launch the on-demand Prima Fork Delta and Integration Boundary audit?"*
3. **HALT and wait** for the user's explicit confirmation. Do NOT proceed to Step 2 until permission is granted.

---

## 2. Environment Setup & Dual Repository Hygiene

Once explicit user permission is confirmed:

1. **Verify Both Checkouts**:
   - `kluster-llm`: Locate the Kluster checkout via `KLLM_REPO_ROOT`, current checkout, or `kllm.shared.state.repo_root()`, containing `src/kllm/modules/primacpp/` and `crates/kllm-native/src/process.rs`.
   - `prima.cpp`: Locate the local `prima.cpp` checkout (or create an isolated checkout of the pinned fork commit) containing fork commits and native implementations.
2. **Strict Read-Only Verification**: Confirm `git status` in both repositories is clean or preserve existing uncommitted work without making any edits.
3. **Pin and Commit Verification**:
   - Check `src/kllm/modules/primacpp/interactors/recipe.py` for the authoritative pinned commit (`source_commit`).
   - Run `python -c "from kllm.modules.primacpp.interactors.recipe import recipe_digest; print(recipe_digest())"` to inspect recipe digest computation. Never hardcode recipe digests or track moving git HEADs.
4. **Inspect Fork History**:
   - Review commit range `git log --oneline 288df6a..origin/main` in the `prima.cpp` checkout to map fork modifications against upstream `OpenCPIL/prima.cpp`.

---

## 3. Boundary & Fork Delta Audit Methodology

Conduct the audit using the comprehensive [Audit Checklist](references/checklist.md) across these core subsystems:

### Subsystem 1: Runtime Qualification & Zero-Trust Integrity
- Inspect `src/kllm/modules/primacpp/qualification.json` and `interactors/qualification.py`.
- **Integrity Check**: Ensure shipped platform profiles (especially Linux and macOS) DO NOT contain `"unrestricted": true` or dummy receipts (`"default-prima-runtime"`).
- Per project constitution (`skills/kllm-prima/12-placement-qualification.md`), non-Windows platform profiles must ship with every gate `false` and empty receipts until physical hardware validation is recorded.

### Subsystem 2: Process Supervisor & Serving Lifecycle
- Inspect `src/kllm/modules/primacpp/interactors/lifecycle.py` (`RankSupervisor.readiness_report`, `ready()`).
- Verify that serving log growth beyond 4 MB does not cause `ValueError` crashes or break status reporting after the rank has reached phase `"ready"`. Ensure the supervisor memoizes validated readiness reports on `self.owner`.
- Audit launch argument generation in `placement.py:rank_launch()["extra_args"]` against `crates/kllm-native/src/process.rs:501-505` (`PrimaLaunchSpec::from_dict`). Ensure arguments do not collide with native reserved ownership flags.

### Subsystem 3: Placement & HiGHS MILP Scheduler Invariants
- Inspect `src/kllm/modules/primacpp/interactors/highs_scheduler.py` (`_solve_with_branch_and_bound`).
- Audit the pure-Python recursive branch-and-bound fallback used when `highspy` is absent.
- Check for hard node visit budgets (`max_nodes`), iteration limits, and monotonic deadline timeouts to prevent $O(W^D)$ exponential hangs on large models (40–80 layers across multiple devices).

### Subsystem 4: Vulkan Hardware Enumeration & ReBAR Memory
- Inspect `ggml/src/ggml-vulkan.cpp` (`ggml_backend_vk_get_device_memory`, `ggml_backend_vk_get_device_identity`) in the `prima.cpp` checkout.
- Check heap enumeration logic: breaking on the first `eDeviceLocal` heap incorrectly reports 256 MB on Resizable BAR (SAM) systems where Heap 0 is the host aperture.
- Verify lower-bound device assertions (`device >= 0`) and check for redundant `enumeratePhysicalDevices()` calls on hot query paths.

### Subsystem 5: Distributed Control Ring & Shutdown Deadlocks
- Inspect `examples/main/main.cpp:585-596, 774-782` and `src/llama.cpp:28226-28243` in `prima.cpp`.
- Trace the shutdown path: when master sends `"STOP"` over the signal socket, verify worker threads blocked in `llama_decode` on `ctx->recv_socket` are unblocked and not left deadlocked.
- Check synchronization of `stop_signal` across threads (atomic boolean vs raw pointer data race).

### Subsystem 6: C ABI Exception Boundary Safety
- Audit exported C API functions in `ggml-vulkan.cpp` (e.g. `ggml_vk_registry_device_get`) and `src/llama.cpp` (`llama_decode`).
- Under MSVC on Windows compiled with `/EHsc`, ensure C++ exceptions (`std::out_of_range`, `std::runtime_error`) do not escape across `extern "C"` boundaries, which triggers immediate `std::terminate()` aborts.

### Subsystem 7: ZeroMQ IPC Memory & Socket Lifecycle
- Inspect error and exception catch blocks in `src/llama.cpp` (`llama_rebuild_topo`, `llama_gather_device_info`, `llama_send_device_info`).
- Verify RAII wrappers are used so heap arrays (`TopoRebuildHelperInfo[]`), serialized buffers, and `zmq::socket_t` handles do not leak on early error returns or `zmq::error_t` throws.

---

## 4. Evidence Standards & Adversarial Verification

Every finding must provide:
1. Exact disk citation (`file:line`) verified against physical files in the `kluster-llm` and `prima.cpp` checkouts.
2. Step-by-step dataflow trace showing trigger conditions and control execution paths.
3. Calibrated severity (Critical, High, Medium, Low) based on real multi-node operational impact.
4. Non-destructive, minimal-diff remediation compliant with repository architecture rules.

Filter out false positives by verifying exact file lines, confirming reachability, and distinguishing intentional development trade-offs from defect states.

---

## 5. Audit Deliverable Structure

Compile findings into the audit report:
- Executive Summary & Fork Verification Matrix.
- Subsystem Risk Analysis (Qualification, Lifecycle, Scheduler, Vulkan, Distributed IPC).
- Line-by-Line Verified Findings with Citations, Traces, and Fixes.
- Attestation confirming zero modifications to tracked files in either repository.
