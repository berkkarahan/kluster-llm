#include "kllm_mpi.hpp"

#include <iostream>
#include <string>
#include <vector>

namespace {

std::string hex_of(const std::vector<std::uint8_t>& bytes) {
    static const char* kDigits = "0123456789abcdef";
    std::string out;
    out.resize(bytes.size() * 2);
    for (std::size_t i = 0; i < bytes.size(); ++i) {
        out[i * 2] = kDigits[bytes[i] >> 4];
        out[i * 2 + 1] = kDigits[bytes[i] & 0x0f];
    }
    return out;
}

void expect(bool ok, const char* message) {
    if (!ok) {
        throw std::runtime_error(message);
    }
}

}  // namespace

int main() {
    const std::uint16_t probe = 1;
    expect(reinterpret_cast<const unsigned char*>(&probe)[0] == 1, "wire format requires a little-endian host");

    const std::vector<std::vector<std::uint8_t>> shards = {{0x01, 0x02}, {0x03}, {0x04, 0x05, 0x06}};
    const auto envelope = kllm::build_bcast_envelope(shards);
    expect(hex_of(envelope) == "020000000102000100000003000003000000040506", "bcast envelope bytes drifted");
    expect(kllm::take_bcast_slot(envelope, 0, 3) == shards[0], "slot 0");
    expect(kllm::take_bcast_slot(envelope, 1, 3) == shards[1], "slot 1");
    expect(kllm::take_bcast_slot(envelope, 2, 3) == shards[2], "slot 2");
    std::cout << "envelope " << hex_of(envelope) << "\n";
    std::cout << "mpi_bcast_count " << envelope.size() << "\n";

    kllm::MpiCall reduce_call;
    const std::vector<float> stitched = kllm::weighted_allreduce(
        {{0, {1.0f, 2.0f}}, {2, {3.0f, 4.0f}}},
        4,
        &reduce_call);
    expect(stitched.size() == 4, "stitched width");
    expect(stitched[0] == 1.0f && stitched[1] == 2.0f && stitched[2] == 3.0f && stitched[3] == 4.0f,
           "column allreduce");
    expect(reduce_call.primitive == "MPI_Allreduce", "primitive");
    expect(reduce_call.count_bytes == 16, "homogeneous allreduce count");

    bool threw = false;
    try {
        kllm::mpi_allreduce_sum({{1.0f, 2.0f}, {1.0f}}, nullptr);
    } catch (const std::runtime_error&) {
        threw = true;
    }
    expect(threw, "unequal MPI_Allreduce counts must fail");

    std::vector<kllm::MpiCall> chunks;
    const std::vector<float> summed = kllm::allreduce_chunked(
        {{1.0f, 2.0f, 3.0f, 4.0f}, {10.0f, 20.0f, 30.0f, 40.0f}},
        3,
        &chunks);
    expect(summed.size() == 4 && summed[0] == 11.0f && summed[3] == 44.0f, "chunked sum");
    expect(chunks.size() == 2 && chunks[0].count_bytes == 12, "chunk mpi count");

    const float activation[4] = {1.0f, 0.0f, 0.0f, 0.0f};
    kllm::PackArgs args;
    args.op = kllm::CollectiveOp::Handoff;
    args.type = kllm::GgmlType::F32;
    args.ne[0] = 4;
    args.ne[1] = 1;
    args.ne[2] = 1;
    args.ne[3] = 1;
    args.rank = 1;
    args.n_ranks = 3;
    args.root = 0;
    args.split_axis = 0xff;
    const auto packed = kllm::pack_message(args, reinterpret_cast<const std::uint8_t*>(activation), sizeof(activation));
    expect(packed.size() == 64 + sizeof(activation), "packed frame size");
    std::cout << "frame " << hex_of(packed) << "\n";
    std::cout << "ok\n";
    return 0;
}
