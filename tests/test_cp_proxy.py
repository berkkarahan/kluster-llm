from __future__ import annotations

import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kllm.agent.services.gpus import ResolvedGpuSelection
from kllm.core.gpu_selection import GpuSelection


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    import importlib

    import kllm.agent as agent_mod
    import kllm.netsec as netsec_mod
    import kllm.state as state_mod
    from fastapi.testclient import TestClient

    importlib.reload(state_mod)
    importlib.reload(netsec_mod)
    importlib.reload(agent_mod)
    agent_mod.STATE = state_mod.ClusterState.load()
    if not agent_mod.STATE.node_id:
        agent_mod.STATE.node_id = "testnode"
        agent_mod.STATE.save()
    test_client = TestClient(agent_mod.app)
    yield test_client, agent_mod, netsec_mod
    test_client.close()


@pytest.fixture()
def echo_tcp():
    """Minimal TCP acceptor that reads one line and closes."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    stop = threading.Event()
    clients = set()
    clients_lock = threading.Lock()

    def _run():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                cs, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                if stop.is_set():
                    return
                raise
            with clients_lock:
                clients.add(cs)
            try:
                cs.recv(64)
            except (ConnectionResetError, OSError):
                pass
            finally:
                with clients_lock:
                    clients.discard(cs)
                cs.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    yield port
    stop.set()
    with clients_lock:
        pending = list(clients)
    for client in pending:
        try:
            client.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        client.close()
    srv.close()
    t.join(timeout=1)
    assert not t.is_alive(), "echo server thread did not stop"


@pytest.mark.integration
def test_tcp_reachable_localhost(echo_tcp):
    from kllm import cp_proxy

    assert cp_proxy.tcp_reachable("127.0.0.1", echo_tcp)


def test_tcp_reachable_handles_refusal(monkeypatch):
    from kllm import cp_proxy

    def refused(*_args, **_kwargs):
        raise OSError("controlled refusal")

    monkeypatch.setattr(cp_proxy.socket, "create_connection", refused)
    assert not cp_proxy.tcp_reachable("127.0.0.1", 1)


@pytest.mark.integration
def test_shell_can_reach_localhost(echo_tcp, monkeypatch):
    from kllm import cp_proxy

    run = cp_proxy.subprocess.run

    def controlled_run(args, **kwargs):
        command = str(args[-1])
        if command.endswith("/1") or ".Connect('127.0.0.1',1)" in command:
            return subprocess.CompletedProcess(args, 1)
        return run(args, **kwargs)

    monkeypatch.setattr(cp_proxy.subprocess, "run", controlled_run)
    assert cp_proxy._shell_can_reach("127.0.0.1", echo_tcp)
    assert not cp_proxy._shell_can_reach("127.0.0.1", 1)
    assert not cp_proxy._shell_can_reach("127.0.0.1; rm -rf /", echo_tcp)


def test_ensure_for_master_direct_when_reachable(echo_tcp):
    from kllm import cp_proxy

    cp_proxy.stop_current()
    assert cp_proxy.ensure_for_master("127.0.0.1", echo_tcp) is None


@pytest.mark.unit
def test_ensure_skips_proxy_when_connection_refused(monkeypatch):
    import errno
    from kllm import cp_proxy

    def refused(*_args, **_kwargs):
        raise OSError(errno.ECONNREFUSED, "connection refused")

    cp_proxy.stop_current()
    monkeypatch.setattr(cp_proxy.socket, "create_connection", refused)
    assert cp_proxy.probe_master("127.0.0.1", 1) == "down"
    assert cp_proxy.ensure_for_master("127.0.0.1", 1) is None


@pytest.mark.integration
def test_proxy_relays_when_blocked(echo_tcp, monkeypatch):
    from kllm import cp_proxy

    cp_proxy.stop_current()
    monkeypatch.setattr(cp_proxy, "probe_master", lambda host, port, timeout=1.5: "blocked")
    monkeypatch.setattr(cp_proxy, "find_lan_python", lambda host, port: sys.executable)

    handle = cp_proxy.start("127.0.0.1", echo_tcp)
    try:
        assert handle.listen_port > 0
        assert cp_proxy.tcp_reachable("127.0.0.1", handle.listen_port)
        with socket.create_connection(("127.0.0.1", handle.listen_port), timeout=2) as s:
            s.sendall(b"hi\n")
    finally:
        cp_proxy.stop_current()


@pytest.mark.integration
def test_join_uses_proxy_base(client, monkeypatch):
    """Join register URL goes via localhost proxy when LAN probe fails."""
    _client, agent, _netsec = client
    from kllm import cp_proxy

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'{"ok":true,"control_protocol":2}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            assert self.path == "/v2/cluster/nodes/register"
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = (
                b'{"ok":true,"members":1,"cluster_members":{"testnode":{"node_id":"testnode",'
                b'"host":"10.0.0.2","role":"worker","backend":"cpu","control_port":18765,'
                b'"rpc_port":50052}},"last_plan":null}'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    master_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def ensure_route(host, port):
        agent.STATE.cp_proxy_port = master_port
        return ["control-plane via proxy"]

    monkeypatch.setattr(agent, "_ensure_master_control_route", ensure_route)
    monkeypatch.setattr(
        agent,
        "_probe_capacity_for_cluster",
        lambda: {
            "total_ram_bytes": 1,
            "available_ram_bytes": 1,
            "vram_bytes": 0,
            "backend": "cpu",
        },
    )
    monkeypatch.setattr(
        agent,
        "_resolve_gpu_selection",
        lambda gpu_ids, listed=None: ResolvedGpuSelection(
            GpuSelection((), "cpu", 0), ()
        ),
    )
    monkeypatch.setattr(agent.netsec, "ensure_rpc_firewall", lambda *a, **k: {})
    monkeypatch.setattr(agent.netsec, "pick_rpc_bind_host", lambda preferred, toward=None: "10.0.0.2")
    monkeypatch.setattr(agent.native, "start_worker", lambda **kw: {"pid": None})

    try:
        r = _client.post(
            "/v1/join",
            json={
                "key": "k",
                "master_host": "10.9.9.9",
                "master_port": 18765,
                "overlay": "none",
                "rpc_bind": "10.0.0.2",
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["role"] == "worker"
        assert data["master"] == "10.9.9.9"
        assert data.get("cp_proxy_port") == master_port
        assert any("proxy" in w for w in (data.get("netsec_warnings") or []))
    finally:
        httpd.shutdown()
        cp_proxy.stop_current()
        agent.STATE.cp_proxy_port = None
