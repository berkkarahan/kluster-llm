from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def kllm_home(tmp_path, monkeypatch):
    home = tmp_path / "kllm-home"
    monkeypatch.setenv("KLLM_HOME", str(home))
    import kllm.state as state_mod
    import kllm.config as config_mod

    importlib.reload(state_mod)
    importlib.reload(config_mod)
    return home, config_mod


def test_default_config(kllm_home):
    _, config = kllm_home
    assert config.resolve_device(None) == "gpu"
    assert config.load_config() == {
        "device": "gpu",
        "gpu_ids": None,
        "vram_percent": 80,
        "ram_percent": 80,
        "accel_backend": None,
        "split_mode": "layer",
    }
    assert config.resolve_memory_fraction("gpu") == 0.80
    assert config.resolve_memory_fraction("cpu") == 0.80


def test_save_and_load_accel_backend(kllm_home):
    home, config = kllm_home
    out = config.save_config({"accel_backend": "vulkan"})
    assert out["accel_backend"] == "vulkan"
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert 'accel_backend = "vulkan"' in text
    assert config.load_config()["accel_backend"] == "vulkan"
    config.save_config({"accel_backend": None})
    assert config.get_accel_backend() is None
    text2 = (home / "config.toml").read_text(encoding="utf-8")
    assert "accel_backend" not in text2


def test_invalid_accel_backend_rejected(kllm_home):
    _, config = kllm_home
    with pytest.raises(ValueError):
        config.save_config({"accel_backend": "tpu"})
    with pytest.raises(ValueError):
        config.save_config({"accel_backend": "cpu"})


def test_save_and_load_split_mode(kllm_home):
    home, config = kllm_home
    out = config.save_config({"split_mode": "tensor"})
    assert out["split_mode"] == "tensor"
    assert 'split_mode = "tensor"' in (home / "config.toml").read_text(encoding="utf-8")
    assert config.get_split_mode() == "tensor"
    with pytest.raises(ValueError):
        config.save_config({"split_mode": "pipeline"})


def test_save_and_load_full_config(kllm_home):
    home, config = kllm_home
    out = config.save_config(
        {"device": "cpu", "vram_percent": 70, "ram_percent": 50, "gpu_ids": [0, 2]}
    )
    assert out["device"] == "cpu"
    assert out["vram_percent"] == 70
    assert out["ram_percent"] == 50
    assert out["gpu_ids"] == [0, 2]
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert 'device = "cpu"' in text
    assert "vram_percent = 70" in text
    assert "ram_percent = 50" in text
    assert "gpu_ids = [0, 2]" in text
    assert config.load_config()["ram_percent"] == 50
    assert config.resolve_memory_fraction("cpu") == 0.50
    assert config.resolve_memory_fraction("gpu") == 0.70


def test_partial_save_preserves_other_keys(kllm_home):
    _, config = kllm_home
    config.save_config({"device": "gpu", "vram_percent": 60, "ram_percent": 40, "gpu_ids": [1]})
    config.save_config({"device": "cpu"})
    cfg = config.load_config()
    assert cfg["device"] == "cpu"
    assert cfg["vram_percent"] == 60
    assert cfg["ram_percent"] == 40
    assert cfg["gpu_ids"] == [1]


def test_clear_gpu_ids(kllm_home):
    _, config = kllm_home
    config.save_config({"gpu_ids": [0]})
    config.save_config({"gpu_ids": None})
    assert config.get_gpu_ids() is None
    text = (kllm_home[0] / "config.toml").read_text(encoding="utf-8")
    assert "gpu_ids" not in text


def test_resolve_precedence_override_beats_config(kllm_home):
    _, config = kllm_home
    config.save_config({"device": "cpu"})
    assert config.resolve_device(None) == "cpu"
    assert config.resolve_device("gpu") == "gpu"
    assert config.resolve_device("CPU") == "cpu"


def test_invalid_device_rejected(kllm_home):
    _, config = kllm_home
    with pytest.raises(ValueError):
        config.save_config({"device": "tpu"})
    with pytest.raises(ValueError):
        config.resolve_device("tpu")


def test_invalid_percent_rejected(kllm_home):
    _, config = kllm_home
    with pytest.raises(ValueError):
        config.save_config({"vram_percent": 5})
    with pytest.raises(ValueError):
        config.save_config({"ram_percent": 101})


def test_invalid_gpu_ids_rejected(kllm_home):
    _, config = kllm_home
    with pytest.raises(ValueError):
        config.save_config({"gpu_ids": []})
    with pytest.raises(ValueError):
        config.save_config({"gpu_ids": [-1]})
