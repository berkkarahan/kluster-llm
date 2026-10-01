from __future__ import annotations

from types import SimpleNamespace

from kllm.commands import connect, dashboard


def test_dashboard_command_starts_agent_and_can_skip_browser(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(dashboard, "_ensure_agent", lambda port: calls.append(("agent", port)))
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: calls.append(("browser", url)))

    dashboard.ui_cmd(port=18765, open_browser=False)

    assert calls == [("agent", 18765)]


def test_connect_dry_run_builds_and_displays_launch_plan(monkeypatch):
    calls: list[object] = []
    plan = SimpleNamespace(agent="claude")
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
