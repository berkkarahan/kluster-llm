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

option(KLLM_WITH_MPI "Build real MPI collective smoke test" OFF)

if(KLLM_WITH_MPI)
    find_package(MPI REQUIRED COMPONENTS CXX)
    target_compile_definitions(kllm_mpi_reference PUBLIC KLLM_HAVE_MPI=1)
    target_link_libraries(kllm_mpi_reference PUBLIC MPI::MPI_CXX)
    add_executable(kllm_mpi_smoke mpi_smoke_test.cpp)
    target_link_libraries(kllm_mpi_smoke PRIVATE kllm_mpi_reference)
    set_target_properties(kllm_mpi_smoke PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
    add_test(NAME mpi_real_collectives
        COMMAND "${MPIEXEC_EXECUTABLE}" ${MPIEXEC_NUMPROC_FLAG} 3
            ${MPIEXEC_PREFLAGS} $<TARGET_FILE:kllm_mpi_smoke>
            ${MPIEXEC_POSTFLAGS})
    set_tests_properties(mpi_real_collectives PROPERTIES TIMEOUT 45)
endif()

# Keep the Rust provider ABI consumable by the pinned C++ engine on every host.
# Runtime lifecycle and collective behavior are covered by the Rust provider
# suite; this target catches C/C++ declaration drift without linking native MPI.
add_executable(kllm_mpi_provider_abi_compile provider_abi_compile_test.cpp)
target_compile_features(kllm_mpi_provider_abi_compile PUBLIC cxx_std_17)
target_include_directories(kllm_mpi_provider_abi_compile PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
add_test(NAME mpi_provider_abi_compile COMMAND kllm_mpi_provider_abi_compile)
