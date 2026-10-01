"""CLI boundary tests for the installed command runner."""

from kllm.cli import main


def test_main_help_returns_success(capsys) -> None:
    assert main(["--help"]) == 0
    assert "Kluster LLM router CLI" in capsys.readouterr().out


def test_main_reports_invalid_command_to_stderr(capsys) -> None:
    assert main(["unknown-command"]) == 2
    assert "Error" in capsys.readouterr().err


def test_main_reports_agent_protocol_mismatch(monkeypatch, tmp_path, capsys) -> None:
    import kllm.commands.agent_runtime as cli_runtime

    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    monkeypatch.setattr(
        cli_runtime, "_agent_healthy", lambda _port: {"ok": True, "control_protocol": 1}
    )
    assert main(["status"]) == 1
    assert "incompatible control protocol" in capsys.readouterr().err
