"""Gate tests for the cached llama.cpp pack installer.

The pack path is a join-time convenience. It must never change which backends
are installed, so these tests pin the two behaviours that used to be wrong:
a correctly built macOS node is recognised (instead of being overwritten with a
Metal-only pack), and a pack that cannot satisfy the platform contract is
refused instead of being silently applied.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_install():
    spec = importlib.util.spec_from_file_location(
        "kllm_install_script_packtest", ROOT / "scripts" / "install.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kllm_install_script_packtest"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def install():
    return _load_install()


@pytest.fixture
def pack(install, monkeypatch):
    """llama_pack wired to the real install.py module under test control."""
    from kllm.shared import llama_pack

    monkeypatch.setattr(llama_pack, "_load_install", lambda root: install)
    monkeypatch.setattr(install, "read_pinned_release", lambda override=None: "v0.5.0")
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    return llama_pack


def _make_install_tree(tmp_path, monkeypatch, install, *, backend):
    """A vendor/bin + stamp as the installer would leave them."""
    bin_dir = tmp_path / "vendor-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "llama-server").write_bytes(b"x")
    (bin_dir / "rpc-server").write_bytes(b"x")
    monkeypatch.setattr(install, "BIN_DIR", bin_dir)
    monkeypatch.setattr(install, "VENDOR_LLAMA", tmp_path / "llama.cpp")
    monkeypatch.setattr(install, "STAMP_FILE", bin_dir / "llama-release.txt")
    install.write_install_stamp(tag="v0.5.0", backend=backend, asset="source", https="on")
    return bin_dir


def _make_pack_archive(tmp_path) -> Path:
    src = tmp_path / "llama-b1"
    src.mkdir()
    (src / "llama-server").write_bytes(b"x")
    (src / "rpc-server").write_bytes(b"x")
    archive = tmp_path / "llama-v0.5.0-bin-macos-arm64.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(src, arcname="llama-b1")
    return archive


def test_skips_when_install_already_satisfies_contract(pack, install, tmp_path, monkeypatch):
    """A correct metal+vulkan install must not be overwritten by a cached pack."""
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    _make_install_tree(tmp_path, monkeypatch, install, backend="metal+vulkan")

    writes: list[dict] = []
    monkeypatch.setattr(install, "write_install_stamp", lambda **kw: writes.append(kw))
    monkeypatch.setattr(
        pack,
        "find_cached_archive",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not look for a pack")),
    )

    result = pack.install_cached_llama_pack(backend="metal", force=False)

    assert result["ok"] is True
    assert result["skipped"] is True
    assert writes == [], "a skipped install must not rewrite the stamp"


def test_refuses_metal_only_pack_on_macos(pack, install, tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    # A previous Metal-only install leaves the stamp non-conforming.
    _make_install_tree(tmp_path, monkeypatch, install, backend="metal")
    archive = _make_pack_archive(tmp_path)
    monkeypatch.setattr(pack, "find_cached_archive", lambda *a, **k: archive)

    copies: list = []
    monkeypatch.setattr(
        install, "_copy_llama_binaries_from", lambda root: copies.append(root) or True
    )

    with pytest.raises(pack.PackNotInstallable) as excinfo:
        pack.install_cached_llama_pack(backend="metal", force=False)

    message = str(excinfo.value)
    assert "Metal+Vulkan" in message
    assert "scripts/install.py --llama-only --from-source --force" in message
    assert copies == [], "a refused pack must not touch vendor/bin"


def test_refuses_even_when_forced_on_macos(pack, install, tmp_path, monkeypatch):
    """`force` re-extracts a conforming install; it must not bypass the contract."""
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    _make_install_tree(tmp_path, monkeypatch, install, backend="metal+vulkan")
    archive = _make_pack_archive(tmp_path)
    monkeypatch.setattr(pack, "find_cached_archive", lambda *a, **k: archive)

    with pytest.raises(pack.PackNotInstallable):
        pack.install_cached_llama_pack(backend="metal", force=True)


def test_missing_pack_still_reports_not_found_on_macos(pack, install, tmp_path, monkeypatch):
    """No pack and no install is a "not found" problem, not a contract problem."""
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "_resolve_runtime_binary", lambda name: None)
    monkeypatch.setattr(pack, "find_cached_archive", lambda *a, **k: None)

    with pytest.raises(FileNotFoundError):
        pack.install_cached_llama_pack(backend="metal", force=False)


def test_non_macos_still_installs_cached_pack(pack, install, tmp_path, monkeypatch):
    """The local-pack convenience is unchanged where the stamp is authoritative."""
    monkeypatch.setattr(install, "_is_macos", lambda: False)
    bin_dir = tmp_path / "vendor-bin"
    monkeypatch.setattr(install, "BIN_DIR", bin_dir)
    monkeypatch.setattr(install, "VENDOR_LLAMA", tmp_path / "llama.cpp")
    monkeypatch.setattr(install, "STAMP_FILE", bin_dir / "llama-release.txt")

    archive = tmp_path / "llama-v0.5.0-bin-win-vulkan-x64.tar.gz"
    src = tmp_path / "llama-b1"
    src.mkdir()
    (src / "llama-server").write_bytes(b"x")
    (src / "rpc-server").write_bytes(b"x")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(src, arcname="llama-b1")
    monkeypatch.setattr(pack, "find_cached_archive", lambda *a, **k: archive)
    # The stubs are not runnable binaries; the probe is covered by the installer tests.
    monkeypatch.setattr(install, "_probe_llama_backends", lambda binary: ["vulkan"])

    result = pack.install_cached_llama_pack(backend="vulkan", force=True)

    assert result["ok"] is True
    assert (bin_dir / "llama-server").is_file()
    assert install.read_install_stamp().get("backend") == "vulkan"


def test_legacy_state_without_llama_warnings_loads(tmp_path, monkeypatch):
    """state.json written before llama_warnings existed must still load."""
    from kllm.shared import state

    state_file = tmp_path / "cluster.json"
    state_file.write_text(
        json.dumps({"node_id": "abcd1234", "netsec_warnings": ["old"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(state, "state_path", lambda: state_file)
    monkeypatch.setattr(state, "ensure_home", lambda: tmp_path)

    loaded = state.ClusterState.load()

    assert loaded.node_id == "abcd1234"
    assert loaded.llama_warnings == []
