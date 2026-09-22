#include "kllm_mpi.hpp"

#include <algorithm>
#include <cstring>
#include <stdexcept>
#include <utility>

#if defined(KLLM_HAVE_MPI)
#include <mpi.h>
#endif

namespace kllm_mpi {
namespace {

[[noreturn]] void fail(const std::string& message) {
    throw std::runtime_error(message);
}

void put_u16(std::uint8_t* p, std::uint16_t v) {
    p[0] = static_cast<std::uint8_t>(v);
    p[1] = static_cast<std::uint8_t>(v >> 8);
}

void put_u32(std::uint8_t* p, std::uint32_t v) {
    for (int i = 0; i < 4; ++i) {
        p[i] = static_cast<std::uint8_t>(v >> (8 * i));
    }
}

void put_i32(std::uint8_t* p, std::int32_t v) { put_u32(p, static_cast<std::uint32_t>(v)); }

void put_u64(std::uint8_t* p, std::uint64_t v) {
    for (int i = 0; i < 8; ++i) {
        p[i] = static_cast<std::uint8_t>(v >> (8 * i));
    }
}

void put_i64(std::uint8_t* p, std::int64_t v) { put_u64(p, static_cast<std::uint64_t>(v)); }

std::uint16_t get_u16(const std::uint8_t* p) {
    return static_cast<std::uint16_t>(p[0] | (p[1] << 8));
}

std::uint32_t get_u32(const std::uint8_t* p) {
    std::uint32_t v = 0;
    for (int i = 0; i < 4; ++i) {
        v |= static_cast<std::uint32_t>(p[i]) << (8 * i);
    }
    return v;
}

std::int32_t get_i32(const std::uint8_t* p) { return static_cast<std::int32_t>(get_u32(p)); }

std::uint64_t get_u64(const std::uint8_t* p) {
    std::uint64_t v = 0;
    for (int i = 0; i < 8; ++i) {
        v |= static_cast<std::uint64_t>(p[i]) << (8 * i);
    }
    return v;
}

std::int64_t get_i64(const std::uint8_t* p) { return static_cast<std::int64_t>(get_u64(p)); }

std::size_t itemsize(std::uint32_t ggml_type) {
    switch (ggml_type) {
        case kF32:
        case kI32:
            return 4;
        case kF16:
        case kBf16:
            return 2;
        default:
            fail("ggml type is not a dense activation type; quantized weights are not broadcast");
    }
}

void check_flags(std::uint16_t flags, std::uint32_t ggml_type) {
    constexpr std::uint16_t kAllowed = kActivation | kTokenIds | kLogits | kPartial;
    if (flags & ~kAllowed) {
        fail("refusing payload that is not tokens, activations, or logits; weights stay on the node");
    }
    const std::uint16_t kind = flags & (kActivation | kTokenIds | kLogits);
    if (kind == 0 || (kind & (kind - 1)) != 0) {
        fail("payload needs exactly one of activation, token_ids, or logits");
    }
    if ((flags & kPartial) && !(flags & kActivation)) {
        fail("partial flag is only valid on activations");
    }
    // itemsize throws on quantized types.
    (void)itemsize(ggml_type);
    if (ggml_type == kI32 && !(flags & kTokenIds)) {
        fail("i32 tensors on this wire are token ids");
    }
    if (ggml_type != kI32 && (flags & kTokenIds)) {
        fail("token ids are ggml i32");
    }
}

std::uint64_t numel(const std::int64_t ne[4]) {
    std::uint64_t n = 1;
    for (int i = 0; i < 4; ++i) {
        if (ne[i] < 0) {
            fail("ne dimensions must be >= 0");
        }
        n *= static_cast<std::uint64_t>(ne[i]);
    }
    return n;
}

void expect_contiguous(const Tensor& tensor) {
    const std::size_t item = itemsize(tensor.ggml_type);
    std::uint64_t nb = item;
    for (int i = 0; i < 4; ++i) {
        if (tensor.nb[i] != nb) {
            fail("only contiguous activations are broadcast (nb must be packed)");
        }
        if (i < 3) {
            nb *= static_cast<std::uint64_t>(tensor.ne[i]);
        }
    }
}

struct ParsedHeader {
    Tensor meta;
    std::uint64_t nbytes = 0;
};

ParsedHeader parse_header(const std::uint8_t* bytes) {
    if (std::memcmp(bytes, "KMPI", 4) != 0 || get_u16(bytes + 4) != kVersion) {
        fail("not a KMPI activation header");
    }
    ParsedHeader parsed;
    parsed.meta.flags = get_u16(bytes + 6);
    parsed.meta.ggml_type = get_u32(bytes + 8);
    parsed.meta.src_rank = get_i32(bytes + 12);
    parsed.meta.layer = get_i32(bytes + 16);
    parsed.meta.collective = get_u32(bytes + 20);
    for (int i = 0; i < 4; ++i) {
        parsed.meta.ne[i] = get_i64(bytes + 24 + 8 * i);
        parsed.meta.nb[i] = get_u64(bytes + 56 + 8 * i);
    }
    parsed.nbytes = get_u64(bytes + 88);
    return parsed;
}

}  // namespace

std::vector<std::size_t> payload_chunks(std::uint64_t nbytes) {
    std::vector<std::size_t> out;
    if (nbytes == 0) {
        out.push_back(0);
        return out;
    }
    std::uint64_t left = nbytes;
    while (left) {
        const std::uint64_t take = std::min(kMpiChunkBytes, left);
        out.push_back(static_cast<std::size_t>(take));
        left -= take;
    }
    return out;
}

std::vector<int> largest_remainder(const std::vector<double>& weights, int total,
                                   const std::vector<int>* caps) {
    const int n = static_cast<int>(weights.size());
    if (n == 0) {
        fail("at least one rank is required");
    }
    if (total < 0) {
        fail("total must be >= 0");
    }
    if (caps && static_cast<int>(caps->size()) != n) {
        fail("caps length must match weights");
    }
    if (total == 0) {
        return std::vector<int>(n, 0);
    }
    if (caps) {
        int cap_sum = 0;
        for (int c : *caps) {
            if (c < 0) {
                fail("caps must be >= 0");
            }
            cap_sum += c;
        }
        if (cap_sum < total) {
            fail("memory caps cannot hold the assignment");
        }
    }
    std::vector<double> capped = weights;
    for (double w : capped) {
        if (w < 0) {
            fail("weights must be >= 0");
        }
    }
    if (caps) {
        for (int i = 0; i < n; ++i) {
            if ((*caps)[i] == 0) {
                capped[i] = 0;
            }
        }
    }
    double wsum = 0;
    for (double w : capped) {
        wsum += w;
    }
    if (wsum <= 0) {
        int room = 0;
        for (int i = 0; i < n; ++i) {
            if (!caps || (*caps)[i] > 0) {
                capped[i] = 1;
                room += 1;
            } else {
                capped[i] = 0;
            }
        }
        if (room == 0) {
            fail("no rank can take a share");
        }
        wsum = static_cast<double>(room);
    }
    std::vector<double> exact(n);
    std::vector<int> counts(n);
    std::vector<double> remainders(n);
    for (int i = 0; i < n; ++i) {
        exact[i] = total * (capped[i] / wsum);
        counts[i] = static_cast<int>(exact[i]);  // positive values truncate toward zero
        remainders[i] = exact[i] - static_cast<double>(counts[i]);
        if (caps && counts[i] > (*caps)[i]) {
            counts[i] = (*caps)[i];
        }
    }
    int leftover = total;
    for (int c : counts) {
        leftover -= c;
    }
    std::vector<int> order(n);
    for (int i = 0; i < n; ++i) {
        order[i] = i;
    }
    std::sort(order.begin(), order.end(), [&](int a, int b) {
        if (remainders[a] != remainders[b]) {
            return remainders[a] > remainders[b];
        }
        return a < b;
    });
    int cursor = 0;
    int steps = 0;
    const int limit = total + n + 1;
    while (leftover > 0) {
        if (++steps > limit) {
            fail("failed to place shares under memory caps");
        }
        const int i = order[cursor % n];
        cursor += 1;
        if (caps && counts[i] >= (*caps)[i]) {
            continue;
        }
        counts[i] += 1;
        leftover -= 1;
    }
    return counts;
}

void Tensor::set_contiguous() {
    const std::size_t item = itemsize(ggml_type);
    std::uint64_t stride = item;
    for (int i = 0; i < 4; ++i) {
        nb[i] = stride;
        if (i < 3) {
            stride *= static_cast<std::uint64_t>(ne[i]);
        }
    }
}

std::vector<std::uint8_t> Tensor::pack() const {
    check_flags(flags, ggml_type);
    expect_contiguous(*this);
    const std::uint64_t bytes = numel(ne) * itemsize(ggml_type);
    if (data.size() != bytes) {
        fail("payload length does not match contiguous ne");
    }
    std::vector<std::uint8_t> out(kHeaderBytes + data.size());
    std::uint8_t* p = out.data();
    p[0] = 'K';
    p[1] = 'M';
    p[2] = 'P';
    p[3] = 'I';
    put_u16(p + 4, kVersion);
    put_u16(p + 6, flags);
    put_u32(p + 8, ggml_type);
    put_i32(p + 12, src_rank);
    put_i32(p + 16, layer);
    put_u32(p + 20, collective);
    for (int i = 0; i < 4; ++i) {
        put_i64(p + 24 + 8 * i, ne[i]);
    }
    for (int i = 0; i < 4; ++i) {
        put_u64(p + 56 + 8 * i, nb[i]);
    }
    put_u64(p + 88, bytes);
    if (!data.empty()) {
        std::memcpy(out.data() + kHeaderBytes, data.data(), data.size());
    }
    return out;
}

Tensor Tensor::unpack(const std::uint8_t* bytes, std::size_t n) {
    if (n < kHeaderBytes) {
        fail("truncated GGML MPI header");
    }
    ParsedHeader parsed = parse_header(bytes);
    if (n - kHeaderBytes != parsed.nbytes) {
        fail("payload length does not match header nbytes");
    }
    parsed.meta.data.assign(bytes + kHeaderBytes, bytes + n);
    parsed.meta.pack();
    return parsed.meta;
}

Tensor Tensor::f32(const std::vector<float>& values, const std::int64_t ne_in[4],
                   std::int32_t src_rank_in, std::int32_t layer_in, bool partial) {
    Tensor tensor;
    tensor.ggml_type = kF32;
    tensor.flags = kActivation | (partial ? kPartial : 0);
    tensor.src_rank = src_rank_in;
    tensor.layer = layer_in;
    tensor.collective = kCollectiveBcast;
    for (int i = 0; i < 4; ++i) {
        tensor.ne[i] = ne_in[i];
    }
    tensor.set_contiguous();
    if (numel(tensor.ne) != values.size()) {
        fail("f32 values do not match ne");
    }
    tensor.data.resize(values.size() * sizeof(float));
    for (std::size_t i = 0; i < values.size(); ++i) {
        const float v = values[i];
        static_assert(sizeof(float) == 4, "f32 wire format");
        std::uint32_t bits = 0;
        std::memcpy(&bits, &v, 4);
        put_u32(tensor.data.data() + 4 * i, bits);
    }
    return tensor;
}

std::vector<float> Tensor::f32_values() const {
    if (ggml_type != kF32) {
        fail("expected f32 activation");
    }
    const std::uint64_t n = numel(ne);
    if (data.size() != n * 4) {
        fail("f32 payload size mismatch");
    }
    std::vector<float> out(n);
    for (std::uint64_t i = 0; i < n; ++i) {
        const std::uint32_t bits = get_u32(data.data() + 4 * i);
        float v = 0;
        std::memcpy(&v, &bits, 4);
        out[i] = v;
    }
    return out;
}

ShardView view_of(const Tensor& tensor) {
    ShardView view;
    view.ggml_type = tensor.ggml_type;
    view.flags = tensor.flags;
    view.layer = tensor.layer;
    for (int i = 0; i < 4; ++i) {
        view.ne[i] = tensor.ne[i];
    }
    view.nbytes = tensor.data.size();
    return view;
}

ReducePlan plan_reduce(const std::vector<ShardView>& shards, const std::string& kind,
                       const std::vector<double>* weights) {
    if (shards.empty()) {
        fail("allreduce needs a slot per rank");
    }
    if (kind != "row_sum" && kind != "column_concat") {
        fail("kind must be row_sum or column_concat");
    }
    ReducePlan plan;
    plan.row_sum = kind == "row_sum";
    plan.layer = shards[0].layer;
    std::uint64_t total_elems = 0;
    for (const ShardView& shard : shards) {
        check_flags(shard.flags, shard.ggml_type);
        if (shard.ggml_type != kF32) {
            fail("reductions are f32 activations");
        }
        const std::uint64_t elems = numel(shard.ne);
        if (elems * 4 != shard.nbytes) {
            fail("payload is not a whole number of ggml elements");
        }
        if (kind == "column_concat" && !(shard.flags & kPartial)) {
            fail("column shards must be marked partial activations");
        }
        if (shard.ne[1] != shards[0].ne[1] || shard.ne[2] != shards[0].ne[2] ||
            shard.ne[3] != shards[0].ne[3]) {
            fail("column shards must share ne[1:]");
        }
        plan.counts.push_back(shard.nbytes);
        total_elems += elems;
    }
    if (weights) {
        if (weights->size() != shards.size()) {
            fail("weights length must match the communicator");
        }
        if (total_elems > static_cast<std::uint64_t>(0x7fffffff)) {
            fail("shard is too wide for the weight check");
        }
        const std::vector<int> expected =
            largest_remainder(*weights, static_cast<int>(total_elems), nullptr);
        for (std::size_t i = 0; i < shards.size(); ++i) {
            if (static_cast<int>(numel(shards[i].ne)) != expected[i]) {
                fail("payload widths disagree with weights");
            }
        }
    }
    if (plan.row_sum) {
        for (const ShardView& shard : shards) {
            for (int d = 0; d < 4; ++d) {
                if (shard.ne[d] != shards[0].ne[d]) {
                    fail("row-parallel MPI_Allreduce needs equal shapes; "
                         "refusing to pad unequal slices (use column_concat for unequal widths)");
                }
            }
        }
    }
    std::uint64_t off = 0;
    for (std::uint64_t n : plan.counts) {
        plan.displs.push_back(off);
        off += n;
    }
    for (int d = 0; d < 4; ++d) {
        plan.out_ne[d] = shards[0].ne[d];
    }
    if (!plan.row_sum) {
        const std::int64_t tail = shards[0].ne[1] * shards[0].ne[2] * shards[0].ne[3];
        plan.out_ne[0] = tail <= 0 ? 0 : static_cast<std::int64_t>(total_elems / static_cast<std::uint64_t>(tail));
    }
    return plan;
}

void balanced_bcast(std::vector<Tensor>& slots, int root, std::vector<TraceEvent>* trace) {
    if (slots.empty() || root < 0 || root >= static_cast<int>(slots.size())) {
        fail("bcast root is outside the communicator");
    }
    // Step 1. Root packs the GGML header. The struct has type, ne, nb, nbytes.
    // No data pointer and no quantized weight blocks.
    const std::vector<std::uint8_t> packed = slots[root].pack();
    if (trace) {
        TraceEvent header;
        header.op = "MPI_Bcast";
        header.what = "header";
        header.bytes = kHeaderBytes;
        header.root = root;
        trace->push_back(header);
        // Step 2-4. Payload count comes from the header, then is chunked.
        for (std::size_t chunk : payload_chunks(packed.size() - kHeaderBytes)) {
            TraceEvent payload;
            payload.op = "MPI_Bcast";
            payload.what = "payload";
            payload.bytes = chunk;
            payload.root = root;
            payload.dynamic = true;
            trace->push_back(payload);
        }
    }
    const Tensor cloned = Tensor::unpack(packed.data(), packed.size());
    const std::vector<std::uint8_t> again = cloned.pack();
    for (Tensor& slot : slots) {
        slot = Tensor::unpack(again.data(), again.size());
    }
}

void balanced_allreduce(std::vector<Tensor>& slots, const std::string& kind,
                        const std::vector<double>* weights, std::vector<TraceEvent>* trace) {
    std::vector<ShardView> shards;
    shards.reserve(slots.size());
    for (const Tensor& slot : slots) {
        slot.pack();
        shards.push_back(view_of(slot));
    }
    const ReducePlan plan = plan_reduce(shards, kind, weights);
    if (trace) {
        TraceEvent gather;
        gather.op = "MPI_Allgather";
        gather.what = "counts";
        gather.bytes = 8 * slots.size();
        gather.counts = plan.counts;
        trace->push_back(gather);
    }
    if (plan.row_sum) {
        if (trace) {
            TraceEvent reduce;
            reduce.op = "MPI_Allreduce";
            reduce.what = "payload";
            reduce.count = numel(plan.out_ne);
            if (!plan.counts.empty()) {
                for (std::size_t chunk : payload_chunks(plan.counts[0])) {
                    reduce.counts.push_back(chunk);
                }
            }
            trace->push_back(reduce);
        }
        std::vector<float> acc = slots[0].f32_values();
        for (std::size_t r = 1; r < slots.size(); ++r) {
            const std::vector<float> part = slots[r].f32_values();
            for (std::size_t i = 0; i < acc.size(); ++i) {
                acc[i] += part[i];
            }
        }
        Tensor reduced = Tensor::f32(acc, plan.out_ne, 0, plan.layer, false);
        reduced.collective = kCollectiveAllreduce;
        const std::vector<std::uint8_t> packed = reduced.pack();
        for (Tensor& slot : slots) {
            slot = Tensor::unpack(packed.data(), packed.size());
        }
        return;
    }
    if (trace) {
        TraceEvent gather;
        gather.op = "MPI_Allgatherv";
        gather.what = "payload";
        gather.counts = plan.counts;
        gather.displs = plan.displs;
        trace->push_back(gather);
    }
    std::vector<std::uint8_t> merged(plan.displs.empty() ? 0 : plan.displs.back() + plan.counts.back());
    for (std::size_t r = 0; r < slots.size(); ++r) {
        if (plan.counts[r] == 0) {
            continue;
        }
        std::memcpy(merged.data() + plan.displs[r], slots[r].data.data(), plan.counts[r]);
    }
    Tensor gathered;
    gathered.ggml_type = kF32;
    gathered.flags = kActivation;
    gathered.src_rank = 0;
    gathered.layer = plan.layer;
    gathered.collective = kCollectiveAllgatherv;
    for (int d = 0; d < 4; ++d) {
        gathered.ne[d] = plan.out_ne[d];
    }
    gathered.set_contiguous();
    gathered.data = std::move(merged);
    const std::vector<std::uint8_t> packed = gathered.pack();
    for (Tensor& slot : slots) {
        slot = Tensor::unpack(packed.data(), packed.size());
    }
}

#if defined(KLLM_HAVE_MPI)

namespace {

int fits_int(std::uint64_t n) {
    if (n > static_cast<std::uint64_t>(0x7fffffff)) {
        fail("MPI count does not fit in int; chunk the collective");
    }
    return static_cast<int>(n);
}

void mpi_check(int rc, const char* what) {
    if (rc != MPI_SUCCESS) {
        fail(std::string(what) + " failed");
    }
}

int mpi_rank() {
    int rank = 0;
    mpi_check(MPI_Comm_rank(MPI_COMM_WORLD, &rank), "MPI_Comm_rank");
    return rank;
}

int mpi_size() {
    int size = 0;
    mpi_check(MPI_Comm_size(MPI_COMM_WORLD, &size), "MPI_Comm_size");
    return size;
}

int mpi_any_error(int local_error) {
    int any = 0;
    mpi_check(MPI_Allreduce(&local_error, &any, 1, MPI_INT, MPI_MAX, MPI_COMM_WORLD),
              "MPI_Allreduce");
    return any;
}

void mpi_bcast_bytes(std::uint8_t* buf, std::uint64_t nbytes, int root) {
    unsigned char dummy = 0;
    std::uint64_t off = 0;
    for (std::size_t chunk : payload_chunks(nbytes)) {
        void* ptr = chunk == 0 ? static_cast<void*>(&dummy) : static_cast<void*>(buf + off);
        mpi_check(MPI_Bcast(ptr, static_cast<int>(chunk), MPI_BYTE, root, MPI_COMM_WORLD), "MPI_Bcast");
        off += chunk;
    }
}

}  // namespace

void mpi_balanced_bcast(Tensor& tensor, int root) {
    const int rank = mpi_rank();
    std::vector<std::uint8_t> header(kHeaderBytes);
    int err = 0;
    if (rank == root) {
        try {
            const std::vector<std::uint8_t> packed = tensor.pack();
            std::memcpy(header.data(), packed.data(), kHeaderBytes);
        } catch (const std::runtime_error&) {
            err = 1;
        }
    }
    // One integer allreduce so a bad root does not leave the others blocked in Bcast.
    if (mpi_any_error(err)) {
        fail("root refused the activation header");
    }
    mpi_check(MPI_Bcast(header.data(), static_cast<int>(kHeaderBytes), MPI_BYTE, root, MPI_COMM_WORLD),
              "MPI_Bcast");
    ParsedHeader parsed = parse_header(header.data());
    std::vector<std::uint8_t> payload(static_cast<std::size_t>(parsed.nbytes));
    if (rank == root) {
        payload = tensor.data;
    }
    mpi_bcast_bytes(payload.empty() ? nullptr : payload.data(), parsed.nbytes, root);
    parsed.meta.data = std::move(payload);
    parsed.meta.pack();
    tensor = std::move(parsed.meta);
}

void mpi_balanced_allreduce(Tensor& local, const std::string& kind,
                            const std::vector<double>* weights) {
    const int size = mpi_size();
    std::vector<std::uint8_t> my_header(kHeaderBytes);
    int err = 0;
    try {
        const std::vector<std::uint8_t> packed = local.pack();
        std::memcpy(my_header.data(), packed.data(), kHeaderBytes);
    } catch (const std::runtime_error&) {
        err = 1;
    }
    if (mpi_any_error(err)) {
        fail("a rank refused its activation header");
    }
    // Headers first. nbytes and ne may differ; the payload collective is chosen from them.
    std::vector<std::uint8_t> all_headers(static_cast<std::size_t>(size) * kHeaderBytes);
    mpi_check(MPI_Allgather(my_header.data(), static_cast<int>(kHeaderBytes), MPI_BYTE, all_headers.data(),
                            static_cast<int>(kHeaderBytes), MPI_BYTE, MPI_COMM_WORLD),
              "MPI_Allgather");
    std::vector<ShardView> shards;
    shards.reserve(static_cast<std::size_t>(size));
    for (int i = 0; i < size; ++i) {
        const ParsedHeader parsed = parse_header(all_headers.data() + static_cast<std::size_t>(i) * kHeaderBytes);
        ShardView shard = view_of(parsed.meta);
        shard.nbytes = parsed.nbytes;
        shards.push_back(shard);
    }
    const ReducePlan plan = plan_reduce(shards, kind, weights);
    if (plan.row_sum) {
        std::vector<float> send = local.f32_values();
        std::vector<float> recv(send.size());
        std::uint64_t off = 0;
        for (std::size_t chunk : payload_chunks(static_cast<std::uint64_t>(send.size()) * 4)) {
            const int count = static_cast<int>(chunk / 4);
            float dummy_in = 0;
            float dummy_out = 0;
            float* in = count == 0 ? &dummy_in : send.data() + off / 4;
            float* out = count == 0 ? &dummy_out : recv.data() + off / 4;
            mpi_check(MPI_Allreduce(in, out, count, MPI_FLOAT, MPI_SUM, MPI_COMM_WORLD), "MPI_Allreduce");
            off += chunk;
        }
        local = Tensor::f32(recv, plan.out_ne, 0, plan.layer, false);
        local.collective = kCollectiveAllreduce;
        return;
    }
    std::vector<int> counts(size);
    std::vector<int> displs(size);
    std::uint64_t total = 0;
    for (int i = 0; i < size; ++i) {
        counts[i] = fits_int(plan.counts[static_cast<std::size_t>(i)]);
        displs[i] = fits_int(plan.displs[static_cast<std::size_t>(i)]);
        total += plan.counts[static_cast<std::size_t>(i)];
    }
    std::vector<std::uint8_t> recv(static_cast<std::size_t>(total));
    const int send_count = fits_int(local.data.size());
    unsigned char dummy = 0;
    const void* send_ptr = local.data.empty() ? static_cast<const void*>(&dummy) : local.data.data();
    mpi_check(MPI_Allgatherv(send_ptr, send_count, MPI_BYTE, recv.empty() ? nullptr : recv.data(), counts.data(),
                             displs.data(), MPI_BYTE, MPI_COMM_WORLD),
              "MPI_Allgatherv");
    Tensor gathered;
    gathered.ggml_type = kF32;
    gathered.flags = kActivation;
    gathered.layer = plan.layer;
    gathered.collective = kCollectiveAllgatherv;
    for (int d = 0; d < 4; ++d) {
        gathered.ne[d] = plan.out_ne[d];
    }
    gathered.set_contiguous();
    gathered.data = std::move(recv);
    gathered.pack();
    local = std::move(gathered);
}

#endif

}  // namespace kllm_mpi
