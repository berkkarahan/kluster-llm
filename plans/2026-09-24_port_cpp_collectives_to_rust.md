# Feature: Port C++ Balanced Collectives to Rust

**Created:** 2026-09-24  
**Status:** PHASE 1-3 COMPLETE (header, plan_reduce, balanced collectives)  
**Goal:** Eliminate C++ dependency for MPI activation collectives

---

## Progress

### Phase 1: Header and Shard Types ✅ COMPLETE
- Created `crates/kllm-mpi-provider/src/header.rs`
- Ported `Tensor` struct with packing/unpacking (96-byte header)
- Ported `ShardView`, `ReducePlan`
- Ported constants: `K_HEADER_BYTES`, `K_VERSION`, flag bits, dtype constants
- Ported `payload_chunks()` algorithm
- **Tests:** 17 tests pass (tensor roundtrip, header validation, chunking, view_of, fits_int)

### Phase 2: plan_reduce Logic ✅ COMPLETE
- Ported `view_of()` - extract ShardView from Tensor
- Ported `largest_remainder()` - allocate layers by capacity (with normalization)
- Ported `plan_reduce()` - decide allreduce vs allgatherv
- **Tests:** 17 tests pass (row_sum, column_concat, mismatched shapes, invalid kind, weights validation)

### Phase 3: Balanced Collectives ✅ COMPLETE
- Implemented `balanced_bcast()` - broadcast with header validation
- Implemented `balanced_allreduce()` - allreduce/allgatherv with header negotiation
- Added error propagation (MPI_Allreduce before payload)
- Added chunked transfers for large payloads
- **Tests:** 44 Rust tests pass, 70 Python tests pass

### Phase 4: Integration and Cleanup ⏳ TODO
- Update `cpp/kllm_mpi/CMakeLists.txt` - remove `KLLM_WITH_MPI` option
- Update `cpp/kllm_mpi/kllm_mpi.cpp` - deprecate or remove balanced ops
- Update `cpp/kllm_mpi/kllm_mpi.hpp` - deprecate or remove balanced ops
- Run full test suite
- Update validation ledger

---

## Background

`cpp/kllm_mpi/` implements custom collective algorithms (`mpi_balanced_bcast`, `mpi_balanced_allreduce`) that handle unequal tensor shards across ranks. The Rust MPI provider (`crates/kllm-mpi-provider/`) has 10,601 lines of tested MPI code but lacks these custom algorithms.

**Why port:**
- C++ code must compile on Windows/macOS/Linux with different MPI implementations
- Rust binary works everywhere (already builds on all three OSes)
- 1,376 lines of C++ → 0 lines of C++ (eliminate build dependency)
- Cross-platform consistency

---

## What to Port

### Core Algorithms (C++ → Rust)

| C++ Symbol | Lines | Description |
|---|---|---|
| `mpi_balanced_bcast()` | 150 | Broadcast with header validation, unequal shards |
| `mpi_balanced_allreduce()` | 200 | Allreduce/allgatherv with header negotiation, plan_reduce logic |
| `plan_reduce()` | 100 | Decide allreduce vs allgatherv based on shard shapes |
| `ShardView` | 10 | Shard metadata (type, flags, layer, nbytes) |
| `ReducePlan` | 20 | Reduction plan (counts, displs, out_ne) |
| `payload_chunks()` | 15 | Chunk large payloads into MPI-safe sizes |
| `largest_remainder()` | 20 | Allocate layers to ranks by capacity |
| `view_of()` | 10 | Extract ShardView from Tensor |
| `Tensor` packing/unpacking | 50 | Serialize/deserialize activation tensors |
| Header types/constants | 30 | `kHeaderBytes`, `kVersion`, flag bits |

**Total:** ~600 lines of C++ → ~600 lines of Rust

### Supporting Infrastructure

| File | Action |
|---|---|
| `crates/kllm-mpi-provider/src/collective.rs` | Extend with balanced operations |
| `crates/kllm-mpi-provider/src/header.rs` | New file for header types and packing |
| `crates/kllm-mpi-provider/src/lib.rs` | Export new modules |
| `cpp/kllm_mpi/CMakeLists.txt` | Remove `KLLM_WITH_MPI` option |
| `cpp/kllm_mpi/kllm_mpi.cpp` | Deprecate or remove balanced ops |
| `cpp/kllm_mpi/kllm_mpi.hpp` | Deprecate or remove balanced ops |
| `tests/test_mpi_runtime.py` | Update if needed |
| `tests/test_mpi_policy.py` | Update if needed |

---

## Implementation Plan

### Phase 1: Header and Shard Types (1 hour)

1. Create `crates/kllm-mpi-provider/src/header.rs`
2. Port `Tensor` struct with packing/unpacking
3. Port `ShardView`, `ReducePlan`
4. Port constants: `kHeaderBytes`, `kVersion`, flag bits, dtype constants
5. Port `payload_chunks()` algorithm

**Tests:**
- `test_header_packing` - round-trip Tensor serialization
- `test_shard_view_extract` - ShardView from Tensor
- `test_payload_chunks` - chunking logic

### Phase 2: plan_reduce Logic (2 hours)

1. Port `view_of()` - extract ShardView from Tensor
2. Port `largest_remainder()` - allocate layers by capacity
3. Port `plan_reduce()` - decide allreduce vs allgatherv
4. Port `Agree` struct (kind, nweights, hash)

**Tests:**
- `test_plan_reduce_row_sum` - equal f32 partials → allreduce
- `test_plan_reduce_column_concat` - unequal columns → allgatherv
- `test_largest_remainder` - allocation by capacity

### Phase 3: Balanced Collectives (3 hours)

1. Implement `balanced_bcast()` - broadcast with header validation
2. Implement `balanced_allreduce()` - allreduce/allgatherv with header negotiation
3. Add error propagation (MPI_Allreduce before payload)
4. Add chunked transfers for large payloads

**Tests:**
- `test_balanced_bcast_equal` - equal shards
- `test_balanced_bcast_unequal` - unequal shards
- `test_balanced_allreduce_row_sum` - allreduce path
- `test_balanced_allreduce_column_concat` - allgatherv path
- `test_balanced_bcast_header_rejection` - invalid header fails closed

### Phase 4: Integration and Cleanup (2 hours)

1. Update `crates/kllm-mpi-provider/src/lib.rs` - export new modules
2. Update `cpp/kllm_mpi/CMakeLists.txt` - remove `KLLM_WITH_MPI` option
3. Update `cpp/kllm_mpi/kllm_mpi.cpp` - deprecate or remove balanced ops
4. Update `cpp/kllm_mpi/kllm_mpi.hpp` - deprecate or remove balanced ops
5. Run full test suite
6. Update validation ledger

**Tests:**
- `cargo test` - all Rust tests pass
- `ctest` - C++ tests still pass (loopback, ABI compile)
- `uv run python -m pytest tests/test_mpi_*.py` - all Python tests pass

---

## Acceptance Criteria

1. **All C++ balanced operations ported to Rust**
   - `balanced_bcast()` and `balanced_allreduce()` implemented
   - Header negotiation and validation in place
   - Unequal shard handling works

2. **C++ no longer required for MPI collectives**
   - `KLLM_WITH_MPI` CMake option removed or deprecated
   - C++ smoke test can use Rust provider (or be removed)
   - Build succeeds without `cpp/kllm_mpi/`

3. **All tests pass**
   - Rust tests: 600+ lines of new code, 10+ new tests
   - C++ tests: loopback and ABI compile still pass
   - Python tests: 70+ MPI tests still pass

4. **Cross-platform validation**
   - Build on Windows (MSVC)
   - Build on macOS (Apple Clang)
   - Build on Linux (GCC/Clang)
   - All three produce identical Rust binary (modulo platform linking)

---

## Risks

| Risk | Mitigation |
|---|---|
| C++ `plan_reduce` logic is complex | Port verbatim, add unit tests for each branch |
| Header format mismatch | Define schema once, use in both C++ and Rust |
| Chunked transfers subtle | Port `payload_chunks()` exactly, add edge case tests |
| C ABI breakage | Update `cabi.rs` if engine needs new symbols |

---

## References

- C++ source: `cpp/kllm_mpi/kllm_mpi.cpp` (944 lines)
- C++ header: `cpp/kllm_mpi/kllm_mpi.hpp` (136 lines)
- Rust collective: `crates/kllm-mpi-provider/src/collective.rs` (1,599 lines)
- Rust C ABI: `crates/kllm-mpi-provider/src/cabi.rs` (569 lines)
- Validation ledger: `docs/mpi-runtime-validation.md`

---

**Last updated:** 2026-09-24  
**Next review:** After Phase 1 completion
