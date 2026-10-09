from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
import typer

from kllm.cli import main
from kllm.commands import agent_runtime, connect, dashboard, rebuild


class FakePipe:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, pid: int, return_code: int | None, *, exits_on_wait: bool = False) -> None:
        self.pid = pid
        self.return_code = return_code
        self.exits_on_wait = exits_on_wait
        self.stdin = FakePipe()
        self.terminated = False
        self.killed = False
        self.wait_timeouts: list[float] = []

    def poll(self):
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = -15

    def kill(self) -> None:
        self.killed = True
        self.return_code = -9

    def wait(self, timeout: float):
        self.wait_timeouts.append(timeout)
        if self.return_code is None:
            if self.exits_on_wait:
                self.return_code = 0
            else:
                raise subprocess.TimeoutExpired("fake", timeout)
        return self.return_code


def test_rebuild_runs_install_script(monkeypatch, tmp_path):
    marker = tmp_path / "ran"
    script = tmp_path / "scripts" / "install.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        f"from pathlib import Path\nPath({marker.as_posix()!r}).write_text('ok')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rebuild, "repo_root", lambda: tmp_path)
    calls: list[list[str]] = []
    real_run = rebuild.subprocess.run

    def capture_run(argv, **kwargs):
        calls.append(list(argv))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(rebuild.subprocess, "run", capture_run)
    assert main(["rebuild"]) == 0
    assert marker.read_text() == "ok"
    assert calls[0][1:] == [str(script), "--sync"]


def test_rebuild_missing_repo_or_script_exits_one(monkeypatch, tmp_path):
    monkeypatch.setattr(rebuild, "repo_root", lambda: None)
    assert main(["rebuild"]) == 1

    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setattr(rebuild, "repo_root", lambda: root)
    assert main(["rebuild"]) == 1


def test_dashboard_command_starts_agent_and_can_skip_browser(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(dashboard, "_ensure_agent", lambda port: calls.append(("agent", port)))
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: calls.append(("browser", url)))

    dashboard.ui_cmd(port=18765, open_browser=False)

    assert calls == [("agent", 18765)]


def test_dashboard_command_opens_browser_after_agent_is_ready(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(dashboard, "_ensure_agent", lambda port: calls.append(("agent", port)))
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: calls.append(("browser", url)))

    dashboard.ui_cmd(port=18765, open_browser=True)

    assert calls == [
        ("agent", 18765),
        ("browser", "http://127.0.0.1:18765/"),
    ]


def test_agent_start_rejects_corrupt_state_with_recovery_guidance(monkeypatch, tmp_path, capsys):
    state_path = tmp_path / "cluster.json"
    error = agent_runtime.ClusterStateLoadError(state_path, "invalid JSON at line 1, column 1")

    def fail_load(cls, *, migrate=True):
        raise error

    monkeypatch.setattr(agent_runtime.ClusterState, "load", classmethod(fail_load))
    monkeypatch.setattr(agent_runtime, "ensure_home", lambda: tmp_path)
    monkeypatch.setattr(agent_runtime, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_runtime, "_agent_healthy", lambda _port: None)
    monkeypatch.setattr(agent_runtime, "_stop_local", lambda _port, graceful=False: False)

    def unexpected_spawn(*args, **kwargs):
        raise AssertionError("corrupt state must be rejected before spawning")

    monkeypatch.setattr(agent_runtime.subprocess, "Popen", unexpected_spawn)

    with pytest.raises(typer.Exit) as raised:
        agent_runtime._ensure_agent(18765)

    assert raised.value.exit_code == 1
    stderr = capsys.readouterr().err
    assert str(state_path) in stderr
    assert str(tmp_path / "logs" / "agent.log") in stderr
    assert "move it to a backup name" in stderr
    assert "downloaded models are kept" in stderr


def _prepare_failed_agent_start(monkeypatch, tmp_path, proc, sink):
    spawned = iter((sink, proc))
    registrations: list[tuple[int, int, int]] = []
    cleared: list[int] = []
    monkeypatch.setattr(agent_runtime, "ensure_home", lambda: tmp_path)
    monkeypatch.setattr(agent_runtime, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_runtime, "_agent_healthy", lambda _port: None)
    monkeypatch.setattr(agent_runtime, "_stop_local", lambda _port, graceful=False: False)
    monkeypatch.setattr(agent_runtime, "_validate_saved_state", lambda: None)
    monkeypatch.setattr(agent_runtime.log_sink, "command", lambda _path: ["fake-sink"])
    monkeypatch.setattr(agent_runtime.subprocess, "Popen", lambda *args, **kwargs: next(spawned))
    monkeypatch.setattr(
        agent_runtime.runtime,
        "register_agent",
        lambda pid, port, sink_pid: registrations.append((pid, port, sink_pid)),
    )
    monkeypatch.setattr(
        agent_runtime.runtime,
        "clear_agent_if_matches",
        lambda pid: cleared.append(pid) or True,
    )
    return registrations, cleared


def test_agent_start_reports_early_exit_and_cleans_owned_processes(monkeypatch, tmp_path, capsys):
    sink = FakeProcess(71, None, exits_on_wait=True)
    proc = FakeProcess(72, 3)
    registrations, cleared = _prepare_failed_agent_start(monkeypatch, tmp_path, proc, sink)

    with pytest.raises(typer.Exit) as raised:
        agent_runtime._ensure_agent(18765)

    assert raised.value.exit_code == 1
    assert registrations == [(72, 18765, 71)]
    assert cleared == [72]
    assert proc.terminated is False
    assert sink.wait_timeouts == [2]
    stderr = capsys.readouterr().err
    assert "exited during startup with code 3" in stderr
    assert str(tmp_path / "logs" / "agent.log") in stderr


def test_agent_start_timeout_terminates_agent_and_sink(monkeypatch, tmp_path, capsys):
    sink = FakeProcess(81, None)
    proc = FakeProcess(82, None)
    _, cleared = _prepare_failed_agent_start(monkeypatch, tmp_path, proc, sink)

    class UnhealthyClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, _url):
            return SimpleNamespace(status_code=503)

    monkeypatch.setattr(agent_runtime.httpx, "Client", UnhealthyClient)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda _seconds: None)

    with pytest.raises(typer.Exit) as raised:
        agent_runtime._ensure_agent(18765)

    assert raised.value.exit_code == 1
    assert cleared == [82]
    assert proc.terminated is True
    assert sink.terminated is True
    assert "did not become healthy within 10 seconds" in capsys.readouterr().err


def test_connect_dry_run_builds_and_displays_launch_plan(monkeypatch):
    calls: list[object] = []
    plan = SimpleNamespace(agent="claude", cleanup_dirs=[])
    monkeypatch.setattr(connect, "_ensure_agent", lambda port: calls.append(("agent", port)))

    def build(agent_name: str, **kwargs: object) -> SimpleNamespace:
        calls.append(("build", agent_name, kwargs))
        return plan

    monkeypatch.setattr(connect, "build_launch_plan", build)
    monkeypatch.setattr(connect, "print_plan", lambda value: calls.append(("print", value)))

    context = SimpleNamespace(args=["--verbose"])
    connect.connect_cmd(
        ctx=context,
        agent="claude",
        model="toy.gguf",
        api_key="test-key",
        port=18765,
        launch=False,
        serve=False,
        persist=True,
        yolo=True,
        context=8192,
    )

    assert calls == [
        ("agent", 18765),
        (
            "build",
            "claude",
            {
                "port": 18765,
                "model": "toy.gguf",
                "api_key": "test-key",
                "serve": False,
                "persist": True,
                "yolo": True,
                "context": 8192,
                "extra": ["--verbose"],
            },
        ),
        ("print", plan),
    ]
