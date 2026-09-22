#include "kllm_mpi.hpp"

#include <cstring>

namespace kllm {
namespace {

void require(bool ok, const char* message) {
    if (!ok) {
        throw std::runtime_error(message);
    }
}

std::uint32_t type_size(GgmlType type) {
    switch (type) {
        case GgmlType::F32:
        case GgmlType::I32:
            return 4;
        case GgmlType::I8:
            return 1;
    }
    throw std::runtime_error("wire format accepts F32 activations, I32 tokens, and I8 metadata only");
}

std::uint64_t element_count(const std::uint32_t ne[4]) {
    std::uint64_t count = 1;
    for (int i = 0; i < 4; ++i) {
        count *= ne[i];
    }
    return count;
}

std::uint8_t n_dims(const std::uint32_t ne[4]) {
    std::uint8_t dims = 1;
    for (int i = 1; i < 4; ++i) {
        if (ne[i] > 1) {
            dims = static_cast<std::uint8_t>(i + 1);
        }
    }
    return dims;
}

}  // namespace

std::vector<std::uint8_t> build_bcast_envelope(const std::vector<std::vector<std::uint8_t>>& shards) {
    require(!shards.empty(), "bcast envelope needs one slot per rank");
    std::size_t max_len = 0;
    for (const auto& shard : shards) {
        if (shard.size() > max_len) {
            max_len = shard.size();
        }
    }
    require(4 + max_len <= kMaxActivationBytes, "bcast slot exceeds activation cap");
    const std::size_t stride = 4 + max_len;
    const std::size_t total = shards.size() * stride;
    require(total <= kMaxActivationBytes, "bcast envelope exceeds activation cap");
    std::vector<std::uint8_t> buffer(total, 0);
    for (std::size_t index = 0; index < shards.size(); ++index) {
        const auto& shard = shards[index];
        const std::size_t base = index * stride;
        const auto logical = static_cast<std::uint32_t>(shard.size());
        std::memcpy(buffer.data() + base, &logical, sizeof(logical));
        if (!shard.empty()) {
            std::memcpy(buffer.data() + base + 4, shard.data(), shard.size());
        }
    }
    return buffer;
}

std::vector<std::uint8_t> take_bcast_slot(const std::vector<std::uint8_t>& buffer, int rank, int n_ranks) {
    require(n_ranks > 0, "n_ranks must be positive");
    require(buffer.size() % static_cast<std::size_t>(n_ranks) == 0, "MPI_Bcast buffer is not divisible by n_ranks");
    const std::size_t stride = buffer.size() / static_cast<std::size_t>(n_ranks);
    require(stride >= 4, "bcast slot missing its length prefix");
    require(rank >= 0 && rank < n_ranks, "rank outside communicator");
    const std::size_t base = static_cast<std::size_t>(rank) * stride;
    std::uint32_t logical = 0;
    std::memcpy(&logical, buffer.data() + base, sizeof(logical));
    require(logical <= stride - 4, "logical shard longer than its padded slot");
    return std::vector<std::uint8_t>(buffer.begin() + static_cast<std::ptrdiff_t>(base + 4),
                                     buffer.begin() + static_cast<std::ptrdiff_t>(base + 4 + logical));
}

std::vector<float> expand_shard(int offset, const std::vector<float>& values, int full_elems) {
    require(offset >= 0 && full_elems >= 0, "negative allreduce shape");
    require(offset + static_cast<int>(values.size()) <= full_elems, "shard extends past the allreduce buffer");
    std::vector<float> dense(static_cast<std::size_t>(full_elems), 0.0f);
    for (std::size_t i = 0; i < values.size(); ++i) {
        dense[static_cast<std::size_t>(offset) + i] = values[i];
    }
    return dense;
}

std::vector<float> mpi_allreduce_sum(const std::vector<std::vector<float>>& buffers, MpiCall* call) {
    require(!buffers.empty(), "allreduce needs a contribution from every rank");
    const std::size_t width = buffers[0].size();
    for (const auto& buffer : buffers) {
        require(buffer.size() == width,
                "MPI_Allreduce count differs across ranks; expand weighted shards onto one width first");
    }
    std::vector<float> acc(width, 0.0f);
    for (const auto& buffer : buffers) {
        for (std::size_t i = 0; i < width; ++i) {
            acc[i] += buffer[i];
        }
    }
    if (call != nullptr) {
        call->primitive = "MPI_Allreduce";
        call->count_bytes = static_cast<std::uint32_t>(width * sizeof(float));
        call->root = -1;
        call->note = "MPI_SUM on f32; identical count; zeros hold non-owned spans";
    }
    return acc;
}

std::vector<float> allreduce_chunked(
    const std::vector<std::vector<float>>& buffers,
    int chunk_elems,
    std::vector<MpiCall>* calls) {
    require(chunk_elems > 0, "chunk_elems must be positive");
    require(!buffers.empty(), "allreduce needs a contribution from every rank");
    const std::size_t width = buffers[0].size();
    for (const auto& buffer : buffers) {
        require(buffer.size() == width, "chunked allreduce requires equal logical widths");
    }
    std::vector<float> acc(width, 0.0f);
    const int chunk_count = width == 0 ? 1 : static_cast<int>((width + static_cast<std::size_t>(chunk_elems) - 1) /
                                                             static_cast<std::size_t>(chunk_elems));
    for (int chunk_index = 0; chunk_index < chunk_count; ++chunk_index) {
        const std::size_t start = static_cast<std::size_t>(chunk_index * chunk_elems);
        const std::size_t end = std::min(width, start + static_cast<std::size_t>(chunk_elems));
        std::vector<std::vector<float>> parts;
        parts.reserve(buffers.size());
        for (const auto& buffer : buffers) {
            std::vector<float> part(static_cast<std::size_t>(chunk_elems), 0.0f);
            for (std::size_t i = start; i < end; ++i) {
                part[i - start] = buffer[i];
            }
            parts.push_back(std::move(part));
        }
        MpiCall call;
        const std::vector<float> reduced = mpi_allreduce_sum(parts, &call);
        for (std::size_t i = start; i < end; ++i) {
            acc[i] = reduced[i - start];
        }
        call.note = "chunked MPI_SUM";
        if (calls != nullptr) {
            calls->push_back(call);
        }
    }
    return acc;
}

std::vector<float> weighted_allreduce(
    const std::vector<std::pair<int, std::vector<float>>>& spans,
    int full_elems,
    MpiCall* call) {
    require(full_elems > 0, "full_elems must be positive");
    require(!spans.empty(), "weighted allreduce needs every rank's span");
    bool full_width = true;
    std::vector<int> covered(static_cast<std::size_t>(full_elems), 0);
    for (const auto& span : spans) {
        const int offset = span.first;
        const auto& values = span.second;
        if (offset != 0 || static_cast<int>(values.size()) != full_elems) {
            full_width = false;
        }
        require(offset >= 0 && offset + static_cast<int>(values.size()) <= full_elems,
                "span lies outside the allreduce buffer");
        for (std::size_t i = 0; i < values.size(); ++i) {
            covered[static_cast<std::size_t>(offset) + i] += 1;
        }
    }
    if (!full_width) {
        for (int count : covered) {
            require(count == 1, "column spans must be disjoint and cover the full buffer exactly once");
        }
    }
    std::vector<std::vector<float>> dense;
    dense.reserve(spans.size());
    for (const auto& span : spans) {
        dense.push_back(expand_shard(span.first, span.second, full_elems));
    }
    return mpi_allreduce_sum(dense, call);
}

std::vector<std::uint8_t> pack_message(const PackArgs& args, const std::uint8_t* data, std::uint32_t nbytes) {
    const std::uint32_t size = type_size(args.type);
    const std::uint64_t expect = element_count(args.ne) * size;
    require(expect == nbytes, "payload size does not match ggml ne");
    require(nbytes <= kMaxActivationBytes, "payload exceeds activation cap");
    const std::uint32_t mpi_count = args.mpi_count == 0 ? nbytes : args.mpi_count;
    require(mpi_count >= nbytes, "mpi_count shorter than logical payload");
    require(mpi_count <= kMaxActivationBytes, "mpi_count exceeds activation cap");

    Header header{};
    header.magic[0] = 'K';
    header.magic[1] = 'A';
    header.magic[2] = 'C';
    header.magic[3] = 'T';
    header.version = kHeaderVersion;
    header.op = static_cast<std::uint8_t>(args.op);
    header.flags = mpi_count == nbytes ? 0 : 1;
    header.ggml_type = static_cast<std::uint32_t>(args.type);
    header.n_dims = n_dims(args.ne);
    header.split_axis = args.split_axis;
    header.rank = args.rank;
    header.n_ranks = args.n_ranks;
    header.root = args.root;
    for (int i = 0; i < 4; ++i) {
        header.ne[i] = args.ne[i];
    }
    header.shard_offset = args.shard_offset;
    header.shard_length = args.shard_length;
    header.nbytes = nbytes;
    header.mpi_count = mpi_count;
    header.chunk_index = args.chunk_index;
    header.chunk_count = args.chunk_count;
    header.reserved = 0;

    std::vector<std::uint8_t> out(sizeof(Header) + nbytes);
    std::memcpy(out.data(), &header, sizeof(header));
    if (nbytes > 0 && data != nullptr) {
        std::memcpy(out.data() + sizeof(Header), data, nbytes);
    }
    return out;
}

}  // namespace kllm
