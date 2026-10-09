from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def kllm_home(tmp_path, monkeypatch):
    home = tmp_path / "kllm-home"
    monkeypatch.setenv("KLLM_HOME", str(home))
    import kllm.state as state_mod
    import kllm.runtime as runtime_mod

    importlib.reload(state_mod)
    importlib.reload(runtime_mod)
    return home, runtime_mod


def test_register_agent_and_child_roundtrip(kllm_home):
    home, runtime = kllm_home
    runtime.register_agent(111, 18765)
    runtime.register_child(222, "llama-server")
    path = home / ".runtime.json"
    assert path.exists()
    info = runtime.load()
    assert info.agent_pid == 111
    assert info.control_port == 18765
    assert len(info.children) == 1
    assert info.children[0].pid == 222
    assert info.children[0].kind == "llama-server"


def test_register_child_replaces_previous(kllm_home):
    _, runtime = kllm_home
    runtime.register_child(1, "rpc-server")
    runtime.register_child(2, "llama-server")
    info = runtime.load()
    assert [c.pid for c in info.children] == [2]
    assert info.children[0].kind == "llama-server"


def test_clear_removes_file(kllm_home):
    home, runtime = kllm_home
    runtime.register_agent(42)
    assert (home / ".runtime.json").exists()
    runtime.clear()
    assert not (home / ".runtime.json").exists()
    assert runtime.load().agent_pid is None


def test_clear_agent_only_removes_matching_registration(kllm_home):
    home, runtime = kllm_home
    runtime.register_agent(42, 18765, 43)

    assert runtime.clear_agent_if_matches(99) is False
    assert runtime.load().agent_pid == 42

    assert runtime.clear_agent_if_matches(42) is True
    assert not (home / ".runtime.json").exists()


def test_clear_agent_keeps_tracked_children(kllm_home, monkeypatch):
    _, runtime = kllm_home
    monkeypatch.setattr(runtime.native, "registered_process_identity", lambda _pid: None)
    runtime.register_agent(42, 18765, 43)
    runtime.register_child(99, "llama-server")

    assert runtime.clear_agent_if_matches(42) is True
    info = runtime.load()
    assert info.agent_pid is None
    assert info.agent_log_sink_pid is None
    assert [child.pid for child in info.children] == [99]


def test_any_live_and_kill_tracked(kllm_home, monkeypatch):
    _, runtime = kllm_home
    runtime.register_agent(10, 18765)
    runtime.register_child(20, "llama-server")

    alive = {10: True, 20: True}

    def fake_alive(pid: int) -> bool:
        return alive.get(pid, False)

    stopped: list[tuple[int, bool]] = []

    def fake_stop(pid: int, force: bool = False) -> bool:
        stopped.append((pid, force))
        alive[pid] = False
        return True

    monkeypatch.setattr(runtime.native, "process_alive", fake_alive)
    monkeypatch.setattr(runtime.native, "stop_process", fake_stop)

    assert runtime.any_live() is True
    assert runtime.kill_tracked() is True
    assert stopped == [(20, True), (10, True)]
    assert runtime.any_live() is False


def test_stop_local_does_not_start_agent(kllm_home, monkeypatch):
    _, runtime = kllm_home
    runtime.register_agent(99, 18765)

    import kllm.cli as cli
    import kllm.commands.agent_runtime as cli_runtime

    importlib.reload(cli)

    calls: list[str] = []

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True, "pid": 99}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url: str):
            calls.append(f"GET {url}")
            return FakeResp()

        def post(self, url: str):
            calls.append(f"POST {url}")
            return FakeResp()

        def delete(self, url: str):
            calls.append(f"DELETE {url}")
            return FakeResp()

    monkeypatch.setattr(cli.httpx, "Client", FakeClient)
    monkeypatch.setattr(cli_runtime.runtime, "kill_tracked", lambda: True)
    monkeypatch.setattr(cli_runtime.runtime, "clear", lambda: None)

    ensure_calls: list[int] = []
    monkeypatch.setattr(cli_runtime, "_ensure_agent", lambda port=18765: ensure_calls.append(port))

    assert cli._stop_local(18765) is True
    assert ensure_calls == []
    assert any("/v2/cluster" in c for c in calls)


def test_stop_command_nothing_to_stop(kllm_home, monkeypatch, capsys):
    from kllm.commands import cluster_lifecycle
    import kllm.cli as cli

    importlib.reload(cli)
    monkeypatch.setattr(cluster_lifecycle, "_stop_local", lambda port=18765: False)
    cli.stop(port=18765)
    out = capsys.readouterr().out
    assert "Nothing to stop" in out


def test_health_includes_pid(kllm_home):
    from kllm.agent import health

    data = health()
    assert data["ok"] is True
    assert data["pid"] == os.getpid()


def test_stop_stray_rpc_servers_kills_only_rpc_processes(kllm_home, monkeypatch):
    import psutil

    _, runtime = kllm_home

    class Proc:
        def __init__(self, pid: int, name: str):
            self.info = {"pid": pid, "name": name}
            self.killed = False

        def kill(self) -> None:
            self.killed = True

    procs = [
        Proc(101, "rpc-server"),
        Proc(102, "RPC-SERVER.EXE"),
        Proc(103, "llama-server"),
        Proc(os.getpid(), "python"),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs=None: iter(procs))

    assert runtime.stop_stray_rpc_servers() == 2
    assert procs[0].killed is True
    assert procs[1].killed is True
    assert procs[2].killed is False
    assert procs[3].killed is False
