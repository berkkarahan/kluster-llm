# Prima Fork Delta & Integration Boundary Audit Checklist

Detailed inspection checklist for auditing the `berkkarahan/prima.cpp` fork delta and the `kluster-llm` integration boundary (`src/kllm/modules/primacpp/`).

---

## 1. Recipe Pins & Digest Invariants

### 1.1 Authoritative Commit Pins & Dynamic Digest
- [ ] **Check**: Are recipe source commits and digests strictly synchronized and dynamically calculated?
  - **Inspection Target**: `src/kllm/modules/primacpp/interactors/recipe.py:25-45`.
  - **Normative Rule**: Per `skills/kllm-prima/01-sources-setup.md`, treat `recipe.py` / SQLite recipe pins as authoritative. Compute the digest through `recipe_digest()`. Never hardcode it, never read a moving git HEAD, and ensure isolated pinned-commit checkouts.
  - **Hazard**: Hardcoded recipe hashes desynchronize from actual native builds, causing qualification and runtime preflight rejections.

---

## 2. Zero-Trust Runtime Qualification & Integrity

### 2.1 Unrestricted Scopes and Placeholder Receipts (Integrity Invariant)
- [ ] **Check**: Do shipped default platform profiles in `qualification.json` contain `"unrestricted": true` or dummy receipts?
  - **Inspection Target**: `src/kllm/modules/primacpp/qualification.json:70-205` and `interactors/qualification.py:410-465`.
  - **Normative Rule**: Per `skills/kllm-prima/12-placement-qualification.md`, defining a scope qualifies nothing. Linux/macOS entries must ship with every gate `false` (`vulkan_gpu_inference`, `api_parity`, `model_and_placement_parity`, `signed_rank_lifecycle`, etc.), empty devices (`"devices": []`), and empty receipts (`"receipts": []`) until a physical run records genuine evidence.
  - **Hazard**: Setting `"unrestricted": true` bypasses model, device, and digest checks, subverting zero-trust qualification on non-Windows platforms.
  - **Remediation**:
    - Update all non-Windows profiles in `qualification.json` to have `"unrestricted": false`, all gates `false`, and empty arrays.
    - Require an explicit command-line developer override flag (`--allow-unqualified-runtime`) in `qualification.py` rather than permitting default JSON files to bypass verification.

---

## 3. Process Supervisor & Serving Lifecycle

### 3.1 Serving Log Growth & Readiness Memoization
- [ ] **Check**: Does `RankSupervisor.readiness_report` crash when log files exceed 4 MB during active serving?
  - **Inspection Target**: `src/kllm/modules/primacpp/interactors/lifecycle.py:220-275`.
  - **Normative Rule**: Per `skills/kllm-prima/11-run-lifecycle-network.md`, after validated readiness, retain acknowledgement in owned state and check PID/start token plus HTTP health. Growing serving logs must not trigger the startup parser's 4 MB limit or require another startup acknowledgement.
  - **Hazard**: Calling `readiness_report()` on an active rank with > 4 MB logs raises `ValueError("rank log exceeded 4MB without readiness marker")`, crashing monitoring loops and reconciliation routines.
  - **Remediation**:
    - In `RankSupervisor.ready()`, store `self.owner["readiness_report"] = dict(report)` and save state.
    - In `RankSupervisor.readiness_report()`, return `self.owner["readiness_report"]` immediately if the child process is alive.
    - When parsing startup logs, read up to 4 MB or scan from the beginning for `KLLM_PRIMA_RANK_READY=` instead of failing on total file size.

### 3.2 Launch Argument Schema Collisions with Native Rust Supervisor
- [ ] **Check**: Do generated launch arguments conflict with reserved execution ownership flags?
  - **Inspection Target**: `src/kllm/modules/primacpp/interactors/placement.py:250-275` and `crates/kllm-native/src/process.rs:501-505`.
  - **Hazard**: `placement.py:rank_launch()` emits arguments (`-ngl`, `--world`, `--rank`, `--master`, etc.) into `"extra_args"`. In `process.rs`, `PrimaLaunchSpec::from_dict` rejects any `"extra_args"` matching those reserved keys with `ValueError: Prima extra arguments cannot override execution ownership`.
  - **Remediation**: Refactor `rank_launch()` to return structured manifest fields, and split raw CLI arguments into an isolated helper `rank_cli_args()`.

---

## 4. Placement & MILP Scheduler Invariants

### 4.1 Exponential Fallback Complexity in HiGHS Scheduler
- [ ] **Check**: Does the pure-Python branch-and-bound solver have node budgets and wall-clock timeouts?
  - **Inspection Target**: `src/kllm/modules/primacpp/interactors/highs_scheduler.py:180-289`.
  - **Hazard**: When `highspy` is absent, execution falls back to recursive branch-and-bound `search()`. With no iteration counter, no node limit, and no deadline check, searching 40–80 layers across $\ge 4$ devices runs in $O(W^D)$ exponential time, causing an indefinite Python hang.
  - **Remediation**:
    - Impose `max_nodes = 50_000` and `deadline = time.monotonic() + 2.0`.
    - If limits are reached, immediately abort the recursive search and fall back to the greedy capacity-proportional heuristic allocator.

---

## 5. Vulkan Hardware Detection & ReBAR Memory

### 5.1 Resizable BAR (SAM) 256 MB Heap Misdetection
- [ ] **Check**: Does Vulkan device memory inspection break on the first device-local heap?
  - **Inspection Target**: `ggml/src/ggml-vulkan.cpp:7410-7418` (in `prima.cpp`).
  - **Pattern**: `if (heap.flags & vk::MemoryHeapFlagBits::eDeviceLocal) { *total = heap.size; *free = heap.size; break; }`.
  - **Hazard**: On systems with Resizable BAR / SAM (and Intel Arc GPUs), Heap 0 is the 256 MB host-visible BAR aperture, and Heap 1 is the 8–16 GB VRAM heap. Breaking on the first heap reports total VRAM as 256 MB.
  - **Remediation**: Iterate all memory heaps, identify all `eDeviceLocal` heaps, and select the heap with the maximum size: `if ((heap.flags & eDeviceLocal) && heap.size > max_size) max_size = heap.size;`.

### 5.2 Missing Lower-Bound Assertion on Device Index
- [ ] **Check**: Is `device >= 0` validated in device memory queries?
  - **Inspection Target**: `ggml/src/ggml-vulkan.cpp:7404-7406` (in `prima.cpp`).
  - **Pattern**: `GGML_ASSERT(device < (int) vk_instance.device_indices.size());` (missing `device >= 0`).
  - **Remediation**: Use `GGML_ASSERT(device >= 0 && device < (int) vk_instance.device_indices.size());` matching line 7393.

### 5.3 Redundant Driver Round-Trips in Enumeration
- [ ] **Check**: Does every memory and property query invoke `enumeratePhysicalDevices()`?
  - **Inspection Target**: `ggml/src/ggml-vulkan.cpp:7395, 7407` (in `prima.cpp`).
  - **Hazard**: Repeated driver round-trips and vector reallocations during startup and profiling queries.
  - **Remediation**: Cache `physical_devices` in `vk_instance` during initialization.

---

## 6. Distributed Control Ring & Shutdown Lifecycle

### 6.1 Worker Deadlock Trap on Stop Signal
- [ ] **Check**: Do worker processes deadlock when receiving a stop signal while waiting for tensor activations?
  - **Inspection Target**: `examples/main/main.cpp:585-596, 774-782` and `src/llama.cpp:28226-28243` (in `prima.cpp`).
  - **Hazard**: Main worker thread is blocked in `llama_decode` on `ctx->recv_socket` awaiting tensor activations from rank 0. When master stops, it broadcasts `"STOP"` to `signal_socket`. The worker's `signal_thread` receives `"STOP"` and exits, but the main thread remains permanently blocked on `ctx->recv_socket`.
  - **Hazard (Data Race)**: `stop_signal` is an unsynchronized `char *` written in `signal_thread` and read in the main thread without memory barriers.
  - **Remediation**:
    - Use `std::atomic<bool> stop_requested{false}`.
    - On stop signal, interrupt or close `ctx->recv_socket` so `llama_decode` returns immediately.

---

## 7. C ABI Exception Boundary Safety

### 7.1 C++ Exceptions Escaping Exported `extern "C"` Functions
- [ ] **Check**: Do exported C API functions throw C++ exceptions under MSVC `/EHsc`?
  - **Inspection Targets**:
    - `ggml/src/ggml-vulkan.cpp:7369` (in `prima.cpp`): `throw std::out_of_range("Invalid Vulkan registry index");` inside `ggml_vk_registry_device_get`.
    - `src/llama.cpp:31541-31546` (in `prima.cpp`): `llama_decode` calling `llama_decode_internal` without an outer `try/catch`.
  - **Hazard**: Under MSVC `/EHsc`, throwing exceptions across `extern "C"` boundaries violates the ABI and causes immediate process termination via `std::terminate()`.
  - **Remediation**: Return `nullptr` or negative error codes instead of throwing across C boundaries.

---

## 8. ZeroMQ IPC Memory & Resource Leaks

### 8.1 Array and Socket Leaks on Topology Error Paths
- [ ] **Check**: Are raw heap arrays and ZeroMQ sockets safely managed with RAII across error returns?
  - **Inspection Target**: `src/llama.cpp:28023-28160` (in `prima.cpp`).
  - **Pattern**: `TopoRebuildHelperInfo * topo_helper = new TopoRebuildHelperInfo[n_world];` with 5 early returns (`return -1;`) that bypass `delete[] topo_helper`.
  - **Hazard**: Network errors during ring topology reconfiguration leak arrays and socket handles.
  - **Remediation**: Use `std::vector<TopoRebuildHelperInfo> topo_helper(n_world)` and `std::unique_ptr<zmq::socket_t>`.

### 8.2 Serialized Buffer Leak on zmq::send_multipart Exception
- [ ] **Check**: Are serialized buffers freed when `zmq::send_multipart` throws?
  - **Inspection Target**: `src/llama.cpp:27869-27880, 27907-27917` (in `prima.cpp`).
  - **Pattern**: `char * buffer` allocated by `serialize()`; `catch (const zmq::error_t &)` catches exceptions without calling `free(buffer)`.
  - **Remediation**: Wrap `buffer` in `std::unique_ptr<char[], decltype(&std::free)>` immediately after allocation.
