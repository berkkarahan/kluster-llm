cmake_minimum_required(VERSION 3.19)
project(kllm_mpi_reference_tests LANGUAGES CXX)

# The C++ activation protocol remains a local reference test. Production MPI
# uses the pinned pure-Rust provider; this target has no native-MPI path.
include(CTest)
add_library(kllm_mpi_reference STATIC kllm_mpi.cpp)
target_compile_features(kllm_mpi_reference PUBLIC cxx_std_17)
target_include_directories(kllm_mpi_reference PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")

add_executable(kllm_mpi_loopback loopback_test.cpp)
target_link_libraries(kllm_mpi_loopback PRIVATE kllm_mpi_reference)
add_test(NAME mpi_loopback COMMAND kllm_mpi_loopback)

# Keep the Rust provider ABI consumable by the pinned C++ engine on every host.
# Runtime lifecycle and collective behavior are covered by the Rust provider
# suite; this target catches C/C++ declaration drift without linking native MPI.
add_executable(kllm_mpi_provider_abi_compile provider_abi_compile_test.cpp)
target_compile_features(kllm_mpi_provider_abi_compile PUBLIC cxx_std_17)
target_include_directories(kllm_mpi_provider_abi_compile PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
add_test(NAME mpi_provider_abi_compile COMMAND kllm_mpi_provider_abi_compile)
