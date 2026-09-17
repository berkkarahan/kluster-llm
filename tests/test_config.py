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


def test_default_device_is_gpu(kllm_home):
    _, config = kllm_home
    assert config.resolve_device(None) == "gpu"
    assert config.load_config() == {"device": "gpu"}


def test_save_and_load_device(kllm_home):
    home, config = kllm_home
    config.save_config({"device": "cpu"})
    assert (home / "config.toml").read_text(encoding="utf-8").strip() == 'device = "cpu"'
    assert config.get_device() == "cpu"


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
