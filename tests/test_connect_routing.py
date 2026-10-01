"""Served catalog and harness configuration boundaries."""
import json
import shutil
import tomllib

import httpx
import pytest
import typer

from kllm import connect


def install_transport(monkeypatch, handler):
    client_type = httpx.Client
    monkeypatch.setattr(connect.httpx, "Client", lambda **kwargs: client_type(
        transport=httpx.MockTransport(handler), **kwargs))


def test_worker_resolves_master_and_waits_for_actual_served_id(monkeypatch):
    calls = []
    catalog_calls = 0
    served = '/models/a "quoted" model.gguf'

    def handler(request):
        nonlocal catalog_calls
        calls.append(request)
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=(dict(role="worker", master_host="master", master_port=1234)
                if request.url.host == "worker" else dict(role="master", process_pid=42, model_name=served)))
        assert request.headers["authorization"] == "Bearer private-key"
        catalog_calls += 1
        return httpx.Response(503 if catalog_calls < 3 else 200, json={"data": [{"id": served}]})

    install_transport(monkeypatch, handler)
    monkeypatch.setattr(connect.time, "sleep", lambda _: None)
    base, model = connect._ensure_inference("http://worker:18765", "private-key", served, True, 4096, 5)
    assert base == "http://master:1234"
    assert model == served
    assert all(r.method == "GET" for r in calls)


@pytest.mark.parametrize("probe_status, message", [(401, "token rejected"), (404, "needs updating")])
def test_auth_or_old_agent_failure_never_starts_model(monkeypatch, probe_status, message):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"role": "master"}) if request.url.path == "/v1/status" else httpx.Response(probe_status)

    install_transport(monkeypatch, handler)
    with pytest.raises(typer.BadParameter, match=message):
        connect._ensure_inference("http://master", "key", "model.gguf", True, 4096, 1)
    assert all(r.method == "GET" for r in calls)


def test_no_implicit_model_and_readiness_timeout(monkeypatch):
    install_transport(monkeypatch, lambda r: httpx.Response(200, json={"role": "master"})
        if r.url.path == "/v1/status" else httpx.Response(200 if r.method == "POST" else 503))
    with pytest.raises(typer.BadParameter, match="pass --model"):
        connect._ensure_inference("http://master", "key", None, True, 4096, 1)
    with pytest.raises(typer.BadParameter, match="timed out"):
        connect._ensure_inference("http://master", "key", "a.gguf", True, 4096, 0)


@pytest.mark.parametrize("agent", connect.AGENTS)
def test_all_harnesses_preserve_served_model_and_provider(monkeypatch, agent):
    model = '/models/a "quoted" model.gguf'
    monkeypatch.setattr(connect, "_which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(connect, "_ensure_inference", lambda *args: ("http://master:1234", model))
    plan = connect.build_launch_plan(agent, api_key="private-key", url="http://worker")
    try:
        if agent == "claude":
            assert plan.env["ANTHROPIC_MODEL"] == model
            assert plan.env["ANTHROPIC_BASE_URL"] == "http://master:1234"
        elif agent == "opencode":
            cfg = json.loads(plan.env["OPENCODE_CONFIG_CONTENT"])
            assert cfg["model"] == f"kllm/{model}"
            assert cfg["provider"]["kllm"]["options"]["baseURL"] == "http://master:1234/v1"
        elif agent == "codex":
            cfg = tomllib.loads((plan.cleanup_dirs[0] / "config.toml").read_text())
            assert cfg["model"] == model
            assert cfg["model_providers"]["kllm"]["wire_api"] == "responses"
            assert not (plan.cleanup_dirs[0] / "auth.json").exists()
        elif agent == "hermes":
            cfg = json.loads((plan.cleanup_dirs[0] / "config.yaml").read_text())
            assert cfg["model"] == dict(default=model, provider="custom", base_url="http://master:1234/v1")
            assert cfg["auxiliary"]["compression"]["provider"] == "main"
        else:
            cfg = json.loads((plan.cleanup_dirs[0] / ("models.json" if agent == "pi" else "openclaw.json")).read_text())
            provider = cfg["providers"]["kllm"] if agent == "pi" else cfg["models"]["providers"]["kllm"]
            assert provider["models"][0]["id"] == model
            assert provider["apiKey"] == "${KLLM_API_KEY}"
            assert provider["api"] == "openai-completions"
        for directory in plan.cleanup_dirs:
            for file in directory.iterdir():
                assert "private-key" not in file.read_text(), "credentials belong in process environment"
    finally:
        for directory in plan.cleanup_dirs:
            shutil.rmtree(directory)


def test_remote_endpoint_requires_explicit_token(monkeypatch):
    monkeypatch.delenv("KLLM_URL", raising=False)
    monkeypatch.delenv("KLLM_API_KEY", raising=False)
    with pytest.raises(typer.BadParameter, match="explicit endpoint"):
        connect.build_launch_plan("codex", url="http://master")


@pytest.mark.parametrize("url", ["file:///tmp/model", "http://key@master", "http://master?key=secret"])
def test_invalid_endpoint_rejected(url):
    with pytest.raises(typer.BadParameter):
        connect._base_url(18765, url)


def test_ready_catalog_prevents_launch_when_status_is_transiently_down(monkeypatch):
    calls = []
    served = "/models/ready.gguf"
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"role": "master", "inference_up": False}) if request.url.path == "/v1/status" else httpx.Response(200, json={"data": [{"id": served}]})
    install_transport(monkeypatch, handler)
    assert connect._ensure_inference("http://master", "key", "ready.gguf", True, 4096, 1) == ("http://master", served)
    assert all(request.method == "GET" for request in calls)


def test_opencode_v2_gets_private_server_flag_after_subcommand(monkeypatch):
    monkeypatch.setattr(connect, "_which", lambda _: "/bin/opencode")
    monkeypatch.setattr(connect, "_opencode_service_client", lambda _: True)
    plan = connect.plan_opencode("http://master", "key", "model.gguf", False, ["run", "hello"])
    assert plan.argv == ["/bin/opencode", "run", "--standalone", "hello"]
    with pytest.raises(typer.BadParameter, match="another service"):
        connect.plan_opencode("http://master", "key", "model.gguf", False, ["--server=http://other"])


def test_response_instructions_coalesce_only_leading_text_and_preserve_tools():
    from kllm.gateway import _coalesce_response_instructions
    user = {"role": "user", "content": "hello"}
    late = {"role": "developer", "content": "later"}
    body = {"instructions": "base", "input": [
        {"role": "developer", "content": [{"type": "input_text", "text": "first"}]},
        {"role": "developer", "content": "second"}, user, late], "tools": [{"type": "function", "name": "read"}]}
    result = _coalesce_response_instructions(body)
    assert result["instructions"] == "base\n\nfirst\n\nsecond"
    assert result["input"] == [user, late]
    assert result["tools"] == body["tools"]
    assert len(body["input"]) == 4
