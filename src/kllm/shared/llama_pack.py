"""Install a pinned llama.cpp pack already present under ~/.kllm/models."""

from __future__ import annotations

import importlib.util
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

from kllm.shared.state import models_dir, repo_root


class PackNotInstallable(RuntimeError):
    """The cached pack cannot satisfy this platform's llama.cpp contract."""


def _load_install(root: Path) -> ModuleType:
    path = root / "scripts" / "install.py"
    spec = importlib.util.spec_from_file_location("kllm_install_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kllm_install_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def find_cached_archive(tag: str, backend: str) -> Path | None:
    root = models_dir()
    if not root.is_dir():
        return None
    assets: list[tuple[str, Path]] = [
        (p.name, p)
        for p in root.iterdir()
        if p.is_file() and p.name.startswith(f"llama-{tag}-bin-")
    ]
    if not assets:
        return None
    install = _load_install(repo_root() or Path(__file__).resolve().parents[2])
    picked = install.pick_release_asset(
        [{"name": name, "browser_download_url": ""} for name, _path in assets],
        backend,
    )
    if picked:
        for name, path in assets:
            if name == picked["name"]:
                return path
    return None


def install_cached_llama_pack(
    *,
    tag: str | None = None,
    backend: str = "auto",
    force: bool = False,
) -> dict[str, Any]:
    """Extract llama-{tag}-bin-* from ~/.kllm/models into vendor/bin.

    The pack is only a convenience for reusing a local download. It must never
    change which backends are installed, so the "already installed" check is
    judged against the platform contract (macOS: Metal+Vulkan) rather than the
    raw resolved backend, and a pack that cannot satisfy that contract is
    refused instead of silently downgrading the install.
    """
    root = repo_root()
    if root is None:
        raise RuntimeError("kllm repo root not found; set KLLM_REPO_ROOT")
    install = _load_install(root)
    pin = install.read_pinned_release(tag)
    backend = install.detect_backend(backend)
    # macOS requires Metal+Vulkan; everywhere else a single backend is the
    # contract, so `contract` is None and the stamp stays authoritative.
    contract = install.expected_stamp_for_default(backend)
    llama = install._resolve_runtime_binary("llama-server")
    rpc = install._resolve_runtime_binary("rpc-server")
    if llama and rpc and not force and install.stamp_matches(pin, contract or backend):
        return {
            "ok": True,
            "skipped": True,
            "tag": pin,
            "llama_server": str(llama),
            "rpc_server": str(rpc),
        }
    archive = find_cached_archive(pin, backend)
    if archive is None:
        raise FileNotFoundError(
            f"no llama-{pin}-bin-* archive in {models_dir()}; "
            "run python scripts/install.py --llama-only or pull the GitHub pack"
        )
    if contract and not install.backends_satisfied(contract, install.parse_stamp_backends(backend)):
        raise PackNotInstallable(
            f"cached llama.cpp pack {archive.name!r} is {backend}-only, but this "
            f"platform requires {contract!r} (GitHub macOS packs have no Vulkan). "
            "Run `python scripts/install.py --llama-only --from-source --force` to "
            "build Metal+Vulkan, or `--backend cpu` for a CPU-only install."
        )
    with tempfile.TemporaryDirectory() as td:
        extract_dir = Path(td) / "out"
        extract_dir.mkdir()
        name = archive.name
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract_dir)
        else:
            with tarfile.open(archive, "r:*") as tf:
                tf.extractall(extract_dir, filter="data")
        copied = install._copy_llama_binaries_from(extract_dir)
        if not copied:
            raise RuntimeError(f"archive {name} had no llama-server/rpc-server")
    # Stamp what the pack actually provides, never the platform contract.
    install.write_install_stamp(tag=pin, backend=backend, asset=name, https="on")
    status = install.llama_install_status(pin)
    return {
        "ok": bool(status.get("ok")),
        "tag": pin,
        "asset": name,
        "llama_server": status.get("llama_server"),
        "rpc_server": status.get("rpc_server"),
    }
