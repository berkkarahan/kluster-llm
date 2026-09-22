#include "kllm_mpi.hpp"

#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

namespace {

int g_fails = 0;

void check(bool cond, const std::string& label) {
    if (!cond) {
        std::cerr << "FAIL " << label << "\n";
        g_fails += 1;
    }
}

std::string hex_of(const std::vector<std::uint8_t>& bytes) {
    static const char* kDigits = "0123456789abcdef";
    std::string out;
    out.resize(bytes.size() * 2);
    for (std::size_t i = 0; i < bytes.size(); ++i) {
        out[2 * i] = kDigits[bytes[i] >> 4];
        out[2 * i + 1] = kDigits[bytes[i] & 0xf];
    }
    return out;
}

kllm_mpi::Tensor f32(const std::vector<float>& values, std::int32_t layer, bool partial) {
    std::int64_t ne[4] = {static_cast<std::int64_t>(values.size()), 1, 1, 1};
    return kllm_mpi::Tensor::f32(values, ne, 0, layer, partial);
}

}  // namespace

int main() {
    using kllm_mpi::Tensor;
    const std::int64_t ne[4] = {2, 1, 1, 1};
    const Tensor golden = Tensor::f32({1.f, 2.f}, ne, 0, 14, false);
    const std::string packed = hex_of(golden.pack());
    check(packed ==
              "4b4d50490100010000000000000000000e000000010000000200000000000000010000000000000001000000000000000100"
              "000000000000040000000000000008000000000000000800000000000000080000000000000008000000000000000000803f"
              "00000040",
          "ggml header matches the python packer");
    check(Tensor::unpack(golden.pack().data(), golden.pack().size()).f32_values()[1] == 2.f, "roundtrip");

    Tensor bad = golden;
    bad.ggml_type = 12;
    bad.flags = kllm_mpi::kActivation;
    bool threw = false;
    try {
        bad.pack();
    } catch (const std::runtime_error&) {
        threw = true;
    }
    check(threw, "quantized weight type is refused");

    threw = false;
    bad = golden;
    bad.flags = 0x8000;
    try {
        bad.pack();
    } catch (const std::runtime_error&) {
        threw = true;
    }
    check(threw, "weight flag is refused");

    std::vector<Tensor> slots(3);
    slots[0] = f32({1.f, 2.f, 3.f, 4.f}, 14, false);
    slots[1] = f32({0.f}, 0, false);
    slots[2] = f32({9.f}, 0, false);
    std::vector<std::uint8_t> weights_sentinel = slots[1].data;
    std::vector<kllm_mpi::TraceEvent> trace;
    kllm_mpi::balanced_bcast(slots, 0, &trace);
    check(slots[2].f32_values().size() == 4, "receiver resized from the header");
    check(slots[1].f32_values()[3] == 4.f, "payload arrived");
    check(trace.size() >= 2 && trace[0].what == "header" && trace[0].bytes == 96, "header bcast");
    check(trace[1].dynamic && trace[1].bytes == 16, "payload count is dynamic");
    check(slots[1].data != weights_sentinel || slots[1].data.size() != 4, "slot replaced");

    std::vector<std::size_t> chunks = kllm_mpi::payload_chunks((1ull << 30) + 10);
    check(chunks.size() == 2 && chunks[0] == (1ull << 30) && chunks[1] == 10, "int-sized chunks");

    std::vector<int> cols = kllm_mpi::largest_remainder({1.0, 1.4}, 100, nullptr);
    check(cols.size() == 2 && cols[0] == 42 && cols[1] == 58, "weighted column widths");

    std::vector<Tensor> rows = {f32({1.f, 2.f, 3.f}, 3, true), f32({10.f, 20.f, 30.f}, 3, true)};
    trace.clear();
    kllm_mpi::balanced_allreduce(rows, "row_sum", nullptr, &trace);
    const std::vector<float> summed = rows[1].f32_values();
    check(summed.size() == 3 && summed[0] == 11.f && summed[1] == 22.f && summed[2] == 33.f, "allreduce sum");
    bool saw_reduce = false;
    bool saw_gatherv = false;
    for (const auto& event : trace) {
        if (event.op == "MPI_Allreduce") {
            saw_reduce = true;
        }
        if (event.op == "MPI_Allgatherv") {
            saw_gatherv = true;
        }
    }
    check(saw_reduce && !saw_gatherv, "equal partials use MPI_Allreduce");

    std::vector<Tensor> uneven = {f32({1.f, 2.f}, 4, true), f32({3.f, 4.f, 5.f}, 4, true)};
    trace.clear();
    threw = false;
    try {
        kllm_mpi::balanced_allreduce(uneven, "row_sum", nullptr, &trace);
    } catch (const std::runtime_error&) {
        threw = true;
    }
    check(threw, "unequal row_sum is not padded");
    saw_reduce = false;
    bool saw_gather = false;
    for (const auto& event : trace) {
        if (event.op == "MPI_Allreduce") {
            saw_reduce = true;
        }
        if (event.op == "MPI_Allgather") {
            saw_gather = true;
        }
    }
    check(saw_gather && !saw_reduce, "failed row_sum exchanges counts and does not allreduce");

    std::vector<Tensor> heavy(1);
    heavy[0] = f32({1.f, 2.f, 3.f, 4.f}, 0, false);
    threw = false;
    try {
        kllm_mpi::balanced_bcast(heavy, 0, nullptr, 8);
    } catch (const std::runtime_error&) {
        threw = true;
    }
    check(threw, "payload over the budget is refused");

    trace.clear();
    kllm_mpi::balanced_allreduce(uneven, "column_concat", nullptr, &trace);
    const std::vector<float> joined = uneven[0].f32_values();
    check(joined.size() == 5 && joined[0] == 1.f && joined[2] == 3.f && joined[4] == 5.f, "allgatherv order");
    saw_gatherv = false;
    saw_reduce = false;
    for (const auto& event : trace) {
        if (event.op == "MPI_Allgatherv") {
            saw_gatherv = true;
            check(event.counts.size() == 2 && event.counts[0] == 8 && event.counts[1] == 12, "byte counts");
            check(event.displs.size() == 2 && event.displs[0] == 0 && event.displs[1] == 8, "displacements");
        }
        if (event.op == "MPI_Allreduce") {
            saw_reduce = true;
        }
    }
    check(saw_gatherv && !saw_reduce, "unequal columns use MPI_Allgatherv");

    std::vector<float> wide(42, 1.f);
    std::vector<float> rest(58, 2.f);
    std::vector<Tensor> weighted = {f32(wide, 1, true), f32(rest, 1, true)};
    const std::vector<double> speed = {1.0, 1.4};
    threw = false;
    try {
        kllm_mpi::balanced_allreduce(weighted, "column_concat", &speed, nullptr);
    } catch (const std::runtime_error&) {
        threw = true;
    }
    check(!threw && weighted[0].ne[0] == 100, "widths that match the weights are accepted");

    std::vector<Tensor> mismatch = {f32(std::vector<float>(50, 0.f), 1, true),
                                    f32(std::vector<float>(50, 0.f), 1, true)};
    threw = false;
    try {
        kllm_mpi::balanced_allreduce(mismatch, "column_concat", &speed, nullptr);
    } catch (const std::runtime_error&) {
        threw = true;
    }
    check(threw, "homogeneous 50/50 is refused when the weights say 42/58");

    if (g_fails) {
        std::cerr << g_fails << " failure(s)\n";
        return 1;
    }
    std::cout << "kllm_mpi loopback ok\n";
    return 0;
}
