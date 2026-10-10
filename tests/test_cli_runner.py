"""CLI boundary tests for the installed command runner."""

from kllm.cli import main


def test_main_help_returns_success(capsys) -> None:
    assert main(["--help"]) == 0
    assert "Kluster LLM router CLI" in capsys.readouterr().out


def test_main_reports_invalid_command_to_stderr(capsys) -> None:
    assert main(["unknown-command"]) == 2
    assert "Error" in capsys.readouterr().err


def test_main_reports_agent_protocol_mismatch(monkeypatch, tmp_path, capsys) -> None:
    import kllm.modules.cluster.interactors.agent_runtime as cli_runtime

    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    monkeypatch.setattr(
        cli_runtime, "_agent_healthy", lambda _port: {"ok": True, "control_protocol": 1}
    )
    assert main(["status"]) == 1
    assert "incompatible control protocol" in capsys.readouterr().err


def test_plan_cli_forwards_repeated_required_nodes(monkeypatch):
    import json
    import httpx
    import kllm.modules.inference.router_cli as inference

    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"fit_status": "fits"})

    monkeypatch.setattr(
        inference,
        "_client",
        lambda port: httpx.Client(
            base_url="http://agent.test", transport=httpx.MockTransport(respond)
        ),
    )
    assert (
        main(
            [
                "plan",
                "--model",
                "fixture.gguf",
                "--require-node",
                "master",
                "--require-node",
                "worker",
            ]
        )
        == 0
    )
    assert calls[0]["required_nodes"] == ["master", "worker"]


def test_connect_hermes_loopback_uses_local_token(monkeypatch, tmp_path, capsys):
    import json
    import httpx
    from kllm.modules.connect.interactors import connect
    from kllm.modules.connect import router_cli as connect_command

    model = "Ornith-1.5-35B-A3B-Q6_K_L.gguf"
    served = f"/models/{model}"
    token = "local-private-token"
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    monkeypatch.delenv("KLLM_API_KEY", raising=False)
    monkeypatch.delenv("KLLM_URL", raising=False)
    (tmp_path / "api_token").write_text(token)
    monkeypatch.setattr(connect, "_which", lambda name: f"/bin/{name}")

    def unexpected_start(_port):
        raise AssertionError("an explicit URL must not start another agent")

    monkeypatch.setattr(connect_command, "_ensure_agent", unexpected_start)
    calls = []

    def respond(request):
        calls.append(request)
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 18765
        if request.url.path == "/v1/status":
            return httpx.Response(
                200, json={"role": "master", "inference_up": True, "model_name": model}
            )
        assert request.url.path == "/v2/inference/models"
        assert request.headers["authorization"] == f"Bearer {token}"
        return httpx.Response(200, json={"data": [{"id": served}]})

    client_type = httpx.Client
    monkeypatch.setattr(
        connect.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    plans = []
    original_print = connect_command.print_plan

    def inspect_plan(plan):
        plans.append(plan)
        cfg = json.loads((plan.cleanup_dirs[0] / "config.yaml").read_text())
        assert cfg["model"] == {
            "default": served,
            "provider": "custom",
            "base_url": "http://127.0.0.1:18765/v1",
        }
        assert plan.env["OPENAI_API_KEY"] == token
        original_print(plan)

    monkeypatch.setattr(connect_command, "print_plan", inspect_plan)

    assert (
        main(
            [
                "connect",
                "hermes",
                "--url",
                "http://127.0.0.1:18765",
                "--model",
                model,
                "--no-launch",
            ]
        )
        == 0
    )
    assert len(plans) == 1
    assert [request.method for request in calls] == ["GET", "GET"]
    assert all(not directory.exists() for directory in plans[0].cleanup_dirs)
    output = capsys.readouterr()
    assert token not in output.out + output.err
