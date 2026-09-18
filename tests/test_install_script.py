from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL_PATH = ROOT / "scripts" / "install.py"


def _load_install():
    spec = importlib.util.spec_from_file_location("kllm_install_script", INSTALL_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kllm_install_script"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def install():
    return _load_install()


def test_pick_release_asset_windows_vulkan(install, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: True)
    monkeypatch.setattr(install, "_is_macos", lambda: False)
    monkeypatch.setattr(install, "_is_linux", lambda: False)
    monkeypatch.setattr(install.platform, "machine", lambda: "AMD64")
    assets = [
        {"name": "llama-b100-bin-win-cpu-x64.zip", "browser_download_url": "u1"},
        {"name": "llama-b100-bin-win-vulkan-x64.zip", "browser_download_url": "u2"},
        {"name": "llama-b100-bin-win-cuda-12.4-x64.zip", "browser_download_url": "u3"},
        {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "browser_download_url": "u4"},
        {"name": "llama-b100-bin-ubuntu-vulkan-x64.tar.gz", "browser_download_url": "u5"},
    ]
    picked = install.pick_release_asset(assets, "vulkan")
    assert picked is not None
    assert picked["name"] == "llama-b100-bin-win-vulkan-x64.zip"


def test_pick_release_asset_macos_metal(install, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "_is_linux", lambda: False)
    monkeypatch.setattr(install.platform, "machine", lambda: "arm64")
    assets = [
        {"name": "llama-b100-bin-macos-arm64.tar.gz", "browser_download_url": "u1"},
        {"name": "llama-b100-bin-macos-x64.tar.gz", "browser_download_url": "u2"},
        {"name": "llama-b100-bin-win-vulkan-x64.zip", "browser_download_url": "u3"},
    ]
    picked = install.pick_release_asset(assets, "metal")
    assert picked is not None
    assert "macos-arm64" in picked["name"]


def test_detect_backend_explicit(install):
    assert install.detect_backend("cuda") == "cuda"
    assert install.detect_backend("vulkan") == "vulkan"


def test_parse_args_defaults(install):
    args = install.parse_args([])
    assert args.backend == "auto"
    assert args.skip_llama is False
    assert args.skip_wireguard is False


def test_migrate_worker_root_dlls(install, monkeypatch, tmp_path):
    worker = tmp_path / "worker-windows"
    worker.mkdir()
    src_dir = worker / "src"
    (worker / "a.dll").write_bytes(b"a")
    (worker / "b.dll").write_bytes(b"b")

    monkeypatch.setattr(install, "WORKER_WIN", worker)
    monkeypatch.setattr(install, "WORKER_WIN_SRC", src_dir)

    install._migrate_worker_root_dlls()

    assert not list(worker.glob("*.dll"))
    assert (src_dir / "a.dll").read_bytes() == b"a"
    assert (src_dir / "b.dll").read_bytes() == b"b"


def test_copy_dlls_to_worker_src(install, monkeypatch, tmp_path):
    worker = tmp_path / "worker-windows"
    worker.mkdir()
    src_dir = worker / "src"
    from_dir = tmp_path / "extract"
    from_dir.mkdir()
    (from_dir / "ggml.dll").write_bytes(b"ggml")

    monkeypatch.setattr(install, "WORKER_WIN", worker)
    monkeypatch.setattr(install, "WORKER_WIN_SRC", src_dir)

    install._copy_dlls(from_dir, install._ensure_worker_src())

    assert (src_dir / "ggml.dll").read_bytes() == b"ggml"


def test_read_pinned_release_from_repo(install):
    assert install.read_pinned_release() == "b11026"
    assert install.PIN_FILE.name == "llama-release"
    assert "vendor" not in install.PIN_FILE.parts


def test_normalize_llama_tag(install):
    assert install.normalize_llama_tag("b11026") == "b11026"
    assert install.normalize_llama_tag("11026") == "b11026"
    assert install.read_pinned_release("b999") == "b999"


def test_stamp_matches(install, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "BIN_DIR", tmp_path)
    monkeypatch.setattr(install, "STAMP_FILE", tmp_path / "llama-release.txt")
    assert not install.stamp_matches("b11026")
    install.write_install_stamp(tag="b11026", backend="vulkan", asset="pack.zip")
    assert install.stamp_matches("b11026")
    assert not install.stamp_matches("b1")
    assert install.read_install_stamp()["asset"] == "pack.zip"


def test_copy_llama_binaries_renames_ggml_rpc_and_copies_dylibs(install, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    bin_dir = tmp_path / "bin"
    vendor = tmp_path / "llama.cpp"
    worker = tmp_path / "worker-windows"
    monkeypatch.setattr(install, "BIN_DIR", bin_dir)
    monkeypatch.setattr(install, "VENDOR_LLAMA", vendor)
    monkeypatch.setattr(install, "WORKER_WIN", worker)
    monkeypatch.setattr(install, "WORKER_WIN_SRC", worker / "src")

    extract = tmp_path / "extract"
    extract.mkdir()
    (extract / "llama-server").write_bytes(b"srv")
    (extract / "ggml-rpc-server").write_bytes(b"rpc")
    (extract / "libggml.dylib").write_bytes(b"lib")

    assert install._copy_llama_binaries_from(extract)
    assert (bin_dir / "llama-server").read_bytes() == b"srv"
    assert (bin_dir / "rpc-server").read_bytes() == b"rpc"
    assert (bin_dir / "libggml.dylib").read_bytes() == b"lib"
    assert not (bin_dir / "ggml-rpc-server").exists()
    assert (worker / "rpc-server").read_bytes() == b"rpc"


def test_pick_release_asset_macos_vulkan_uses_metal_pack(install, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "_is_linux", lambda: False)
    monkeypatch.setattr(install.platform, "machine", lambda: "arm64")
    assets = [
        {"name": "llama-b11026-bin-macos-arm64.tar.gz", "browser_download_url": "u1"},
        {"name": "llama-b11026-bin-win-vulkan-x64.zip", "browser_download_url": "u2"},
    ]
    picked = install.pick_release_asset(assets, "vulkan")
    assert picked is not None
    assert "macos-arm64" in picked["name"]


def test_parse_args_llama_pin_flags(install):
    args = install.parse_args(["--force", "--tag", "b11026", "--from-source"])
    assert args.force is True
    assert args.tag == "b11026"
    assert args.from_source is True


def test_setup_llama_parse_args():
    spec = importlib.util.spec_from_file_location(
        "kllm_setup_llama_script", ROOT / "scripts" / "setup-llama.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kllm_setup_llama_script"] = mod
    spec.loader.exec_module(mod)
    args = mod.parse_args(["--check", "--backend", "vulkan"])
    assert args.check is True
    assert args.backend == "vulkan"
