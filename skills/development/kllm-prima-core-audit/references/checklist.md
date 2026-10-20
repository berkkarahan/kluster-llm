# Prima Core Native Distributed Runtime Audit Checklist

Detailed inspection checklist for auditing the native distributed engine of `prima.cpp`.

---

## 1. Distributed Ring Tensor Transfers & Error Handling

### 1.1 Asymmetrical Ring Error Catching & Cluster-Wide Deadlocks
- [ ] **Check**: Does `llama_segment_execute` propagate segment computation errors down the ring?
  - **Inspection Target**: `src/llama.cpp:24635-24883`.
  - **Pattern**: When segment execution throws an exception (lines 24876–24883), the catch block sets `ctx.segment_fault = true` and returns `false`, but sends NO notification to `ctx.send_socket`.
  - **Hazard**: Downstream peer nodes in the ring are blocked at line 24760 (`zmq::recv_multipart`) waiting for activations. With `rcvtimeo` set to 30,000 ms (line 24637), the entire cluster hangs for 30 seconds before downstream nodes throw timeouts.
  - **Remediation**: Send an explicit error packet (e.g. `"prima_segment_error"`) along `ctx.send_socket` before returning. Downstream nodes receiving this packet must forward it and abort immediately without waiting for timeouts.

### 1.2 Unchecked Strides & Memory Corruption in `llama_recv_tensors`
- [ ] **Check**: Does `llama_recv_tensors` validate received frame counts, tensor dimensions, and payload sizes?
  - **Inspection Target**: `src/llama.cpp:24588-24610, 25501-25522`.
  - **Hazards**:
    - **Stride Loop**: `for (size_t i = 0; i < recv_msgs.size(); i += 3)`. If `recv_msgs.size() % 3 != 0`, accessing `recv_msgs[i + 1]` or `recv_msgs[i + 2]` is an out-of-bounds `std::vector` access.
    - **Buffer Overflow**: `std::memcpy(batch_embd, data_msg.data(), buf_size)` executes without verifying `data_msg.size() >= buf_size` or checking destination buffer bounds.
    - **Silent Failure**: If `zmq::recv_multipart` fails, the function logs an error and returns `void`. The caller proceeds to execute the graph with uninitialized garbage memory.
  - **Remediation**: Return `bool` status. Verify `recv_msgs.size() % 3 == 0`, `dims_msg.size() >= 2 * sizeof(int64_t)`, and `data_msg.size() >= buf_size`. Abort graph computation on failure.

---

## 2. Server Request Queue & Worker Lifecycles

### 2.1 Orphaned Cancellation Results & 100% CPU Busy-Wait Spin
- [ ] **Check**: Does `server_response::recv` spin continuously when an unrelated task is cancelled?
  - **Inspection Target**: `examples/server/server.cpp:693-730, 2331-2382`.
  - **Hazard**: When an HTTP client aborts an SSE stream, `cancel_tasks` pushes a cancellation result into `queue_results`. Because the streaming thread has exited, nobody dequeues this result.
  - **Bug Trace**: In `server_response::recv(other_id_tasks)`:
    ```cpp
    while (true) {
        std::unique_lock<std::mutex> lock(mutex_results);
        condition_results.wait(lock, [&]{ return !queue_results.empty(); });
        for (int i = 0; i < (int) queue_results.size(); i++) {
            if (id_tasks.find(queue_results[i].id) != id_tasks.end()) {
                ...
                return res;
            }
        }
    }
    ```
    Because `queue_results` contains the orphaned cancellation result, `!queue_results.empty()` is perpetually `true`. `wait` returns immediately, finds no matching task, loops back, and spins in an infinite 100% CPU loop, starving the entire server process.
  - **Remediation**:
    - Change the condition variable predicate to evaluate whether matching results exist:
      ```cpp
      condition_results.wait(lock, [&] {
          return std::any_of(queue_results.begin(), queue_results.end(),
              [&](const server_task_result & r) { return id_tasks.count(r.id) > 0; });
      });
      ```
    - In `cancel_tasks`, do not push results to `queue_results` if the receiver has already terminated.

### 2.2 Unhandled Missing Key Exception on Task Cancellation
- [ ] **Check**: Does non-streaming completion dispatch handle cancelled tasks safely?
  - **Inspection Target**: `examples/server/server.cpp:2362-2365`.
  - **Pattern**: In `receive_cmpl_results`, `result.data["index"]` is accessed without verifying key existence. Cancelled tasks return `{"cancelled": true}` without `"index"`, throwing an unhandled `nlohmann::json::type_error` that aborts the thread.
  - **Remediation**: Check `if (result.data.contains("cancelled") || !result.data.contains("index")) return;`.

---

## 3. Hot-Path Allocations & Serialization

### 3.1 Heap Churn on Token Sampling Hot Path
- [ ] **Check**: Does `llama_sampler_sample` reallocate multi-megabyte vectors on every token?
  - **Inspection Target**: `src/llama-sampling.cpp:279-307`.
  - **Pattern**: `std::vector<llama_token_data> cur; cur.reserve(n_vocab);`.
  - **Hazard**: For large vocabularies ($V = 152,064$), allocating and deallocating 2.43 MB per generated token creates hundreds of megabytes per second of heap churn, page cache thrashing, and memory allocator lock contention.
  - **Remediation**: Maintain a reusable scratch vector in `llama_context` or `llama_sampler` initialized once at startup.

### 3.2 Dynamic JSON DOM Overhead on Distributed Activation Ring
- [ ] **Check**: Does the distributed ring construct and parse JSON strings on per-hop micro-batches?
  - **Inspection Target**: `src/llama.cpp:24669-24677, 24768, 24817-24845`.
  - **Hazard**: Allocating, formatting, and DOM-parsing JSON strings per hop adds milliseconds of CPU serialization latency directly to the auto-regressive generation critical path.
  - **Remediation**: Replace JSON headers with a fixed-size binary struct `prima_segment_header` (e.g. 36 bytes).

---

## 4. Vulkan Compute Pipelines & Thread Safety

### 4.1 Data Race in `ggml_vk_graph_cleanup` on Descriptor Set Requirements
- [ ] **Check**: Is `pipeline_descriptor_set_requirements` protected by `device->mutex` during graph cleanup?
  - **Inspection Target**: `ggml/src/ggml-vulkan.cpp:778-818, 6524-6566`.
  - **Hazard**: Allocation paths acquire `std::lock_guard<std::mutex> guard(device->mutex)`. However, `ggml_vk_graph_cleanup` iterates over and clears `ctx->device->pipeline_descriptor_set_requirements` WITHOUT acquiring `device->mutex`. Concurrent graph execution causes iterator invalidation and heap corruption.
  - **Remediation**: Acquire `device->mutex` in `ggml_vk_graph_cleanup` before reading or modifying shared device pipeline maps.

### 4.2 System Error Escapes Across C ABI & Exception Slicing
- [ ] **Check**: Are Vulkan queue submission errors caught before crossing C ABI boundaries?
  - **Inspection Target**: `ggml/src/ggml-vulkan.cpp:856-862, 928-931, 1119-1129`.
  - **Hazard**: Under GPU timeout (TDR) or VRAM exhaustion, `queue.submit()` throws `vk::DeviceLostError` or `vk::SystemError`. Uncaught exceptions crossing `extern "C"` functions abort the process under MSVC `/EHsc`.
  - **Hazard (Slicing)**: In `ggml_vk_create_buffer`, `catch (const vk::SystemError & e) { ... throw e; }` re-throws by value, slicing polymorphic exception types.
  - **Remediation**: Catch `const vk::SystemError &` at graph compute boundaries and return failure status; use `throw;` instead of `throw e;`.

---

## 5. Remote RPC Security & Robustness

### 5.1 Process Abort via `GGML_ASSERT` on Network Input
- [ ] **Check**: Do RPC packet decoders invoke `GGML_ASSERT` or `GGML_ABORT` on untrusted inputs?
  - **Inspection Target**: `ggml/src/ggml-rpc.cpp:901-903, 945-947`.
  - **Hazard**: Calling `GGML_ASSERT` or `GGML_ABORT` on remote network input allows any malformed packet or client bug to immediately crash the server daemon (Denial of Service).
  - **Remediation**: Replace assertions with graceful packet validation that returns `false` or `nullptr`.

### 5.2 Non-Null-Terminated String Overreads
- [ ] **Check**: Are tensor name buffers null-terminated before passing to string functions?
  - **Inspection Target**: `ggml/src/ggml-rpc.cpp:911`.
  - **Pattern**: `ggml_set_name(result, tensor->name)` where `tensor->name` is a fixed 64-byte array from the network.
  - **Hazard**: If the remote client sends a non-null-terminated string, `ggml_set_name` overreads past the buffer boundary.
  - **Remediation**: Enforce `tensor->name[63] = '\0'` before passing to `ggml_set_name`.

---

## 6. Socket Timeouts & Windows Winsock

### 6.1 Blocking `connect()` Ignoring `SO_RCVTIMEO`
- [ ] **Check**: Does socket connection logic rely on `SO_RCVTIMEO` to enforce connect timeouts?
  - **Inspection Target**: `src/network-utils.cpp:13-48`.
  - **Hazard**: Neither Winsock nor POSIX applies `SO_RCVTIMEO` to blocking `connect()` calls. If a peer host is unreachable, `connect()` blocks for up to 75 seconds (TCP SYN retry timeout), ignoring `timeout_sec`.
  - **Remediation**: Set socket to non-blocking mode (`FIONBIO`), call `connect()`, and use `select()` with `struct timeval` to enforce the deadline.

---

## 7. Layer Allocation Arithmetic & Model Loader Lifecycle

### 7.1 Zero-Layer Division-by-Zero and Infinite Loops
- [ ] **Check**: Do layer ownership functions handle zero-layer window assignments?
  - **Inspection Target**: `src/llama.cpp:4237-4264, 8851-8855`.
  - **Hazards**:
    - `this_layer_is_mine`: If all ranks have 0 layers, `cumulative_layers` never increments, causing an infinite `while (true)` loop.
    - `map_layer_to_local_id`: If `cycle_size == 0`, `layer_id % cycle_size` crashes with `SIGFPE` (division by zero).
    - `llm_load_tensors_impl`: If `window_size == 0`, `local_i % window_size` crashes with division by zero.
  - **Remediation**: Add explicit preconditions: return false if total window size is 0, and validate `cycle_size > 0` before modulo operations.

### 7.2 Resource and Memory Leaks in Model Loader
- [ ] **Check**: Is `llama_model_loader` freed when exceptions occur during model loading?
  - **Inspection Target**: `src/llama.cpp:10897-10953`.
  - **Pattern**: `ml = new llama_model_loader(...)` followed by calls that throw exceptions. Catch block jumps past `delete ml`.
  - **Hazard**: File descriptors and memory-mapped file handles leak on corrupt model load failures.
  - **Remediation**: Wrap `ml` in `std::unique_ptr<llama_model_loader>`.
