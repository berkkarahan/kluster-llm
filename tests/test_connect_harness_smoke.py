"""Opt-in installed-harness requests through a private gateway to running inference."""

from contextlib import asynccontextmanager
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from starlette.responses import Response

from kllm.modules.connect.interactors import connect

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("KLLM_TEST_HARNESS_SMOKE") != "1",
        reason="opt-in installed harness and running model acceptance",
    ),
]


@pytest.fixture
def private_gateway(tmp_path, monkeypatch):
    from kllm import agent
    from kllm.shared import auth
    from kllm.shared.state import ClusterState

    infer_port = int(os.environ.get("KLLM_TEST_INFER_PORT", "8080"))
    with httpx.Client(trust_env=False) as client:
        response = client.get(f"http://127.0.0.1:{infer_port}/v1/models")
        response.raise_for_status()
        model = response.json()["data"][0]["id"]
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm"))
    state = ClusterState(
        role="master", node_id="private-smoke", model_name=Path(model).name, infer_port=infer_port
    )
    monkeypatch.setattr(agent, "STATE", state)

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            trust_env=False, timeout=httpx.Timeout(None, connect=5)
        ) as upstream:
            monkeypatch.setattr(agent.app.state, "inference_client", upstream, raising=False)
            yield

    wrapper = FastAPI(lifespan=lifespan)
    wrapper.mount("/", agent.app)
    requests = []

    @wrapper.middleware("http")
    async def record(request, call_next):
        response = await call_next(request)
        if response.status_code >= 400:
            body = b"".join([chunk async for chunk in response.body_iterator])
            requests.append(
                (
                    request.method,
                    request.url.path,
                    response.status_code,
                    body.decode(errors="replace")[:1000],
                )
            )
            return Response(body, status_code=response.status_code, headers=dict(response.headers))
        requests.append((request.method, request.url.path, response.status_code))
        return response

    token = auth.ensure_api_token()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(wrapper, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "private gateway failed to start"
            time.sleep(0.02)
        yield f"http://127.0.0.1:{port}", token, model, requests
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


@pytest.mark.parametrize("harness", ["codex", "opencode", "pi"])
def test_installed_harness_uses_running_cluster_model(private_gateway, tmp_path, harness):
    if not shutil.which(harness):
        pytest.skip(f"{harness} is not installed; configuration unit coverage remains separate")
    base, token, served, requests = private_gateway
    prompt = "Reply with READY only. Do not inspect files, execute commands, or call tools."
    args = {
        "codex": ["exec", "--skip-git-repo-check", "--json", "--sandbox", "read-only", prompt],
        "opencode": ["run", "--format", "json", prompt],
        "pi": ["--print", "--mode", "json", "--no-session", prompt],
    }[harness]
    plan = connect.build_launch_plan(
        harness, url=base, api_key=token, model=Path(served).name, extra=args
    )
    try:
        env = {
            **os.environ,
            **plan.env,
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
        }
        output = subprocess.run(
            plan.argv, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180
        )
        diagnostic = (output.stdout + output.stderr).replace(token, "[redacted]")
        assert output.returncode == 0, str(requests) + diagnostic[-5000:]
        assert "READY" in output.stdout, diagnostic[-5000:]
        assert any(
            record[1] in ("/v1/responses", "/v1/chat/completions") and record[2] == 200
            for record in requests
        ), "harness must call this gateway"
    finally:
        for directory in plan.cleanup_dirs:
            shutil.rmtree(directory, ignore_errors=True)
