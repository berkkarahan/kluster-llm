from __future__ import annotations

from pathlib import Path

from kllm.cli import main
from kllm.cluster_update import UpdateError
from kllm.commands import update


def _checkout(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _install_script(tmp_path: Path, marker: Path) -> Path:
    script = tmp_path / "scripts" / "install.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "from pathlib import Path\n"
        f"Path({marker.as_posix()!r}).write_text('ok')\n",
        encoding="utf-8",
    )
    return script


def test_update_stops_pulls_and_rebuilds(monkeypatch, tmp_path):
    marker = tmp_path / "ran"
    _install_script(tmp_path, marker)
    root = _checkout(tmp_path)
    notes: list[object] = []
    monkeypatch.setattr(update, "repo_root", lambda: root)
    monkeypatch.setattr(update, "_git_ensure_clean", lambda _root: notes.append("clean"))
    monkeypatch.setattr(
        update, "_stop_local", lambda port: notes.append(("stop", port)) or True
    )
    monkeypatch.setattr(update, "_git_pull_ff_only", lambda _root: notes.append("pull"))

    assert main(["update"]) == 0
    assert marker.read_text() == "ok"
    assert notes == ["clean", ("stop", 18765), "pull"]


def test_update_respects_port_option(monkeypatch, tmp_path):
    root = _checkout(tmp_path)
    ports: list[int] = []
    monkeypatch.setattr(update, "repo_root", lambda: root)
    monkeypatch.setattr(update, "_git_ensure_clean", lambda _root: None)
    monkeypatch.setattr(update, "_stop_local", lambda port: ports.append(port))
    monkeypatch.setattr(update, "_git_pull_ff_only", lambda _root: None)
    monkeypatch.setattr(update, "run_full_install", lambda _root: 0)

    assert main(["update", "--port", "19000"]) == 0
    assert ports == [19000]


def test_update_fails_before_stopping_without_repo_root(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(update, "repo_root", lambda: None)
    monkeypatch.setattr(update, "_stop_local", lambda port: calls.append("stop"))

    assert main(["update"]) == 1
    assert calls == []


def test_update_fails_before_stopping_when_not_git_checkout(monkeypatch, tmp_path):
    calls: list[object] = []
    monkeypatch.setattr(update, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(update, "_stop_local", lambda port: calls.append("stop"))
    monkeypatch.setattr(update, "_git_pull_ff_only", lambda _root: calls.append("pull"))
    monkeypatch.setattr(update, "run_full_install", lambda _root: calls.append("rebuild") or 0)

    assert main(["update"]) == 1
    assert calls == []


def test_update_rejects_dirty_tree_before_stopping(monkeypatch, tmp_path):
    root = _checkout(tmp_path)
    calls: list[object] = []

    def dirty(_root):
        raise UpdateError("working tree is dirty; commit or stash locally before cluster update")

    monkeypatch.setattr(update, "repo_root", lambda: root)
    monkeypatch.setattr(update, "_git_ensure_clean", dirty)
    monkeypatch.setattr(update, "_stop_local", lambda port: calls.append("stop"))

    assert main(["update"]) == 1
    assert calls == []


def test_update_reports_pull_failure_without_rebuilding(monkeypatch, tmp_path, capsys):
    root = _checkout(tmp_path)
    calls: list[object] = []

    def fail_pull(_root):
        raise UpdateError("git pull --ff-only failed: diverged")

    monkeypatch.setattr(update, "repo_root", lambda: root)
    monkeypatch.setattr(update, "_git_ensure_clean", lambda _root: None)
    monkeypatch.setattr(update, "_stop_local", lambda port: calls.append("stop") or True)
    monkeypatch.setattr(update, "_git_pull_ff_only", fail_pull)
    monkeypatch.setattr(update, "run_full_install", lambda _root: calls.append("rebuild") or 0)

    assert main(["update"]) == 1
    assert calls == ["stop"]
    assert "git pull --ff-only failed" in capsys.readouterr().out


def test_update_returns_rebuild_exit_code(monkeypatch, tmp_path):
    root = _checkout(tmp_path)
    monkeypatch.setattr(update, "repo_root", lambda: root)
    monkeypatch.setattr(update, "_git_ensure_clean", lambda _root: None)
    monkeypatch.setattr(update, "_stop_local", lambda port: True)
    monkeypatch.setattr(update, "_git_pull_ff_only", lambda _root: None)
    monkeypatch.setattr(update, "run_full_install", lambda _root: 3)

    assert main(["update"]) == 3


def test_update_fails_when_installer_missing(monkeypatch, tmp_path, capsys):
    root = _checkout(tmp_path)
    monkeypatch.setattr(update, "repo_root", lambda: root)
    monkeypatch.setattr(update, "_git_ensure_clean", lambda _root: None)
    monkeypatch.setattr(update, "_stop_local", lambda port: True)
    monkeypatch.setattr(update, "_git_pull_ff_only", lambda _root: None)

    assert main(["update"]) == 1
    assert "Missing installer" in capsys.readouterr().err
