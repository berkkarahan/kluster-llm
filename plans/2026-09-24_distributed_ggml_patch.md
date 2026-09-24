# Feature: Distributed GGML Patch for llama.cpp

**Created:** 2026-09-24  
**Status:** IN PROGRESS  
**Goal:** Enable tensor-parallel inference across multiple GPUs/nodes via MPI

---

## Background

`llama.cpp b11026` runs inference on **one GPU**. The distributed GGML patch splits the model across ranks, enabling multi-GPU/multi-node inference via MPI collectives.

**Why:** A single GPU cannot hold large models. Splitting across 3 GPUs (25.6 GiB + 12.8 GiB + 9.6 GiB = 48 GiB) enables 70B+ parameter models.

**Prerequisites met:**
- Rust MPI provider has collectives (broadcast, allreduce, allgatherv) ✅
- C ABI bridge (cabi.rs) exists and tested ✅
- C++ `kllm_mpi` library ported to Rust ✅

**Blockers:**
- No distributed GGML allocation/execution patch
- No GPU warmup, logits, streaming HTTP, or cancellation evidence
- MPI Plan reports "graph engine blocker"
- MPI Run returns 503 without entering RPC

---

## Architecture

### Model Split (32 layers, 40 GiB weights)

```
Rank 0 (macOS Metal):   layers [0, 15)    25.6 GiB unified
Rank 1 (Ubuntu ROCm):   layers [15, 25)   12.8 GiB VRAM
Rank 2 (Windows CUDA):  layers [25, 32)    9.6 GiB VRAM
```

**Unequal shards:** Rank 0 has 16 layers, rank 1 has 8, rank 2 has 12. This requires custom collective algorithms (already ported to Rust).

### Inference Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ Step 0: Distributed Warmup                                      │
│   - All ranks sync shapes/strides via MPI_Allgatherv            │
│   - Validate shard boundaries match                             │
│   - Allocate rank-local tensors                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Step 1: Token Embedding (rank-local)                            │
│   - Embed input tokens on each rank (same embedding layer)      │
│   - No collective needed (all ranks have full embedding)        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Step 2: Layer-by-Layer Inference (rank-local + collectives)     │
│   - Rank-local: self-attention, FFN on this rank's layers       │
│   - Collective: MPI_Bcast activation to all ranks after layer   │
│   - Collective: MPI_Allreduce logits if row-parallel            │
│   - Collective: MPI_Allgatherv logits if column-parallel        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Step 3: Logit Aggregation (last rank)                           │
│   - Last rank (rank 2) has final layer                          │
│   - Gather logits from all ranks if needed                      │
│   - Apply softmax, sample next token                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Step 4: Streaming HTTP Response                                 │
│   - Stream tokens to client via HTTP                            │
│   - Support cancellation mid-generation                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1: GGUF Shard Loading (2 hours)

1. **Modify `llama.cpp` GGUF loader** to accept rank-local shards
2. **Add shard manifest parser** (JSON: layer ranges, byte offsets)
3. **Validate shard boundaries** (no overlap, no gaps)
4. **Allocate rank-local tensors** (CPU → GPU transfer)

**Files:**
- `llama.cpp/src/llama.cpp` (GGUF loader)
- `llama.cpp/src/llama-model.h` (shard manifest)
- `crates/kllm-mpi-provider/src/production.rs` (shard spec)

**Tests:**
- `test_gguf_shard_loading` - load rank-local shard from manifest
- `test_shard_manifest_validation` - reject overlapping/gappy shards
- `test_rank_local_tensor_allocation` - allocate CPU/GPU tensors

### Phase 2: Graph Split (3 hours)

1. **Modify graph execution** to insert collectives at layer boundaries
2. **Add collective descriptors** (broadcast, allreduce, allgatherv)
3. **Handle unequal shards** (custom collective algorithms)
4. **Chunk large payloads** (1 GiB chunks for MPI)

**Files:**
- `llama.cpp/src/llama.cpp` (graph execution)
- `crates/kllm-mpi-provider/src/balanced.rs` (collectives)
- `crates/kllm-mpi-provider/src/cabi.rs` (C ABI)

**Tests:**
- `test_graph_split_equal_shards` - all ranks have same layers
- `test_graph_split_unequal_shards` - rank 0 has 16, rank 1 has 8, rank 2 has 12
- `test_chunked_payload_transfer` - 1 GiB chunks work correctly

### Phase 3: Distributed Warmup (2 hours)

1. **Add warmup collective** (sync shapes/strides across ranks)
2. **Validate rank-local tensor shapes** match expected
3. **Synchronize rank-local KV caches**
4. **Initialize rank-local attention masks**

**Files:**
- `llama.cpp/src/llama.cpp` (warmup fn)
- `crates/kllm-mpi-provider/src/production.rs` (warmup spec)

**Tests:**
- `test_warmup_shape_sync` - all ranks agree on shapes
- `test_warmup_kv_cache_init` - KV caches initialized correctly
- `test_warmup_attention_mask` - attention masks synced

### Phase 4: Logit Aggregation (2 hours)

1. **Gather logits from all ranks** (if needed)
2. **Apply softmax globally** (last rank)
3. **Sample next token** (rank-local or global)
4. **Stream tokens to client** (HTTP)

**Files:**
- `llama.cpp/src/llama.cpp` (logit fn)
- `crates/kllm-mpi-provider/src/cabi.rs` (logit collective)

**Tests:**
- `test_logit_gather_equal` - all ranks have same logits
- `test_logit_gather_unequal` - aggregate partial logits
- `test_softmax_sampling` - sample next token correctly

### Phase 5: Streaming HTTP + Cancellation (2 hours)

1. **Add streaming HTTP endpoint** (SSE or chunked)
2. **Support cancellation mid-generation**
3. **Graceful shutdown on rank loss**
4. **Bounded timeout for collectives**

**Files:**
- `crates/kllm-mpi-provider/src/production.rs` (HTTP server)
- `llama.cpp/src/llama.cpp` (cancellation hook)

**Tests:**
- `test_streaming_http` - stream tokens to client
- `test_cancellation` - cancel mid-generation
- `test_rank_loss_recovery` - recover from rank failure

---

## Acceptance Criteria

1. **GGUF shard loading works**
   - Load rank-local shard from manifest
   - Validate shard boundaries (no overlap, no gaps)
   - Allocate rank-local tensors (CPU → GPU)

2. **Graph execution with collectives works**
   - Insert collectives at layer boundaries
   - Handle unequal shards (rank 0: 16, rank 1: 8, rank 2: 12)
   - Chunk large payloads (1 GiB chunks)

3. **Distributed warmup works**
   - Sync shapes/strides across ranks
   - Validate rank-local tensor shapes
   - Initialize KV caches and attention masks

4. **Logit aggregation works**
   - Gather logits from all ranks (if needed)
   - Apply softmax globally (last rank)
   - Sample next token correctly

5. **Streaming HTTP + cancellation works**
   - Stream tokens to client (SSE or chunked)
   - Cancel mid-generation gracefully
   - Recover from rank failure

6. **All tests pass**
   - Rust tests: 50+ new tests
   - Python tests: 100+ new tests
   - C++ tests: 20+ new tests

---

## Risks

| Risk | Mitigation |
|------|------------|
| GGUF quantization-aware splitting is complex | Start with f32/f16, add quantization later |
| Unequal shards require custom collectives | Already ported to Rust (phases 1-3) |
| Chunked transfers have edge cases | Test with 1 GiB, 1.5 GiB, 2 GiB payloads |
| Rank loss during inference is rare | Implement bounded timeout + graceful shutdown |
| Streaming HTTP adds latency | Use SSE or chunked encoding, not full response |

---

## References

- llama.cpp source: `llama.cpp/src/llama.cpp` (b11026)
- Rust MPI provider: `crates/kllm-mpi-provider/src/balanced.rs`
- C ABI: `crates/kllm-mpi-provider/src/cabi.rs`
- Validation ledger: `docs/mpi-runtime-validation.md`
- Blocked items plan: `plans/2026-09-24_mpi-blocked-items.md`

---

**Last updated:** 2026-09-24  
**Next review:** After Phase 1 completion
