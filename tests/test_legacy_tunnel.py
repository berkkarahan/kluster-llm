from __future__ import annotations

from types import SimpleNamespace

import pytest

from kllm.shared import legacy_tunnel


def test_legacy_unix_teardown_uses_only_saved_tunnel_and_preserves_files(monkeypatch, tmp_path):
    config = tmp_path / "wg" / "kllm0.conf"
    config.parent.mkdir()
    config.write_text("old config", encoding="utf-8")
    key = config.parent / "private.key"
    key.write_text("old key", encoding="utf-8")
    calls = []
    monkeypatch.setattr(legacy_tunnel, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(legacy_tunnel.platform, "system", lambda: "Linux")
    monkeypatch.setattr(legacy_tunnel.shutil, "which", lambda _: "/usr/bin/wg-quick")
    monkeypatch.setattr(legacy_tunnel.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    legacy_tunnel.teardown_legacy_tunnel()
    assert calls[0][0][0] == ["/usr/bin/wg-quick", "down", str(config)]
    assert config.exists() and key.read_text(encoding="utf-8") == "old key"


def test_legacy_unix_teardown_refuses_missing_tool_or_failure(monkeypatch, tmp_path):
    config = tmp_path / "wg" / "kllm0.conf"
    config.parent.mkdir()
    config.write_text("old config", encoding="utf-8")
    monkeypatch.setattr(legacy_tunnel, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(legacy_tunnel.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(legacy_tunnel.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="wg-quick is missing"):
        legacy_tunnel.teardown_legacy_tunnel()
    monkeypatch.setattr(legacy_tunnel.shutil, "which", lambda _: "/usr/bin/wg-quick")
    monkeypatch.setattr(legacy_tunnel.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="denied"))
    with pytest.raises(RuntimeError, match="denied"):
        legacy_tunnel.teardown_legacy_tunnel()
    assert config.exists()


def test_legacy_windows_teardown_targets_only_owned_tunnel(monkeypatch):
    calls = []
    monkeypatch.setattr(legacy_tunnel.platform, "system", lambda: "Windows")
    monkeypatch.setattr(legacy_tunnel.Path, "is_file", lambda _: True)
    monkeypatch.setattr(legacy_tunnel.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    legacy_tunnel.teardown_legacy_tunnel()
    assert calls[0][0][0][-2:] == ["/uninstalltunnelservice", "kllm0"]
