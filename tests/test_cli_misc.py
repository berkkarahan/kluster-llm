from __future__ import annotations

from types import SimpleNamespace

from kllm.cli import main
from kllm.commands import connect, dashboard, rebuild


def test_rebuild_runs_install_script(monkeypatch, tmp_path):
    marker = tmp_path / "ran"
    script = tmp_path / "scripts" / "install.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "from pathlib import Path\n"
        f"Path({marker.as_posix()!r}).write_text('ok')\n",
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
    assert calls[0][1:] == [str(script)]


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
