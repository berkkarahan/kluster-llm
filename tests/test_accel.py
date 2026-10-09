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
    from kllm.accel import ProbeTimeoutError, binary_capabilities

    calls = []
    _fake_probe(monkeypatch, calls, fail_first=True)
    with pytest.raises(ProbeTimeoutError, match="once manually"):
        binary_capabilities("probe-e", 1, 0)
    assert binary_capabilities("probe-e", 1, 0)["devices"]
    assert len(calls) == 3


def test_binary_capabilities_uses_long_timeout_for_an_unknown_binary(monkeypatch):
    from kllm.accel import _PROBE_FIRST_TIMEOUT_S, _PROBE_TIMEOUT_S, binary_capabilities

    timeouts: list[float] = []

    class Result:
        def __init__(self, text: str):
            self.stdout, self.stderr, self.returncode = text, "", 0

        def check_returncode(self) -> None:
            pass

    def run(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        text = "Available devices:\n  MTL0: Apple M5 Pro (38338 MiB, 38337 MiB free)\n"
        return Result(text if "--list-devices" in argv else "version 0.5.0")

    monkeypatch.setattr("subprocess.run", run)
    binary_capabilities("fresh-bin", 1, 0)
    assert timeouts == [_PROBE_FIRST_TIMEOUT_S, _PROBE_FIRST_TIMEOUT_S]
    # A warm result inside the TTL is reused; it must not start another probe.
    binary_capabilities("fresh-bin", 1, 1)
    assert len(timeouts) == 2
    # Once the binary has completed a probe, a later re-probe stays on the short budget.
    monkeypatch.setattr("kllm.accel._PROBE_TTL_S", 0.0)
    binary_capabilities("fresh-bin", 1, 2)
    assert timeouts[2:] == [_PROBE_TIMEOUT_S, _PROBE_TIMEOUT_S]


def test_binary_capabilities_timeout_names_a_manual_first_launch(monkeypatch):
    from kllm.accel import ProbeTimeoutError, binary_capabilities

    def run(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 120)

    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(ProbeTimeoutError, match="once manually") as caught:
        binary_capabilities("/tmp/llama-server", 1, 0)
    message = str(caught.value)
    assert "timed out after 120 seconds" in message
    assert "run `/tmp/llama-server --list-devices` once manually" in message


def test_failed_probe_keeps_the_long_budget(monkeypatch):
    from kllm.accel import _PROBE_FIRST_TIMEOUT_S, ProbeTimeoutError, binary_capabilities

    timeouts: list[float] = []
    calls = {"n": 0}

    class Result:
        def __init__(self, text: str):
            self.stdout, self.stderr, self.returncode = text, "", 0

        def check_returncode(self) -> None:
            pass

    def run(argv, **kwargs):
        calls["n"] += 1
        timeouts.append(kwargs["timeout"])
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        text = "Available devices:\n  MTL0: Apple M5 Pro (38338 MiB, 38337 MiB free)\n"
        return Result(text if "--list-devices" in argv else "version 0.5.0")

    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(ProbeTimeoutError):
        binary_capabilities("probe-f", 1, 0)
    assert binary_capabilities("probe-f", 1, 0)["devices"]
    assert timeouts[:2] == [_PROBE_FIRST_TIMEOUT_S, _PROBE_FIRST_TIMEOUT_S]


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

    text = "Available devices:\n  MTL0: Apple M1 Pro (25559 MiB, 25558 MiB free)\n"
    devices = parse_devices(text)
    assert len(devices) == 1
    assert devices[0]["device"] == "MTL0"
    assert devices[0]["backend"] == "metal"
    assert devices[0]["name"] == "Apple M1 Pro"


def test_parse_devices_mtl0_m5_pro():
    from kllm.accel import parse_devices

    text = "Available devices:\n  MTL0: Apple M5 Pro (38338 MiB, 38337 MiB free)\n"
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
        "Available devices:\n  :  (0 MiB, 0 MiB free)\n  BLAS: Accelerate (0 MiB, 0 MiB free)\n"
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


def test_unified_memory_heuristic_matches_known_shared_pools(monkeypatch):
    from kllm.accel import detect_unified_memory, is_unified_memory

    monkeypatch.setattr("kllm.config.load_config", lambda: {"unified_memory": None})
    assert detect_unified_memory("metal") is True
    assert detect_unified_memory("vulkan", [{"name": "AMD Radeon RX 9070 XT"}]) is False
    assert detect_unified_memory("vulkan", [{"name": "AMD Radeon 8060S Graphics"}]) is True
    assert detect_unified_memory("rocm", [{"name": "AMD Radeon(TM) Graphics"}]) is True
    assert detect_unified_memory("vulkan", [{"name": "AMD Radeon 890M"}]) is True
    assert detect_unified_memory("vulkan", [{"name": "Intel Iris Xe Graphics"}]) is True
    assert detect_unified_memory("vulkan", [{"name": "Intel(R) UHD Graphics"}]) is True
    assert is_unified_memory("cuda", [{"name": "NVIDIA GeForce RTX 4090"}]) is False


def test_unified_memory_requires_every_device_integrated():
    from kllm.accel import detect_unified_memory, is_integrated_gpu

    assert is_integrated_gpu("AMD Radeon(TM) Graphics", "vulkan") is True
    assert is_integrated_gpu("AMD Radeon RX 9070 XT", "vulkan") is False
    assert is_integrated_gpu("Apple M5 Pro", "metal") is False
    assert is_integrated_gpu(None, "vulkan") is False

    mixed = [
        {"name": "AMD Radeon RX 9070 XT"},
        {"name": "AMD Radeon(TM) Graphics"},
    ]
    assert detect_unified_memory("vulkan", mixed) is False
    assert detect_unified_memory("vulkan", mixed[1:]) is True
    assert detect_unified_memory("vulkan", []) is False


def test_unified_memory_config_override_beats_heuristic(monkeypatch):
    from kllm.accel import is_unified_memory

    monkeypatch.setattr("kllm.config.load_config", lambda: {"unified_memory": False})
    assert is_unified_memory("metal", [{"name": "Apple M4"}]) is False
    monkeypatch.setattr("kllm.config.load_config", lambda: {"unified_memory": True})
    assert is_unified_memory("cuda", [{"name": "NVIDIA GeForce RTX 4090"}]) is True


def test_unified_vram_fraction_env(monkeypatch):
    from kllm.accel import UNIFIED_VRAM_FRACTION_DEFAULT, unified_vram_fraction

    monkeypatch.delenv("KLLM_UNIFIED_VRAM_FRACTION", raising=False)
    assert unified_vram_fraction() == UNIFIED_VRAM_FRACTION_DEFAULT
    monkeypatch.setenv("KLLM_UNIFIED_VRAM_FRACTION", "0.5")
    assert unified_vram_fraction() == 0.5
    monkeypatch.setenv("KLLM_UNIFIED_VRAM_FRACTION", "nope")
    assert unified_vram_fraction() == UNIFIED_VRAM_FRACTION_DEFAULT
    monkeypatch.setenv("KLLM_UNIFIED_VRAM_FRACTION", "0.01")
    assert unified_vram_fraction() == 0.10
    monkeypatch.setenv("KLLM_UNIFIED_VRAM_FRACTION", "1.5")
    assert unified_vram_fraction() == 0.95
