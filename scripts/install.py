#!/usr/bin/env python3
"""Full Kluster LLM install: toolchains, kllm CLI, llama.cpp binaries, WireGuard.

Usage:
  uv run python scripts/install.py
  python scripts/install.py
  uv run python scripts/install.py --skip-ui
  uv run python scripts/install.py --backend vulkan
  uv run python scripts/install.py --skip-wireguard --skip-llama
  python scripts/install.py --llama-only
  python scripts/install.py --check
  python scripts/install.py --llama-only --from-source --force
  python scripts/install.py --sync

On macOS (non-CPU), the default llama.cpp install is a Metal+Vulkan source
build (MoltenVK via Homebrew). Use ``--prefer-prebuilt`` for Metal-only
GitHub packs, or ``--backend cpu`` for a CPU-only build. The default install
fails loudly rather than silently downgrading to a Metal-only pack, and
``--check`` judges the result against that default contract.

The llama.cpp build dir is installer-owned and reused between runs. It is
discarded when the tag, backend flags or recorded toolchain paths change, so a
toolchain upgrade (e.g. ``brew upgrade openssl@3``) cannot leave a cache that
half-resolves a package.

On macOS, Metal + Vulkan in one binary is the contract: install verifies the
backend list of the binary the runtime will actually launch, and
``--prefer-prebuilt`` is rejected because GitHub macOS packs are Metal-only.
llama-server's embedded HTTPS server is the expendable part: when ``cmake``
configure fails, the installer first clears the stale OpenSSL cache and retries
with HTTPS still enabled, and only disables HTTPS (stamped ``https=off``) as a
last resort. That fallback is never latched, so the next run restores it.

This is the one-shot installer. ``scripts/build.py`` remains a thinner
CLI/dashboard-only build (invokes the same helpers with llama/WG skipped).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from concurrent.futures import ProcessPoolExecutor, as_completed
import os
import platform
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import install_http  # noqa: E402
import install_llama_release  # noqa: E402
import install_llama_source  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"
DASHBOARD = ROOT / "dashboard"
VENDOR_LLAMA = ROOT / "vendor" / "llama.cpp"
BIN_DIR = ROOT / "vendor" / "bin"
PRIMA_BIN_DIR = ROOT / "vendor" / "prima"
PIN_FILE = ROOT / "llama-release"
STAMP_FILE = BIN_DIR / "llama-release.txt"
LLAMA_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
GITHUB_HEADERS = {"User-Agent": "kllm-install", "Accept": "application/vnd.github+json"}
# Binary/asset downloads must not use the GitHub API Accept — some CDNs/proxies
# return HTML/JSON error bodies with HTTP 200, which then fail as BadZipFile.
DOWNLOAD_HEADERS = {"User-Agent": "kllm-install", "Accept": "*/*"}
_DOWNLOAD_ATTEMPTS = 3
# Upstream llama.cpp ≥2025 ships ggml-rpc-server; older trees used rpc-server.
_RPC_UPSTREAM_NAMES = ("ggml-rpc-server", "rpc-server")
_SHARED_LIB_PATTERNS = ("*.dll", "*.dylib", "*.so", "*.so.*")
_ZIP_MAGIC = b"PK\x03\x04"
_GZIP_MAGIC = b"\x1f\x8b"
_TAR_MAGIC_OFFSET = 257
_TAR_MAGIC = b"ustar"
# CMake build dirs are installer-owned. The recorded signature (llama tag +
# backend flags + toolchain paths) lets us discard a cache that would
# misconfigure, e.g. after ``brew upgrade openssl@3`` deletes the Cellar
# directory a previous configure pinned.
_BUILD_SIGNATURE_FILE = ".kllm-config"
# Cached package paths that CMake will happily reuse after a toolchain upgrade
# deleted them, producing a half-resolved package (the OpenSSL::SSL failure).
# Strictly package paths: a dangling `-L` in the linker flags is harmless -
# CMake ignores it - and treating it as stale would discard and rebuild the
# whole tree on every run whenever the shell exports a dead LDFLAGS entry.
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
# Cache entries cleared when repairing a half-resolved OpenSSL package.
_OPENSSL_CACHE_RESET = ("-UOPENSSL_*", "-U_OPENSSL_*")
# llama-server's embedded HTTPS server is optional for Kluster; the Metal+Vulkan
# backend is not. The ladder disables HTTPS only after the real fix is tried.
_HTTPS_OFF_FLAG = "-DLLAMA_OPENSSL=OFF"


# ── shared helpers (also used by build.py) ───────────────────────────────────


def _is_windows() -> bool:
    return os.name == "nt"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _venv_python() -> Path:
    if _is_windows():
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _venv_ok(python: Path) -> bool:
    if not python.is_file():
        return False
    if not (VENV_DIR / "pyvenv.cfg").is_file():
        return False
    try:
        subprocess.run(
            [
                str(python),
                "-c",
                "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)",
            ],
            check=True,
            capture_output=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _augment_path(path: str) -> str:
    extras: list[str] = []
    for candidate in (
        Path.home() / ".local" / "bin",
        Path.home() / ".cargo" / "bin",
        Path.home() / "scoop" / "apps" / "rustup" / "current" / ".cargo" / "bin",
        Path(r"C:\Program Files\WireGuard"),
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
    ):
        if candidate.is_dir():
            extras.append(str(candidate))
    return os.pathsep.join([*extras, path] if path else extras)


def _venv_env(python: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(VENV_DIR)
    env.pop("PYTHONHOME", None)
    scripts = python.parent
    env["PATH"] = _augment_path(os.pathsep.join([str(scripts), env.get("PATH", "")]))
    return env


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    display = " ".join(cmd)
    print(f"\n-> {display}", flush=True)
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged["PATH"] = _augment_path(merged.get("PATH", ""))
    return subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        env=merged,
        check=check,
        text=True,
        capture_output=False,
    )


def _which(cmd: str) -> str | None:
    path = shutil.which(cmd)
    if path:
        return path
    search = _augment_path(os.environ.get("PATH", ""))
    return shutil.which(cmd, path=search)


def _warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr, flush=True)


def _ssl_contexts() -> list[ssl.SSLContext]:
    return install_http.ssl_contexts()


def _curl_bytes(url: str) -> bytes:
    return install_http.curl_bytes(
        url,
        which=_which,
        user_agent=GITHUB_HEADERS["User-Agent"],
        accept=GITHUB_HEADERS["Accept"],
    )


def _curl_download(url: str, dest: Path) -> None:
    install_http.curl_download(
        url,
        dest,
        which=_which,
        user_agent=DOWNLOAD_HEADERS["User-Agent"],
        accept=DOWNLOAD_HEADERS["Accept"],
    )


def _http_json(url: str) -> Any:
    return install_http.http_json(
        url, headers=GITHUB_HEADERS, contexts=_ssl_contexts, curl=_curl_bytes
    )


def _sniff_archive_kind(path: Path) -> str | None:
    """Return 'zip', 'gzip', or 'tar' when magic bytes look like an archive."""
    return install_http.sniff_archive_kind(path)


def _expected_archive_kind(name: str) -> str | None:
    return install_http.expected_archive_kind(name)


def _validate_downloaded_file(
    dest: Path,
    *,
    expected_size: int | None = None,
    expected_kind: str | None = None,
    content_length: int | None = None,
) -> None:
    install_http.validate_downloaded_file(
        dest,
        expected_size=expected_size,
        expected_kind=expected_kind,
        content_length=content_length,
        sniff=_sniff_archive_kind,
    )


def _download_once(
    url: str,
    dest: Path,
    *,
    expected_size: int | None = None,
    expected_kind: str | None = None,
) -> None:
    install_http.download_once(
        url,
        dest,
        headers=DOWNLOAD_HEADERS,
        contexts=_ssl_contexts,
        curl=_curl_download,
        validate=_validate_downloaded_file,
        expected_size=expected_size,
        expected_kind=expected_kind,
    )


def _download(
    url: str,
    dest: Path,
    *,
    expected_size: int | None = None,
    expected_kind: str | None = None,
) -> None:
    install_http.download_with_retries(
        url,
        dest,
        attempts=_DOWNLOAD_ATTEMPTS,
        once=_download_once,
        warn=_warn,
        kind_for=_expected_archive_kind,
        expected_size=expected_size,
        expected_kind=expected_kind,
    )


def normalize_llama_tag(raw: str) -> str:
    tag = raw.strip()
    if not tag or tag.startswith("#"):
        raise SystemExit("error: empty llama.cpp release tag")
    if re.fullmatch(r"\d+", tag):
        tag = f"b{tag}"
    return tag


def read_pinned_release(override: str | None = None) -> str:
    """Return the llama.cpp GitHub tag from llama-release (or --tag)."""
    if override:
        return normalize_llama_tag(override)
    if not PIN_FILE.is_file():
        raise SystemExit(
            f"error: pin file {PIN_FILE} is missing. Add a stable llama.cpp tag (e.g. v0.5.0)."
        )
    for line in PIN_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return normalize_llama_tag(line)
    raise SystemExit(f"error: {PIN_FILE} has no llama.cpp tag")


def write_install_stamp(
    *,
    tag: str,
    backend: str,
    asset: str,
    https: str | None = None,
) -> None:
    """Write the install stamp.

    ``https`` records whether llama-server's embedded HTTPS server was built
    (``on``) or left out by the configure ladder's last-resort fallback
    (``off``), so a degraded install stays visible to ``--check`` instead of
    silently latching.
    """
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"tag={tag}", f"backend={backend}", f"asset={asset}"]
    if https:
        lines.append(f"https={https}")
    STAMP_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_install_stamp() -> dict[str, str]:
    if not STAMP_FILE.is_file():
        return {}
    out: dict[str, str] = {}
    for line in STAMP_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def parse_stamp_backends(raw: str | None) -> list[str]:
    """Split stamp backend values such as ``metal+vulkan`` or ``metal,vulkan``."""
    if not raw:
        return []
    return [p.strip().lower() for p in re.split(r"[+,]", raw.strip()) if p.strip()]


def stamp_backend_for_install(backend: str) -> str:
    """Stamp string for an install flavor (macOS multi → ``metal+vulkan``)."""
    if _is_macos() and backend != "cpu":
        if backend in ("cuda", "rocm"):
            raise SystemExit(f"error: {backend} is not supported on macOS")
        return "metal+vulkan"
    return backend


def backends_satisfied(requested: str | None, actual: list[str]) -> bool:
    """True when every stamped backend appears in probed ``--list-devices`` backends."""
    needed = parse_stamp_backends(requested)
    if not needed:
        return False
    actual_set = frozenset(str(b).strip().lower() for b in actual)
    return all(b in actual_set for b in needed)


def stamp_matches(tag: str, backend: str | None = None) -> bool:
    stamp = read_install_stamp()
    if stamp.get("tag") != tag:
        return False
    if backend is None:
        return True
    return frozenset(parse_stamp_backends(stamp.get("backend"))) == frozenset(
        parse_stamp_backends(backend)
    )


def parse_list_devices_backends(text: str) -> list[str]:
    """Map llama-server --list-devices output to backend names.

    Newer llama.cpp labels Metal as ``MTL0:`` rather than ``Metal0:``.
    """
    found: list[str] = []
    # (backend, regex). Patterns match the device id prefix before ':'.
    probes = (
        ("metal", r"\b(?:Metal|MTL)\d*:"),
        ("cuda", r"\bCUDA\d*:"),
        ("rocm", r"\b(?:ROCm|HIP)\d*:"),
        ("vulkan", r"\bVulkan\d*:"),
    )
    for backend_name, pattern in probes:
        if re.search(pattern, text) and backend_name not in found:
            found.append(backend_name)
    return found


def llama_install_status(tag: str | None = None, *, expected: str | None = None) -> dict[str, Any]:
    """Pin/stamp state plus the backends the installed binary actually reports.

    ``expected`` is the stamp string the install was asked to produce. It
    defaults to the recorded stamp, so ``--prefer-prebuilt`` installs are judged
    by what they asked for; callers wanting the platform-default contract pass
    it explicitly.
    """
    pin = tag or read_pinned_release()
    llama = _resolve_runtime_binary("llama-server")
    rpc = _resolve_runtime_binary("rpc-server")
    stamp = read_install_stamp()
    requested = stamp.get("backend")
    wanted = expected or requested
    actual_backends = _probe_llama_backends(llama)
    return {
        "pin": pin,
        "stamp": stamp.get("tag"),
        "llama_server": str(llama) if llama else None,
        "rpc_server": str(rpc) if rpc else None,
        "requested_backend": requested,
        "expected_backend": wanted,
        "actual_backends": actual_backends,
        # "off" means the configure ladder had to disable llama-server's embedded
        # HTTPS server to get the mandatory Metal+Vulkan build through.
        "https": stamp.get("https"),
        "ok": bool(
            llama
            and rpc
            and stamp.get("tag") == pin
            and backends_satisfied(wanted, actual_backends)
        ),
    }


# ── toolchains ───────────────────────────────────────────────────────────────


def _python_version_spec() -> str:
    spec_path = ROOT / ".python-version"
    if spec_path.is_file():
        line = spec_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if line:
            return line
    return "3.12"


def _find_uv() -> str | None:
    uv = _which("uv")
    if uv:
        return uv
    local = Path.home() / ".local" / "bin" / ("uv.exe" if _is_windows() else "uv")
    return str(local) if local.is_file() else None


def ensure_uv() -> str:
    found = _find_uv()
    if found:
        return found
    print("Installing uv (Python toolchain manager)…")
    if _is_windows():
        _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "irm https://astral.sh/uv/install.ps1 | iex",
            ],
            check=False,
        )
    else:
        _run(["bash", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"], check=False)
    found = _find_uv()
    if not found:
        raise SystemExit(
            "error: uv not found after install. Install from https://docs.astral.sh/uv/ "
            "and re-run, or add ~/.local/bin to PATH."
        )
    return found


def ensure_venv() -> Path:
    python = _venv_python()
    if _venv_ok(python):
        print(f"Using existing venv: {VENV_DIR}")
        return python

    uv = ensure_uv()
    py_spec = _python_version_spec()
    if VENV_DIR.exists():
        print(f"Recreating broken venv at {VENV_DIR}")
        shutil.rmtree(VENV_DIR)
    else:
        print(f"Creating venv with uv (Python {py_spec}) at {VENV_DIR}")
    _run([uv, "venv", str(VENV_DIR), "--python", py_spec])
    python = _venv_python()
    if not _venv_ok(python):
        raise SystemExit(f"error: failed to create usable venv at {VENV_DIR}")
    return python


def _sync_python_deps(python: Path) -> None:
    uv = _find_uv()
    env = _venv_env(python)
    if uv:
        _run([uv, "sync", "--python", str(python)], env=env)
        return
    _run([str(python), "-m", "pip", "install", "--upgrade", "pip"], env=env)
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "maturin>=1.7,<2.0",
            "pytest>=8.0",
            "pytest-asyncio>=0.24",
        ],
        env=env,
    )


def ensure_rust() -> None:
    if _which("cargo") and _which("rustc"):
        print("Rust toolchain: OK")
        return
    print("Installing Rust via rustup…")
    if _is_windows():
        with tempfile.TemporaryDirectory() as td:
            installer = Path(td) / "rustup-init.exe"
            _download(
                "https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe",
                installer,
            )
            _run([str(installer), "-y", "--default-toolchain", "stable"])
    else:
        script = "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"
        _run(["bash", "-lc", script])
    if not _which("cargo"):
        raise SystemExit(
            "error: rustup ran but cargo is still not on PATH. "
            "Open a new shell (or add ~/.cargo/bin) and re-run."
        )


def ensure_node() -> None:
    if _which("npm") and _which("node"):
        print("Node.js / npm: OK")
        return
    print("Installing Node.js…")
    try:
        if _is_windows():
            winget = _which("winget")
            if not winget:
                raise RuntimeError("winget not found")
            _run(
                [
                    winget,
                    "install",
                    "-e",
                    "--id",
                    "OpenJS.NodeJS.LTS",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ]
            )
        elif _is_macos():
            brew = _which("brew")
            if not brew:
                raise RuntimeError("Homebrew not found")
            _run([brew, "install", "node"])
        else:
            # Prefer nodesource-free OS packages
            if _which("apt-get"):
                _run(["sudo", "apt-get", "update", "-y"], check=False)
                _run(["sudo", "apt-get", "install", "-y", "nodejs", "npm"])
            elif _which("dnf"):
                _run(["sudo", "dnf", "install", "-y", "nodejs", "npm"])
            elif _which("pacman"):
                _run(["sudo", "pacman", "-Sy", "--noconfirm", "nodejs", "npm"])
            else:
                raise RuntimeError("no supported package manager")
    except Exception as exc:
        raise SystemExit(
            f"error: could not install Node.js ({exc}). Install from https://nodejs.org/ and re-run."
        ) from exc
    if not _which("npm"):
        raise SystemExit(
            "error: Node.js installed but npm not on PATH — open a new shell and re-run."
        )


def ensure_git() -> None:
    if _which("git"):
        return
    print("Installing git…")
    if _is_windows():
        winget = _which("winget")
        if winget:
            _run(
                [
                    winget,
                    "install",
                    "-e",
                    "--id",
                    "Git.Git",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ]
            )
        else:
            raise SystemExit("error: git not found; install Git for Windows")
    elif _is_macos():
        _run(["xcode-select", "--install"], check=False)
    elif _which("apt-get"):
        _run(["sudo", "apt-get", "install", "-y", "git"])
    else:
        raise SystemExit("error: git not found")


def ensure_cmake() -> bool:
    if _which("cmake"):
        return True
    print("Installing cmake…")
    try:
        if _is_windows():
            winget = _which("winget")
            if winget:
                _run(
                    [
                        winget,
                        "install",
                        "-e",
                        "--id",
                        "Kitware.CMake",
                        "--accept-package-agreements",
                        "--accept-source-agreements",
                    ]
                )
        elif _is_macos():
            brew = _which("brew")
            if brew:
                _run([brew, "install", "cmake"])
        elif _which("apt-get"):
            _run(["sudo", "apt-get", "install", "-y", "cmake", "build-essential"])
        elif _which("dnf"):
            _run(["sudo", "dnf", "install", "-y", "cmake", "gcc-c++", "make"])
    except Exception as exc:
        _warn(f"cmake install failed ({exc}); will try prebuilt llama binaries")
        return bool(_which("cmake"))
    return bool(_which("cmake"))


def _brew_prefix() -> Path | None:
    brew = _which("brew")
    if not brew:
        return None
    result = subprocess.run([brew, "--prefix"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    prefix = Path(result.stdout.strip())
    return prefix if prefix.is_dir() else None


# Homebrew installs MoltenVK's ICD manifest under ``etc/vulkan/icd.d`` (the
# loader is built with CMAKE_INSTALL_SYSCONFDIR set to the brew ``etc`` dir),
# while LunarG SDKs and some formula revisions use ``share/vulkan/icd.d``.
# Search both.
_VULKAN_ICD_SUBDIRS = (
    Path("etc") / "vulkan" / "icd.d",
    Path("share") / "vulkan" / "icd.d",
)


def _macos_moltenvk_icd(prefix: Path | None = None) -> Path | None:
    prefix = prefix or _brew_prefix()
    if not prefix:
        return None
    for sub in _VULKAN_ICD_SUBDIRS:
        icd = prefix / sub / "MoltenVK_icd.json"
        if icd.is_file():
            return icd
    return None


def _spirv_headers_config(root: Path) -> Path | None:
    for sub in (
        Path("share") / "cmake" / "SPIRV-Headers",
        Path("lib") / "cmake" / "SPIRV-Headers",
    ):
        cfg = root / sub / "SPIRV-HeadersConfig.cmake"
        if cfg.is_file():
            return cfg
    return None


def _macos_vulkan_missing() -> list[str]:
    """Names the pieces a ``GGML_VULKAN=ON`` cmake configure still needs.

    llama.cpp requires ``find_package(Vulkan COMPONENTS glslc REQUIRED)`` and
    ``find_package(SPIRV-Headers CONFIG REQUIRED)``, plus Vulkan headers and a
    MoltenVK/SDK ICD manifest. Homebrew splits these across formulae:
    ``shaderc`` provides glslc, ``spirv-headers`` the SPIRV-Headers config.
    """
    missing: list[str] = []
    if not _which("glslc"):
        missing.append("glslc (brew: shaderc)")

    roots: list[Path] = []
    sdk = os.environ.get("VULKAN_SDK", "").strip()
    if sdk:
        sdk_path = Path(sdk)
        roots += [sdk_path / "macOS", sdk_path]
    prefix = _brew_prefix()
    if prefix:
        roots.append(prefix)
    if not roots:
        missing.append("Homebrew prefix or VULKAN_SDK")
        return missing

    if not any((root / "include" / "vulkan" / "vulkan.h").is_file() for root in roots):
        missing.append("Vulkan headers (brew: vulkan-headers)")
    if not any(
        any((root / sub / "MoltenVK_icd.json").is_file() for sub in _VULKAN_ICD_SUBDIRS)
        for root in roots
    ):
        missing.append("MoltenVK ICD (brew: molten-vk)")
    if not any(_spirv_headers_config(root) for root in roots):
        missing.append("SPIRV-Headers CMake config (brew: spirv-headers)")
    return missing


def _macos_vulkan_ready() -> bool:
    """True when all Vulkan source-build deps are provisioned."""
    return not _macos_vulkan_missing()


# Homebrew formulae that satisfy every check above.
_BREW_VULKAN_PACKAGES = (
    "molten-vk",
    "vulkan-headers",
    "vulkan-loader",
    "shaderc",
    "spirv-headers",
)


def ensure_vulkan_macos() -> dict[str, str]:
    """Install MoltenVK + glslc + SPIRV-Headers via Homebrew when needed.

    Returns cmake/probe env extras. Raises SystemExit when the multi-backend
    Vulkan deps cannot be provisioned.
    """
    if not _is_macos():
        return {}
    brew = _which("brew")
    if not _macos_vulkan_ready():
        if not brew and not os.environ.get("VULKAN_SDK", "").strip():
            raise SystemExit(
                "error: macOS Metal+Vulkan builds need Homebrew (or VULKAN_SDK). "
                "Install Homebrew (https://brew.sh) and re-run, or install the LunarG "
                "Vulkan SDK and set VULKAN_SDK."
            )
        if brew:
            print("Installing Vulkan build deps via Homebrew...")
            _run([brew, "install", *_BREW_VULKAN_PACKAGES])
        missing = _macos_vulkan_missing()
        if missing:
            raise SystemExit(
                "error: Vulkan build deps still missing after install: "
                + ", ".join(missing)
                + ". Install "
                + " ".join(_BREW_VULKAN_PACKAGES)
                + " (brew) or set VULKAN_SDK to a LunarG Vulkan SDK."
            )

    env: dict[str, str] = {}
    prefix = _brew_prefix()
    paths: list[str] = []
    if prefix:
        paths.append(str(prefix))
        icd = _macos_moltenvk_icd(prefix)
        if icd:
            env["VK_ICD_FILENAMES"] = str(icd)
    sdk = os.environ.get("VULKAN_SDK", "").strip()
    if sdk:
        paths.append(sdk)
        env["VULKAN_SDK"] = sdk
    if paths:
        existing = os.environ.get("CMAKE_PREFIX_PATH", "")
        merged = os.pathsep.join([*paths, existing] if existing else paths)
        env["CMAKE_PREFIX_PATH"] = merged
    return env


# Windows from-source Vulkan builds need the LunarG SDK: glslc and the Vulkan
# headers/library for ``find_package(Vulkan COMPONENTS glslc REQUIRED)``, plus
# the SDK's cmake configs for ``find_package(SPIRV-Headers CONFIG REQUIRED)``.
_VULKAN_SDK_WINDOWS_ID = "KhronosGroup.VulkanSDK"
_VULKAN_SDK_WINDOWS_ROOT = Path(r"C:\VulkanSDK")


def _windows_vulkan_sdk_root() -> Path | None:
    """Locate a Windows Vulkan SDK: VULKAN_SDK first, then default LunarG installs."""
    roots: list[Path] = []
    sdk = os.environ.get("VULKAN_SDK", "").strip()
    if sdk:
        roots.append(Path(sdk))
    if _VULKAN_SDK_WINDOWS_ROOT.is_dir():
        roots += sorted((p for p in _VULKAN_SDK_WINDOWS_ROOT.iterdir() if p.is_dir()), reverse=True)
    return next((root for root in roots if (root / "Bin" / "glslc.exe").is_file()), None)


def _windows_vulkan_missing(root: Path | None) -> list[str]:
    """Names the pieces a Windows ``GGML_VULKAN=ON`` cmake configure still needs."""
    if root is None:
        return (
            []
            if _which("glslc")
            else ["the LunarG Vulkan SDK (glslc, vulkan-1.lib, Vulkan headers)"]
        )
    missing: list[str] = []
    if not (root / "Bin" / "glslc.exe").is_file() and not _which("glslc"):
        missing.append("glslc")
    if not (root / "Include" / "vulkan" / "vulkan.h").is_file():
        missing.append("Vulkan headers")
    if not (root / "Lib" / "vulkan-1.lib").is_file():
        missing.append("vulkan-1.lib")
    return missing


def _windows_vulkan_env(root: Path | None) -> dict[str, str]:
    """Cmake env extras so this process builds before any shell/env refresh."""
    if root is None:
        return {}
    existing = os.environ.get("CMAKE_PREFIX_PATH", "")
    return {
        "VULKAN_SDK": str(root),
        "PATH": os.pathsep.join([str(root / "Bin"), os.environ.get("PATH", "")]),
        "CMAKE_PREFIX_PATH": os.pathsep.join([str(root), existing] if existing else [str(root)]),
    }


def ensure_windows_vulkan() -> dict[str, str]:
    """Provision the LunarG Vulkan SDK when a Windows Vulkan source build needs it.

    Returns cmake env extras. Raises SystemExit when the SDK cannot be provisioned.
    """
    if not _is_windows():
        return {}
    root = _windows_vulkan_sdk_root()
    if _windows_vulkan_missing(root):
        winget = _which("winget")
        if winget:
            print("Installing the LunarG Vulkan SDK via winget (source builds need glslc)...")
            result = _run(
                [
                    winget,
                    "install",
                    "--id",
                    _VULKAN_SDK_WINDOWS_ID,
                    "--exact",
                    "--silent",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                    "--disable-interactivity",
                ],
                check=False,
            )
            if result.returncode != 0:
                _warn(f"winget install exited with {result.returncode}; verifying the SDK anyway")
            root = _windows_vulkan_sdk_root()
    missing = _windows_vulkan_missing(root)
    if missing:
        raise SystemExit(
            "error: a Windows Vulkan source build needs missing: "
            + ", ".join(missing)
            + ". Install the LunarG Vulkan SDK from https://vulkan.lunarg.com/sdk/home#windows "
            + "(winget install --id KhronosGroup.VulkanSDK --exact, elevated) and re-run."
        )
    return _windows_vulkan_env(root)


# ── kllm package + UI ────────────────────────────────────────────────────────

# Console scripts the wheel RECORD deletes on force-reinstall.
_WINDOWS_LAUNCHERS = ("kllm.exe", "kllm-agent.exe")


def _park_destination(path: Path) -> Path:
    """Pick a free sibling name, deleting an unmapped previous park first."""
    candidates = (
        path.with_name(f"{path.name}.locked"),
        path.with_name(f"{path.name}.{os.getpid()}.locked"),
    )
    for candidate in candidates:
        if not candidate.exists():
            return candidate
        try:
            candidate.unlink()
        except OSError:
            continue
        return candidate
    return path.with_name(f"{path.name}.{os.getpid()}.extra.locked")


def _park_windows_launchers(scripts_dir: Path) -> set[str]:
    """Rename Windows console scripts so a reinstall can replace a running one.

    Returns launcher file names that stayed in place. ``kllm rebuild`` is the
    uv trampoline ``Scripts/kllm.exe``, and that trampoline keeps its own file
    open for the whole process. Windows error 32 then blocks both delete and
    rename. A normal image mapping can still be renamed; those launchers are
    moved aside so the wheel can write a new file at the original path.
    """
    busy: set[str] = set()
    if not _is_windows():
        return busy
    for name in _WINDOWS_LAUNCHERS:
        path = scripts_dir / name
        if not path.is_file():
            continue
        try:
            path.rename(_park_destination(path))
        except OSError as exc:
            if getattr(exc, "winerror", None) != 32:
                raise
            busy.add(name)
    return busy


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _launcher_script_names(launchers: set[str]) -> set[str]:
    return {name[:-4] if name.lower().endswith(".exe") else name for name in launchers}


def _strip_console_scripts(text: str, script_names: set[str]) -> str:
    lines = text.splitlines(keepends=True)
    stripped: list[str] = []
    in_scripts = False
    for line in lines:
        body = line.strip()
        if body.startswith("[") and body.endswith("]"):
            in_scripts = body == "[console_scripts]"
            stripped.append(line if line.endswith("\n") else line + "\n")
            continue
        if in_scripts and "=" in body:
            name = body.split("=", 1)[0].strip()
            if name in script_names:
                continue
        stripped.append(line if line.endswith("\n") else line + "\n")
    return "".join(stripped)


def _omit_locked_launchers_from_record(python: Path, launchers: set[str]) -> None:
    """Drop busy console scripts from the installed RECORD so uninstall skips them."""
    site = python.parent.parent / "Lib" / "site-packages"
    script_names = _launcher_script_names(launchers)
    for record in site.glob("kllm-*.dist-info/RECORD"):
        dist = record.parent
        entry = dist / "entry_points.txt"
        new_entry: bytes | None = None
        if entry.is_file():
            new_entry = _strip_console_scripts(
                entry.read_text(encoding="utf-8"), script_names
            ).encode("utf-8")
            entry.write_bytes(new_entry)
        kept: list[str] = []
        for line in record.read_text(encoding="utf-8").splitlines():
            path = line.split(",", 1)[0].replace("\\", "/")
            if Path(path).name in launchers:
                continue
            if new_entry is not None and path.replace("\\", "/").endswith("/entry_points.txt"):
                kept.append(f"{path},sha256={_record_hash(new_entry)},{len(new_entry)}")
                continue
            kept.append(line)
        record.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _wheel_omitting_console_scripts(wheel: Path, launchers: set[str]) -> Path:
    """Return a copy of ``wheel`` that does not reinstall busy console scripts."""
    script_names = _launcher_script_names(launchers)
    dest_dir = Path(tempfile.mkdtemp(prefix="kllm-wheel-"))
    dest = dest_dir / wheel.name
    with zipfile.ZipFile(wheel) as src:
        files = {info.filename: src.read(info.filename) for info in src.infolist()}
    entry_name = next(
        (name for name in files if name.endswith(".dist-info/entry_points.txt")),
        None,
    )
    if entry_name is not None:
        files[entry_name] = _strip_console_scripts(
            files[entry_name].decode("utf-8"), script_names
        ).encode("utf-8")
        record_name = next(
            (name for name in files if name.endswith(".dist-info/RECORD")),
            None,
        )
        if record_name is not None:
            updated: list[str] = []
            for line in files[record_name].decode("utf-8").splitlines():
                path = line.split(",", 1)[0]
                if path == entry_name:
                    data = files[entry_name]
                    updated.append(f"{path},sha256={_record_hash(data)},{len(data)}")
                else:
                    updated.append(line)
            files[record_name] = ("\n".join(updated) + "\n").encode("utf-8")
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, data in files.items():
            out.writestr(name, data)
    return dest


def _wheel_for_reinstall(python: Path, wheel: Path) -> Path:
    """Park replaceable launchers, and keep a busy trampoline out of the reinstall."""
    if not _is_windows():
        return wheel
    busy = _park_windows_launchers(python.parent)
    if not busy:
        return wheel
    _omit_locked_launchers_from_record(python, busy)
    return _wheel_omitting_console_scripts(wheel, busy)


def _stage_editable_extension(wheel: Path) -> None:
    """Keep uv's source-layout editable import on the just-built native module."""
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            path = Path(name)
            if (
                path.parent.as_posix() == "kllm"
                and path.name.startswith("_native.")
                and path.suffix in (".so", ".pyd")
            ):
                destination = ROOT / "src" / "kllm" / path.name
                temporary = destination.with_name(destination.name + ".new")
                temporary.write_bytes(archive.read(name))
                os.replace(temporary, destination)


def build_rust(python: Path, *, release: bool) -> None:
    ensure_rust()
    env = _venv_env(python)
    _sync_python_deps(python)

    wheels = ROOT / "target" / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    for old in wheels.glob("kllm-*.whl"):
        old.unlink(missing_ok=True)

    uv = _find_uv()
    maturin_prefix = (
        [uv, "run", "--python", str(python), "maturin"] if uv else [str(python), "-m", "maturin"]
    )
    maturin_cmd = [
        *maturin_prefix,
        "build",
        "--out",
        str(wheels),
        "--interpreter",
        str(python),
    ]
    if release:
        maturin_cmd.append("--release")
    _run(maturin_cmd, env=env)

    built = sorted(wheels.glob("kllm-*.whl"))
    if not built:
        raise SystemExit(f"error: maturin produced no wheel in {wheels}")
    _stage_editable_extension(built[-1])
    wheel = str(_wheel_for_reinstall(python, built[-1]))
    # --no-deps: only replace the kllm wheel / extension. Force-reinstalling
    # the full dependency tree races with running agents and can corrupt
    # packages like pydantic_core on Windows (locked .pyd / missing RECORD).
    if uv:
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--force-reinstall",
                "--no-deps",
                wheel,
            ],
            env=env,
        )
    else:
        _run(
            [str(python), "-m", "pip", "install", "--force-reinstall", "--no-deps", wheel],
            env=env,
        )

    # The privileged executable must not load Python or a user-writable Python
    # runtime. Build it independently of the extension's PyO3 features.
    helper_target = ROOT / "target" / "network-helper"
    helper_command = [
        "cargo",
        "build",
        "--manifest-path",
        str(ROOT / "crates" / "kllm-native" / "Cargo.toml"),
        "--no-default-features",
        "--features",
        "network-helper",
        "--bin",
        "kllm-network-helper",
        "--target-dir",
        str(helper_target),
    ]
    if release:
        helper_command.append("--release")
    _run(helper_command, env=env)
    helper_name = "kllm-network-helper.exe" if os.name == "nt" else "kllm-network-helper"
    helper_binary = helper_target / ("release" if release else "debug") / helper_name
    (ROOT / "vendor" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(helper_binary, ROOT / "vendor" / "bin" / helper_name)


def build_ui(*, skip_install: bool) -> None:
    ensure_node()
    npm = _which("npm")
    if not npm:
        raise SystemExit("error: npm not found")
    if not DASHBOARD.is_dir():
        raise SystemExit(f"error: dashboard directory missing: {DASHBOARD}")
    if not skip_install:
        _run([npm, "install"], cwd=DASHBOARD)
    _run([npm, "run", "build"], cwd=DASHBOARD)
    if not (DASHBOARD / "build").is_dir():
        raise SystemExit(f"error: dashboard build did not produce {DASHBOARD / 'build'}")


# ── kllm PATH shim ───────────────────────────────────────────────────────────

_PATH_SHIM_MARKER = "# kllm-install: user-local-bin"
_PATH_EXPORT_LINE = 'export PATH="$HOME/.local/bin:$PATH"'


def _user_local_bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def _kllm_shim_path(bin_dir: Path) -> Path:
    name = "kllm.cmd" if _is_windows() else "kllm"
    return bin_dir / name


def _path_entry_contains(path_env: str, directory: Path) -> bool:
    try:
        target = directory.resolve()
    except OSError:
        return False
    for part in path_env.split(os.pathsep):
        if not part:
            continue
        try:
            resolved = Path(part).resolve()
        except OSError:
            continue
        if _is_windows():
            if str(resolved).lower() == str(target).lower():
                return True
        elif resolved == target:
            return True
    return False


def _unix_shell_profile_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / ".zprofile",
        home / ".zshrc",
        home / ".bash_profile",
        home / ".bashrc",
    ]


def _profile_has_path_shim_marker(path: Path) -> bool:
    if not path.is_file():
        return False
    return _PATH_SHIM_MARKER in path.read_text(encoding="utf-8")


def _any_shell_profile_has_path_shim_marker() -> bool:
    return any(_profile_has_path_shim_marker(p) for p in _unix_shell_profile_candidates())


def _write_kllm_shim_unix(shim: Path, *, uv: str, repo_root: Path) -> None:
    uv_q = shlex.quote(str(Path(uv).resolve()))
    root_q = shlex.quote(str(repo_root.resolve()))
    body = f'#!/usr/bin/env sh\nexec {uv_q} run --project {root_q} kllm "$@"\n'
    shim.write_text(body, encoding="utf-8", newline="\n")
    _mark_executable(shim)


def _write_kllm_shim_windows(shim: Path, *, uv: str, repo_root: Path) -> None:
    uv_path = str(Path(uv).resolve())
    root_path = str(repo_root.resolve())
    body = f'@echo off\r\n"{uv_path}" run --project "{root_path}" kllm %*\r\n'
    shim.write_text(body, encoding="utf-8", newline="\r\n")


def install_kllm_path_shim(*, uv: str, repo_root: Path) -> Path:
    """Install a global `kllm` launcher that runs `uv run kllm` for this repo."""
    bin_dir = _user_local_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = _kllm_shim_path(bin_dir)
    if _is_windows():
        _write_kllm_shim_windows(shim, uv=uv, repo_root=repo_root)
    else:
        _write_kllm_shim_unix(shim, uv=uv, repo_root=repo_root)
    return shim


def _ensure_windows_user_path(bin_dir: Path) -> bool:
    """Append bin_dir to the Windows User PATH if missing. Returns True when updated."""
    directory = str(bin_dir.resolve())
    script = (
        "$dir = $args[0]\n"
        "$cur = [Environment]::GetEnvironmentVariable('Path', 'User')\n"
        "if ($null -eq $cur) { $cur = '' }\n"
        "foreach ($p in ($cur -split ';')) {\n"
        "  if ($p -and ($p -ieq $dir)) { exit 2 }\n"
        "}\n"
        "$joined = if ($cur.TrimEnd(';')) { $cur.TrimEnd(';') + ';' + $dir } else { $dir }\n"
        "[Environment]::SetEnvironmentVariable('Path', $joined, 'User')\n"
        "exit 0\n"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script, directory],
        capture_output=True,
        text=True,
    )
    if result.returncode == 2:
        return False
    if result.returncode != 0:
        _warn(
            f"Could not update Windows User PATH; add {directory} manually to use `kllm` globally."
        )
        return False
    return True


def ensure_user_local_bin_on_path(bin_dir: Path) -> bool:
    """Idempotently ensure ~/.local/bin is on the user's PATH. Returns True if modified."""
    resolved = bin_dir.resolve()
    if _is_windows():
        return _ensure_windows_user_path(resolved)

    if _any_shell_profile_has_path_shim_marker():
        return False
    if _path_entry_contains(os.environ.get("PATH", ""), resolved):
        return False

    candidates = _unix_shell_profile_candidates()
    target = next((p for p in candidates if p.is_file()), None)
    if target is None:
        shell = os.environ.get("SHELL", "")
        if _is_macos() or shell.endswith("/zsh"):
            target = Path.home() / ".zshrc"
        else:
            target = Path.home() / ".bashrc"

    block = f"\n{_PATH_SHIM_MARKER}\n{_PATH_EXPORT_LINE}\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return True


def install_kllm_cli_on_path() -> tuple[Path, bool]:
    uv = ensure_uv()
    bin_dir = _user_local_bin_dir()
    shim = install_kllm_path_shim(uv=uv, repo_root=ROOT)
    path_updated = ensure_user_local_bin_on_path(bin_dir)
    print(f"Installed `kllm` shim: {shim}")
    if path_updated:
        print(f"Added {bin_dir} to your shell PATH — open a new terminal to run `kllm` globally.")
    return bin_dir, path_updated


# ── WireGuard ────────────────────────────────────────────────────────────────


def wireguard_installed() -> bool:
    if _which("wg"):
        return True
    if _is_windows() and Path(r"C:\Program Files\WireGuard\wg.exe").is_file():
        return True
    return False


def install_wireguard() -> None:
    if wireguard_installed():
        print("WireGuard: OK")
        return
    print("Installing WireGuard…")
    try:
        if _is_windows():
            winget = _which("winget")
            if not winget:
                raise RuntimeError("winget not found")
            _run(
                [
                    winget,
                    "install",
                    "-e",
                    "--id",
                    "WireGuard.WireGuard",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ]
            )
        elif _is_macos():
            brew = _which("brew")
            if not brew:
                # Install brew first
                _run(
                    [
                        "bash",
                        "-lc",
                        '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                    ]
                )
                brew = _which("brew") or "/opt/homebrew/bin/brew"
            _run([brew, "install", "wireguard-tools"])
        elif _which("apt-get"):
            _run(["sudo", "apt-get", "update", "-y"], check=False)
            _run(["sudo", "apt-get", "install", "-y", "wireguard", "wireguard-tools"])
        elif _which("dnf"):
            _run(["sudo", "dnf", "install", "-y", "wireguard-tools"])
        elif _which("pacman"):
            _run(["sudo", "pacman", "-Sy", "--noconfirm", "wireguard-tools"])
        else:
            raise RuntimeError("no supported package manager for WireGuard")
    except Exception as exc:
        _warn(
            f"WireGuard install failed ({exc}). "
            "Overlay mode needs WireGuard from https://www.wireguard.com/install/"
        )
        return
    if wireguard_installed():
        print("WireGuard: installed")
    else:
        _warn("WireGuard package installed but `wg` not on PATH yet — open a new shell.")


# ── llama.cpp ────────────────────────────────────────────────────────────────


def detect_backend(preferred: str) -> str:
    if preferred != "auto":
        return preferred
    if _is_macos():
        return "metal"
    # Prefer CUDA if toolkit present, else Vulkan, else CPU
    if _which("nvcc") or Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit").exists():
        return "cuda"
    if _is_linux() and (_which("hipcc") or Path("/opt/rocm/bin/hipcc").exists()):
        return "rocm"
    if _is_windows() or _is_linux():
        return "vulkan"
    return "cpu"


def _cmake_backend_flags(backend: str) -> list[str]:
    return install_llama_source.cmake_backend_flags(backend, macos=_is_macos(), linux=_is_linux())


def clone_or_update_llama(tag: str | None = None) -> None:
    ensure_git()
    tag = tag or read_pinned_release()
    VENDOR_LLAMA.parent.mkdir(parents=True, exist_ok=True)
    if (VENDOR_LLAMA / ".git").is_dir():
        print(f"Checking out llama.cpp {tag} at {VENDOR_LLAMA}")
        _run(
            [
                "git",
                "-C",
                str(VENDOR_LLAMA),
                "fetch",
                "--depth",
                "1",
                "origin",
                f"refs/tags/{tag}:refs/tags/{tag}",
            ],
            check=False,
        )
        result = _run(["git", "-C", str(VENDOR_LLAMA), "checkout", "--force", tag], check=False)
        if result.returncode != 0:
            _run(
                ["git", "-C", str(VENDOR_LLAMA), "fetch", "--depth", "1", "origin", tag],
                check=False,
            )
            _run(["git", "-C", str(VENDOR_LLAMA), "checkout", "--force", "FETCH_HEAD"])
    elif VENDOR_LLAMA.is_dir() and any(VENDOR_LLAMA.iterdir()):
        print(f"Using existing llama.cpp tree at {VENDOR_LLAMA} (expected tag {tag})")
    else:
        print(f"Cloning llama.cpp {tag} → {VENDOR_LLAMA}")
        result = _run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                tag,
                "https://github.com/ggml-org/llama.cpp.git",
                str(VENDOR_LLAMA),
            ],
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(f"error: git clone of llama.cpp tag {tag} failed")


def _read_cmake_cache(build_dir: Path) -> dict[str, str]:
    return install_llama_source.read_cmake_cache(build_dir)


def _build_signature(tag: str, flags: list[str], env: dict[str, str] | None) -> str:
    """Identity of a build dir: tag, backend flags and toolchain paths."""
    return install_llama_source.build_signature(tag, flags, env)


def _read_build_signature(build_dir: Path) -> str | None:
    return install_llama_source.read_build_signature(build_dir)


def _write_build_signature(build_dir: Path, signature: str) -> None:
    install_llama_source.write_build_signature(build_dir, signature)


def _dangling_cached_paths(
    entries: dict[str, str], keys: tuple[str, ...] = install_llama_source._CACHE_PACKAGE_PATH_KEYS
) -> list[str]:
    """Absolute paths recorded in CMakeCache.txt that no longer exist."""
    return install_llama_source.dangling_cached_paths(entries, keys)


def _cache_reset_flags(entries: dict[str, str]) -> list[str]:
    """``-U`` flags that clear a half-resolved OpenSSL cache."""
    return install_llama_source.cache_reset_flags(entries)


def _discard_stale_cmake_build(
    source_dir: Path,
    build_dir: Path,
    signature: str | None = None,
) -> None:
    """Remove an installer-owned CMake build dir that would misconfigure."""
    install_llama_source.discard_stale_cmake_build(source_dir, build_dir, signature)


def build_llama_from_source(
    backend: str, tag: str | None = None, *, rpc_tcp_only: bool = False
) -> tuple[bool, str]:
    """Compile the pinned llama.cpp.

    Returns ``(ok, https)`` where ``https`` is ``"on"`` normally and ``"off"``
    when the last-resort configure fallback had to drop llama-server's embedded
    HTTPS server. The fallback is never latched: every run starts from upstream
    defaults, so the next run re-attempts HTTPS and restores it.
    """
    if not ensure_cmake():
        return False, "on"
    tag = tag or read_pinned_release()
    clone_or_update_llama(tag)
    build_dir = VENDOR_LLAMA / "build"
    flags = _cmake_backend_flags(backend)
    if rpc_tcp_only:
        flags.append("-DGGML_RPC_RDMA=OFF")
    cmake_env: dict[str, str] | None = None
    if _is_macos() and backend != "cpu":
        cmake_env = ensure_vulkan_macos()
        print("macOS source build: enabling Metal + Vulkan (CPU always available)")
    elif _is_windows() and backend == "vulkan":
        cmake_env = ensure_windows_vulkan()

    # Keep the signature independent of the HTTPS ladder so a run that had to
    # disable HTTPS still reuses this cache and re-attempts HTTPS next time.
    signature = _build_signature(tag, flags, cmake_env)
    _discard_stale_cmake_build(VENDOR_LLAMA, build_dir, signature)
    reset_flags = _cache_reset_flags(_read_cmake_cache(build_dir))
    return install_llama_source.compile_configured_tree(
        build_dir=build_dir,
        vendor=VENDOR_LLAMA,
        flags=flags,
        cmake_env=cmake_env,
        signature=signature,
        reset_flags=reset_flags,
        https_off_flag=_HTTPS_OFF_FLAG,
        rpc_names=_RPC_UPSTREAM_NAMES,
        run=_run,
        warn=_warn,
        write_signature=_write_build_signature,
        remove_binaries=_remove_stale_build_binaries,
        find_binary=_find_build_tree_binary,
        probe_backends=_probe_llama_backends,
        expected_stamp=stamp_backend_for_install(backend),
        backends_ok=backends_satisfied,
        parse_backends=parse_stamp_backends,
    )


def _remove_stale_build_binaries(build_dir: Path) -> None:
    """Drop previously built/prebuilt binaries so the next probe is honest."""
    for name in ("llama-server", *_RPC_UPSTREAM_NAMES):
        exe = f"{name}.exe" if _is_windows() else name
        for parent in (build_dir / "bin", build_dir / "bin" / "Release", build_dir):
            (parent / exe).unlink(missing_ok=True)


def _find_build_tree_binary(name: str) -> Path | None:
    """Locate a binary produced by the CMake build tree (never vendor/bin)."""
    for base in _upstream_binary_names(name):
        exe = f"{base}.exe" if _is_windows() else base
        for candidate in (
            VENDOR_LLAMA / "build" / "bin" / exe,
            VENDOR_LLAMA / "build" / "bin" / "Release" / exe,
            VENDOR_LLAMA / "build" / "Release" / "bin" / exe,
            VENDOR_LLAMA / "build" / exe,
        ):
            if candidate.is_file():
                return candidate
    return None


def _probe_llama_backends(binary: Path | None) -> list[str]:
    """Backends a llama-server reports via ``--list-devices``."""
    if binary is None:
        return []
    try:
        probe = subprocess.run(
            [str(binary), "--list-devices"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found = parse_list_devices_backends(probe.stdout + probe.stderr)
    if probe.returncode == 0 and not found:
        return ["cpu"]
    return found


def _upstream_binary_names(logical: str) -> tuple[str, ...]:
    if logical == "rpc-server":
        return _RPC_UPSTREAM_NAMES
    return (logical,)


def _resolve_runtime_binary(name: str) -> Path | None:
    """Resolve a binary the way kllm-native does when it launches one.

    Mirrors ``crates/kllm-native/src/process.rs``: ``vendor/bin`` wins over the
    CMake build tree. Verification has to probe this path, otherwise ``--check``
    can pass on a build-tree binary while the runtime launches a different one.
    """
    exe = f"{name}.exe" if _is_windows() else name
    staged = BIN_DIR / exe
    if staged.is_file():
        return staged
    return _find_build_tree_binary(name)


def pick_release_asset(assets: list[dict[str, Any]], backend: str) -> dict[str, Any] | None:
    """Pick the best llama.cpp release asset for this OS/arch/backend."""
    machine = platform.machine().lower()
    return install_llama_release.pick_best_asset(
        assets,
        backend=backend,
        windows=_is_windows(),
        macos=_is_macos(),
        linux=_is_linux(),
        arm=machine in ("arm64", "aarch64"),
    )


def _asset_looks_like_bin_pack(name: str) -> bool:
    n = name.lower()
    if not (n.endswith(".zip") or n.endswith(".tar.gz")):
        return False
    return bool(
        re.match(r"llama-b\d+-bin-", name)
        or "bin-win" in n
        or "bin-ubuntu" in n
        or "bin-macos" in n
        or "bin-linux" in n
        or ("bin" in n and "llama" in n)
    )


def release_has_bin_assets(assets: list[dict[str, Any]]) -> bool:
    return any(_asset_looks_like_bin_pack(a.get("name", "")) for a in assets)


def _refuse_unstable_pin(tag: str, rel: dict[str, Any]) -> None:
    if rel.get("draft") or rel.get("prerelease"):
        raise SystemExit(
            f"error: llama.cpp {tag} is a GitHub pre-release or draft. "
            "Pin a stable vX.Y.Z tag (e.g. v0.5.0), not a bNNNN nightly."
        )


def resolve_nightly_tag(assets: list[dict[str, Any]]) -> str | None:
    """Read nightly-tag.txt from a stable release's assets, if present."""
    for asset in assets:
        if asset.get("name") != "nightly-tag.txt":
            continue
        tag_url = asset.get("browser_download_url")
        if not tag_url:
            continue
        try:
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "nightly-tag.txt"
                _download(tag_url, path)
                tag = path.read_text(encoding="utf-8").strip()
            return tag or None
        except Exception as exc:
            _warn(f"nightly-tag resolve failed ({exc})")
            return None
    return None


def _fetch_release_json(tag: str) -> dict[str, Any] | None:
    try:
        rel = _http_json(f"{LLAMA_API}/tags/{tag}")
    except Exception as exc:
        _warn(f"GitHub release {tag} fetch failed ({exc})")
        return None
    if not isinstance(rel, dict):
        return None
    return rel


def fetch_release_by_tag(tag: str, *, require_stable: bool = True) -> dict[str, Any] | None:
    """Fetch one ggml-org/llama.cpp GitHub release by tag (e.g. v0.5.0).

    Stable semver releases often ship only nightly-tag.txt; when so, follow that
    pointer to the associated nightly for binary assets. The caller's pin/stamp
    remains ``tag`` (not the nightly).
    """
    rel = _fetch_release_json(tag)
    if not rel:
        return None
    if require_stable:
        _refuse_unstable_pin(tag, rel)
    assets = rel.get("assets") or []
    if release_has_bin_assets(assets):
        return rel
    nightly = resolve_nightly_tag(assets)
    if nightly:
        print(f"Stable {tag} uses nightly build {nightly} for prebuilt binaries")
        bin_rel = _fetch_release_json(nightly)
        if bin_rel and (bin_rel.get("assets") or []):
            return bin_rel
        _warn(f"nightly {nightly} for {tag} has no usable assets")
        return None
    if not assets:
        _warn(f"GitHub release {tag} has no assets")
        return None
    return rel


def fetch_latest_stable_release() -> dict[str, Any] | None:
    """Find the newest non-draft, non-prerelease release with installable binaries."""
    try:
        releases = _http_json(f"{LLAMA_API}?per_page=30")
    except Exception as exc:
        _warn(f"GitHub releases fetch failed ({exc})")
        return None
    if not isinstance(releases, list):
        return None
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name") or ""
        assets = rel.get("assets") or []
        if release_has_bin_assets(assets):
            return rel
        nightly = resolve_nightly_tag(assets)
        if not nightly:
            continue
        bin_rel = _fetch_release_json(nightly)
        if bin_rel and release_has_bin_assets(bin_rel.get("assets") or []):
            print(f"Latest stable {tag} uses nightly build {nightly} for prebuilt binaries")
            return bin_rel
    return None


def assert_stable_llama_pin(tag: str) -> None:
    """Refuse draft/prerelease pins before install (stable vX.Y.Z only)."""
    if re.fullmatch(r"b\d+", tag):
        raise SystemExit(
            f"error: llama.cpp pin {tag!r} is a nightly (bNNNN). "
            "Pin a stable vX.Y.Z tag (e.g. v0.5.0)."
        )
    rel = _fetch_release_json(tag)
    if rel is None:
        # Offline / API failure: still block obvious nightlies; allow v* through.
        return
    _refuse_unstable_pin(tag, rel)


def install_llama_prebuilt(
    backend: str,
    tag: str | None = None,
    *,
    stamp_backend: str | None = None,
) -> bool:
    """Install a GitHub release pack.

    ``stamp_backend`` records what the pack actually provides. macOS packs are
    Metal-only, so they must stamp ``metal`` and not the resolved
    ``metal+vulkan`` default.
    """
    tag = tag or read_pinned_release()
    local = _cached_llama_archive(tag, backend)
    if local is not None:
        return _install_llama_archive(local, tag=tag, backend=backend, stamp_backend=stamp_backend)

    rel = fetch_release_by_tag(tag)
    if not rel:
        return False
    assets = rel.get("assets") or []
    asset = pick_release_asset(assets, backend)
    if not asset:
        # Fall back to CPU pack
        asset = pick_release_asset(assets, "cpu")
    if not asset:
        _warn(f"no matching binary asset in release {rel.get('tag_name')}")
        return False

    url = asset["browser_download_url"]
    name = asset["name"]
    size_raw = asset.get("size")
    expected_size: int | None = None
    if isinstance(size_raw, int):
        expected_size = size_raw
    elif isinstance(size_raw, str) and size_raw.isdigit():
        expected_size = int(size_raw)
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / name
        _download(
            url,
            archive,
            expected_size=expected_size,
            expected_kind=_expected_archive_kind(name),
        )
        return _install_llama_archive(
            archive, tag=tag, backend=backend, asset_name=name, stamp_backend=stamp_backend
        )


def _cached_llama_archive(tag: str, backend: str) -> Path | None:
    root = Path.home() / ".kllm" / "models"
    if not root.is_dir():
        return None
    files = [p for p in root.glob(f"llama-{tag}-bin-*") if p.is_file()]
    if not files:
        return None
    assets = [{"name": p.name, "browser_download_url": str(p)} for p in files]
    picked = pick_release_asset(assets, backend)
    if not picked:
        return None
    return Path(picked["browser_download_url"])


def _install_llama_archive(
    archive: Path,
    *,
    tag: str,
    backend: str,
    asset_name: str | None = None,
    stamp_backend: str | None = None,
) -> bool:
    name = asset_name or archive.name
    kind = _expected_archive_kind(name)
    sniffed = _sniff_archive_kind(archive)
    if kind and sniffed != kind:
        preview = archive.read_bytes()[:120] if archive.is_file() else b""
        _warn(
            f"archive {name} is not a valid {kind} "
            f"(size={archive.stat().st_size if archive.is_file() else 0}, magic={preview!r})"
        )
        return False
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        extract_dir = Path(td) / "out"
        extract_dir.mkdir()
        try:
            if name.endswith(".zip"):
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(extract_dir)
            else:
                with tarfile.open(archive, "r:*") as tf:
                    # ``data`` is the safe default and what Python 3.14 will
                    # require; packs only contain relative in-tree links.
                    tf.extractall(extract_dir, filter="data")
        except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
            preview = archive.read_bytes()[:120] if archive.is_file() else b""
            _warn(
                f"failed to extract {name} ({exc}); "
                f"size={archive.stat().st_size if archive.is_file() else 0}, magic={preview!r}"
            )
            return False
        copied = _copy_llama_binaries_from(extract_dir)
        if not copied:
            _warn(f"archive {name} extracted but llama-server/rpc-server not found inside")
            return False
    write_install_stamp(
        tag=tag,
        backend=stamp_backend or stamp_backend_for_install(backend),
        asset=name,
        # GitHub release packs are upstream builds with HTTPS support compiled in.
        https="on",
    )
    print(f"Installed llama.cpp {tag} from {name}")
    return True


def _copy_shared_libs(from_dir: Path, to_dir: Path) -> None:
    to_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for pattern in _SHARED_LIB_PATTERNS:
        for lib in from_dir.glob(pattern):
            if not lib.is_file() or lib.name in seen:
                continue
            seen.add(lib.name)
            shutil.copy2(lib, to_dir / lib.name)


def _mark_executable(path: Path) -> None:
    if _is_windows() or not path.is_file():
        return
    path.chmod(path.stat().st_mode | 0o111)


def _copy_llama_binaries_from(root: Path) -> bool:
    """Copy llama-server and rpc-server (and sibling shared libs) into vendor/bin."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    names = ["llama-server", "rpc-server"]
    found_any = False
    for base in names:
        exe = f"{base}.exe" if _is_windows() else base
        matches = list(root.rglob(exe))
        if not matches and base == "rpc-server":
            alt = "ggml-rpc-server.exe" if _is_windows() else "ggml-rpc-server"
            matches = list(root.rglob(alt))
        if not matches:
            continue
        src = matches[0]
        dest = BIN_DIR / exe
        shutil.copy2(src, dest)
        _mark_executable(dest)
        found_any = True
        print(f"  -> {dest}")
        _copy_shared_libs(src.parent, BIN_DIR)
    # Deliberately never stage into vendor/llama.cpp/build/bin: that is the
    # CMake output dir, and a prebuilt binary left there makes an unrelated,
    # failed source build look successful (and poisons the build cache).
    return found_any


def stage_built_binaries() -> None:
    """If we built from source, copy runtime binaries into vendor/bin."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("llama-server", "rpc-server"):
        src = _find_build_tree_binary(name)
        if not src:
            continue
        staged = f"{name}.exe" if _is_windows() else name
        dest = BIN_DIR / staged
        shutil.copy2(src, dest)
        _mark_executable(dest)
        print(f"  staged {dest}")
        _copy_shared_libs(src.parent, BIN_DIR)


def install_llama(
    backend: str,
    *,
    prefer_prebuilt: bool,
    force: bool = False,
    tag: str | None = None,
    expected: str | None = None,
    rpc_tcp_only: bool = False,
) -> None:
    backend = detect_backend(backend)
    stamp_be = expected or stamp_backend_for_install(backend)
    tag = tag or read_pinned_release()
    assert_stable_llama_pin(tag)
    print(f"llama.cpp backend: {backend}" + (f" (stamp {stamp_be})" if stamp_be != backend else ""))
    print(f"llama.cpp pin:     {tag}")

    llama = _resolve_runtime_binary("llama-server")
    rpc = _resolve_runtime_binary("rpc-server")
    if (
        llama
        and rpc
        and not force
        and not rpc_tcp_only
        and stamp_matches(tag, stamp_be)
        and llama_install_status(tag, expected=stamp_be)["ok"]
    ):
        print(f"llama-server: {llama}")
        print(f"rpc-server:   {rpc}")
        print(f"Already at pinned {tag}")
        return

    if llama and rpc and not force:
        current = read_install_stamp().get("tag") or "unknown"
        print(f"Installed llama.cpp is {current}; upgrading to pin {tag}")

    # Invalidate old proof before staging another runtime. This never asserts
    # that an upstream release archive was compiled with TCP-only transport.
    (BIN_DIR / "rpc-transport.json").unlink(missing_ok=True)
    if rpc_tcp_only:
        prefer_prebuilt = False

    ok = False
    built_from_source = False
    https_state = "on"
    macos_multi = _is_macos() and backend != "cpu"

    if macos_multi:
        # macOS must ship Metal + Vulkan in one binary, so there is no prebuilt
        # path: --prefer-prebuilt is rejected before reaching here.
        ok, https_state = build_llama_from_source(
            backend, tag=tag, **({"rpc_tcp_only": True} if rpc_tcp_only else {})
        )
        built_from_source = ok
        if not ok:
            # Never downgrade the advertised Metal+Vulkan contract to a
            # Metal-only pack; that is how a broken Vulkan build went unnoticed
            # while --check reported "ok".
            raise SystemExit(
                "error: macOS Metal+Vulkan source build failed (see the cmake output "
                "above). Fix the build, or use --backend cpu for a CPU-only install."
            )
    elif prefer_prebuilt:
        ok = install_llama_prebuilt(backend, tag=tag, stamp_backend=stamp_be)
        if not ok:
            ok, https_state = build_llama_from_source(
                backend, tag=tag, **({"rpc_tcp_only": True} if rpc_tcp_only else {})
            )
            built_from_source = ok
    else:
        ok, https_state = build_llama_from_source(
            backend, tag=tag, **({"rpc_tcp_only": True} if rpc_tcp_only else {})
        )
        built_from_source = ok
        if not ok and not rpc_tcp_only:
            ok = install_llama_prebuilt(backend, tag=tag, stamp_backend=stamp_be)

    if rpc_tcp_only and not built_from_source:
        raise SystemExit(
            "error: TCP-only RPC source build failed; no prebuilt fallback is eligible"
        )
    stage_built_binaries()
    llama = _resolve_runtime_binary("llama-server")
    rpc = _resolve_runtime_binary("rpc-server")
    if not llama:
        raise SystemExit(
            "error: llama-server not available after install. "
            "Build manually with -DGGML_RPC=ON or set KLLM_LLAMA_SERVER."
        )
    # Enforce the macOS contract on the binary the runtime will actually launch
    # (vendor/bin first), not on the build tree.
    if macos_multi:
        probed = _probe_llama_backends(llama)
        if not backends_satisfied(stamp_be, probed):
            raise SystemExit(
                f"error: {llama} reports backends {probed!r}, expected "
                f"{parse_stamp_backends(stamp_be)!r}. macOS must provide Metal and "
                "Vulkan in one binary."
            )
    if not rpc:
        _warn(
            "rpc-server binary missing. Workers need it — rebuild with -DGGML_RPC=ON "
            "or set KLLM_RPC_SERVER. Prebuilt packs sometimes omit it."
        )
    else:
        print(f"llama-server: {llama}")
        print(f"rpc-server:   {rpc}")
    if built_from_source and llama and rpc:
        stamp = read_install_stamp()
        if not stamp_matches(tag, stamp_be) or stamp.get("https") != https_state:
            write_install_stamp(tag=tag, backend=stamp_be, asset="source", https=https_state)
    if built_from_source and llama and rpc:
        _write_rpc_transport_evidence(tag)


def _write_rpc_transport_evidence(tag: str) -> None:
    """Bind CMake transport flags to the exact staged binaries and shared libs."""
    import hashlib
    import json

    def sha(path: Path) -> str:
        result = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                result.update(chunk)
        return result.hexdigest()

    cache_path = VENDOR_LLAMA / "build" / "CMakeCache.txt"
    if not cache_path.is_file():
        return
    text = cache_path.read_text(encoding="utf-8")
    rpc_flag = re.search(r"^GGML_RPC:BOOL=(ON|OFF)$", text, re.M)
    rdma_flag = re.search(r"^GGML_RPC_RDMA:BOOL=(ON|OFF)$", text, re.M)
    artifacts = {}
    for name in ("llama-server", "rpc-server"):
        built = _find_build_tree_binary(name)
        staged = BIN_DIR / (name + (".exe" if _is_windows() else ""))
        if built is None or not staged.is_file() or sha(built) != sha(staged):
            return
        artifacts[staged.name] = sha(staged)
    for path in BIN_DIR.iterdir():
        if (
            path.is_file()
            and ("ggml" in path.name or "llama" in path.name)
            and path.suffix in (".dll", ".so", ".dylib")
        ):
            artifacts[path.name] = sha(path)
    import time

    evidence = {
        "installed_at": time.time(),
        "schema": 1,
        "tag": tag,
        "source_build": True,
        "ggml_rpc": rpc_flag.group(1) if rpc_flag else "unknown",
        "ggml_rpc_rdma": rdma_flag.group(1) if rdma_flag else "unknown",
        "artifacts": artifacts,
    }
    destination = BIN_DIR / "rpc-transport.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    os.replace(temporary, destination)


# ── CLI ──────────────────────────────────────────────────────────────────────


def expected_stamp_for_default(backend: str = "auto") -> str | None:
    """Stamp the platform-default install should produce (None = trust stamp).

    Only macOS has a default stronger than its stamp: the default install
    promises Metal *and* Vulkan in one binary. Everywhere else the pinned
    release is a single backend, so the recorded stamp is authoritative.

    This is a query, not a validation: it must never raise, because callers
    (e.g. the join-time pack check) run inside request handlers where an
    escaping ``SystemExit`` would take the agent down. A backend that macOS
    cannot install simply has no contract to assert.
    """
    if not _is_macos():
        return None
    resolved = detect_backend(backend)
    if resolved in ("cuda", "rocm"):
        return None
    return stamp_backend_for_install(resolved)


def _print_llama_mismatch_hint(status: dict[str, Any]) -> None:
    wanted = parse_stamp_backends(status["expected_backend"])
    probed = status["actual_backends"]
    if status["stamp"] != status["pin"]:
        print("hint:         stamp/pin differ - run python scripts/install.py --llama-only --force")
        return
    if not backends_satisfied(status["expected_backend"], probed):
        missing = [b for b in wanted if b not in probed]
        print(f"hint:         binary is missing {missing!r} (probed {probed!r})")
        if _is_macos() and "vulkan" in wanted:
            print(
                "hint:         Vulkan needs MoltenVK - check "
                "`brew install molten-vk vulkan-headers vulkan-loader shaderc spirv-headers`, "
                "then re-run python scripts/install.py --llama-only --from-source --force"
            )
        return
    print("hint:         run python scripts/install.py --llama-only --force")


def print_llama_check(tag: str, *, expected: str | None = None) -> int:
    status = llama_install_status(tag, expected=expected)
    print(f"pin:          {status['pin']}")
    print(f"installed:    {status['stamp'] or '(no stamp)'}")
    print(f"llama-server: {status['llama_server'] or 'MISSING'}")
    print(f"rpc-server:   {status['rpc_server'] or 'MISSING'}")
    print(
        "backends:     "
        f"requested={status['requested_backend'] or '(none)'} "
        f"probed={','.join(status['actual_backends']) or '(none)'}"
    )
    if expected and expected != status["requested_backend"]:
        print(f"expected:     {expected}")
    https = status.get("https")
    if https:
        suffix = (
            ""
            if https == "on"
            else "  (last-resort fallback - re-run --llama-only --from-source --force to restore)"
        )
        print(f"https:        {https}{suffix}")
    if status["ok"]:
        print("status:       ok")
        return 0
    print("status:       mismatch")
    _print_llama_mismatch_hint(status)
    return 1


def verify_llama_installed(tag: str, *, expected: str | None = None) -> None:
    status = llama_install_status(tag, expected=expected)
    if status["ok"]:
        return
    if status["stamp"] == tag and not backends_satisfied(
        status["expected_backend"], status["actual_backends"]
    ):
        raise SystemExit(
            f"error: llama.cpp {tag} stamp ok but backend mismatch "
            f"(expected={status['expected_backend']!r}, probed={status['actual_backends']!r}). "
            "Retry with --force or --from-source."
        )
    raise SystemExit(
        f"error: llama.cpp {tag} is not installed (stamp={status['stamp']!r}). "
        "Retry with --force or --from-source."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Install Kluster LLM and all runtime dependencies.")
    p.add_argument("--source-repo", help="Public HTTPS Git source for an isolated managed runtime")
    p.add_argument("--revision", help="Full immutable Git commit for --source-repo")
    p.add_argument("--engine", choices=("llamacpp", "primacpp"), default="llamacpp",
                   help="Managed inference engine; bootstrap continues to use llama.cpp")
    p.add_argument("--recipe-digest", help="Resolved Prima compatibility/build recipe SHA-256")
    p.add_argument(
        "--runtime-dir", type=Path, help="Isolated destination; never replaces vendor/bin"
    )
    p.add_argument("--skip-ui", action="store_true", help="Skip dashboard npm build")
    p.add_argument(
        "--rpc-tcp-only",
        action="store_true",
        help="Source-build RPC with RDMA disabled for managed private networks",
    )
    p.add_argument("--skip-rust", action="store_true", help="Skip maturin / Python package build")
    p.add_argument(
        "--skip-npm-install", action="store_true", help="Skip `npm install` (build only)"
    )
    p.add_argument("--skip-llama", action="store_true", help="Skip llama-server / rpc-server")
    p.add_argument("--skip-wireguard", action="store_true", help="Skip WireGuard install")
    p.add_argument(
        "--skip-toolchains", action="store_true", help="Do not auto-install Rust/Node/git/cmake"
    )
    p.add_argument("--release", action="store_true", help="Build Rust extension in release mode")
    p.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "metal", "cuda", "rocm", "vulkan", "cpu"],
        help="llama.cpp accelerator backend (default: auto-detect)",
    )
    p.add_argument(
        "--prefer-prebuilt",
        action="store_true",
        help=(
            "Prefer GitHub release binaries (default on non-macOS). Rejected on macOS, "
            "where the install must build Metal+Vulkan in one binary"
        ),
    )
    p.add_argument(
        "--from-source",
        action="store_true",
        help="Compile the pinned llama.cpp tag (macOS default: Metal+Vulkan multi-backend)",
    )
    p.add_argument(
        "--prebuilt",
        action="store_true",
        help=(
            "Managed mode: install a matching prebuilt GitHub release pack instead of "
            "compiling. Ineligible or failing packs exit with a marker so the caller can "
            "fall back to --from-source."
        ),
    )
    p.add_argument(
        "--tag",
        default=None,
        help="Override the llama.cpp tag in llama-release (e.g. v0.5.0; stable only)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Reinstall llama.cpp even when vendor/bin/llama-release.txt matches the pin",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Print pin vs installed stamp and exit 0 only when they match",
    )
    p.add_argument(
        "--llama-only",
        action="store_true",
        help="Install only llama-server / rpc-server (pinned in llama-release)",
    )
    p.add_argument(
        "--skip-path-shim",
        action="store_true",
        help="Do not install a global `kllm` shim or update PATH",
    )
    p.add_argument(
        "--sync",
        action="store_true",
        help="Run install stages one after another in this process",
    )
    return p.parse_args(argv)


def _prefer_llama_prebuilt(args: argparse.Namespace, resolved_backend: str) -> bool:
    """Whether this install should download a GitHub pack instead of compiling."""
    macos_gpu = _is_macos() and resolved_backend != "cpu"
    if macos_gpu and args.prefer_prebuilt:
        raise SystemExit(
            "error: --prefer-prebuilt is not available on macOS: GitHub macOS packs "
            "are Metal-only and macOS installs must provide Metal+Vulkan. Drop "
            "--prefer-prebuilt, or use --backend cpu for a CPU-only install."
        )
    if args.from_source:
        return False
    if args.prefer_prebuilt:
        return True
    if macos_gpu:
        return False
    return True


def _run_install_llama_stage(
    backend: str,
    *,
    prefer_prebuilt: bool,
    force: bool,
    tag: str,
    expected: str | None,
    rpc_tcp_only: bool,
) -> None:
    install_llama(
        backend,
        prefer_prebuilt=prefer_prebuilt,
        force=force,
        tag=tag,
        expected=expected,
        rpc_tcp_only=rpc_tcp_only,
    )


def _record_stage_error(name: str, exc: BaseException) -> str:
    detail = str(exc) or type(exc).__name__
    _warn(f"{name} failed ({detail})")
    return f"{name}: {detail}"


def _run_stages(
    jobs: list[tuple[str, Any, tuple[Any, ...], dict[str, Any]]],
    *,
    sync: bool = False,
) -> list[str]:
    errors: list[str] = []
    if sync:
        for name, fn, args, kwargs in jobs:
            try:
                fn(*args, **kwargs)
            except (Exception, SystemExit) as exc:
                errors.append(_record_stage_error(name, exc))
        return errors
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn, *args, **kwargs): name for name, fn, args, kwargs in jobs}
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
            except (Exception, SystemExit) as exc:
                errors.append(_record_stage_error(name, exc))
    return errors


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.engine == "primacpp" and not all((args.source_repo, args.revision, args.runtime_dir)):
        raise SystemExit("Prima requires isolated managed source arguments; see docs/agent-install.md")
    if args.engine == "llamacpp" and args.recipe_digest:
        raise SystemExit("--recipe-digest is only valid for Prima managed builds")
    if any((args.source_repo, args.revision, args.runtime_dir)):
        if (
            not all((args.source_repo, args.revision, args.runtime_dir))
            or not args.llama_only
            or args.check
        ):
            raise SystemExit(
                "Managed builds require --llama-only, --source-repo, --revision and --runtime-dir"
            )
        if args.prebuilt and args.from_source:
            raise SystemExit("error: choose either --prebuilt or --from-source, not both")
        if args.prebuilt and args.rpc_tcp_only:
            raise SystemExit(
                "error: --rpc-tcp-only requires a source build; prebuilt packs carry no "
                "TCP-only RPC evidence"
            )
        sys.path.insert(0, str(ROOT / "src"))
        if args.engine == "primacpp":
            if args.prebuilt or args.rpc_tcp_only or not args.recipe_digest:
                raise SystemExit("Prima requires --recipe-digest and source mode; RPC/prebuilt mode is incompatible")
            from kllm.modules.primacpp.interactors.build import managed_prima_build

            try:
                managed_prima_build(sys.modules[__name__], args.source_repo, args.revision,
                                    args.runtime_dir, args.backend, args.recipe_digest)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                print(f"error: {error}", file=sys.stderr)
                return 1
            return 0
        if args.prebuilt:
            from kllm.runtimes.build import PrebuiltIneligible, managed_prebuilt

            try:
                managed_prebuilt(
                    sys.modules[__name__],
                    args.source_repo,
                    args.revision,
                    args.runtime_dir,
                    args.backend,
                )
            except PrebuiltIneligible as error:
                # The registry treats any nonzero prebuilt exit as a signal to
                # compile from source; the marker keeps the log honest.
                print(f"KLLM_PREBUILT_INELIGIBLE: {error}", file=sys.stderr)
                return 3
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                print(f"error: {error}", file=sys.stderr)
                return 1
            return 0
        from kllm.runtimes.build import managed_build

        try:
            managed_build(
                sys.modules[__name__],
                args.source_repo,
                args.revision,
                args.runtime_dir,
                args.backend,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        return 0
    tag = read_pinned_release(args.tag)

    if args.llama_only:
        args.skip_rust = True
        args.skip_ui = True
        args.skip_wireguard = True
        args.skip_toolchains = True

    if args.check:
        return print_llama_check(tag, expected=expected_stamp_for_default(args.backend))

    print(f"Repo root: {ROOT}")
    print(f"Python:    {sys.version.split()[0]} ({sys.executable})")
    print(f"Platform:  {platform.system()} {platform.machine()}")

    if not args.skip_toolchains:
        ensure_git()

    python: Path | None = None
    if not args.skip_rust:
        python = ensure_venv()
        if not args.skip_toolchains:
            ensure_rust()

    if not args.skip_ui:
        if not args.skip_toolchains:
            ensure_node()

    # Keep system package installs out of worker processes so package managers
    # (brew/apt/winget) cannot contend with each other.
    if not args.skip_wireguard:
        print("\n=== WireGuard ===")
        install_wireguard()

    jobs: list[tuple[str, Any, tuple[Any, ...], dict[str, Any]]] = []
    if not args.skip_rust:
        assert python is not None
        print("\n=== kllm (Rust + Python) ===")
        jobs.append(
            ("kllm (Rust + Python)", build_rust, (python,), {"release": args.release}),
        )
    if not args.skip_ui:
        print("\n=== Dashboard (Svelte) ===")
        jobs.append(
            ("Dashboard (Svelte)", build_ui, (), {"skip_install": args.skip_npm_install}),
        )

    if not args.skip_llama:
        print("\n=== llama.cpp (llama-server + rpc-server) ===")
        if args.llama_only:
            print(f"Pin file:  {PIN_FILE} ({tag})")
        # macOS GPU installs must source-build Metal+Vulkan; there is no prebuilt
        # path for them (see the rejection above). Elsewhere prefer prebuilt
        # unless --from-source was asked for.
        # (--prefer-prebuilt still applies to `--backend cpu` on macOS.)
        resolved_backend = detect_backend(args.backend)
        prefer_prebuilt = _prefer_llama_prebuilt(args, resolved_backend)
        expected_stamp = stamp_backend_for_install(resolved_backend)

        jobs.append(
            (
                "llama.cpp (llama-server + rpc-server)",
                _run_install_llama_stage,
                (args.backend,),
                {
                    "prefer_prebuilt": prefer_prebuilt,
                    "force": args.force,
                    "tag": tag,
                    "expected": expected_stamp,
                    "rpc_tcp_only": args.rpc_tcp_only,
                },
            ),
        )

    errors = _run_stages(jobs, sync=args.sync)

    if errors:
        print("\nInstall failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    if not args.skip_llama:
        verify_llama_installed(tag, expected=expected_stamp)
        if args.llama_only:
            print(f"llama.cpp {tag} ready.")

    path_updated = False
    if not args.skip_rust and not args.skip_path_shim:
        print("\n=== kllm CLI on PATH ===")
        _, path_updated = install_kllm_cli_on_path()

    print("\nInstall complete.")
    if not args.skip_rust:
        activate = r".\.venv\Scripts\Activate.ps1" if _is_windows() else "source .venv/bin/activate"
        print(f"Activate the venv with: {activate}")
        if args.skip_path_shim:
            print("Run: uv run kllm start   |   uvx --from . kllm join --key <key>")
        else:
            if path_updated:
                print("After opening a new terminal: kllm start   |   kllm join --key <key>")
            else:
                print("Run: kllm start   |   kllm join --key <key>")
            print("(Same as: uv run kllm … from the repo root.)")
            print(
                "Note: the global shim points at this checkout; re-run install after moving the repo."
            )
    if not args.skip_ui:
        print("Dashboard: kllm ui")
    if not args.skip_wireguard:
        print("Optional overlay: kllm start --overlay wireguard")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as exc:
        print(f"\nerror: network failure: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except subprocess.CalledProcessError as exc:
        print(f"\nerror: command failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from None
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130) from None
