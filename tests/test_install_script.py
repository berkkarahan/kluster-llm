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
    assert install.read_pinned_release() == "v0.5.0"
    assert install.PIN_FILE.name == "llama-release"
    assert "vendor" not in install.PIN_FILE.parts


def test_normalize_llama_tag(install):
    assert install.normalize_llama_tag("v0.5.0") == "v0.5.0"
    assert install.normalize_llama_tag("b11026") == "b11026"
    assert install.normalize_llama_tag("11026") == "b11026"
    assert install.read_pinned_release("v0.4.1") == "v0.4.1"


def test_stamp_matches(install, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "BIN_DIR", tmp_path)
    monkeypatch.setattr(install, "STAMP_FILE", tmp_path / "llama-release.txt")
    assert not install.stamp_matches("v0.5.0")
    install.write_install_stamp(tag="v0.5.0", backend="vulkan", asset="pack.zip")
    assert install.stamp_matches("v0.5.0")
    assert not install.stamp_matches("v0.4.1")
    assert install.read_install_stamp()["asset"] == "pack.zip"


def test_parse_list_devices_backends_metal_mtl(install):
    # Newer llama.cpp uses MTL0:; older builds used Metal0:.
    assert install.parse_list_devices_backends(
        "Available devices:\n  MTL0: Apple M1 Pro (25559 MiB, 25558 MiB free)\n"
    ) == ["metal"]
    assert install.parse_list_devices_backends("Metal0: Apple GPU\n") == ["metal"]
    assert install.parse_list_devices_backends("CUDA0: NVIDIA\n") == ["cuda"]
    assert install.parse_list_devices_backends("BLAS: Accelerate\n") == []


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


def test_pick_release_asset_macos_vulkan_does_not_relabel_metal(install, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "_is_linux", lambda: False)
    monkeypatch.setattr(install.platform, "machine", lambda: "arm64")
    assets = [
        {"name": "llama-b11146-bin-macos-arm64.tar.gz", "browser_download_url": "u1"},
        {"name": "llama-b11146-bin-win-vulkan-x64.zip", "browser_download_url": "u2"},
    ]
    picked = install.pick_release_asset(assets, "vulkan")
    assert picked is None


def test_parse_args_llama_pin_flags(install):
    args = install.parse_args(["--force", "--tag", "v0.5.0", "--from-source"])
    assert args.force is True
    assert args.tag == "v0.5.0"
    assert args.from_source is True


def test_parse_args_llama_only_and_check(install):
    args = install.parse_args(["--check", "--llama-only", "--backend", "vulkan"])
    assert args.check is True
    assert args.llama_only is True
    assert args.backend == "vulkan"


def test_print_llama_check_ok(install, monkeypatch):
    monkeypatch.setattr(
        install,
        "llama_install_status",
        lambda tag: {
            "pin": tag,
            "stamp": tag,
            "llama_server": "/bin/llama-server",
            "rpc_server": "/bin/rpc-server",
            "ok": True,
        },
    )
    assert install.print_llama_check("v0.5.0") == 0


def test_print_llama_check_mismatch(install, monkeypatch):
    monkeypatch.setattr(
        install,
        "llama_install_status",
        lambda tag: {
            "pin": tag,
            "stamp": None,
            "llama_server": None,
            "rpc_server": None,
            "ok": False,
        },
    )
    assert install.print_llama_check("v0.5.0") == 1


def test_cached_llama_archive_prefers_platform_pack(install, monkeypatch, tmp_path):
    home = tmp_path
    models = home / ".kllm" / "models"
    models.mkdir(parents=True)
    (models / "llama-b11146-bin-macos-arm64.tar.gz").write_bytes(b"mac")
    (models / "llama-b11146-bin-win-vulkan-x64.zip").write_bytes(b"win")
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "_is_linux", lambda: False)
    monkeypatch.setattr(install.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: home))
    got = install._cached_llama_archive("b11146", "metal")
    assert got is not None
    assert got.name == "llama-b11146-bin-macos-arm64.tar.gz"


def test_assert_stable_llama_pin_rejects_nightly(install):
    with pytest.raises(SystemExit, match="nightly"):
        install.assert_stable_llama_pin("b11146")


def test_assert_stable_llama_pin_rejects_prerelease_release(install, monkeypatch):
    monkeypatch.setattr(
        install,
        "_fetch_release_json",
        lambda tag: {"tag_name": tag, "prerelease": True, "draft": False, "assets": []},
    )
    with pytest.raises(SystemExit, match="pre-release"):
        install.assert_stable_llama_pin("v0.5.0-rc1")


def test_fetch_release_by_tag_follows_nightly_tag(install, monkeypatch, tmp_path):
    nightly_file = tmp_path / "nightly-tag.txt"
    nightly_file.write_text("b11146\n", encoding="utf-8")

    def fake_fetch(tag: str):
        if tag == "v0.5.0":
            return {
                "tag_name": "v0.5.0",
                "prerelease": False,
                "draft": False,
                "assets": [
                    {
                        "name": "nightly-tag.txt",
                        "browser_download_url": "https://example.test/nightly-tag.txt",
                    }
                ],
            }
        if tag == "b11146":
            return {
                "tag_name": "b11146",
                "prerelease": True,
                "draft": False,
                "assets": [
                    {
                        "name": "llama-b11146-bin-macos-arm64.tar.gz",
                        "browser_download_url": "u1",
                    }
                ],
            }
        return None

    def fake_download(url: str, dest):
        dest.write_text("b11146\n", encoding="utf-8")

    monkeypatch.setattr(install, "_fetch_release_json", fake_fetch)
    monkeypatch.setattr(install, "_download", fake_download)
    rel = install.fetch_release_by_tag("v0.5.0")
    assert rel is not None
    assert rel["tag_name"] == "b11146"
    assert install.release_has_bin_assets(rel["assets"])


def test_fetch_release_by_tag_rejects_prerelease_pin(install, monkeypatch):
    monkeypatch.setattr(
        install,
        "_fetch_release_json",
        lambda tag: {
            "tag_name": tag,
            "prerelease": True,
            "draft": False,
            "assets": [{"name": "llama-b1-bin-macos-arm64.tar.gz"}],
        },
    )
    with pytest.raises(SystemExit, match="pre-release"):
        install.fetch_release_by_tag("b11146")


def test_sniff_and_validate_zip_magic(install, tmp_path):
    good = tmp_path / "ok.zip"
    good.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
    assert install._sniff_archive_kind(good) == "zip"
    install._validate_downloaded_file(good, expected_kind="zip", expected_size=24)

    bad = tmp_path / "bad.zip"
    bad.write_text("<!DOCTYPE html>error", encoding="utf-8")
    assert install._sniff_archive_kind(bad) is None
    with pytest.raises(RuntimeError, match="not a valid zip"):
        install._validate_downloaded_file(bad, expected_kind="zip")


def test_download_retries_corrupt_payload(install, monkeypatch, tmp_path):
    dest = tmp_path / "llama-b1-bin-win-vulkan-x64.zip"
    attempts = {"n": 0}

    def fake_once(url, path, *, expected_size=None, expected_kind=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            path.write_text("not a zip", encoding="utf-8")
            raise RuntimeError("download is not a valid zip archive")
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 8)

    monkeypatch.setattr(install, "_DOWNLOAD_ATTEMPTS", 3)
    monkeypatch.setattr(install, "_download_once", fake_once)
    install._download("https://example.test/x.zip", dest, expected_kind="zip")
    assert attempts["n"] == 3
    assert dest.read_bytes().startswith(b"PK\x03\x04")


def test_install_llama_archive_rejects_html_payload(install, tmp_path, monkeypatch):
    archive = tmp_path / "llama-b1-bin-win-vulkan-x64.zip"
    archive.write_text("<html>rate limit</html>", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(install, "BIN_DIR", bin_dir)
    monkeypatch.setattr(install, "write_install_stamp", lambda **kwargs: None)
    assert install._install_llama_archive(archive, tag="v0.5.0", backend="vulkan") is False
    assert not bin_dir.exists() or not any(bin_dir.iterdir())


def test_cmake_backend_flags_macos_multi(install, monkeypatch):
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "_is_linux", lambda: False)
    for backend in ("metal", "vulkan", "auto"):
        # auto is resolved before cmake in install_llama; flags still accept metal/vulkan.
        if backend == "auto":
            continue
        flags = install._cmake_backend_flags(backend)
        assert "-DGGML_METAL=ON" in flags
        assert "-DGGML_VULKAN=ON" in flags
        assert "-DGGML_CUDA=OFF" in flags
        # llama.cpp v0.5.0 statically registers Metal but not Vulkan on macOS.
        assert "-DGGML_BACKEND_DL=ON" in flags


def test_cmake_backend_flags_macos_cpu_only(install, monkeypatch):
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    flags = install._cmake_backend_flags("cpu")
    assert "-DGGML_METAL=OFF" in flags
    assert "-DGGML_VULKAN=OFF" in flags
    assert "-DGGML_METAL=ON" not in flags
    assert "-DGGML_VULKAN=ON" not in flags
    assert "-DGGML_BACKEND_DL=ON" not in flags


def test_cmake_backend_flags_non_macos_exclusive(install, monkeypatch):
    monkeypatch.setattr(install, "_is_macos", lambda: False)
    monkeypatch.setattr(install, "_is_linux", lambda: True)
    vulkan = install._cmake_backend_flags("vulkan")
    assert "-DGGML_VULKAN=ON" in vulkan
    assert "-DGGML_METAL=OFF" in vulkan
    assert "-DGGML_METAL=ON" not in vulkan
    assert "-DGGML_BACKEND_DL=ON" not in vulkan


def test_discard_relocated_cmake_build(install, tmp_path):
    source = tmp_path / "llama.cpp"
    build = source / "build"
    old_source = tmp_path / "old-llama.cpp"
    build.mkdir(parents=True)
    (build / "CMakeCache.txt").write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(old_source) + "\n"
        "CMAKE_CACHEFILE_DIR:INTERNAL=" + str(old_source / "build") + "\n",
        encoding="utf-8",
    )

    install._discard_relocated_cmake_build(source, build)

    assert not build.exists()


def test_keeps_current_cmake_build(install, tmp_path):
    source = tmp_path / "llama.cpp"
    build = source / "build"
    build.mkdir(parents=True)
    cache = build / "CMakeCache.txt"
    cache.write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(source.resolve()) + "\n"
        "CMAKE_CACHEFILE_DIR:INTERNAL=" + str(build.resolve()) + "\n",
        encoding="utf-8",
    )

    install._discard_relocated_cmake_build(source, build)

    assert cache.exists()


def test_pick_release_asset_macos_cpu_rejects_metal_pack(install, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "_is_linux", lambda: False)
    monkeypatch.setattr(install.platform, "machine", lambda: "arm64")
    assets = [
        {"name": "llama-b100-bin-macos-arm64.tar.gz", "browser_download_url": "u1"},
        {"name": "llama-b100-bin-macos-arm64-cpu.tar.gz", "browser_download_url": "u2"},
    ]
    picked = install.pick_release_asset(assets, "cpu")
    assert picked is not None
    assert "cpu" in picked["name"]
    assert install.pick_release_asset(
        [{"name": "llama-b100-bin-macos-arm64.tar.gz", "browser_download_url": "u1"}],
        "cpu",
    ) is None


def test_stamp_backend_helpers(install, monkeypatch):
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    assert install.stamp_backend_for_install("metal") == "metal+vulkan"
    assert install.stamp_backend_for_install("vulkan") == "metal+vulkan"
    assert install.stamp_backend_for_install("cpu") == "cpu"
    assert install.parse_stamp_backends("metal+vulkan") == ["metal", "vulkan"]
    assert install.parse_stamp_backends("metal,vulkan") == ["metal", "vulkan"]
    assert install.backends_satisfied("metal+vulkan", ["metal", "vulkan"])
    assert install.backends_satisfied("metal+vulkan", ["vulkan", "metal", "cuda"])
    assert not install.backends_satisfied("metal+vulkan", ["metal"])
    assert install.backends_satisfied("metal", ["metal"])


def test_stamp_matches_multi_backend(install, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "BIN_DIR", tmp_path)
    monkeypatch.setattr(install, "STAMP_FILE", tmp_path / "llama-release.txt")
    install.write_install_stamp(tag="v0.5.0", backend="metal+vulkan", asset="source")
    assert install.stamp_matches("v0.5.0", "metal+vulkan")
    assert install.stamp_matches("v0.5.0", "vulkan+metal")  # order-independent via frozenset
    assert not install.stamp_matches("v0.5.0", "metal")


def test_llama_install_status_ok_requires_all_stamped_backends(install, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "BIN_DIR", tmp_path)
    monkeypatch.setattr(install, "STAMP_FILE", tmp_path / "llama-release.txt")
    install.write_install_stamp(tag="v0.5.0", backend="metal+vulkan", asset="source")
    llama = tmp_path / "llama-server"
    rpc = tmp_path / "rpc-server"
    llama.write_bytes(b"x")
    rpc.write_bytes(b"x")

    monkeypatch.setattr(
        install,
        "_find_built_binary",
        lambda name: llama if name == "llama-server" else rpc,
    )

    class Result:
        returncode = 0
        stdout = "Available devices:\n  MTL0: Apple M1 (8192 MiB, 8000 MiB free)\n  Vulkan0: Apple M1 (8192 MiB, 8000 MiB free)\n"
        stderr = ""

    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: Result())
    status = install.llama_install_status("v0.5.0")
    assert status["ok"] is True
    assert status["actual_backends"] == ["metal", "vulkan"]

    class MetalOnly:
        returncode = 0
        stdout = "MTL0: Apple M1 (8192 MiB, 8000 MiB free)\n"
        stderr = ""

    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: MetalOnly())
    status = install.llama_install_status("v0.5.0")
    assert status["ok"] is False
