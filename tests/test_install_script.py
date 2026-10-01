from __future__ import annotations

import importlib.util
import sys
from threading import Barrier
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
    monkeypatch.setattr(install, "BIN_DIR", bin_dir)
    monkeypatch.setattr(install, "VENDOR_LLAMA", vendor)

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
        lambda tag, **kwargs: {
            "pin": tag,
            "stamp": tag,
            "llama_server": "/bin/llama-server",
            "rpc_server": "/bin/rpc-server",
            "requested_backend": "metal+vulkan",
            "expected_backend": kwargs.get("expected") or "metal+vulkan",
            "actual_backends": ["metal", "vulkan"],
            "ok": True,
        },
    )
    assert install.print_llama_check("v0.5.0") == 0


def test_print_llama_check_mismatch(install, monkeypatch):
    monkeypatch.setattr(
        install,
        "llama_install_status",
        lambda tag, **kwargs: {
            "pin": tag,
            "stamp": None,
            "llama_server": None,
            "rpc_server": None,
            "requested_backend": None,
            "expected_backend": kwargs.get("expected"),
            "actual_backends": [],
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

    install._discard_stale_cmake_build(source, build)

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

    install._discard_stale_cmake_build(source, build)

    assert cache.exists()


def test_discards_cmake_build_when_signature_changes(install, tmp_path):
    source = tmp_path / "llama.cpp"
    build = source / "build"
    build.mkdir(parents=True)
    (build / "CMakeCache.txt").write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(source.resolve()) + "\n"
        "CMAKE_CACHEFILE_DIR:INTERNAL=" + str(build.resolve()) + "\n",
        encoding="utf-8",
    )
    install._write_build_signature(build, "tag=v0.5.0\n-DGGML_METAL=ON")

    install._discard_stale_cmake_build(source, build, "tag=v0.5.0\n-DGGML_VULKAN=ON")

    assert not build.exists()


def test_keeps_cmake_build_when_signature_matches(install, tmp_path):
    source = tmp_path / "llama.cpp"
    build = source / "build"
    build.mkdir(parents=True)
    (build / "CMakeCache.txt").write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(source.resolve()) + "\n"
        "CMAKE_CACHEFILE_DIR:INTERNAL=" + str(build.resolve()) + "\n",
        encoding="utf-8",
    )
    install._write_build_signature(build, "tag=v0.5.0\n-DGGML_VULKAN=ON")

    install._discard_stale_cmake_build(source, build, "tag=v0.5.0\n-DGGML_VULKAN=ON")

    assert (build / "CMakeCache.txt").exists()


def test_discards_cmake_build_with_dangling_openssl_path(install, tmp_path):
    """`brew upgrade openssl@3` deletes the Cellar dir the cache pinned."""
    source = tmp_path / "llama.cpp"
    build = source / "build"
    build.mkdir(parents=True)
    (build / "CMakeCache.txt").write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(source.resolve()) + "\n"
        "CMAKE_CACHEFILE_DIR:INTERNAL=" + str(build.resolve()) + "\n"
        "OPENSSL_SSL_LIBRARY:FILEPATH=" + str(tmp_path / "gone" / "libssl.dylib") + "\n"
        "CMAKE_EXE_LINKER_FLAGS:STRING=-L" + str(tmp_path / "gone" / "lib") + "\n",
        encoding="utf-8",
    )

    install._discard_stale_cmake_build(source, build)

    assert not build.exists()


def test_dangling_linker_flag_alone_does_not_discard_build(install, tmp_path, monkeypatch):
    """A dead `-L` (e.g. an exported LDFLAGS) is harmless and must not loop.

    It is re-injected by the environment on every configure, so treating it as
    stale would discard and fully rebuild the tree on every run.
    """
    monkeypatch.setenv("LDFLAGS", f"-L{tmp_path / 'gone' / 'lib'}")
    source = tmp_path / "llama.cpp"
    build = source / "build"
    build.mkdir(parents=True)
    (build / "CMakeCache.txt").write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(source.resolve()) + "\n"
        "CMAKE_CACHEFILE_DIR:INTERNAL=" + str(build.resolve()) + "\n"
        "CMAKE_EXE_LINKER_FLAGS:STRING=-L" + str(tmp_path / "gone" / "lib") + "\n",
        encoding="utf-8",
    )

    install._discard_stale_cmake_build(source, build)

    assert (build / "CMakeCache.txt").exists()
    # The repair step still scrubs the dead flag.
    assert "-UCMAKE_EXE_LINKER_FLAGS" in install._cache_reset_flags(
        install._read_cmake_cache(build)
    )


def test_keeps_cmake_build_when_cached_paths_exist(install, tmp_path):
    source = tmp_path / "llama.cpp"
    build = source / "build"
    build.mkdir(parents=True)
    lib = tmp_path / "live" / "libssl.dylib"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"x")
    (build / "CMakeCache.txt").write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(source.resolve()) + "\n"
        "CMAKE_CACHEFILE_DIR:INTERNAL=" + str(build.resolve()) + "\n"
        "OPENSSL_SSL_LIBRARY:FILEPATH=" + str(lib) + "\n"
        "CMAKE_EXE_LINKER_FLAGS:STRING=-L" + str(tmp_path / "live") + "\n",
        encoding="utf-8",
    )

    install._discard_stale_cmake_build(source, build)

    assert (build / "CMakeCache.txt").exists()


def test_source_build_rejects_binary_missing_expected_backend(install, tmp_path, monkeypatch):
    vendor = tmp_path / "llama.cpp"
    monkeypatch.setattr(install, "VENDOR_LLAMA", vendor)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "ensure_cmake", lambda: True)
    monkeypatch.setattr(install, "clone_or_update_llama", lambda tag=None: None)
    monkeypatch.setattr(install, "ensure_vulkan_macos", lambda: {})
    monkeypatch.setattr(install, "_probe_llama_backends", lambda binary: ["metal"])
    built = vendor / "build" / "bin" / "llama-server"
    built.parent.mkdir(parents=True)
    built.write_bytes(b"x")
    monkeypatch.setattr(install, "_find_build_tree_binary", lambda name: built)

    class Result:
        returncode = 0

    monkeypatch.setattr(install, "_run", lambda cmd, **kwargs: Result())

    assert install.build_llama_from_source("metal", tag="v0.5.0") == (False, "on")


def test_configure_ladder_scrubs_cache_before_disabling_https(install, tmp_path, monkeypatch):
    """Step 2 is the real fix and keeps HTTPS; -DLLAMA_OPENSSL=OFF is step 3."""
    vendor = tmp_path / "llama.cpp"
    monkeypatch.setattr(install, "VENDOR_LLAMA", vendor)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "ensure_cmake", lambda: True)
    monkeypatch.setattr(install, "clone_or_update_llama", lambda tag=None: None)
    monkeypatch.setattr(install, "ensure_vulkan_macos", lambda: {})
    monkeypatch.setattr(install, "_probe_llama_backends", lambda binary: ["metal", "vulkan"])
    built = vendor / "build" / "bin" / "llama-server"
    built.parent.mkdir(parents=True)
    built.write_bytes(b"x")
    monkeypatch.setattr(install, "_find_build_tree_binary", lambda name: built)

    calls: list[list[str]] = []

    class Result:
        def __init__(self, rc: int) -> None:
            self.returncode = rc

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        # Only the third configure (last resort) succeeds.
        return Result(0 if len(calls) >= 3 else 1)

    monkeypatch.setattr(install, "_run", fake_run)

    ok, https = install.build_llama_from_source("metal", tag="v0.5.0")
    assert (ok, https) == (True, "off")

    first, second, third = calls[0], calls[1], calls[2]
    assert install._HTTPS_OFF_FLAG not in first
    assert install._HTTPS_OFF_FLAG not in second, "step 2 must keep HTTPS enabled"
    assert any(flag.startswith("-UOPENSSL") for flag in second), "step 2 must scrub OpenSSL"
    assert install._HTTPS_OFF_FLAG in third


def test_configure_ladder_keeps_https_after_scrub_succeeds(install, tmp_path, monkeypatch):
    vendor = tmp_path / "llama.cpp"
    monkeypatch.setattr(install, "VENDOR_LLAMA", vendor)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "ensure_cmake", lambda: True)
    monkeypatch.setattr(install, "clone_or_update_llama", lambda tag=None: None)
    monkeypatch.setattr(install, "ensure_vulkan_macos", lambda: {})
    monkeypatch.setattr(install, "_probe_llama_backends", lambda binary: ["metal", "vulkan"])
    built = vendor / "build" / "bin" / "llama-server"
    built.parent.mkdir(parents=True)
    built.write_bytes(b"x")
    monkeypatch.setattr(install, "_find_build_tree_binary", lambda name: built)

    calls: list[list[str]] = []

    class Result:
        def __init__(self, rc: int) -> None:
            self.returncode = rc

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return Result(0 if len(calls) >= 2 else 1)

    monkeypatch.setattr(install, "_run", fake_run)

    ok, https = install.build_llama_from_source("metal", tag="v0.5.0")
    assert (ok, https) == (True, "on")
    assert all(install._HTTPS_OFF_FLAG not in cmd for cmd in calls)


def test_https_fallback_is_not_latched_from_cache(install, tmp_path, monkeypatch):
    """A cache recorded as LLAMA_OPENSSL=OFF must not seed the flag again."""
    vendor = tmp_path / "llama.cpp"
    build = vendor / "build"
    build.mkdir(parents=True)
    (build / "CMakeCache.txt").write_text(
        "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(vendor.resolve()) + "\n"
        "CMAKE_CACHEFILE_DIR:INTERNAL=" + str(build.resolve()) + "\n"
        "LLAMA_OPENSSL:BOOL=OFF\n"
        "OPENSSL_SSL_LIBRARY:FILEPATH=/opt/homebrew/lib/libssl.dylib\n",
        encoding="utf-8",
    )
    install._write_build_signature(
        build,
        install._build_signature("v0.5.0", install._cmake_backend_flags("vulkan"), {}),
    )

    monkeypatch.setattr(install, "VENDOR_LLAMA", vendor)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "ensure_cmake", lambda: True)
    monkeypatch.setattr(install, "clone_or_update_llama", lambda tag=None: None)
    monkeypatch.setattr(install, "ensure_vulkan_macos", lambda: {})
    monkeypatch.setattr(install, "_probe_llama_backends", lambda binary: ["metal", "vulkan"])
    monkeypatch.setattr(install, "_find_build_tree_binary", lambda name: build / "bin" / "llama-server")

    calls: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return Result()

    monkeypatch.setattr(install, "_run", fake_run)

    ok, https = install.build_llama_from_source("metal", tag="v0.5.0")
    assert (ok, https) == (True, "on")
    assert all(install._HTTPS_OFF_FLAG not in cmd for cmd in calls)


def test_cache_reset_flags_only_scrub_dangling_linker_flags(install, tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    dangling_entries = {
        "OPENSSL_SSL_LIBRARY:FILEPATH": str(tmp_path / "gone" / "libssl.dylib"),
        "CMAKE_EXE_LINKER_FLAGS:STRING": f"-L{tmp_path / 'gone' / 'lib'}",
    }
    flags = install._cache_reset_flags(dangling_entries)
    assert any(flag.startswith("-UOPENSSL") for flag in flags)
    assert "-UCMAKE_EXE_LINKER_FLAGS" in flags

    live_entries = {
        "OPENSSL_SSL_LIBRARY:FILEPATH": str(tmp_path / "gone" / "libssl.dylib"),
        "CMAKE_EXE_LINKER_FLAGS:STRING": f"-L{live}",
    }
    flags = install._cache_reset_flags(live_entries)
    assert "-UCMAKE_EXE_LINKER_FLAGS" not in flags, "a live linker flag must survive"


def test_cached_package_and_quoted_linker_paths_with_spaces_survive(install, tmp_path):
    sdk = tmp_path / "SDK with spaces #1"
    sdk.mkdir()
    library = sdk / "libssl.a"
    library.write_bytes(b"reference")
    entries = {
        "OPENSSL_SSL_LIBRARY:FILEPATH": str(library),
        "OPENSSL_INCLUDE_DIR:PATH": str(sdk),
        "CMAKE_EXE_LINKER_FLAGS:STRING": f'-L"{sdk}"',
    }
    assert install._dangling_cached_paths(entries) == []
    assert "-UCMAKE_EXE_LINKER_FLAGS" not in install._cache_reset_flags(entries)
    library.unlink()
    assert install._dangling_cached_paths(entries) == [f"OPENSSL_SSL_LIBRARY:FILEPATH={library}"]


def test_quoted_missing_linker_path_resets_only_that_flag(install, tmp_path):
    missing = tmp_path / "missing SDK" / "lib"
    flags = install._cache_reset_flags({"CMAKE_MODULE_LINKER_FLAGS:STRING": f'-L"{missing}"'})
    assert "-UCMAKE_MODULE_LINKER_FLAGS" in flags
    assert "-UCMAKE_EXE_LINKER_FLAGS" not in flags


def test_expected_stamp_for_default_never_raises(install, monkeypatch):
    """Callers run inside request handlers; an escaping SystemExit kills the agent."""
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    assert install.expected_stamp_for_default("auto") == "metal+vulkan"
    assert install.expected_stamp_for_default("metal") == "metal+vulkan"
    assert install.expected_stamp_for_default("vulkan") == "metal+vulkan"
    assert install.expected_stamp_for_default("cpu") == "cpu"
    # Not installable on macOS, so there is no contract to assert.
    assert install.expected_stamp_for_default("cuda") is None
    assert install.expected_stamp_for_default("rocm") is None

    monkeypatch.setattr(install, "_is_macos", lambda: False)
    assert install.expected_stamp_for_default("cuda") is None
    assert install.expected_stamp_for_default("auto") is None


def test_resolve_runtime_binary_prefers_vendor_bin(install, tmp_path, monkeypatch):
    """kllm-native launches vendor/bin first, so verification must probe it."""
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    bin_dir = tmp_path / "vendor-bin"
    vendor = tmp_path / "llama.cpp"
    staged = bin_dir / "llama-server"
    built = vendor / "build" / "bin" / "llama-server"
    for path in (staged, built):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    monkeypatch.setattr(install, "BIN_DIR", bin_dir)
    monkeypatch.setattr(install, "VENDOR_LLAMA", vendor)

    assert install._resolve_runtime_binary("llama-server") == staged


def test_resolve_runtime_binary_falls_back_to_build_tree(install, tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    bin_dir = tmp_path / "vendor-bin"
    bin_dir.mkdir()
    vendor = tmp_path / "llama.cpp"
    built = vendor / "build" / "bin" / "llama-server"
    built.parent.mkdir(parents=True, exist_ok=True)
    built.write_bytes(b"x")
    monkeypatch.setattr(install, "BIN_DIR", bin_dir)
    monkeypatch.setattr(install, "VENDOR_LLAMA", vendor)

    assert install._resolve_runtime_binary("llama-server") == built


def test_install_llama_gates_on_runtime_binary_backends(install, tmp_path, monkeypatch):
    """macOS must not accept a runtime binary that only reports Metal."""
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(install, "VENDOR_LLAMA", tmp_path / "llama.cpp")
    monkeypatch.setattr(install, "read_pinned_release", lambda override=None: "v0.5.0")
    monkeypatch.setattr(install, "assert_stable_llama_pin", lambda tag: None)
    monkeypatch.setattr(install, "stamp_matches", lambda *a, **k: False)
    monkeypatch.setattr(install, "stage_built_binaries", lambda: None)

    staged = tmp_path / "bin" / "llama-server"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"x")
    monkeypatch.setattr(install, "_resolve_runtime_binary", lambda name: staged)
    monkeypatch.setattr(install, "_probe_llama_backends", lambda binary: ["metal"])
    monkeypatch.setattr(
        install, "build_llama_from_source", lambda backend, tag=None: (True, "on")
    )

    with pytest.raises(SystemExit, match="macOS must provide Metal and Vulkan"):
        install.install_llama("metal", prefer_prebuilt=False, force=True)


def test_prefer_prebuilt_rejected_on_macos_gpu(install, monkeypatch):
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "read_pinned_release", lambda override=None: "v0.5.0")
    with pytest.raises(SystemExit, match="not available on macOS"):
        install.main(["--llama-only", "--prefer-prebuilt"])


def test_prefer_prebuilt_allowed_on_macos_cpu(install, monkeypatch, tmp_path):
    """--backend cpu is the sanctioned macOS opt-out."""
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "read_pinned_release", lambda override=None: "v0.5.0")
    monkeypatch.setattr(install, "assert_stable_llama_pin", lambda tag: None)
    monkeypatch.setattr(install, "install_llama", lambda *a, **k: None)
    monkeypatch.setattr(install, "verify_llama_installed", lambda *a, **k: None)

    assert install.main(["--llama-only", "--prefer-prebuilt", "--backend", "cpu"]) == 0


def test_main_runs_install_stages_concurrently_and_reports_failure(
    install, monkeypatch, tmp_path, capsys
):
    barrier = Barrier(3, timeout=5)

    def wait_for_other_stages():
        barrier.wait()

    def build_rust(*args, **kwargs):
        wait_for_other_stages()
        raise RuntimeError("rust build broke")

    monkeypatch.setattr(install, "read_pinned_release", lambda override=None: "v0.5.0")
    monkeypatch.setattr(install, "_is_macos", lambda: False)
    monkeypatch.setattr(install, "ensure_venv", lambda: tmp_path / "python")
    monkeypatch.setattr(install, "build_rust", build_rust)
    monkeypatch.setattr(install, "build_ui", lambda **kwargs: wait_for_other_stages())
    monkeypatch.setattr(install, "detect_backend", lambda backend: "cpu")
    monkeypatch.setattr(install, "install_llama", lambda *args, **kwargs: wait_for_other_stages())
    monkeypatch.setattr(
        install,
        "verify_llama_installed",
        lambda *args, **kwargs: pytest.fail("llama verification must not mask a stage failure"),
    )

    assert install.main(["--skip-toolchains", "--skip-wireguard", "--backend", "cpu"]) == 1
    output = capsys.readouterr()
    assert "kllm (Rust + Python): rust build broke" in output.err
    assert "Install complete." not in output.out


def test_check_reports_https_fallback(install, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(install, "BIN_DIR", tmp_path)
    monkeypatch.setattr(install, "STAMP_FILE", tmp_path / "llama-release.txt")
    install.write_install_stamp(
        tag="v0.5.0", backend="metal+vulkan", asset="source", https="off"
    )
    llama = tmp_path / "llama-server"
    rpc = tmp_path / "rpc-server"
    llama.write_bytes(b"x")
    rpc.write_bytes(b"x")
    monkeypatch.setattr(
        install, "_resolve_runtime_binary", lambda name: llama if name == "llama-server" else rpc
    )

    class Both:
        returncode = 0
        stdout = "MTL0: Apple M1\nVulkan0: Apple M1\n"
        stderr = ""

    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: Both())

    # Backends are the contract, so a visible HTTPS fallback still passes...
    assert install.print_llama_check("v0.5.0", expected="metal+vulkan") == 0
    # ...but it must not be silent.
    assert "https:        off" in capsys.readouterr().out


def test_copy_llama_binaries_never_stages_into_build_tree(install, tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    bin_dir = tmp_path / "vendor-bin"
    vendor = tmp_path / "llama.cpp"
    monkeypatch.setattr(install, "BIN_DIR", bin_dir)
    monkeypatch.setattr(install, "VENDOR_LLAMA", vendor)

    src = tmp_path / "extract" / "llama-b1"
    src.mkdir(parents=True)
    (src / "llama-server").write_bytes(b"x")
    (src / "rpc-server").write_bytes(b"x")
    (src / "libggml.dylib").write_bytes(b"x")

    assert install._copy_llama_binaries_from(tmp_path / "extract")
    assert (bin_dir / "llama-server").is_file()
    assert (bin_dir / "libggml.dylib").is_file()
    assert not (vendor / "build" / "bin").exists()
    assert not (tmp_path / "worker-windows").exists()


def test_install_llama_archive_stamps_normalized_backend(install, tmp_path, monkeypatch):
    import tarfile as _tarfile

    monkeypatch.setattr(install, "_is_windows", lambda: False)
    monkeypatch.setattr(install, "_is_macos", lambda: True)
    monkeypatch.setattr(install, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(install, "VENDOR_LLAMA", tmp_path / "llama.cpp")

    packdir = tmp_path / "llama-b1"
    packdir.mkdir()
    (packdir / "llama-server").write_bytes(b"x")
    (packdir / "rpc-server").write_bytes(b"x")
    archive = tmp_path / "llama-b1-bin-macos-arm64.tar.gz"
    with _tarfile.open(archive, "w:gz") as tf:
        tf.add(packdir, arcname="llama-b1")

    captured: dict = {}
    monkeypatch.setattr(install, "write_install_stamp", lambda **kw: captured.update(kw))

    # A Metal-only macOS pack must not be stamped as the Metal+Vulkan default.
    assert install._install_llama_archive(
        archive, tag="v0.5.0", backend="vulkan", stamp_backend="metal"
    )
    assert captured["backend"] == "metal"

    assert install._install_llama_archive(archive, tag="v0.5.0", backend="vulkan")
    assert captured["backend"] == "metal+vulkan"


def test_check_rejects_metal_only_for_default_macos(install, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "BIN_DIR", tmp_path)
    monkeypatch.setattr(install, "STAMP_FILE", tmp_path / "llama-release.txt")
    install.write_install_stamp(
        tag="v0.5.0", backend="metal", asset="llama-b1-bin-macos-arm64.tar.gz"
    )
    llama = tmp_path / "llama-server"
    rpc = tmp_path / "rpc-server"
    llama.write_bytes(b"x")
    rpc.write_bytes(b"x")
    monkeypatch.setattr(
        install, "_resolve_runtime_binary", lambda name: llama if name == "llama-server" else rpc
    )

    class MetalOnly:
        returncode = 0
        stdout = "Available devices:\n  MTL0: Apple M1\n"
        stderr = ""

    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: MetalOnly())

    assert install.print_llama_check("v0.5.0", expected="metal+vulkan") == 1
    # An explicitly requested Metal-only install is still valid.
    assert install.print_llama_check("v0.5.0", expected="metal") == 0


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


def test_path_entry_contains_respects_platform_case(install, monkeypatch, tmp_path):
    bin_dir = tmp_path / "Local" / "Bin"
    bin_dir.mkdir(parents=True)
    monkeypatch.setattr(install, "_is_windows", lambda: True)
    monkeypatch.setattr(install.os, "pathsep", ";")
    path_env = f"C:\\Tools;{bin_dir};C:\\Other"
    assert install._path_entry_contains(path_env, bin_dir)
    path_env_upper = f"C:\\Tools;{str(bin_dir).upper()};C:\\Other"
    assert install._path_entry_contains(path_env_upper, bin_dir)


def test_install_kllm_path_shim_unix(install, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    repo = tmp_path / "repo"
    repo.mkdir()
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")

    shim = install.install_kllm_path_shim(uv=str(uv), repo_root=repo)

    assert shim == home / ".local" / "bin" / "kllm"
    text = shim.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env sh\nexec ")
    assert " run --directory " in text
    assert ' kllm "$@"' in text
    assert str(uv.resolve()) in text
    assert str(repo.resolve()) in text
    assert (shim.stat().st_mode & 0o111) != 0


def test_install_kllm_path_shim_windows(install, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(install, "_is_windows", lambda: True)
    repo = tmp_path / "repo"
    repo.mkdir()
    uv = tmp_path / "uv.exe"
    uv.write_text("", encoding="utf-8")

    shim = install.install_kllm_path_shim(uv=str(uv), repo_root=repo)

    assert shim.name == "kllm.cmd"
    text = shim.read_text(encoding="utf-8")
    assert "@echo off" in text
    assert " run --directory " in text
    assert " kllm %*" in text


def test_ensure_user_local_bin_on_path_idempotent(install, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    zshrc = home / ".zshrc"
    zshrc.write_text("# existing\n", encoding="utf-8")
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(install, "_is_windows", lambda: False)
    monkeypatch.setenv("PATH", "/usr/bin")
    bin_dir = home / ".local" / "bin"

    assert install.ensure_user_local_bin_on_path(bin_dir) is True
    first = zshrc.read_text(encoding="utf-8")
    assert install._PATH_SHIM_MARKER in first
    assert install._PATH_EXPORT_LINE in first

    assert install.ensure_user_local_bin_on_path(bin_dir) is False
    assert zshrc.read_text(encoding="utf-8") == first


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
        "_resolve_runtime_binary",
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
