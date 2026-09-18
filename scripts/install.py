#!/usr/bin/env python3
"""Full Kluster LLM install: toolchains, kllm CLI, llama.cpp binaries, WireGuard.

Usage:
  uv run python scripts/install.py
  python scripts/install.py
  uv run python scripts/install.py --skip-ui
  uv run python scripts/install.py --backend vulkan
  uv run python scripts/install.py --skip-wireguard --skip-llama

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
LLAMA_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
GITHUB_HEADERS = {"User-Agent": "kllm-install", "Accept": "application/vnd.github+json"}
# Upstream llama.cpp ≥2025 ships ggml-rpc-server; older trees used rpc-server.
_RPC_UPSTREAM_NAMES = ("ggml-rpc-server", "rpc-server")


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


def _http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=GITHUB_HEADERS)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}", flush=True)
    req = urllib.request.Request(url, headers=GITHUB_HEADERS)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=600) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)


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
    if _is_windows() or _is_linux():
        return "vulkan"
    return "cpu"


def _cmake_backend_flags(backend: str) -> list[str]:
    flags = ["-DGGML_RPC=ON", "-DCMAKE_BUILD_TYPE=Release"]
    if backend == "metal":
        flags.append("-DGGML_METAL=ON")
    elif backend == "cuda":
        flags.append("-DGGML_CUDA=ON")
    elif backend == "vulkan":
        flags.append("-DGGML_VULKAN=ON")
    elif backend == "cpu":
        pass
    else:
        raise SystemExit(f"error: unknown backend {backend!r}")
    return flags


def clone_or_update_llama() -> None:
    ensure_git()
    VENDOR_LLAMA.parent.mkdir(parents=True, exist_ok=True)
    if (VENDOR_LLAMA / ".git").is_dir():
        print(f"Updating llama.cpp at {VENDOR_LLAMA}")
        _run(["git", "-C", str(VENDOR_LLAMA), "pull", "--ff-only"], check=False)
    elif VENDOR_LLAMA.is_dir() and any(VENDOR_LLAMA.iterdir()):
        print(f"Using existing llama.cpp tree at {VENDOR_LLAMA}")
    else:
        print(f"Cloning llama.cpp → {VENDOR_LLAMA}")
        _run(["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp.git", str(VENDOR_LLAMA)])


def build_llama_from_source(backend: str) -> bool:
    if not ensure_cmake():
        return False
    clone_or_update_llama()
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
        elif backend == "metal" and "macos" in n:
            s += 50
        elif backend == "cpu" and ("cpu" in n or ("cuda" not in n and "vulkan" not in n and "rocm" not in n)):
            s += 40
        elif "cuda" in n or "vulkan" in n or "rocm" in n or "sycl" in n:
            s += 10
        else:
            s += 20

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


def fetch_latest_b_release() -> dict[str, Any] | None:
    """Find a recent release that has binary assets (prefer b* nightlies)."""
    try:
        releases = _http_json(f"{LLAMA_API}?per_page=30")
    except Exception as exc:
        _warn(f"GitHub releases fetch failed ({exc})")
        return None
    if not isinstance(releases, list):
        return None
    for rel in releases:
        assets = rel.get("assets") or []
        if any(
            re.match(r"llama-b\d+-bin-", a.get("name", "")) or "bin-win" in a.get("name", "") or "bin-ubuntu" in a.get("name", "") or "bin-macos" in a.get("name", "")
            for a in assets
        ):
            return rel
        # Semver releases may only ship nightly-tag.txt
        for a in assets:
            if a.get("name") == "nightly-tag.txt":
                try:
                    tag_url = a["browser_download_url"]
                    with tempfile.TemporaryDirectory() as td:
                        p = Path(td) / "nightly-tag.txt"
                        _download(tag_url, p)
                        tag = p.read_text(encoding="utf-8").strip()
                    if tag:
                        return _http_json(f"{LLAMA_API}/tags/{tag}")
                except Exception as exc:
                    _warn(f"nightly-tag resolve failed ({exc})")
    return None


def install_llama_prebuilt(backend: str) -> bool:
    rel = fetch_latest_b_release()
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
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / name
        _download(url, archive)
        extract_dir = Path(td) / "out"
        extract_dir.mkdir()
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract_dir)
        else:
            with tarfile.open(archive, "r:*") as tf:
                tf.extractall(extract_dir)

        copied = _copy_llama_binaries_from(extract_dir)
        if not copied:
            _warn(f"archive {name} extracted but llama-server/rpc-server not found inside")
            return False
    print(f"Installed prebuilt llama.cpp from {rel.get('tag_name')} ({name})")
    return True


def _ensure_worker_src() -> Path:
    WORKER_WIN_SRC.mkdir(parents=True, exist_ok=True)
    return WORKER_WIN_SRC


def _copy_dlls(from_dir: Path, to_dir: Path) -> None:
    to_dir.mkdir(parents=True, exist_ok=True)
    for dll in from_dir.glob("*.dll"):
        shutil.copy2(dll, to_dir / dll.name)


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
    """Copy llama-server and rpc-server (and sibling DLLs) into vendor/bin (+ worker-windows)."""
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
        found_any = True
        print(f"  → {dest}")
        # Copy adjacent shared libs on Windows
        if _is_windows():
            for dll in src.parent.glob("*.dll"):
                shutil.copy2(dll, BIN_DIR / dll.name)
        if base == "rpc-server":
            WORKER_WIN.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, WORKER_WIN / exe)
            if _is_windows():
                _copy_dlls(src.parent, _ensure_worker_src())
    # Also stage under vendor/llama.cpp/build/bin for resolve_llama_server paths
    staged = VENDOR_LLAMA / "build" / "bin"
    if found_any:
        staged.mkdir(parents=True, exist_ok=True)
        for p in BIN_DIR.iterdir():
            if p.is_file():
                shutil.copy2(p, staged / p.name)
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
        shutil.copy2(src, BIN_DIR / staged)
        print(f"  staged {BIN_DIR / staged}")
        if name == "rpc-server":
            WORKER_WIN.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, WORKER_WIN / staged)
            if _is_windows():
                _copy_dlls(src.parent, _ensure_worker_src())
    _migrate_worker_root_dlls()


def install_llama(backend: str, *, prefer_prebuilt: bool) -> None:
    backend = detect_backend(backend)
    print(f"llama.cpp backend: {backend}")

    llama = _find_built_binary("llama-server")
    rpc = _find_built_binary("rpc-server")
    if llama and rpc:
        print(f"llama-server: {llama}")
        print(f"rpc-server:   {rpc}")
        stage_built_binaries()
        return

    ok = False
    if prefer_prebuilt:
        ok = install_llama_prebuilt(backend)
        if not ok:
            ok = build_llama_from_source(backend)
    else:
        ok = build_llama_from_source(backend)
        if not ok:
            ok = install_llama_prebuilt(backend)

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


# ── CLI ──────────────────────────────────────────────────────────────────────


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
        choices=["auto", "metal", "cuda", "vulkan", "cpu"],
        help="llama.cpp accelerator backend (default: auto-detect)",
    )
    p.add_argument(
        "--prefer-prebuilt",
        action="store_true",
        help="Prefer GitHub release binaries over compiling llama.cpp",
    )
    p.add_argument(
        "--from-source",
        action="store_true",
        help="Prefer compiling llama.cpp (default unless --prefer-prebuilt)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
        prefer_prebuilt = args.prefer_prebuilt or (args.from_source is False and _is_windows())
        # Default: Windows prefers prebuilt (no MSVC assumed); others prefer source when cmake works.
        if not args.prefer_prebuilt and not args.from_source:
            prefer_prebuilt = _is_windows()
        install_llama(args.backend, prefer_prebuilt=prefer_prebuilt)

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
