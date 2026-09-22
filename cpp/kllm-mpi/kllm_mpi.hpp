#pragma once

// Activation collectives for kluster-llm.
//
// Weights never enter these buffers. Callers pass token ids or f32 activations.
// MPI_Bcast and MPI_Allreduce need the same count on every rank, so unequal
// column shards are padded into an envelope (bcast) or expanded into a dense
// buffer with zeros outside the owned span (allreduce). The MPI count is
// homogeneous; the non-zero span follows the capacity split.
//
// Wire header is 64 bytes, little-endian, matching python/kllm/mpi_layer.py.
// A mixed macOS/Linux/Windows cluster does not share one MPI library. The
// loops below are the counts either MPI or the private-LAN TCP star must use.
// Do not bind them on a public address.

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace kllm {

inline constexpr std::uint32_t kMaxActivationBytes = 512u * 1024u * 1024u;
inline constexpr std::uint16_t kHeaderVersion = 1;

enum class CollectiveOp : std::uint8_t {
    Bcast = 1,
    AllreduceSum = 2,
    Allgather = 3,
    Handoff = 4,
    Placement = 5,
};

enum class GgmlType : std::uint32_t {
    F32 = 0,
    I8 = 24,
    I32 = 26,
};

#pragma pack(push, 1)
struct Header {
    char magic[4];
    std::uint16_t version;
    std::uint8_t op;
    std::uint8_t flags;
    std::uint32_t ggml_type;
    std::uint8_t n_dims;
    std::uint8_t split_axis;
    std::uint16_t rank;
    std::uint16_t n_ranks;
    std::uint16_t root;
    std::uint32_t ne[4];
    std::uint32_t shard_offset;
    std::uint32_t shard_length;
    std::uint32_t nbytes;
    std::uint32_t mpi_count;
    std::uint32_t chunk_index;
    std::uint32_t chunk_count;
    std::uint32_t reserved;
};
#pragma pack(pop)

static_assert(sizeof(Header) == 64, "activation header must stay 64 bytes");

struct MpiCall {
    std::string primitive;
    std::uint32_t count_bytes = 0;
    int root = -1;
    std::string note;
};

// One padded slot per rank: u32 logical length, payload, zero fill to max length.
// MPI_Bcast count is the returned buffer size on every rank.
std::vector<std::uint8_t> build_bcast_envelope(const std::vector<std::vector<std::uint8_t>>& shards);

std::vector<std::uint8_t> take_bcast_slot(const std::vector<std::uint8_t>& buffer, int rank, int n_ranks);

// Copy `values` into a dense full-width buffer at `offset`. Foreign elements stay 0.
std::vector<float> expand_shard(int offset, const std::vector<float>& values, int full_elems);

// MPI_Allreduce(MPI_SUM) on equal-length buffers. Unequal lengths throw.
std::vector<float> mpi_allreduce_sum(const std::vector<std::vector<float>>& buffers, MpiCall* call);

// Same sum, issued in equal `chunk_elems` rounds. The tail chunk is padded.
std::vector<float> allreduce_chunked(
    const std::vector<std::vector<float>>& buffers,
    int chunk_elems,
    std::vector<MpiCall>* calls);

// Disjoint spans are concatenated by SUM of expanded buffers.
// Equal full-width spans are partials and are summed.
std::vector<float> weighted_allreduce(
    const std::vector<std::pair<int, std::vector<float>>>& spans,
    int full_elems,
    MpiCall* call);

struct PackArgs {
    CollectiveOp op = CollectiveOp::Handoff;
    GgmlType type = GgmlType::F32;
    std::uint32_t ne[4] = {0, 1, 1, 1};
    std::uint16_t rank = 0;
    std::uint16_t n_ranks = 1;
    std::uint16_t root = 0;
    std::uint32_t shard_offset = 0;
    std::uint32_t shard_length = 0;
    std::uint8_t split_axis = 0xff;
    std::uint32_t chunk_index = 0;
    std::uint32_t chunk_count = 1;
    std::uint32_t mpi_count = 0;  // 0 means "use logical nbytes"
};

std::vector<std::uint8_t> pack_message(const PackArgs& args, const std::uint8_t* data, std::uint32_t nbytes);

}  // namespace kllm
