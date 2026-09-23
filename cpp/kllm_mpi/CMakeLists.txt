cmake_minimum_required(VERSION 3.19)
project(kllm_mpi_tests LANGUAGES CXX)

include(CTest)
option(KLLM_WITH_MPI "Build real MPI collective tests" OFF)

add_library(kllm_mpi STATIC kllm_mpi.cpp)
target_compile_features(kllm_mpi PUBLIC cxx_std_17)
target_include_directories(kllm_mpi PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")

add_executable(kllm_mpi_loopback loopback_test.cpp)
target_link_libraries(kllm_mpi_loopback PRIVATE kllm_mpi)
add_test(NAME mpi_loopback COMMAND kllm_mpi_loopback)

if(KLLM_WITH_MPI)
    set(_kllm_msmpi_fallback FALSE)
    set(_kllm_msmpi_fallback_possible FALSE)
    if(WIN32)
        set(_kllm_mpi_explicit_selection FALSE)
        if(TARGET MPI::MPI_CXX OR MPI_FOUND OR MPI_CXX_FOUND
           OR MPI_CXX_COMPILER
           OR MPI_CXX_LIBRARIES OR MPI_CXX_LIB_NAMES
           OR MPI_CXX_HEADER_DIR OR MPI_CXX_ADDITIONAL_INCLUDE_DIRS
           OR MPI_CXX_INCLUDE_PATH
           OR MPIEXEC_EXECUTABLE
           OR NOT "${MPI_EXECUTABLE_SUFFIX}" STREQUAL ""
           OR MPI_GUESS_LIBRARY_NAME OR MPI_HOME OR MPI_ROOT
           OR NOT "$ENV{MPI_ROOT}" STREQUAL ""
           OR MPI_COMPILER OR MPI_SKIP_GUESSING
           OR (NOT MPI_IGNORE_LEGACY_VARIABLES AND
               (MPI_LIBRARY OR MPI_EXTRA_LIBRARY OR MPI_INCLUDE_PATH))
           OR (CMAKE_FIND_PACKAGE_PREFER_CONFIG AND
               (MPI_DIR OR NOT "$ENV{MPI_DIR}" STREQUAL ""))
           OR NOT "$ENV{MSMPI_INC}" STREQUAL ""
           OR NOT "$ENV{MSMPI_LIB64}" STREQUAL ""
           OR NOT "$ENV{MSMPI_LIB32}" STREQUAL ""
           OR NOT "$ENV{MSMPI_BIN}" STREQUAL ""
           OR NOT "$ENV{MPI_HOME}" STREQUAL ""
           OR NOT "$ENV{I_MPI_ROOT}" STREQUAL ""
           OR NOT "$ENV{OPAL_PREFIX}" STREQUAL "")
            set(_kllm_mpi_explicit_selection TRUE)
        endif()

        set(_kllm_mpi_cxx_wrapper_names
            mpicxx mpiCC mpcxx mpCC mpic++ mpc++
            mpicxx_r mpiCC_r mpcxx_r mpCC_r mpic++_r mpc++_r
            mpicxx.bat
            mpig++ mpg++ mpig++_r mpg++_r mpigxx
            mpiicpc mpiicxx mpiic++ mpiicpx mpiicx mpiicpc.bat mpiicx.bat
            mpicl mpicl.bat mpiFCCpx mpiFCC
            mpipgic++ mpipgCC mppgCC
            mpixlcxx mpixlC mpixlc++ mpxlcxx mpxlc++ mpixlc++ mpxlCC
            mpixlcxx_r mpixlC_r mpixlc++_r mpxlcxx_r mpxlc++_r mpixlc++_r mpxlCC_r
            CC)
        unset(_kllm_mpi_cxx_wrapper CACHE)
        unset(_kllm_mpi_cxx_wrapper)
        find_program(_kllm_mpi_cxx_wrapper
            NAMES ${_kllm_mpi_cxx_wrapper_names}
            PATH_SUFFIXES bin sbin)
        get_filename_component(_kllm_cxx_compiler_name "${CMAKE_CXX_COMPILER}" NAME)
        get_filename_component(_kllm_cxx_compiler_stem "${CMAKE_CXX_COMPILER}" NAME_WE)
        list(FIND _kllm_mpi_cxx_wrapper_names "${_kllm_cxx_compiler_name}"
             _kllm_cxx_compiler_name_index)
        list(FIND _kllm_mpi_cxx_wrapper_names "${_kllm_cxx_compiler_stem}"
             _kllm_cxx_compiler_stem_index)
        if(_kllm_mpi_cxx_wrapper
           OR NOT _kllm_cxx_compiler_name_index EQUAL -1
           OR NOT _kllm_cxx_compiler_stem_index EQUAL -1)
            set(_kllm_mpi_explicit_selection TRUE)
        endif()
        unset(_kllm_mpi_cxx_wrapper CACHE)
        unset(_kllm_mpi_cxx_wrapper)

        set(_kllm_msmpi_sdk "C:/Program Files (x86)/Microsoft SDKs/MPI")
        set(_kllm_msmpi_include "${_kllm_msmpi_sdk}/Include")
        if(CMAKE_SIZEOF_VOID_P EQUAL 8)
            set(_kllm_msmpi_library "${_kllm_msmpi_sdk}/Lib/x64/msmpi.lib")
            set(_kllm_msmpi_library_dir "${_kllm_msmpi_sdk}/Lib/x64")
        else()
            set(_kllm_msmpi_library "${_kllm_msmpi_sdk}/Lib/x86/msmpi.lib")
            set(_kllm_msmpi_library_dir "${_kllm_msmpi_sdk}/Lib/x86")
        endif()

        if(NOT _kllm_mpi_explicit_selection
           AND EXISTS "${_kllm_msmpi_include}/mpi.h"
           AND EXISTS "${_kllm_msmpi_library}")
            set(_kllm_msmpi_fallback_possible TRUE)
        endif()
    endif()

    find_package(MPI QUIET COMPONENTS CXX)
    if(NOT MPI_CXX_FOUND)
        if(_kllm_msmpi_fallback_possible)
            set(_kllm_msmpi_fallback TRUE)
            # Let the retry replace failed generic include-directory results.
            unset(MPI_CXX_ADDITIONAL_INCLUDE_DIRS CACHE)
            unset(MPI_CXX_ADDITIONAL_INCLUDE_DIRS)
            unset(MPI_CXX_HEADER_DIR CACHE)
            unset(MPI_CXX_HEADER_DIR)
            unset(MPI_CXX_INCLUDE_DIRS)
            set(ENV{MSMPI_INC} "${_kllm_msmpi_include}")
            if(CMAKE_SIZEOF_VOID_P EQUAL 8)
                set(ENV{MSMPI_LIB64} "${_kllm_msmpi_library_dir}")
            else()
                set(ENV{MSMPI_LIB32} "${_kllm_msmpi_library_dir}")
            endif()
        endif()
        find_package(MPI REQUIRED COMPONENTS CXX)
    endif()

    if(_kllm_msmpi_fallback)
        get_filename_component(_kllm_expected_msmpi_library
            "${_kllm_msmpi_library}" REALPATH)
        string(TOLOWER "${_kllm_expected_msmpi_library}"
               _kllm_expected_msmpi_library_lower)
        set(_kllm_mpi_cxx_libraries "${MPI_CXX_LIBRARIES}")
        if(DEFINED CACHE{MPI_CXX_LIBRARIES})
            get_property(_kllm_mpi_cxx_libraries
                CACHE MPI_CXX_LIBRARIES PROPERTY VALUE)
        endif()
        set(_kllm_selected_expected_msmpi_library FALSE)
        list(LENGTH _kllm_mpi_cxx_libraries _kllm_mpi_cxx_library_count)
        if(_kllm_mpi_cxx_library_count EQUAL 1)
            list(GET _kllm_mpi_cxx_libraries 0 _kllm_mpi_cxx_library)
            get_filename_component(_kllm_mpi_cxx_library_real
                "${_kllm_mpi_cxx_library}" REALPATH)
            string(TOLOWER "${_kllm_mpi_cxx_library_real}"
                   _kllm_mpi_cxx_library_real_lower)
            if("${_kllm_mpi_cxx_library_real_lower}" STREQUAL
               "${_kllm_expected_msmpi_library_lower}")
                set(_kllm_selected_expected_msmpi_library TRUE)
            endif()
        endif()
        if(NOT _kllm_selected_expected_msmpi_library)
            message(FATAL_ERROR
                "KLLM MS-MPI fallback found '${_kllm_mpi_cxx_libraries}', not the expected "
                "'${_kllm_expected_msmpi_library}'. Refusing to mix it with MS-MPI mpiexec.")
        endif()

        set(_kllm_msmpi_mpiexec "C:/Program Files/Microsoft MPI/Bin/mpiexec.exe")
        if(NOT EXISTS "${_kllm_msmpi_mpiexec}")
            message(FATAL_ERROR
                "KLLM MS-MPI fallback found '${_kllm_expected_msmpi_library}', but "
                "'${_kllm_msmpi_mpiexec}' is unavailable.")
        endif()
        # FindMPI may cache its registry-discovered launcher; keep fallback state local.
        unset(MPIEXEC_EXECUTABLE CACHE)
        set(MPIEXEC_EXECUTABLE "${_kllm_msmpi_mpiexec}")
    endif()
    target_compile_definitions(kllm_mpi PUBLIC KLLM_HAVE_MPI=1)
    target_link_libraries(kllm_mpi PUBLIC MPI::MPI_CXX)
    if(_kllm_msmpi_fallback)
        # The imported target retains these values for this configure.
        unset(MPI_CXX_HEADER_DIR CACHE)
        unset(MPI_CXX_LIB_NAMES CACHE)
        unset(MPI_msmpi_LIBRARY CACHE)
    endif()

    add_executable(kllm_mpi_smoke mpi_smoke_test.cpp)
    target_link_libraries(kllm_mpi_smoke PRIVATE kllm_mpi)
    set_target_properties(kllm_mpi_smoke PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
        RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/bin")
    if("${MPIEXEC_EXECUTABLE}" STREQUAL ""
       OR "${MPIEXEC_EXECUTABLE}" MATCHES "-NOTFOUND$"
       OR NOT EXISTS "${MPIEXEC_EXECUTABLE}"
       OR IS_DIRECTORY "${MPIEXEC_EXECUTABLE}")
        message(FATAL_ERROR
            "KLLM_WITH_MPI requires a real MPI launcher, but "
            "MPIEXEC_EXECUTABLE is '${MPIEXEC_EXECUTABLE}'.")
    endif()
    if(WIN32)
        get_filename_component(_kllm_mpi_launcher_extension
            "${MPIEXEC_EXECUTABLE}" LAST_EXT)
        string(TOLOWER "${_kllm_mpi_launcher_extension}"
            _kllm_mpi_launcher_extension_lower)
        if(NOT "${_kllm_mpi_launcher_extension_lower}" STREQUAL ".exe"
           AND NOT "${_kllm_mpi_launcher_extension_lower}" STREQUAL ".com"
           AND NOT "${_kllm_mpi_launcher_extension_lower}" STREQUAL ".bat"
           AND NOT "${_kllm_mpi_launcher_extension_lower}" STREQUAL ".cmd")
            message(FATAL_ERROR
                "KLLM_WITH_MPI requires a real MPI launcher, but "
                "MPIEXEC_EXECUTABLE is '${MPIEXEC_EXECUTABLE}'.")
        endif()
    endif()
    add_test(NAME mpi_real_collectives
        COMMAND "${MPIEXEC_EXECUTABLE}" ${MPIEXEC_NUMPROC_FLAG} 3
            ${MPIEXEC_PREFLAGS} $<TARGET_FILE:kllm_mpi_smoke>
            ${MPIEXEC_POSTFLAGS})
    set_tests_properties(mpi_real_collectives PROPERTIES TIMEOUT 45)
endif()
