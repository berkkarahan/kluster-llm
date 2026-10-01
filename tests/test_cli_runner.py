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


def test_plan_cli_forwards_repeated_required_nodes(monkeypatch):
    import json
    import httpx
    import kllm.commands.inference as inference

    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"fit_status": "fits"})
    monkeypatch.setattr(inference, "_client", lambda port: httpx.Client(base_url="http://agent.test", transport=httpx.MockTransport(respond)))
    assert main(["plan", "--model", "fixture.gguf", "--require-node", "master", "--require-node", "worker"]) == 0
    assert calls[0]["required_nodes"] == ["master", "worker"]
