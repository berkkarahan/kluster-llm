#ifndef KLLM_MPI_PROVIDER_ABI_H
#define KLLM_MPI_PROVIDER_ABI_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Synchronous host-memory ABI for one authenticated world per process.
 * All pointers remain caller-owned and need only stay valid for the call.
 * Activation pointers must refer to CPU-accessible staging memory; synchronize
 * the selected GGML backend before calling and upload results afterward.
 *
 * `phase` is 1..256 UTF-8 bytes without control characters. `shape` contains
 * at most 16 dimensions. Operation byte limits and socket timeouts come from
 * the authenticated JSON job spec passed to kllm_mpi_init.
 *
 * Every collective call advances the world sequence when its network
 * operation completes. Any non-OK status is fatal to that fixed world: call
 * kllm_mpi_abort and do not retry the collective. In particular,
 * KLLM_MPI_BUFFER_TOO_SMALL reports required lengths after consuming the
 * collective; callers must preallocate from the accepted shard manifest.
 *
 * `error` is optional. When non-null with nonzero capacity it receives a
 * truncated, NUL-terminated UTF-8 message. Errors never unwind across the ABI.
 */
enum {
    KLLM_MPI_OK = 0,
    KLLM_MPI_ERROR = 1,
    KLLM_MPI_BUFFER_TOO_SMALL = 2,
};

/* `spec` is a complete bounded MpiJobSpec JSON record. It is parsed and copied
 * during the call; the provider retains and zeroizes only its owned copy. */
int32_t kllm_mpi_init(const uint8_t * spec, size_t spec_len,
                      uint8_t * error, size_t error_cap);
int32_t kllm_mpi_identity(uint32_t * rank, uint32_t * world_size,
                          uint8_t * error, size_t error_cap);
int32_t kllm_mpi_health(uint64_t * sequence, uint8_t * error, size_t error_cap);

/* Barrier agrees phase, layer, manifest identity, and sequence before arrival. */
int32_t kllm_mpi_barrier(const uint8_t * phase, size_t phase_len, int32_t layer,
                         uint8_t * error, size_t error_cap);
int32_t kllm_mpi_allreduce_f32(float * values, size_t values_len,
                               const uint8_t * phase, size_t phase_len,
                               int32_t layer, const uint64_t * shape,
                               size_t shape_len, uint8_t * error,
                               size_t error_cap);

/* On the root, *length_in_out is the input byte length. On other ranks it is
 * output-only. All ranks receive the final or required byte length. */
int32_t kllm_mpi_broadcast_bytes(uint32_t root, uint8_t * buffer,
                                 size_t buffer_cap, size_t * length_in_out,
                                 const uint8_t * phase, size_t phase_len,
                                 int32_t layer, const uint64_t * shape,
                                 size_t shape_len, uint8_t * error,
                                 size_t error_cap);

/* Concatenates rank contributions in rank order. output_len and counts_len are
 * always populated after a completed collective, including BUFFER_TOO_SMALL. */
int32_t kllm_mpi_allgatherv_bytes(
    const uint8_t * local, size_t local_len, uint8_t * output,
    size_t output_cap, size_t * output_len, uint64_t * counts,
    size_t counts_cap, size_t * counts_len, const uint8_t * phase,
    size_t phase_len, int32_t layer, const uint64_t * shape,
    size_t shape_len, uint8_t * error, size_t error_cap);
int32_t kllm_mpi_finalize(uint8_t * error, size_t error_cap);

/* Abort has local socket/credential semantics. The KLLM process supervisor
 * owns cross-process termination and cleanup for the complete job. */
int32_t kllm_mpi_abort(uint8_t * error, size_t error_cap);

#ifdef __cplusplus
}
#endif

#endif
