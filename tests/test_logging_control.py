"""Persistent policy, file sink, and real detached producer acceptance."""

import json
import os
import subprocess
import sys
import time

import httpx
import psutil
import pytest

from kllm.modules.logs.interactors import logging_control
from kllm.shared import native
from kllm.modules.logs.interactors.cluster_log import LogHub, make_event
from kllm.shared.log_sink import OutputSink
from kllm.shared.state import ClusterState


def wait_for(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("condition did not become true")


def test_persisted_stop_keeps_history_and_discards_output(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState(node_id="n", role="master")
    state.save()
    sink = OutputSink(
        tmp_path / "logs" / "llama.log", tmp_path / "cluster.json", tmp_path / ".log-sinks"
    )
    hub = LogHub()
    try:
        sink.sync()
        sink.write(b"before\n")
        hub.ingest([make_event("n", "llama", "before")], persist=True)
        stream = hub.subscribe()
        journal = tmp_path / "logs" / "cluster.jsonl"
        before_journal = journal.read_bytes()
        logging_control.apply(state, {"enabled": False, "generation": "0001"}, hub)
        assert ClusterState.load().logging_enabled is False
        sink.sync()
        assert sink.file is None
        sink.write(b"discarded\n" * 10000)
        assert hub.ingest([make_event("n", "llama", "discarded")], persist=True) == 0
        assert stream.get(timeout=1) is None
        assert hub.subscribe().get(timeout=1) is None
        assert journal.read_bytes() == before_journal
        assert [event["line"] for event in hub.recent()] == ["before"]
        (tmp_path / "cluster.json").unlink()
        sink.sync()
        assert sink.enabled is False, "missing policy cannot reenable a stopped sink"
        logging_control.apply(state, {"enabled": True, "generation": "0002"}, hub)
        logging_control.apply(state, {"enabled": False, "generation": "0001"}, hub)
        assert state.logging_enabled is True, "delayed heartbeat cannot undo resume"
        sink.sync()
        sink.write(b"after\n")
        assert sink.path.read_bytes() == b"before\nafter\n"
    finally:
        sink.close()


def test_sink_write_failure_keeps_draining_and_reports_error(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    path = tmp_path / "directory"
    path.mkdir()
    sink = OutputSink(path, policy, tmp_path / "acks")
    try:
        sink.sync()
        sink.write(b"output")
        expected = "PermissionError" if os.name == "nt" else "IsADirectoryError"
        assert json.loads(sink.status_path.read_text())["error"] == expected
        policy.write_text(json.dumps(dict(logging_enabled=False, logging_generation="1")))
        sink.sync()
        sink.write(b"output")
        assert sink.error is None
    finally:
        sink.close()


@pytest.mark.integration
@pytest.mark.skipif(
    os.name == "nt" or not native.native_available(),
    reason="requires native launcher and Unix executable fixture",
)
@pytest.mark.parametrize("role", ["master", "worker"])
def test_native_output_sink_survives_parent_and_toggles_without_restarting(
    tmp_path, monkeypatch, role
):
    # Private HTTP producer exercises the same native launch path as llama/RPC.
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState(node_id="private", role="master")
    state.save()
    producer = tmp_path / "producer"
    address = tmp_path / "address"
    producer.write_text(f"""#!{sys.executable}
import os,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        data=self.rfile.read(int(self.headers['Content-Length']))
        sys.stdout.buffer.write(data+b'\\n');sys.stdout.flush()
        sys.stderr.buffer.write(data+b'\\n');sys.stderr.flush()
        self.send_response(200);self.end_headers();self.wfile.write(str(os.getpid()).encode())
    def log_message(self,*args): pass
server=HTTPServer(('127.0.0.1',0),Handler)
open({str(address)!r},'w').write(str(server.server_port))
server.serve_forever()
""")
    producer.chmod(0o755)
    log = tmp_path / "logs" / f"{role}.log"
    env = os.environ.copy()
    env["KLLM_LLAMA_SERVER" if role == "master" else "KLLM_RPC_SERVER"] = str(producer)
    launch = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import json; from kllm import native; print(json.dumps(native.start_{role}(model='unused', tensor_split='1', devices=[], log_path={str(log)!r})))",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert launch.returncode == 0, launch.stderr
    info = json.loads(launch.stdout)
    pid, sink_pid = info["pid"], info["log_sink_pid"]
    ack_path = tmp_path / ".log-sinks" / f"{sink_pid}.json"

    def applied(enabled, generation):
        if not ack_path.exists():
            return False
        ack = json.loads(ack_path.read_text())
        return ack["enabled"] is enabled and ack["generation"] == generation

    try:
        port = wait_for(lambda: address.read_text() if address.exists() else None)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:

            def infer(marker):
                response = client.post("/infer", content=marker)
                assert response.status_code == 200
                assert response.text == str(pid)

            infer("before")
            wait_for(lambda: log.exists() and b"before" in log.read_bytes())
            state.logging_enabled, state.logging_generation = False, "0001"
            state.save()
            wait_for(lambda: applied(False, "0001"))
            size = log.stat().st_size
            for _ in range(4):
                infer("discarded" * 10000)  # Output exceeds pipe capacity; helper must drain.
            assert log.stat().st_size == size
            assert psutil.pid_exists(pid) and psutil.pid_exists(sink_pid)
            state.logging_enabled, state.logging_generation = True, "0002"
            state.save()
            wait_for(lambda: applied(True, "0002"))
            infer("after")
            wait_for(lambda: b"after" in log.read_bytes())
            assert b"discarded" not in log.read_bytes()
    finally:
        psutil.Process(pid).kill()
        wait_for(lambda: not ack_path.exists())


def test_new_sink_inherits_persisted_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState(logging_enabled=False, logging_generation="0001")
    state.save()
    sink = OutputSink(tmp_path / "log", tmp_path / "cluster.json", tmp_path / "acks")
    try:
        sink.sync()
        sink.write(b"discarded")
        assert not sink.path.exists()
        assert json.loads(sink.status_path.read_text())["enabled"] is False
    finally:
        sink.close()


def test_terminal_handler_applies_stop_without_a_new_log_record(tmp_path, monkeypatch):
    import logging
    from kllm.shared.log_sink import PolicyFileHandler

    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState()
    state.save()
    handler = PolicyFileHandler(str(tmp_path / "agent.log"))
    try:
        handler.emit(logging.makeLogRecord({"msg": "before"}))
        assert handler.sink.file is not None
        state.logging_enabled, state.logging_generation = False, "0001"
        state.save()
        wait_for(lambda: handler.sink.file is None and not handler.sink.enabled)
        handler.emit(logging.makeLogRecord({"msg": "discarded"}))
        assert (tmp_path / "agent.log").read_text() == "before\n"
    finally:
        handler.close()
