// Multi-process MPI smoke test for kllm_mpi collectives.

#include "kllm_mpi.hpp"
#include <mpi.h>
#include <cstdint>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <vector>

static void require_all(bool ok, const char * message) {
    const int local = ok ? 0 : 1;
    int any = 0;
    MPI_Allreduce(&local, &any, 1, MPI_INT, MPI_MAX, MPI_COMM_WORLD);
    if (any) {
        throw std::runtime_error(message);
    }
}

static const char * platform_name() {
#if defined(__CYGWIN__)
    return "windows-cygwin";
#elif defined(_WIN32)
    return "windows-native";
#elif defined(__APPLE__)
    return "macos";
#elif defined(__linux__)
    return "linux";
#else
    return "other";
#endif
}

int main(int argc, char ** argv) {
    int provided = 0;
    if (MPI_Init_thread(&argc, &argv, MPI_THREAD_SERIALIZED, &provided) != MPI_SUCCESS) {
        return 2;
    }
    int rank = 0;
    int size = 0;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);
    try {
        require_all(provided >= MPI_THREAD_SERIALIZED, "MPI thread support insufficient");
        require_all(size >= 2, "this test needs at least two ranks");

        char hostname[MPI_MAX_PROCESSOR_NAME] = {};
        int hostname_len = 0;
        MPI_Get_processor_name(hostname, &hostname_len);
        std::cout << "rank=" << rank << " world=" << size
                  << " platform=" << platform_name()
                  << " host=" << std::string(hostname, hostname_len) << std::endl;

        const std::int64_t three[4] = {3, 1, 1, 1};
        auto broadcast = kllm_mpi::Tensor::f32({1.0f, 2.0f, 3.0f}, three, 0, 0, false);
        if (rank != 0) {
            broadcast.data.clear();
        }
        kllm_mpi::mpi_balanced_bcast(broadcast, 0);
        require_all(broadcast.f32_values() == std::vector<float>({1, 2, 3}), "broadcast mismatch");

        auto sum = kllm_mpi::Tensor::f32(
            std::vector<float>(3, static_cast<float>(rank + 1)), three, rank, 1, true);
        kllm_mpi::mpi_balanced_allreduce(sum, "row_sum", nullptr);
        const float expected = static_cast<float>(size * (size + 1) / 2);
        require_all(sum.f32_values() == std::vector<float>(3, expected), "allreduce mismatch");

        const std::int64_t width[4] = {rank + 1, 1, 1, 1};
        auto shard = kllm_mpi::Tensor::f32(
            std::vector<float>(rank + 1, static_cast<float>(rank)), width, rank, 2, true);
        kllm_mpi::mpi_balanced_allreduce(shard, "column_concat", nullptr);
        std::vector<float> concatenated;
        for (int r = 0; r < size; ++r) {
            concatenated.insert(concatenated.end(), r + 1, static_cast<float>(r));
        }
        require_all(shard.f32_values() == concatenated, "allgatherv mismatch");

        auto invalid = kllm_mpi::Tensor::f32({1, 2, 3}, three, 0, 3, false);
        if (rank == 0) {
            invalid.flags = 0x8000;
        }
        bool rejected = false;
        try {
            kllm_mpi::mpi_balanced_bcast(invalid, 0);
        } catch (const std::exception &) {
            rejected = true;
        }
        require_all(rejected, "invalid header was not rejected on every rank");
        if (rank == 0) {
            std::cout << "kllm_mpi real collectives ok world=" << size << std::endl;
        }
        MPI_Finalize();
        return 0;
    } catch (const std::exception & error) {
        std::cerr << "rank=" << rank << " error=" << error.what() << std::endl;
        MPI_Abort(MPI_COMM_WORLD, 1);
        return 1;
    }
}
