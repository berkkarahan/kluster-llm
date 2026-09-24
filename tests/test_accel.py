from __future__ import annotations

import pytest


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

    assert llama_device_for_backend("metal") == ("Metal", "metal")
    assert llama_device_for_backend("vulkan") == ("Vulkan0", "vulkan")
    assert llama_device_for_backend("cuda") == ("CUDA0", "cuda")
    assert llama_device_for_backend("rocm") == ("HIP0", "rocm")


def test_list_gpus_respects_accel_override(monkeypatch):
    monkeypatch.setenv("KLLM_ACCEL_BACKEND", "vulkan")
    from kllm import native

    if not native.native_available():
        pytest.skip("native extension not built")
    data = native.list_gpus()
    assert data["backend"] == "vulkan"
    assert "probed_backend" in data
