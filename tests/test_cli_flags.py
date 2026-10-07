"""CLI contract for the feature flag command."""

from kllm.cli import main


def test_flags_lists_and_toggles(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))

    assert main(["flags"]) == 0
    assert "private-network-setup = disabled" in capsys.readouterr().out

    assert main(["flags", "private-network-setup", "--enable"]) == 0
    assert "private-network-setup = enabled" in capsys.readouterr().out

    assert main(["flags", "private-network-setup"]) == 0
    assert "private-network-setup = enabled" in capsys.readouterr().out

    assert main(["flags", "private-network-setup", "--disable"]) == 0
    assert "private-network-setup = disabled" in capsys.readouterr().out


def test_flags_unknown_name_is_usage_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    assert main(["flags", "nope", "--enable"]) == 2
    assert "unknown feature flag" in capsys.readouterr().err
