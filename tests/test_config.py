from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def kllm_home(tmp_path, monkeypatch):
    home = tmp_path / "kllm-home"
    monkeypatch.setenv("KLLM_HOME", str(home))
    import kllm.shared.state as state_mod
    import kllm.shared.config as config_mod

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
        "engine": "llamacpp",
        "split_mode": "layer",
        "unified_memory": None,
        "flags": {"private-network-setup": False, "prima-auto-deploy": False},
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


def test_save_and_load_unified_memory_override(kllm_home):
    home, config = kllm_home
    out = config.save_config({"unified_memory": True})
    assert out["unified_memory"] is True
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "unified_memory = true" in text
    assert config.load_config()["unified_memory"] is True
    config.save_config({"unified_memory": False})
    assert "unified_memory = false" in (home / "config.toml").read_text(encoding="utf-8")
    config.save_config({"unified_memory": None})
    assert "unified_memory" not in (home / "config.toml").read_text(encoding="utf-8")
    assert config.load_config()["unified_memory"] is None
    with pytest.raises(ValueError):
        config.save_config({"unified_memory": "yes"})


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


def test_save_and_load_engine(kllm_home):
    home, config = kllm_home
    out = config.save_config({"engine": "primacpp"})
    assert out["engine"] == "primacpp"
    assert 'engine = "primacpp"' in (home / "config.toml").read_text(encoding="utf-8")
    assert config.get_engine() == "primacpp"
    config.save_config({"engine": "llamacpp"})
    assert config.get_engine() == "llamacpp"
    with pytest.raises(ValueError):
        config.save_config({"engine": "exo"})


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


def test_feature_flags_default_disabled(kllm_home):
    _, config = kllm_home
    assert config.feature_flags() == {"private-network-setup": False, "prima-auto-deploy": False}
    assert config.get_feature_flag("private-network-setup") is False
    assert config.get_feature_flag("prima-auto-deploy") is False
    with pytest.raises(ValueError):
        config.get_feature_flag("nope")


def test_set_feature_flag_roundtrip_and_sqlite(kllm_home):
    home, config = kllm_home
    assert config.set_feature_flag("private-network-setup", True) is True
    assert (home / "managed-network.sqlite").is_file()
    if (home / "config.toml").exists():
        assert "[flags]" not in (home / "config.toml").read_text(encoding="utf-8")
    assert config.get_feature_flag("private-network-setup") is True
    assert config.get_feature_flag("prima-auto-deploy") is False
    config.set_feature_flag("private-network-setup", False)
    assert config.get_feature_flag("private-network-setup") is False


def test_prima_auto_deploy_flag_roundtrip_and_sqlite(kllm_home):
    home, config = kllm_home
    assert config.get_feature_flag("prima-auto-deploy") is False
    assert config.set_feature_flag("prima-auto-deploy", True) is True
    assert (home / "managed-network.sqlite").is_file()
    config.save_config({"device": "cpu"})
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "[flags]" not in text
    assert "prima-auto-deploy" not in text
    assert config.get_feature_flag("prima-auto-deploy") is True
    config.set_feature_flag("prima-auto-deploy", False)
    assert config.get_feature_flag("prima-auto-deploy") is False


def test_unified_sqlite_stores_coexist_in_single_db(kllm_home):
    home, config = kllm_home
    from kllm.modules.managed_network.gateways.store import NetworkStore
    from kllm.modules.primacpp.gateways.store import ProfilingStore

    net_store = NetworkStore(home)
    prof_store = ProfilingStore(home)
    try:
        assert net_store.path == (home / "managed-network.sqlite").resolve()
        assert prof_store.path == (home / "managed-network.sqlite").resolve()
        assert not (home / "prima-profiles.sqlite").exists()

        config.set_feature_flag("prima-auto-deploy", True)
        assert config.feature_flags() == {
            "private-network-setup": False,
            "prima-auto-deploy": True,
        }
    finally:
        prof_store.close()
        net_store.close()


def test_feature_flag_survives_other_config_saves(kllm_home):
    _, config = kllm_home
    config.set_feature_flag("private-network-setup", True)
    config.save_config({"device": "cpu"})
    assert config.load_config()["device"] == "cpu"
    assert config.get_feature_flag("private-network-setup") is True


def test_invalid_feature_flags_rejected(kllm_home):
    _, config = kllm_home
    with pytest.raises(ValueError):
        config.set_feature_flag("nope", True)
    with pytest.raises(ValueError):
        config.set_feature_flag("private-network-setup", "yes")
    with pytest.raises(ValueError):
        config.set_feature_flag("prima-auto-deploy", "yes")
    with pytest.raises(ValueError):
        config.save_config({"flags": {"nope": True}})
    with pytest.raises(ValueError):
        config.save_config({"flags": {"private-network-setup": "yes"}})
    with pytest.raises(ValueError):
        config.save_config({"flags": {"prima-auto-deploy": "yes"}})
    assert config.feature_flags() == {"private-network-setup": False, "prima-auto-deploy": False}
