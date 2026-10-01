"""Unsloth-style harness connect: bake kllm API into coding agents."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import typer
from rich.console import Console

from kllm.auth import ensure_api_token
from kllm.state import DEFAULT_CONTROL_PORT, home_dir

console = Console()

AGENTS = ("claude", "opencode", "codex", "hermes", "openclaw", "pi")


@dataclass
class LaunchPlan:
    agent: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    cleanup_dirs: list[Path] = field(default_factory=list)


def _base_url(port: int, url: str | None = None) -> str:
    base = (url or os.environ.get("KLLM_URL") or f"http://127.0.0.1:{port}").rstrip("/")
    parsed = httpx.URL(base)
    if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo or parsed.query or parsed.fragment:
        raise typer.BadParameter("--url must be an HTTP(S) agent URL without credentials, query, or fragment")
    return base


def _status(port: int) -> dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{_base_url(port)}/v1/status")
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict):
            raise typer.BadParameter("agent returned an invalid status response")
        return payload


def _models(port: int) -> list[dict[str, Any]]:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(f"{_base_url(port)}/v1/models")
        r.raise_for_status()
        return list(r.json().get("data") or [])


def _served_model_id(catalog: dict[str, Any], requested: str | None) -> str | None:
    data = catalog.get("data")
    if not isinstance(data, list):
        raise typer.BadParameter("invalid served-model catalog")
    ids = [item.get("id") for item in data if isinstance(item, dict)]
    ids = [item for item in ids if isinstance(item, str) and item]
    matches = [item for item in ids if requested and Path(item).name == Path(requested).name]
    if len(matches) == 1:
        return matches[0]
    if len(ids) == 1 and not requested:
        return ids[0]
    if ids:
        raise typer.BadParameter("served model does not uniquely match the requested model")
    return None


def _ensure_inference(base: str, token: str, model: str | None, serve: bool, context: int, timeout: float) -> tuple[str, str]:
    """Resolve workers to their master and wait for its authenticated served catalog."""
    deadline = time.monotonic() + timeout
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=10.0) as client:
        response = client.get(f"{base}/v1/status")
        response.raise_for_status()
        st = response.json()
        if not isinstance(st, dict):
            raise typer.BadParameter("agent returned an invalid status response")
        if st.get("role") == "worker":
            host = st.get("master_host")
            if not isinstance(host, str) or not host:
                raise typer.BadParameter("worker has no reachable cluster master")
            base = f"http://{host}:{int(st.get('master_port') or DEFAULT_CONTROL_PORT)}"
            response = client.get(f"{base}/v1/status")
            response.raise_for_status()
            st = response.json()
        if st.get("role") != "master":
            raise typer.BadParameter("cluster master required — run `kllm start` first")
        running = st.get("model_name") if st.get("process_pid") or st.get("inference_up") else None
        if running and model and Path(model).name != Path(running).name:
            raise typer.BadParameter(f"inference is using {running}; stop it before selecting --model {model}")
        # Authenticate before starting a model or creating harness configuration.
        probe = client.get(f"{base}/v2/inference/models", headers=headers)
        if probe.status_code in (401, 403):
            raise typer.BadParameter("target API token rejected; set KLLM_API_KEY or --api-key to the master's token")
        if probe.status_code == 404:
            raise typer.BadParameter("target agent needs updating: served-model discovery is unavailable")
        if probe.status_code == 200:
            served = _served_model_id(probe.json(), model or running)
            if served:
                return base, served
        elif probe.status_code != 503:
            probe.raise_for_status()
        if not running:
            if not serve:
                raise typer.BadParameter("inference not running and --no-serve set")
            if not model:
                raise typer.BadParameter("pass --model NAME or start a model in Plan & Run first")
            started = client.post(f"{base}/v1/run", json={"model": model, "context": context}, timeout=timeout)
            started.raise_for_status()
        while True:
            response = client.get(f"{base}/v2/inference/models", headers=headers)
            if response.status_code == 200:
                served = _served_model_id(response.json(), model or running)
                if served:
                    return base, served
            elif response.status_code != 503:
                response.raise_for_status()
            if time.monotonic() >= deadline:
                raise typer.BadParameter("inference readiness timed out; inspect Plan & Run or increase --timeout")
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _opencode_service_client(binary: str) -> bool:
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    match = re.search(r"(\d+)\.\d+\.\d+", version.stdout + version.stderr)
    return bool(match and int(match.group(1)) >= 2)


def plan_claude(base: str, token: str, model: str, yolo: bool, extra: list[str]) -> LaunchPlan:
    bin_ = _which("claude")
    if not bin_:
        raise typer.BadParameter("`claude` CLI not found on PATH")
    env = {
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_AUTH_TOKEN": token,
        "ANTHROPIC_API_KEY": token,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
    }
    argv = [bin_, *extra]
    if yolo and "--dangerously-skip-permissions" not in extra:
        argv.insert(1, "--dangerously-skip-permissions")
    return LaunchPlan(agent="claude", argv=argv, env=env)


def plan_opencode(base: str, token: str, model: str, yolo: bool, extra: list[str]) -> LaunchPlan:
    bin_ = _which("opencode")
    if not bin_:
        raise typer.BadParameter("`opencode` CLI not found on PATH")
    provider_id = "kllm"
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider_id}/{model}",
        "small_model": f"{provider_id}/{model}",
        "provider": {
            provider_id: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Kluster LLM",
                "options": {"baseURL": f"{base}/v1", "apiKey": token},
                "models": {model: {"name": model}},
            }
        },
    }
    if yolo:
        config["permission"] = {"edit": "allow", "bash": "allow", "webfetch": "allow"}
    env = {"OPENCODE_CONFIG_CONTENT": json.dumps(config)}
    # v2's background service has its own environment and ignores this session's
    # provider configuration. A private server inherits the local model/token.
    argv = [bin_, *extra]
    if _opencode_service_client(bin_):
        if any(arg == "--server" or arg.startswith("--server=") for arg in extra):
            raise typer.BadParameter("OpenCode --server uses another service; omit it to connect this session to kllm")
        if "--standalone" not in extra:
            argv.insert(2 if extra and extra[0] in ("run", "mini", "acp") else 1, "--standalone")
    return LaunchPlan(agent="opencode", argv=argv, env=env)


def plan_codex(
    base: str, token: str, model: str, persist: bool, yolo: bool, extra: list[str]
) -> LaunchPlan:
    bin_ = _which("codex")
    if not bin_:
        raise typer.BadParameter("`codex` CLI not found on PATH")
    home = (
        home_dir() / "harness" / "codex"
        if persist
        else Path(tempfile.mkdtemp(prefix="kllm-codex-"))
    )
    home.mkdir(parents=True, exist_ok=True)
    config_toml = f'''model = {json.dumps(model)}
model_provider = "kllm"

[model_providers.kllm]
name = "Kluster LLM"
base_url = {json.dumps(f"{base}/v1")}
wire_api = "responses"
env_key = "KLLM_API_KEY"
requires_openai_auth = false
'''
    (home / "config.toml").write_text(config_toml, encoding="utf-8")
    env = {"CODEX_HOME": str(home), "KLLM_API_KEY": token}
    argv = [bin_, *extra]
    if yolo and "--ask-for-approval" not in " ".join(extra):
        argv[1:1] = ["--ask-for-approval", "never"]
    cleanup = [] if persist else [home]
    return LaunchPlan(
        agent="codex", argv=argv, env=env, cleanup_dirs=cleanup, notes=[f"CODEX_HOME={home}"]
    )


def plan_hermes(base: str, token: str, model: str, persist: bool, extra: list[str]) -> LaunchPlan:
    bin_ = _which("hermes")
    if not bin_:
        raise typer.BadParameter("`hermes` CLI not found on PATH")
    home = (
        home_dir() / "harness" / "hermes"
        if persist
        else Path(tempfile.mkdtemp(prefix="kllm-hermes-"))
    )
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.yaml"
    # JSON is valid YAML and safely quotes model IDs and URLs.
    cfg.write_text(json.dumps({"model": {"default": model, "provider": "custom", "base_url": f"{base}/v1"},
                               "auxiliary": {"compression": {"provider": "main"}}}), encoding="utf-8")
    env = {"HERMES_HOME": str(home), "OPENAI_API_KEY": token, "OPENAI_BASE_URL": f"{base}/v1", "CUSTOM_BASE_URL": f"{base}/v1"}
    cleanup = [] if persist else [home]
    return LaunchPlan(agent="hermes", argv=[bin_, *extra], env=env, cleanup_dirs=cleanup)


def plan_openclaw(base: str, token: str, model: str, persist: bool, extra: list[str]) -> LaunchPlan:
    bin_ = _which("openclaw")
    if not bin_:
        raise typer.BadParameter("`openclaw` CLI not found on PATH")
    home = (
        home_dir() / "harness" / "openclaw"
        if persist
        else Path(tempfile.mkdtemp(prefix="kllm-openclaw-"))
    )
    home.mkdir(parents=True, exist_ok=True)
    cfg_path = home / "openclaw.json"
    cfg_path.write_text(
        json.dumps(
            {
                "models": {
                    "providers": {
                        "kllm": {
                            "baseUrl": f"{base}/v1",
                            "apiKey": "${KLLM_API_KEY}",
                            "api": "openai-completions",
                            "models": [{"id": model, "name": model}],
                        }
                    }
                },
                "agents": {"defaults": {"model": {"primary": f"kllm/{model}"}}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    env = {"OPENCLAW_STATE_DIR": str(home), "OPENCLAW_CONFIG_PATH": str(cfg_path), "KLLM_API_KEY": token}
    cleanup = [] if persist else [home]
    return LaunchPlan(agent="openclaw", argv=[bin_, *extra], env=env, cleanup_dirs=cleanup)


def plan_pi(base: str, token: str, model: str, persist: bool, extra: list[str]) -> LaunchPlan:
    bin_ = _which("pi") or _which("pi-agent")
    if not bin_:
        raise typer.BadParameter("`pi` / `pi-agent` CLI not found on PATH")
    home = home_dir() / "harness" / "pi" if persist else Path(tempfile.mkdtemp(prefix="kllm-pi-"))
    home.mkdir(parents=True, exist_ok=True)
    (home / "models.json").write_text(json.dumps({"providers": {"kllm": {
        "baseUrl": f"{base}/v1", "api": "openai-completions", "apiKey": "${KLLM_API_KEY}",
        "models": [{"id": model, "name": model}]
    }}}), encoding="utf-8")
    env = {"PI_CODING_AGENT_DIR": str(home), "KLLM_API_KEY": token}
    cleanup = [] if persist else [home]
    return LaunchPlan(agent="pi", argv=[bin_, "--provider", "kllm", "--model", model, *extra], env=env, cleanup_dirs=cleanup)


PLANNERS: dict[str, Callable[..., LaunchPlan]] = {
    "claude": lambda **kw: plan_claude(
        kw["base"], kw["token"], kw["model"], kw["yolo"], kw["extra"]
    ),
    "opencode": lambda **kw: plan_opencode(
        kw["base"], kw["token"], kw["model"], kw["yolo"], kw["extra"]
    ),
    "codex": lambda **kw: plan_codex(
        kw["base"], kw["token"], kw["model"], kw["persist"], kw["yolo"], kw["extra"]
    ),
    "hermes": lambda **kw: plan_hermes(
        kw["base"], kw["token"], kw["model"], kw["persist"], kw["extra"]
    ),
    "openclaw": lambda **kw: plan_openclaw(
        kw["base"], kw["token"], kw["model"], kw["persist"], kw["extra"]
    ),
    "pi": lambda **kw: plan_pi(kw["base"], kw["token"], kw["model"], kw["persist"], kw["extra"]),
}


def build_launch_plan(
    agent: str,
    *,
    port: int = DEFAULT_CONTROL_PORT,
    model: str | None = None,
    api_key: str | None = None,
    serve: bool = True,
    persist: bool = False,
    yolo: bool = False,
    context: int = 4096,
    extra: list[str] | None = None,
    url: str | None = None,
    timeout: float = 600,
) -> LaunchPlan:
    agent = agent.lower().strip()
    if agent not in PLANNERS:
        raise typer.BadParameter(f"unknown agent {agent!r}; choose from {', '.join(AGENTS)}")
    base = _base_url(port, url)
    api_key = api_key or os.environ.get("KLLM_API_KEY")
    if (url or os.environ.get("KLLM_URL")) and not api_key:
        raise typer.BadParameter("an explicit endpoint requires --api-key or KLLM_API_KEY")
    token = api_key or ensure_api_token()
    base, model_id = _ensure_inference(base, token, model, serve, context, timeout)
    return PLANNERS[agent](
        base=base,
        token=token,
        model=model_id,
        persist=persist,
        yolo=yolo,
        extra=list(extra or []),
    )


def print_plan(plan: LaunchPlan) -> None:
    console.print(f"[bold]agent[/bold] {plan.agent}")
    console.print(f"[bold]argv[/bold]  {' '.join(plan.argv)}")
    console.print("[bold]env[/bold]")
    for k, v in sorted(plan.env.items()):
        display = (
            v if k not in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY") else "***"
        )
        if "apiKey" in k.lower() or k.endswith("TOKEN") or k.endswith("KEY"):
            display = "***"
        # redact token values that look like secrets
        if isinstance(v, str) and v.startswith("sk-"):
            display = "***"
        if k == "OPENCODE_CONFIG_CONTENT":
            display = "<json config>"
        console.print(f"  {k}={display}")
    for n in plan.notes:
        console.print(f"[dim]{n}[/dim]")


def run_plan(plan: LaunchPlan) -> int:
    env = os.environ.copy()
    env.update(plan.env)
    try:
        proc = subprocess.run(plan.argv, env=env, check=False)
        return int(proc.returncode)
    finally:
        for d in plan.cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)
