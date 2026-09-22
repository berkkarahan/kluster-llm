// MPI activation collectives for kluster-llm.
//
// Production inference remains llama.cpp RPC with split-mode layer. This
// library moves tokens, activations, and logits. It does not move weights.
//
// Graph split for the reference 3-node cluster (32 layers, 40 GiB weights),
// computed by python/kllm/mpi_comm.py::split_graph(example_mixed_cluster()):
//
//   rank 0  macOS   Metal   layers [ 0, 15)   25.6 GiB unified   speed 1.00
//           memory: [ weights resident | KV resident | activation slot ]
//   rank 1  Ubuntu  ROCm    layers [15, 25)   12.8 GiB VRAM      speed 0.85
//           memory: [ weights resident | KV resident | activation slot ]
//   rank 2  Windows CUDA    layers [25, 32)    9.6 GiB VRAM      speed 1.40
//           memory: [ weights resident | KV resident | activation slot ]
//   -------- private fabric (MPI has no auth; do not publish the job) --------
//   step 0  MPI_Bcast header 96B + token ids
//   step 1  MPI_Bcast header 96B + activation from rank 0 (after layer 14)
//   step 2  MPI_Bcast header 96B + activation from rank 1 (after layer 24)
//   step 3  MPI_Bcast header 96B + activation from rank 2 (after layer 31)
//   step 4  MPI_Bcast logits from rank 2
//   weights on wire: 0
//   no MPI_Allreduce: backends differ, and the Windows slice is not 1/3
//
// The Windows GPU is the fastest and still holds the fewest layers. Its 12 GiB
// cap is 7 layers; the overflow goes back through largest-remainder instead of
// being padded into a homogeneous allreduce.

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace kllm_mpi {

constexpr std::size_t kHeaderBytes = 96;
constexpr std::uint64_t kMpiChunkBytes = 1ull << 30;
constexpr std::uint16_t kVersion = 1;

constexpr std::uint32_t kF32 = 0;
constexpr std::uint32_t kF16 = 1;
constexpr std::uint32_t kI32 = 26;
constexpr std::uint32_t kBf16 = 30;

constexpr std::uint16_t kActivation = 0x1;
constexpr std::uint16_t kTokenIds = 0x2;
constexpr std::uint16_t kLogits = 0x4;
constexpr std::uint16_t kPartial = 0x8;

constexpr std::uint32_t kCollectiveBcast = 1;
constexpr std::uint32_t kCollectiveAllreduce = 2;
constexpr std::uint32_t kCollectiveAllgatherv = 3;

struct Tensor {
    std::uint32_t ggml_type = kF32;
    std::uint16_t flags = kActivation;
    std::int32_t src_rank = 0;
    std::int32_t layer = -1;
    std::uint32_t collective = kCollectiveBcast;
    std::int64_t ne[4] = {1, 1, 1, 1};
    std::uint64_t nb[4] = {0, 0, 0, 0};
    std::vector<std::uint8_t> data;

    void set_contiguous();
    std::vector<std::uint8_t> pack() const;
    static Tensor unpack(const std::uint8_t* bytes, std::size_t n);
    static Tensor f32(const std::vector<float>& values, const std::int64_t ne[4],
                      std::int32_t src_rank, std::int32_t layer, bool partial);
    std::vector<float> f32_values() const;
};

struct TraceEvent {
    std::string op;
    std::string what;
    std::size_t bytes = 0;
    int root = -1;
    bool dynamic = false;
    std::vector<std::uint64_t> counts;
    std::vector<std::uint64_t> displs;
    std::uint64_t count = 0;
};

struct ShardView {
    std::uint32_t ggml_type = kF32;
    std::uint16_t flags = kActivation;
    std::int32_t layer = -1;
    std::int64_t ne[4] = {1, 1, 1, 1};
    std::uint64_t nbytes = 0;
};

struct ReducePlan {
    bool row_sum = false;
    std::vector<std::uint64_t> counts;
    std::vector<std::uint64_t> displs;
    std::int64_t out_ne[4] = {1, 1, 1, 1};
    std::int32_t layer = -1;
};

std::vector<std::size_t> payload_chunks(std::uint64_t nbytes);

std::vector<int> largest_remainder(const std::vector<double>& weights, int total,
                                   const std::vector<int>* caps = nullptr);

ShardView view_of(const Tensor& tensor);

// Pure decision: equal f32 partials -> allreduce, unequal columns -> allgatherv.
// Throws if row_sum would need padding, or if widths disagree with `weights`.
ReducePlan plan_reduce(const std::vector<ShardView>& shards, const std::string& kind,
                       const std::vector<double>* weights);

// Loopback stand-in for a communicator. One slot per rank.
void balanced_bcast(std::vector<Tensor>& slots, int root, std::vector<TraceEvent>* trace);

void balanced_allreduce(std::vector<Tensor>& slots, const std::string& kind,
                        const std::vector<double>* weights, std::vector<TraceEvent>* trace);

#if defined(KLLM_HAVE_MPI)
// Per-rank loops. Every rank in MPI_COMM_WORLD must call these.
// A root that fails validation allreduces an error flag before the payload
// bcast, so the other ranks do not block forever.
void mpi_balanced_bcast(Tensor& tensor, int root);
void mpi_balanced_allreduce(Tensor& local, const std::string& kind,
                            const std::vector<double>* weights);
#endif

}  // namespace kllm_mpi
