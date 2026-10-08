from __future__ import annotations

import subprocess
import threading

import pytest


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    from kllm import accel

    accel._probe_cache.clear()
    yield
    accel._probe_cache.clear()


def _fake_probe(monkeypatch, calls, *, fail_first=False, delay=0.0):
    output = "Available devices:\n  MTL0: Apple M5 Pro (38338 MiB, 38337 MiB free)\n"

    class Result:
        def __init__(self, text):
            self.stdout, self.stderr, self.returncode = text, "", 0

        def check_returncode(self):
            pass

    def run(argv, **_kwargs):
        calls.append(list(argv))
        if fail_first and len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, 15)
        if delay:
            import time

            time.sleep(delay)
        return Result(output if "--list-devices" in argv else "version 0.5.0")

    monkeypatch.setattr("subprocess.run", run)


def test_binary_capabilities_reuses_fresh_probe(monkeypatch):
    from kllm.accel import binary_capabilities

    calls = []
    _fake_probe(monkeypatch, calls)
    first = binary_capabilities("probe-a", 1, 0)
    second = binary_capabilities("probe-a", 1, 1)
    assert first is second
    assert [c[0] for c in calls] == ["probe-a", "probe-a"]
    assert first["devices"][0]["name"] == "Apple M5 Pro"


def test_binary_capabilities_reprobes_after_ttl(monkeypatch):
    from kllm.accel import binary_capabilities

    calls = []
    _fake_probe(monkeypatch, calls)
    monkeypatch.setattr("kllm.accel._PROBE_TTL_S", 0.0)
    binary_capabilities("probe-b", 1, 0)
    binary_capabilities("probe-b", 1, 1)
    assert len(calls) == 4


def test_binary_capabilities_mtime_change_invalidates(monkeypatch):
    from kllm.accel import binary_capabilities

    calls = []
    _fake_probe(monkeypatch, calls)
    binary_capabilities("probe-c", 1, 0)
    binary_capabilities("probe-c", 2, 0)
    assert len(calls) == 4


def test_binary_capabilities_serializes_concurrent_probes(monkeypatch):
    from kllm.accel import binary_capabilities

    calls = []
    _fake_probe(monkeypatch, calls, delay=0.1)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(binary_capabilities("probe-d", 1, 0)))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    assert len(calls) == 2  # one --list-devices + one --version, no duplicate probe


def test_binary_capabilities_does_not_cache_failures(monkeypatch):
    from kllm.accel import binary_capabilities

    calls = []
    _fake_probe(monkeypatch, calls, fail_first=True)
    with pytest.raises(subprocess.TimeoutExpired):
        binary_capabilities("probe-e", 1, 0)
    assert binary_capabilities("probe-e", 1, 0)["devices"]
    assert len(calls) == 3


def test_effective_accel_backend_override(monkeypatch):
    from kllm.accel import effective_accel_backend

    monkeypatch.delenv("KLLM_ACCEL_BACKEND", raising=False)
    assert effective_accel_backend("metal") == "metal"

    monkeypatch.setenv("KLLM_ACCEL_BACKEND", "vulkan")
    assert effective_accel_backend("metal") == "metal"


def test_effective_accel_backend_config_after_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    monkeypatch.delenv("KLLM_ACCEL_BACKEND", raising=False)
    import importlib

    import kllm.accel as accel_mod
    import kllm.config as config_mod
    import kllm.state as state_mod

    importlib.reload(state_mod)
    importlib.reload(config_mod)
    importlib.reload(accel_mod)

    assert accel_mod.effective_accel_backend("metal") == "metal"
    config_mod.save_config({"accel_backend": "vulkan"})
    assert accel_mod.effective_accel_backend("metal") == "metal"

    monkeypatch.setenv("KLLM_ACCEL_BACKEND", "cuda")
    assert accel_mod.effective_accel_backend("metal") == "metal"


def test_allowed_accel_backends_lists_all_rpc_engines():
    from kllm.accel import allowed_accel_backends, selectable_accel_backends

    expected = selectable_accel_backends()
    assert allowed_accel_backends("metal", platform="darwin") == expected
    assert allowed_accel_backends("cpu", platform="linux") == expected


def test_llama_device_for_backend_names():
    from kllm.accel import llama_device_for_backend

    assert llama_device_for_backend("metal") == ("MTL0", "metal")
    assert llama_device_for_backend("vulkan") == ("Vulkan0", "vulkan")
    assert llama_device_for_backend("cuda") == ("CUDA0", "cuda")
    assert llama_device_for_backend("rocm") == ("HIP0", "rocm")


def test_parse_devices_mtl0_prefix():
    from kllm.accel import parse_devices

    text = (
        "Available devices:\n"
        "  MTL0: Apple M1 Pro (25559 MiB, 25558 MiB free)\n"
    )
    devices = parse_devices(text)
    assert len(devices) == 1
    assert devices[0]["device"] == "MTL0"
    assert devices[0]["backend"] == "metal"
    assert devices[0]["name"] == "Apple M1 Pro"


def test_parse_devices_mtl0_m5_pro():
    from kllm.accel import parse_devices

    text = (
        "Available devices:\n"
        "  MTL0: Apple M5 Pro (38338 MiB, 38337 MiB free)\n"
    )
    devices = parse_devices(text)
    assert len(devices) == 1
    assert devices[0]["backend"] == "metal"
    assert devices[0]["name"] == "Apple M5 Pro"


def test_resolve_effective_backend_honors_installed_request():
    from kllm.accel import resolve_effective_backend

    actual, warning = resolve_effective_backend(
        probed="metal",
        supported=["metal", "vulkan"],
        requested="vulkan",
        platform="darwin",
    )
    assert actual == "vulkan"
    assert warning is None


def test_resolve_effective_backend_falls_back_to_probed_metal():
    from kllm.accel import resolve_effective_backend

    actual, warning = resolve_effective_backend(
        probed="metal",
        supported=["metal"],
        requested="vulkan",
        platform="darwin",
    )
    assert actual == "metal"
    assert warning is not None
    assert "using metal" in warning


def test_resolve_effective_backend_auto_uses_probed():
    from kllm.accel import resolve_effective_backend

    actual, warning = resolve_effective_backend(
        probed="metal",
        supported=["metal"],
        requested=None,
        platform="darwin",
    )
    assert actual == "metal"
    assert warning is None


def test_devices_for_backend_filters_mtl_entries():
    from kllm.accel import devices_for_backend

    installed = [
        {"device": "MTL0", "backend": "metal", "name": "Apple M5 Pro"},
        {"device": "Vulkan0", "backend": "vulkan", "name": "Discrete GPU"},
    ]
    metal = devices_for_backend(installed, "metal")
    assert len(metal) == 1
    assert metal[0]["name"] == "Apple M5 Pro"
    assert devices_for_backend(installed, "vulkan") == [installed[1]]
    assert devices_for_backend(installed, "cpu") == []


def test_metal_device_init_failed_detects_zombie_line():
    from kllm.accel import metal_device_init_failed, parse_devices

    zombie = (
        "Available devices:\n"
        "  :  (0 MiB, 0 MiB free)\n"
        "  BLAS: Accelerate (0 MiB, 0 MiB free)\n"
    )
    assert metal_device_init_failed(zombie)
    assert parse_devices(zombie) == []


def test_list_gpus_respects_accel_override(monkeypatch):
    monkeypatch.setenv("KLLM_ACCEL_BACKEND", "vulkan")
    from kllm import native

    if not native.native_available():
        pytest.skip("native extension not built")
    data = native.list_gpus()
    assert data["requested_backend"] == "vulkan"
    assert "probed_backend" in data
