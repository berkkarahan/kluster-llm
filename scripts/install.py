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

This is the one-shot installer. ``scripts/build.py`` remains a thinner
CLI/dashboard-only build (invokes the same helpers with llama/WG skipped).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
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

ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"
DASHBOARD = ROOT / "dashboard"
VENDOR_LLAMA = ROOT / "vendor" / "llama.cpp"
BIN_DIR = ROOT / "vendor" / "bin"
WORKER_WIN = ROOT / "worker-windows"
WORKER_WIN_SRC = WORKER_WIN / "src"
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
            [str(python), "-c", "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)"],
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
    ctxs = [ssl.create_default_context()]
    try:
        import certifi

        ctxs.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    return ctxs


def _curl_bytes(url: str) -> bytes:
    curl = _which("curl")
    if not curl:
        raise RuntimeError("curl not found")
    result = subprocess.run(
        [
            curl,
            "-fsSL",
            "-A",
            GITHUB_HEADERS["User-Agent"],
            "-H",
            f"Accept: {GITHUB_HEADERS['Accept']}",
            url,
        ],
        check=True,
        capture_output=True,
    )
    return result.stdout


def _curl_download(url: str, dest: Path) -> None:
    curl = _which("curl")
    if not curl:
        raise RuntimeError("curl not found")
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            curl,
            "-fsSL",
            "-A",
            DOWNLOAD_HEADERS["User-Agent"],
            "-H",
            f"Accept: {DOWNLOAD_HEADERS['Accept']}",
            "-o",
            str(dest),
            url,
        ],
        check=True,
    )


def _http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=GITHUB_HEADERS)
    errors: list[BaseException] = []
    for ctx in _ssl_contexts():
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            errors.append(exc)
    try:
        return json.loads(_curl_bytes(url).decode("utf-8"))
    except Exception as exc:
        errors.append(exc)
    raise errors[-1]


def _sniff_archive_kind(path: Path) -> str | None:
    """Return 'zip', 'gzip', or 'tar' when magic bytes look like an archive."""
    try:
        with path.open("rb") as f:
            head = f.read(512)
    except OSError:
        return None
    if head.startswith(_ZIP_MAGIC):
        return "zip"
    if head.startswith(_GZIP_MAGIC):
        return "gzip"
    if len(head) > _TAR_MAGIC_OFFSET + 5 and head[_TAR_MAGIC_OFFSET : _TAR_MAGIC_OFFSET + 5] == _TAR_MAGIC:
        return "tar"
    return None


def _expected_archive_kind(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return "gzip"
    if lower.endswith(".tar"):
        return "tar"
    return None


def _validate_downloaded_file(
    dest: Path,
    *,
    expected_size: int | None = None,
    expected_kind: str | None = None,
    content_length: int | None = None,
) -> None:
    if not dest.is_file():
        raise RuntimeError(f"download missing: {dest}")
    size = dest.stat().st_size
    if size <= 0:
        raise RuntimeError(f"download empty: {dest}")
    if content_length is not None and content_length > 0 and size != content_length:
        raise RuntimeError(
            f"download truncated: got {size} bytes, Content-Length was {content_length}"
        )
    if expected_size is not None and expected_size > 0 and size != expected_size:
        raise RuntimeError(
            f"download size mismatch: got {size} bytes, expected {expected_size}"
        )
    if expected_kind is None:
        return
    kind = _sniff_archive_kind(dest)
    if kind != expected_kind:
        preview = dest.read_bytes()[:120]
        raise RuntimeError(
            f"download is not a valid {expected_kind} archive "
            f"(got {size} bytes, magic={preview!r})"
        )


def _download_once(
    url: str,
    dest: Path,
    *,
    expected_size: int | None = None,
    expected_kind: str | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=DOWNLOAD_HEADERS)
    errors: list[BaseException] = []
    for ctx in _ssl_contexts():
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=600) as resp, dest.open("wb") as out:
                cl_raw = resp.headers.get("Content-Length")
                content_length = int(cl_raw) if cl_raw and cl_raw.isdigit() else None
                shutil.copyfileobj(resp, out)
            _validate_downloaded_file(
                dest,
                expected_size=expected_size,
                expected_kind=expected_kind,
                content_length=content_length,
            )
            return
        except Exception as exc:
            errors.append(exc)
            if dest.exists():
                dest.unlink()
    try:
        _curl_download(url, dest)
        _validate_downloaded_file(
            dest,
            expected_size=expected_size,
            expected_kind=expected_kind,
        )
        return
    except Exception as exc:
        errors.append(exc)
        if dest.exists():
            dest.unlink()
    raise errors[-1]


def _download(
    url: str,
    dest: Path,
    *,
    expected_size: int | None = None,
    expected_kind: str | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}", flush=True)
    kind = expected_kind or _expected_archive_kind(dest.name) or _expected_archive_kind(url)
    errors: list[BaseException] = []
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            _download_once(
                url,
                dest,
                expected_size=expected_size,
                expected_kind=kind,
            )
            return
        except Exception as exc:
            errors.append(exc)
            if dest.exists():
                dest.unlink()
            if attempt < _DOWNLOAD_ATTEMPTS:
                _warn(f"download attempt {attempt}/{_DOWNLOAD_ATTEMPTS} failed ({exc}); retrying")
    raise errors[-1]


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


def write_install_stamp(*, tag: str, backend: str, asset: str) -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    STAMP_FILE.write_text(
        f"tag={tag}\nbackend={backend}\nasset={asset}\n",
        encoding="utf-8",
    )


def read_install_stamp() -> dict[str, str]:
    if not STAMP_FILE.is_file():
        return {}
    out: dict[str, str] = {}
    for line in STAMP_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def stamp_matches(tag: str, backend: str | None = None) -> bool:
    stamp = read_install_stamp()
    return stamp.get("tag") == tag and (backend is None or stamp.get("backend") == backend)


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


def llama_install_status(tag: str | None = None) -> dict[str, Any]:
    pin = tag or read_pinned_release()
    llama = _find_built_binary("llama-server")
    rpc = _find_built_binary("rpc-server")
    stamp = read_install_stamp()
    actual_backends = []
    if llama:
        try:
            probe = subprocess.run([str(llama), "--list-devices"], capture_output=True, text=True, timeout=15)
            actual_backends = parse_list_devices_backends(probe.stdout + probe.stderr)
            if probe.returncode == 0 and not actual_backends:
                actual_backends = ["cpu"]
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "pin": pin,
        "stamp": stamp.get("tag"),
        "llama_server": str(llama) if llama else None,
        "rpc_server": str(rpc) if rpc else None,
        "requested_backend": stamp.get("backend"),
        "actual_backends": actual_backends,
        "ok": bool(llama and rpc and stamp.get("tag") == pin and stamp.get("backend") in actual_backends),
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
        [str(python), "-m", "pip", "install", "maturin>=1.7,<2.0", "pytest>=8.0", "pytest-asyncio>=0.24"],
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
            _download("https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe", installer)
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
            _run([winget, "install", "-e", "--id", "OpenJS.NodeJS.LTS", "--accept-package-agreements", "--accept-source-agreements"])
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
        raise SystemExit("error: Node.js installed but npm not on PATH — open a new shell and re-run.")


def ensure_git() -> None:
    if _which("git"):
        return
    print("Installing git…")
    if _is_windows():
        winget = _which("winget")
        if winget:
            _run([winget, "install", "-e", "--id", "Git.Git", "--accept-package-agreements", "--accept-source-agreements"])
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
                _run([winget, "install", "-e", "--id", "Kitware.CMake", "--accept-package-agreements", "--accept-source-agreements"])
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


# ── kllm package + UI ────────────────────────────────────────────────────────


def build_rust(python: Path, *, release: bool) -> None:
    ensure_rust()
    env = _venv_env(python)
    _sync_python_deps(python)

    wheels = ROOT / "target" / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    for old in wheels.glob("kllm-*.whl"):
        old.unlink(missing_ok=True)

    uv = _find_uv()
    maturin_prefix = [uv, "run", "--python", str(python), "maturin"] if uv else [str(python), "-m", "maturin"]
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
    wheel = str(built[-1])
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
    flags = ["-DGGML_RPC=ON", "-DCMAKE_BUILD_TYPE=Release", "-DGGML_CUDA=OFF", "-DGGML_HIP=OFF", "-DGGML_VULKAN=OFF", "-DGGML_METAL=OFF"]
    if backend == "metal":
        flags.append("-DGGML_METAL=ON")
    elif backend == "cuda":
        flags.append("-DGGML_CUDA=ON")
    elif backend == "rocm":
        if not _is_linux():
            raise SystemExit("ROCm installation is supported on Linux")
        flags.extend(["-DGGML_HIP=ON", "-DCMAKE_C_COMPILER=hipcc", "-DCMAKE_CXX_COMPILER=hipcc"])
    elif backend == "vulkan":
        flags.append("-DGGML_VULKAN=ON")
    elif backend == "cpu":
        pass
    else:
        raise SystemExit(f"error: unknown backend {backend!r}")
    return flags


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
            _run(["git", "-C", str(VENDOR_LLAMA), "fetch", "--depth", "1", "origin", tag], check=False)
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


def build_llama_from_source(backend: str, tag: str | None = None) -> bool:
    if not ensure_cmake():
        return False
    tag = tag or read_pinned_release()
    clone_or_update_llama(tag)
    build_dir = VENDOR_LLAMA / "build"
    flags = _cmake_backend_flags(backend)
    try:
        _run(["cmake", "-B", str(build_dir), *flags], cwd=VENDOR_LLAMA)
        jobs = str(os.cpu_count() or 4)
        # llama-server + RPC worker (target name varies by upstream revision).
        rpc_target_ok = False
        for t in ["llama-server", *_RPC_UPSTREAM_NAMES]:
            if t in _RPC_UPSTREAM_NAMES and rpc_target_ok:
                continue
            result = _run(
                ["cmake", "--build", str(build_dir), "--config", "Release", "--target", t, "-j", jobs],
                cwd=VENDOR_LLAMA,
                check=False,
            )
            if result.returncode == 0 and t in _RPC_UPSTREAM_NAMES:
                rpc_target_ok = True
            elif result.returncode != 0:
                _warn(f"cmake target {t} failed (exit {result.returncode})")
        return _find_built_binary("llama-server") is not None
    except Exception as exc:
        _warn(f"llama.cpp source build failed ({exc})")
        return False


def _upstream_binary_names(logical: str) -> tuple[str, ...]:
    if logical == "rpc-server":
        return _RPC_UPSTREAM_NAMES
    return (logical,)


def _find_built_binary(name: str) -> Path | None:
    for base in _upstream_binary_names(name):
        exe = f"{base}.exe" if _is_windows() else base
        candidates = [
            VENDOR_LLAMA / "build" / "bin" / exe,
            VENDOR_LLAMA / "build" / "bin" / "Release" / exe,
            VENDOR_LLAMA / "build" / "Release" / "bin" / exe,
            VENDOR_LLAMA / "build" / exe,
            BIN_DIR / exe,
            WORKER_WIN / exe if name == "rpc-server" else Path(),
        ]
        for p in candidates:
            if p and p.is_file():
                return p
    return None


def pick_release_asset(assets: list[dict[str, Any]], backend: str) -> dict[str, Any] | None:
    """Pick the best llama.cpp release asset for this OS/arch/backend."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    names = [a.get("name", "") for a in assets]

    def score(name: str) -> int:
        n = name.lower()
        s = 0
        if not (n.endswith(".zip") or n.endswith(".tar.gz")):
            return -1
        if "bin" not in n:
            return -1
        if _is_windows():
            if "win" not in n:
                return -1
            if arm and "arm64" not in n:
                return -1
            if not arm and "x64" not in n and "amd64" not in n:
                return -1
        elif _is_macos():
            if "macos" not in n:
                return -1
            if arm and "arm64" not in n:
                return -1
            if not arm and "x64" not in n and "x86_64" not in n:
                return -1
        elif _is_linux():
            if "ubuntu" not in n and "linux" not in n:
                return -1
            if arm and "arm64" not in n:
                return -1
            if not arm and "x64" not in n and "amd64" not in n:
                return -1
        else:
            return -1

        # Backend preference
        if backend == "cuda" and "cuda" in n:
            s += 50
        elif backend == "vulkan" and "vulkan" in n:
            s += 50
        elif backend == "rocm" and ("rocm" in n or "hip" in n):
            s += 50
        elif backend == "metal" and "macos" in n:
            s += 50
        elif backend == "cpu" and ("cpu" in n or ("cuda" not in n and "vulkan" not in n and "rocm" not in n)):
            s += 40
        else:
            return -1

        # Prefer smaller / more common packs slightly when tied
        if "cudart" in n:
            return -1
        return s

    ranked = sorted(
        ((score(a.get("name", "")), a) for a in assets),
        key=lambda t: t[0],
        reverse=True,
    )
    for sc, asset in ranked:
        if sc > 0:
            return asset
    return None


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


def install_llama_prebuilt(backend: str, tag: str | None = None) -> bool:
    tag = tag or read_pinned_release()
    local = _cached_llama_archive(tag, backend)
    if local is not None:
        return _install_llama_archive(local, tag=tag, backend=backend)

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
        return _install_llama_archive(archive, tag=tag, backend=backend, asset_name=name)


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
                    tf.extractall(extract_dir)
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
    write_install_stamp(tag=tag, backend=backend, asset=name)
    print(f"Installed llama.cpp {tag} from {name}")
    return True


def _ensure_worker_src() -> Path:
    WORKER_WIN_SRC.mkdir(parents=True, exist_ok=True)
    return WORKER_WIN_SRC


def _copy_dlls(from_dir: Path, to_dir: Path) -> None:
    _copy_shared_libs(from_dir, to_dir)


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


def _migrate_worker_root_dlls() -> None:
    """Move legacy worker-windows/*.dll into worker-windows/src/."""
    if not WORKER_WIN.is_dir():
        return
    dlls = list(WORKER_WIN.glob("*.dll"))
    if not dlls:
        return
    dest = _ensure_worker_src()
    for dll in dlls:
        target = dest / dll.name
        if target.exists():
            dll.unlink()
        else:
            shutil.move(str(dll), str(target))


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
        if base == "rpc-server":
            WORKER_WIN.mkdir(parents=True, exist_ok=True)
            worker_dest = WORKER_WIN / exe
            shutil.copy2(src, worker_dest)
            _mark_executable(worker_dest)
            _copy_shared_libs(src.parent, _ensure_worker_src())
    # Also stage under vendor/llama.cpp/build/bin for resolve_llama_server paths
    staged = VENDOR_LLAMA / "build" / "bin"
    if found_any:
        staged.mkdir(parents=True, exist_ok=True)
        for p in BIN_DIR.iterdir():
            if p.is_file():
                shutil.copy2(p, staged / p.name)
                if p.suffix.lower() not in {".dll", ".dylib", ".txt"} and ".so" not in p.name:
                    _mark_executable(staged / p.name)
    _migrate_worker_root_dlls()
    return found_any


def stage_built_binaries() -> None:
    """If we built from source, also copy into vendor/bin and worker-windows."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("llama-server", "rpc-server"):
        src = _find_built_binary(name)
        if not src:
            continue
        staged = f"{name}.exe" if _is_windows() else name
        dest = BIN_DIR / staged
        shutil.copy2(src, dest)
        _mark_executable(dest)
        print(f"  staged {dest}")
        _copy_shared_libs(src.parent, BIN_DIR)
        if name == "rpc-server":
            WORKER_WIN.mkdir(parents=True, exist_ok=True)
            worker_dest = WORKER_WIN / staged
            shutil.copy2(src, worker_dest)
            _mark_executable(worker_dest)
            _copy_shared_libs(src.parent, _ensure_worker_src())
    _migrate_worker_root_dlls()


def install_llama(
    backend: str,
    *,
    prefer_prebuilt: bool,
    force: bool = False,
    tag: str | None = None,
) -> None:
    backend = detect_backend(backend)
    tag = tag or read_pinned_release()
    assert_stable_llama_pin(tag)
    print(f"llama.cpp backend: {backend}")
    print(f"llama.cpp pin:     {tag}")

    llama = _find_built_binary("llama-server")
    rpc = _find_built_binary("rpc-server")
    if llama and rpc and not force and stamp_matches(tag, backend) and llama_install_status(tag)["ok"]:
        print(f"llama-server: {llama}")
        print(f"rpc-server:   {rpc}")
        print(f"Already at pinned {tag}")
        return

    if llama and rpc and not force:
        current = read_install_stamp().get("tag") or "unknown"
        print(f"Installed llama.cpp is {current}; upgrading to pin {tag}")

    ok = False
    if prefer_prebuilt:
        ok = install_llama_prebuilt(backend, tag=tag)
        if not ok:
            ok = build_llama_from_source(backend, tag=tag)
    else:
        ok = build_llama_from_source(backend, tag=tag)
        if not ok:
            ok = install_llama_prebuilt(backend, tag=tag)

    stage_built_binaries()
    llama = _find_built_binary("llama-server")
    rpc = _find_built_binary("rpc-server")
    if not llama:
        raise SystemExit(
            "error: llama-server not available after install. "
            "Build manually with -DGGML_RPC=ON or set KLLM_LLAMA_SERVER."
        )
    if not rpc:
        _warn(
            "rpc-server binary missing. Workers need it — rebuild with -DGGML_RPC=ON "
            "or set KLLM_RPC_SERVER. Prebuilt packs sometimes omit it."
        )
    else:
        print(f"llama-server: {llama}")
        print(f"rpc-server:   {rpc}")
    if llama and rpc and not stamp_matches(tag, backend):
        write_install_stamp(tag=tag, backend=backend, asset="source")


# ── CLI ──────────────────────────────────────────────────────────────────────


def print_llama_check(tag: str) -> int:
    status = llama_install_status(tag)
    print(f"pin:          {status['pin']}")
    print(f"installed:    {status['stamp'] or '(no stamp)'}")
    print(f"llama-server: {status['llama_server'] or 'MISSING'}")
    print(f"rpc-server:   {status['rpc_server'] or 'MISSING'}")
    if status["ok"]:
        print("status:       ok")
        return 0
    print("status:       mismatch - run python scripts/install.py --llama-only")
    return 1


def verify_llama_installed(tag: str) -> None:
    status = llama_install_status(tag)
    if status["ok"]:
        return
    requested = status["requested_backend"]
    actual = status["actual_backends"]
    if status["stamp"] == tag and requested and actual and requested not in actual:
        raise SystemExit(
            f"error: llama.cpp {tag} stamp ok but backend mismatch "
            f"(requested={requested!r}, probed={actual!r}). "
            "Retry with --force or --from-source."
        )
    raise SystemExit(
        f"error: llama.cpp {tag} is not installed (stamp={status['stamp']!r}). "
        "Retry with --force or --from-source."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Install Kluster LLM and all runtime dependencies.")
    p.add_argument("--skip-ui", action="store_true", help="Skip dashboard npm build")
    p.add_argument("--skip-rust", action="store_true", help="Skip maturin / Python package build")
    p.add_argument("--skip-npm-install", action="store_true", help="Skip `npm install` (build only)")
    p.add_argument("--skip-llama", action="store_true", help="Skip llama-server / rpc-server")
    p.add_argument("--skip-wireguard", action="store_true", help="Skip WireGuard install")
    p.add_argument("--skip-toolchains", action="store_true", help="Do not auto-install Rust/Node/git/cmake")
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
        help="Prefer GitHub release binaries (default unless --from-source)",
    )
    p.add_argument(
        "--from-source",
        action="store_true",
        help="Compile the pinned llama.cpp tag instead of using GitHub binaries",
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
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tag = read_pinned_release(args.tag)

    if args.llama_only:
        args.skip_rust = True
        args.skip_ui = True
        args.skip_wireguard = True
        args.skip_toolchains = True

    if args.check:
        return print_llama_check(tag)

    print(f"Repo root: {ROOT}")
    print(f"Python:    {sys.version.split()[0]} ({sys.executable})")
    print(f"Platform:  {platform.system()} {platform.machine()}")

    if not args.skip_toolchains:
        ensure_git()

    if not args.skip_rust:
        python = ensure_venv()
        if not args.skip_toolchains:
            ensure_rust()
        print("\n=== kllm (Rust + Python) ===")
        build_rust(python, release=args.release)

    if not args.skip_ui:
        print("\n=== Dashboard (Svelte) ===")
        if not args.skip_toolchains:
            ensure_node()
        build_ui(skip_install=args.skip_npm_install)

    if not args.skip_llama:
        print("\n=== llama.cpp (llama-server + rpc-server) ===")
        if args.llama_only:
            print(f"Pin file:  {PIN_FILE} ({tag})")
        prefer_prebuilt = not args.from_source
        install_llama(
            args.backend,
            prefer_prebuilt=prefer_prebuilt,
            force=args.force,
            tag=tag,
        )
        verify_llama_installed(tag)
        if args.llama_only:
            print(f"llama.cpp {tag} ready.")

    if not args.skip_wireguard:
        print("\n=== WireGuard ===")
        install_wireguard()

    print("\nInstall complete.")
    if not args.skip_rust:
        activate = r".\.venv\Scripts\Activate.ps1" if _is_windows() else "source .venv/bin/activate"
        print(f"Activate the venv with: {activate}")
        print("Or without activate: uv run kllm start   |   uvx --from . kllm join --key <key>")
        print("Then: kllm start   |   kllm join --key <key>")
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
