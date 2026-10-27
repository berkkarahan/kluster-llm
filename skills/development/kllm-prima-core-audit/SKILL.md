---
name: kllm-prima-core-audit
description: "Audit the broader prima.cpp native distributed runtime across ring communication, Vulkan/CPU backends, KV caches, and server APIs. On-demand only; requires explicit human user permission before launching."
disable-model-invocation: true
---

# Audit Prima Native Core Distributed Runtime

Guide for conducting an on-demand, non-destructive audit of the native `prima.cpp` distributed engine.

Covers distributed execution across the ring topology, ZeroMQ tensor streaming, Vulkan compute pipelines and concurrency, KV cache replication and synchronization, server request queue lifecycles, and hot-path memory churn in `src/llama.cpp`, `ggml/src/ggml-vulkan.cpp`, `ggml/src/ggml-rpc.cpp`, `src/llama-sampling.cpp`, and `examples/server/server.cpp`.

This skill is strictly **on-demand** and must **never** be invoked automatically.

---

## 1. Human Permission Required Before Audit Launch

**MANDATORY FIRST STEP**: Before inspecting any code, running compilation checks, or gathering audit notes, you MUST prompt the human user and receive explicit, unambiguous authorization to proceed.

1. State clearly to the user:
   - Target scope: `prima.cpp` native runtime repository.
   - Audit dimensions: Distributed ring tensor transfers, barrier synchronization & deadlocks, server queue thread starvation, hot-path heap churn, Vulkan pipeline race conditions, and RPC denial-of-service vulnerabilities.
   - Non-destructive guarantee: All operations are strictly read-only; no tracked C/C++ source files, build scripts, or artifacts will be modified.
   - Output destination: Review artifacts will be stored in a separate designated audit workspace (e.g. `~/teamwork_projects/kluster_prima_review/`).
2. Ask: *"Do you give permission to launch the on-demand Prima Core Native Runtime audit?"*
3. **HALT and wait** for the user's explicit confirmation. Do NOT proceed to Step 2 until permission is granted.

---

## 2. Environment Setup & Native Codebase Navigation

Once explicit user permission is confirmed:

1. **Verify Repository**: Locate the `prima.cpp` checkout and verify it contains `src/llama.cpp`, `ggml/src/`, `examples/server/`, and `CMakeLists.txt`.
2. **Strict Read-Only Verification**: Check `git status` to verify working tree hygiene. Never alter native source files or build trees during an audit.
3. **Architectural Guidelines**: Consult `skills/kllm-prima/` topics (01 through 12) in the Kluster checkout for core contracts regarding Vulkan devices, KV caches, server templates, and network leases.

---

## 3. Core Native Subsystems Audit Methodology

Conduct the audit using the comprehensive [Audit Checklist](references/checklist.md) across these critical native subsystems:

### Subsystem 1: Distributed Ring Communication & Deadlock Resilience
- **Error Propagation**: Inspect `src/llama.cpp:24635-24883` (`llama_segment_execute`). Ensure catch blocks do not silently swallow segment computation failures. Swallowing exceptions without forwarding an error packet along the ring leaves downstream nodes blocked on `zmq::recv_multipart` for up to 30 seconds (`rcvtimeo`).
- **Packet Validation & Buffer Safety**: Inspect `src/llama.cpp:24588-24610` (`llama_recv_tensors`). Check for unchecked loop strides (`i += 3`), unverified message payload sizes, and unvalidated `memcpy` calls into tensor memory. Ensure receive failures propagate error codes rather than continuing with uninitialized memory.
- **Wire Serialization Overhead**: Audit lines 24669–24845. Check for dynamic JSON DOM parsing and string serialization on per-hop micro-batch activation paths.

### Subsystem 2: Server Queue & Worker Thread Lifecycles
- **Task Cancellation Predicate**: Inspect `examples/server/server.cpp:693-730, 2331-2382` (`server_response::recv`, `cancel_tasks`).
- Audit the condition variable predicate `condition_results.wait(lock, [&]{ return !queue_results.empty(); })`.
- When an HTTP client disconnects from an SSE stream, verify that orphaned cancellation results pushed to `queue_results` do NOT cause other worker threads to spin in a 100% CPU busy-wait loop.
- Verify non-streaming completion handlers (`receive_cmpl_results`) validate the presence of `"index"` and `"cancelled"` keys before indexing `result.data`.

### Subsystem 3: Hot-Path Memory Allocations & Heap Churn
- **Sampling Vector Churn**: Inspect `src/llama-sampling.cpp:279-307` (`llama_sampler_sample`). Check for multi-megabyte heap allocations (`std::vector<llama_token_data> cur`) reallocated on every single generated token.
- Verify whether scratch buffers are pooled or retained in context structures to eliminate heap lock contention under heavy concurrency.

### Subsystem 4: Vulkan Compute Pipelines & Thread Safety
- **Descriptor Set Race Conditions**: Inspect `ggml/src/ggml-vulkan.cpp:778-818, 6524-6566`.
- Verify that `ggml_vk_graph_cleanup` acquires `device->mutex` before iterating and clearing `ctx->device->pipeline_descriptor_set_requirements` and `ctx->device->pipelines`.
- **System Error Escapes**: Ensure `vk::SystemError` and `vk::DeviceLostError` during queue submissions (`ggml_vk_submit`) are caught before bubbling across C ABI boundaries. Check for exception slicing (`throw e;` vs `throw;`).

### Subsystem 5: Remote RPC Security & Robustness
- **Assertion Denial of Service**: Inspect `ggml/src/ggml-rpc.cpp:901-1128`.
- Verify that remote network input does not trigger `GGML_ASSERT` or `GGML_ABORT`, which immediately aborts the server daemon.
- Check tensor name string buffers for guaranteed null-termination before passing to `ggml_set_name`.

### Subsystem 6: Socket Timeout Enforcement & Layer Math
- **Winsock Timeouts**: Inspect `src/network-utils.cpp:13-48`. Ensure `connect()` uses non-blocking sockets and `select()` deadlines rather than relying on `SO_RCVTIMEO` (which does not apply to TCP `connect()`).
- **Layer Allocation Math**: Inspect `src/llama.cpp:4237-4264, 8851-8855`. Verify that zero-layer allocations (e.g. coordinator/forwarder nodes) do not trigger infinite loops in `this_layer_is_mine` or division-by-zero crashes in `map_layer_to_local_id`.

---

## 4. Evidentiary Standards & Finding Trace Verification

All reported findings must satisfy:
1. **Verified Physical Citations**: Verified file path and exact line ranges on disk in the `prima.cpp` checkout.
2. **End-to-End Execution Trace**: Step-by-step walkthrough showing how inputs, network packets, or thread schedules trigger the defect.
3. **Realistic Severity**: Calibrated against distributed cluster failure modes, memory leaks, and thread starvation.
4. **Concrete Remediation**: Native C++ / GGML idioms (RAII guards, condition variable predicates, non-blocking sockets).

---

## 5. Audit Deliverable Structure

Compile findings into the audit report:
- Executive Summary & Distributed Runtime Risk Matrix.
- Subsystem Risk Profiles (Ring IPC, Server Queue, Sampling, Vulkan Backend, RPC).
- Verified Finding Catalog with Line Citations, Dataflow Traces, and Remediation.
- Attestation confirming zero modifications to tracked repository files.
