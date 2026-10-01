"""Unsloth-style harness connect: bake kllm API into coding agents."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
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


def _base_url(port: int) -> str:
    return os.environ.get("KLLM_URL", f"http://127.0.0.1:{port}").rstrip("/")


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


def _ensure_inference(port: int, model: str | None, serve: bool, context: int) -> str:
    st = _status(port)
    if st.get("inference_up") and st.get("model_name"):
        model_name = st["model_name"]
        if not isinstance(model_name, str):
            raise typer.BadParameter("agent returned an invalid model name")
        if model and model not in (st.get("model_name") or ""):
            console.print(
                f"[yellow]Inference already running with {st['model_name']}; "
                f"ignoring --model {model}[/yellow]"
            )
        return model_name

    if not serve:
        raise typer.BadParameter("inference not running and --no-serve set")

    if not model:
        local = _models(port)
        if not local:
            raise typer.BadParameter(
                "inference not running — pass --model NAME or run `kllm run --model …` first"
            )
        model = local[0]["id"]

    if st.get("role") != "master":
        raise typer.BadParameter("cluster master required before serving — run `kllm start` first")

    with httpx.Client(timeout=None) as client:
        r = client.post(f"{_base_url(port)}/v1/run", json={"model": model, "context": context})
        if r.status_code >= 400:
            console.print(f"[red]failed to start inference:[/red] {r.text}")
            raise typer.Exit(1)
        data = r.json()
    name = (data.get("plan") or {}).get("model_name") or Path(str(model)).name
    console.print(f"[green]Inference started[/green] model={name}")
    return Path(str(model)).name


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


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
    return LaunchPlan(agent="opencode", argv=[bin_, *extra], env=env)


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
    (home / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": token}),
        encoding="utf-8",
    )
    config_toml = f'''model = "{model}"
model_provider = "kllm"

[model_providers.kllm]
name = "Kluster LLM"
base_url = "{base}/v1"
wire_api = "chat"
'''
    (home / "config.toml").write_text(config_toml, encoding="utf-8")
    env = {"CODEX_HOME": str(home), "OPENAI_API_KEY": token, "OPENAI_BASE_URL": f"{base}/v1"}
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
    cfg.write_text(
        f"""model:
  default: {model}
  providers:
    kllm:
      base_url: {base}/v1
      api_key: {token}
""",
        encoding="utf-8",
    )
    env = {"HERMES_HOME": str(home), "OPENAI_API_KEY": token, "OPENAI_BASE_URL": f"{base}/v1"}
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
                            "apiKey": token,
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
    env = {"OPENCLAW_HOME": str(home), "OPENAI_API_KEY": token, "OPENAI_BASE_URL": f"{base}/v1"}
    cleanup = [] if persist else [home]
    return LaunchPlan(agent="openclaw", argv=[bin_, *extra], env=env, cleanup_dirs=cleanup)


def plan_pi(base: str, token: str, model: str, persist: bool, extra: list[str]) -> LaunchPlan:
    bin_ = _which("pi") or _which("pi-agent")
    if not bin_:
        raise typer.BadParameter("`pi` / `pi-agent` CLI not found on PATH")
    home = home_dir() / "harness" / "pi" if persist else Path(tempfile.mkdtemp(prefix="kllm-pi-"))
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PI_HOME": str(home),
        "OPENAI_API_KEY": token,
        "OPENAI_BASE_URL": f"{base}/v1",
        "PI_MODEL": model,
    }
    cleanup = [] if persist else [home]
    return LaunchPlan(agent="pi", argv=[bin_, *extra], env=env, cleanup_dirs=cleanup)


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
) -> LaunchPlan:
    agent = agent.lower().strip()
    if agent not in PLANNERS:
        raise typer.BadParameter(f"unknown agent {agent!r}; choose from {', '.join(AGENTS)}")
    base = _base_url(port)
    token = api_key or ensure_api_token()
    model_id = _ensure_inference(port, model, serve, context)
    # Normalize to basename for harness model id
    model_id = Path(model_id).name
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
