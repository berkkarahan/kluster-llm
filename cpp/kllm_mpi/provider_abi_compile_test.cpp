#include "provider_abi.h"

#include <type_traits>

static_assert(KLLM_MPI_OK == 0);
static_assert(KLLM_MPI_ERROR != KLLM_MPI_BUFFER_TOO_SMALL);
static_assert(std::is_same_v<decltype(&kllm_mpi_init),
                             int32_t (*)(const uint8_t *, size_t, uint8_t *, size_t)>);
static_assert(std::is_same_v<decltype(&kllm_mpi_finalize),
                             int32_t (*)(uint8_t *, size_t)>);

int main() {
    return 0;
}
