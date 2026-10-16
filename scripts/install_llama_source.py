"""CMake cache identity and the llama.cpp configure/build ladder.

``install.py`` passes platform checks and the callables tests patch
(``_run``, ``ensure_cmake``, ``_is_macos``) into these functions.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

_BUILD_SIGNATURE_FILE = ".kllm-config"
_CACHE_PACKAGE_PATH_KEYS = (
    "OPENSSL_CRYPTO_LIBRARY:FILEPATH",
    "OPENSSL_SSL_LIBRARY:FILEPATH",
    "OPENSSL_INCLUDE_DIR:PATH",
)
_CACHE_LINKER_FLAGS = (
    "CMAKE_EXE_LINKER_FLAGS",
    "CMAKE_MODULE_LINKER_FLAGS",
    "CMAKE_SHARED_LINKER_FLAGS",
)
_CACHE_LINKER_KEYS = tuple(f"{flag}:STRING" for flag in _CACHE_LINKER_FLAGS)
_OPENSSL_CACHE_RESET = ("-UOPENSSL_*", "-U_OPENSSL_*")


def cmake_backend_flags(backend: str, *, macos: bool, linux: bool) -> list[str]:
    flags = [
        "-DGGML_RPC=ON",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DGGML_CUDA=OFF",
        "-DGGML_HIP=OFF",
        "-DGGML_VULKAN=OFF",
        "-DGGML_METAL=OFF",
    ]
    # llama.cpp v0.5.0's static backend registry on macOS registers Metal but
    # not Vulkan. Build GPU backends as loadable modules so the same binary
    # can discover both Metal and Vulkan/MoltenVK at runtime.
    # CPU-only remains statically linked and needs no dynamic backend loader.
    if macos:
        if backend == "cpu":
            return flags
        if backend in ("cuda", "rocm"):
            raise SystemExit(f"error: {backend} is not supported on macOS")
        return [
            "-DGGML_RPC=ON",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DGGML_CUDA=OFF",
            "-DGGML_HIP=OFF",
            "-DGGML_VULKAN=ON",
            "-DGGML_METAL=ON",
            "-DGGML_BACKEND_DL=ON",
        ]
    if backend == "metal":
        flags.append("-DGGML_METAL=ON")
    elif backend == "cuda":
        flags.append("-DGGML_CUDA=ON")
    elif backend == "rocm":
        if not linux:
            raise SystemExit("ROCm installation is supported on Linux")
        flags.extend(["-DGGML_HIP=ON", "-DCMAKE_C_COMPILER=hipcc", "-DCMAKE_CXX_COMPILER=hipcc"])
    elif backend == "vulkan":
        flags.append("-DGGML_VULKAN=ON")
    elif backend == "cpu":
        pass
    else:
        raise SystemExit(f"error: unknown backend {backend!r}")
    return flags


def build_signature(tag: str, flags: list[str], env: dict[str, str] | None) -> str:
    """Identity of a build dir: tag, backend flags and toolchain paths."""
    parts = [f"tag={tag}"]
    for flag in sorted(flags):
        # The HTTPS ladder is a recovery path, not part of the requested flavor;
        # keep it (and its cache-reset flags) out so trying it does not
        # invalidate an otherwise good cache. Without this, a run that had to
        # disable HTTPS would be treated as a different target and then keep
        # re-disabling it instead of restoring it.
        if flag.startswith("-DLLAMA_OPENSSL=") or flag.startswith("-U"):
            continue
        parts.append(flag)
    for key in ("CMAKE_PREFIX_PATH", "VULKAN_SDK", "VK_ICD_FILENAMES"):
        if env and env.get(key):
            parts.append(f"{key}={env[key]}")
    return "\n".join(parts)


def read_cmake_cache(build_dir: Path) -> dict[str, str]:
    cache = build_dir / "CMakeCache.txt"
    if not cache.is_file():
        return {}
    try:
        return dict(
            line.split("=", 1)
            for line in cache.read_text(encoding="utf-8", errors="replace").splitlines()
            if "=" in line
        )
    except OSError:
        return {}


def read_build_signature(build_dir: Path) -> str | None:
    try:
        return (build_dir / _BUILD_SIGNATURE_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_build_signature(build_dir: Path, signature: str) -> None:
    try:
        (build_dir / _BUILD_SIGNATURE_FILE).write_text(signature + "\n", encoding="utf-8")
    except OSError:
        pass


def dangling_cached_paths(
    entries: dict[str, str], keys: tuple[str, ...] = _CACHE_PACKAGE_PATH_KEYS
) -> list[str]:
    """Absolute paths recorded in CMakeCache.txt that no longer exist."""
    dangling: list[str] = []
    for key in keys:
        raw = entries.get(key)
        if not raw:
            continue
        if key in _CACHE_LINKER_KEYS:
            lexer = shlex.shlex(raw, posix=True)
            lexer.whitespace_split = True
            lexer.whitespace += ";"
            lexer.commenters = ""
            if os.name == "nt":
                lexer.escape = ""  # Backslashes are native path separators, not shell escapes.
            try:
                tokens = list(lexer)
            except ValueError:
                dangling.append(f"{key}=invalid quoting")
                continue
        else:
            tokens = [token.strip() for token in raw.split(";")]
        for token in tokens:
            if not token:
                continue
            if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
                token = token[1:-1]
            is_linker_dir = token.startswith("-L")
            candidate = token[2:] if is_linker_dir else token
            probe = Path(candidate)
            if not (candidate.startswith("/") or probe.is_absolute()):
                continue
            exists = probe.is_dir() if is_linker_dir else probe.exists()
            if not exists:
                dangling.append(f"{key}={token}")
    return dangling


def cache_reset_flags(entries: dict[str, str]) -> list[str]:
    """``-U`` flags that clear a half-resolved OpenSSL cache."""
    flags = list(_OPENSSL_CACHE_RESET)
    dangling = dangling_cached_paths(entries, _CACHE_LINKER_KEYS)
    for name in _CACHE_LINKER_FLAGS:
        if any(entry.startswith(f"{name}:") for entry in dangling):
            flags.append(f"-U{name}")
    return flags


def discard_stale_cmake_build(
    source_dir: Path,
    build_dir: Path,
    signature: str | None = None,
    remove: Callable[[Path], None] | None = None,
) -> None:
    """Remove an installer-owned CMake build dir that would misconfigure.

    ``remove`` lets the caller supply a lock-aware remover so a build dir held
    open by a running node fails loudly instead of being silently left in place.
    """
    entries = read_cmake_cache(build_dir)
    if not entries:
        return

    reason = ""
    expected = {
        "CMAKE_HOME_DIRECTORY:INTERNAL": source_dir.resolve(),
        "CMAKE_CACHEFILE_DIR:INTERNAL": build_dir.resolve(),
    }
    for key, current in expected.items():
        cached = entries.get(key)
        if cached and Path(cached).resolve() != current:
            reason = "checkout relocation"
            break
    if not reason and signature is not None:
        recorded = read_build_signature(build_dir)
        if recorded != signature:
            reason = "backend flag or toolchain change"
    if not reason:
        dangling = dangling_cached_paths(entries)
        if dangling:
            reason = "stale cached path(s): " + ", ".join(dangling[:3])
    if not reason:
        return

    print(f"Discarding llama.cpp CMake build ({reason})")
    if remove is not None:
        remove(build_dir)
    else:
        shutil.rmtree(build_dir, ignore_errors=True)


def compile_configured_tree(
    *,
    build_dir: Path,
    vendor: Path,
    flags: list[str],
    cmake_env: dict[str, str] | None,
    signature: str,
    reset_flags: list[str],
    https_off_flag: str,
    rpc_names: tuple[str, ...],
    run: Callable[..., subprocess.CompletedProcess[str]],
    warn: Callable[[str], None],
    write_signature: Callable[[Path, str], None],
    remove_binaries: Callable[[Path], None],
    find_binary: Callable[[str], Path | None],
    probe_backends: Callable[[Path | None], list[str]],
    expected_stamp: str,
    backends_ok: Callable[[str | None, list[str]], bool],
    parse_backends: Callable[[str | None], list[str]],
    jobs: int | None = None,
) -> tuple[bool, str]:
    """Configure, build, and probe. Returns ``(ok, https)``."""
    https = "on"
    try:

        def configure(extra: list[str]) -> subprocess.CompletedProcess[str]:
            return run(
                ["cmake", "-B", str(build_dir), *flags, *extra],
                cwd=vendor,
                env=cmake_env,
                check=False,
            )

        result = configure([])
        if result.returncode != 0:
            warn(
                "llama.cpp cmake configure failed; clearing cached OpenSSL/linker "
                f"paths ({' '.join(reset_flags)}) and retrying with HTTPS still enabled"
            )
            result = configure(reset_flags)
        if result.returncode != 0:
            warn(
                "llama.cpp cmake configure failed again; disabling llama-server's "
                f"embedded HTTPS as a last resort ({https_off_flag}); Kluster does not use it"
            )
            result = configure([*reset_flags, https_off_flag])
            https = "off"
        if result.returncode != 0:
            raise RuntimeError(
                f"llama.cpp cmake configure failed (exit {result.returncode}); "
                "see the cmake output above"
            )
        write_signature(build_dir, signature)
        remove_binaries(build_dir)

        job_count = str(jobs or os.cpu_count() or 4)
        rpc_target_ok = False
        for target in ["llama-server", *rpc_names]:
            if target in rpc_names and rpc_target_ok:
                continue
            result = run(
                [
                    "cmake",
                    "--build",
                    str(build_dir),
                    "--config",
                    "Release",
                    "--target",
                    target,
                    "-j",
                    job_count,
                ],
                cwd=vendor,
                check=False,
                env=cmake_env,
            )
            if result.returncode == 0 and target in rpc_names:
                rpc_target_ok = True
            elif result.returncode != 0:
                warn(f"cmake target {target} failed (exit {result.returncode})")

        llama = find_binary("llama-server")
        if llama is None:
            warn("llama-server was not produced by the source build")
            return False, https
        probed = probe_backends(llama)
        if not backends_ok(expected_stamp, probed):
            warn(
                f"source build reports backends {probed!r}, "
                f"expected {parse_backends(expected_stamp)!r}"
            )
            return False, https
        if https == "off":
            warn(
                "build succeeded with llama-server's embedded HTTPS disabled; "
                "re-run to restore it once the OpenSSL cache issue is resolved"
            )
        return True, https
    except Exception as exc:
        warn(f"llama.cpp source build failed ({exc})")
        return False, https
