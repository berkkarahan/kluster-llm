"""Prima diagnostics cross the real file/relay boundary without changing execution."""

import json
import subprocess
import sys
import threading
import time

import pytest

from kllm.shared import prima_diagnostics as diagnostics
from kllm.modules.logs.interactors.cluster_log import LogHub


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))


def set_policy(enabled, generation):
    diagnostics.state_path().write_text(json.dumps({"logging_enabled": enabled, "logging_generation": generation}))


def read_log():
    path = diagnostics.home_dir() / "logs" / "prima.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_phases_relay_as_inference_and_notify_subscribers():
    worker, master = LogHub(), LogHub()
    worker.prime_offsets()
    subscription = master.subscribe()
    for area in ("profiling", "inspection", "deployment", "highs"):
        diagnostics.emit(area, "started", identity="job-a")
    events = worker.tail_new_events("worker-a")
    master.ingest(events, persist=True)
    assert len(events) == 4
    assert all(event["tag"] == "inference" and event["source"] == "llama" for event in events)
    assert all(event["node_id"] == "worker-a" for event in master.recent())
    assert [subscription.get_nowait() for _ in events] == events
    assert worker.tail_new_events("worker-a") == []
    assert len(LogHub().recent()) == 0


def test_worker_relay_uses_existing_forwarder():
    class Forwarder:
        def __init__(self):
            self.pending = []
            self.sent = []
        def enqueue(self, events):
            self.pending.extend(events)
        def flush(self, base, key):
            self.sent.extend(self.pending)
            self.pending.clear()
    forwarder = Forwarder()
    worker = LogHub(forwarder=forwarder)
    worker.prime_offsets()
    diagnostics.emit("profiling", "measured", identity="profile-a")
    worker.relay_tick(node_id="worker-a", role="worker", master_base="http://master", join_key="test")
    assert len(forwarder.sent) == 1
    assert forwarder.sent[0]["tag"] == "inference"
    assert forwarder.sent[0]["node_id"] == "worker-a"


def test_partial_utf8_final_line_and_truncation(tmp_path):
    path = tmp_path / "native.log"
    mirror = diagnostics.FileMirror(path, "inspection", identity="inspect-a")
    path.write_bytes(b"device \xe2")
    mirror.poll()
    assert not read_log()
    with path.open("ab") as output:
        output.write(b"\x82\xac\nfinal")
    mirror.poll()
    mirror.close()
    assert "device €" in read_log()
    assert "final" in read_log()
    path.write_bytes(b"new\n")
    mirror = diagnostics.FileMirror(path, "inspection", existing=True)
    path.write_bytes(b"x\n")
    mirror.poll()
    assert " x\n" in read_log()


def test_disable_discards_output_and_resume_does_not_replay(tmp_path):
    set_policy(True, "one")
    path = tmp_path / "native.log"
    path.write_text("before\n")
    mirror = diagnostics.FileMirror(path, "profiling")
    mirror.poll()
    set_policy(False, "two")
    with path.open("a") as output:
        output.write("hidden\n")
    mirror.poll()
    diagnostics.emit("highs", "hidden solver")
    set_policy(True, "three")
    mirror.poll()
    with path.open("a") as output:
        output.write("after\n")
    mirror.close()
    assert "before" in read_log() and "after" in read_log()
    assert "hidden" not in read_log()


def test_reports_secrets_and_large_partial_lines_are_bounded(tmp_path):
    path = tmp_path / "native.log"
    report = {"architecture": "gemma3", "execution_layers": 32, "tensors": ["private"] * 2000}
    path.write_text("KLLM_PRIMA_MODEL_STRUCTURE=" + json.dumps(report) + "\nAuthorization: Bearer secret\n"
                    + '{"prompt": "sensitive"}\n', encoding="utf-8")
    mirror = diagnostics.FileMirror(path, "inspection")
    mirror.poll()
    assert "architecture=gemma3" in read_log() and "execution_layers=32" in read_log()
    assert "private" not in read_log() and "secret" not in read_log() and "sensitive" not in read_log()
    with path.open("a") as output:
        output.write("KLLM_PRIMA_PROFILE=" + "x" * 9000)
    mirror.poll()
    with path.open("a") as output:
        output.write("do-not-log-continuation\nfinished\n")
    mirror.close()
    assert "do-not-log-continuation" not in read_log()
    assert "finished" in read_log()
    assert len(read_log()) < 10000


@pytest.mark.integration
def test_real_process_output_is_visible_before_exit_and_drained(tmp_path):
    path = tmp_path / "child.log"
    release = tmp_path / "release"
    code = ("import pathlib,sys,time; print('native-start', flush=True); "
            "p=pathlib.Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(.02)\n"
            "print('native-final', end='', flush=True)")
    with path.open("wb") as output, diagnostics.FileMirror(path, "profiling", identity="live") as mirror:
        child = subprocess.Popen([sys.executable, "-c", code, str(release)], stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 5
            while "native-start" not in read_log() and time.monotonic() < deadline:
                time.sleep(.02)
            assert "native-start" in read_log()
            assert child.poll() is None
            release.touch()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert "native-final" in read_log()
    assert not mirror.thread.is_alive()


def test_mirrors_are_isolated_and_recovery_skips_history(tmp_path):
    a, b = tmp_path / "a.log", tmp_path / "b.log"
    a.write_text("historic\n")
    b.write_text("other\n")
    first = diagnostics.FileMirror(a, "deployment", identity="run-a", existing=True)
    second = diagnostics.FileMirror(b, "profiling", identity="job-b")
    with a.open("a") as output:
        output.write("new\n")
    first.close()
    second.close()
    assert "historic" not in read_log()
    assert "[run-a] new" in read_log() and "[job-b] other" in read_log()


def test_operation_failure_keeps_original_exception_and_closes_monitor(tmp_path):
    path = tmp_path / "failure.log"
    @diagnostics.operation("inspection")
    def fail():
        with diagnostics.FileMirror(path, "inspection"):
            path.write_text("native failure")
            raise ValueError("original failure")
    with pytest.raises(ValueError, match="original failure"):
        fail()
    assert "native failure" in read_log() and "failed: original failure" in read_log()


def test_sink_failure_does_not_change_execution(tmp_path):
    (tmp_path / "logs").write_text("not a directory")
    @diagnostics.operation("highs")
    def solve():
        diagnostics.emit("highs", "progress")
        return 42
    assert solve() == 42


def test_burst_and_rotation_are_bounded_and_report_omissions(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_READ", 128)
    monkeypatch.setattr(diagnostics, "MAX_FILE", 512)
    path = tmp_path / "native.log"
    path.write_text("old line\n" * 100 + "latest\n")
    mirror = diagnostics.FileMirror(path, "profiling")
    mirror.close()
    assert "earlier bytes omitted" in read_log() and "latest" in read_log()
    for _ in range(15):
        diagnostics.emit("highs", "solver progress " * 5)
    assert len(read_log().encode()) < 1024
    assert "rotated" in read_log()


@pytest.mark.integration
def test_inspection_hook_preserves_report_and_emits_diagnostics(tmp_path, monkeypatch):
    from kllm.modules.primacpp.interactors import model_structure as inspection
    report = {"schema_version": 1, "architecture": "dense", "stored_blocks": 4, "execution_layers": 4}
    script = tmp_path / "inspect.py"
    script.write_text("print('native-inspection')\nprint(" + repr(inspection.MARKER + json.dumps(report)) + ")")
    real_popen = subprocess.Popen
    monkeypatch.setattr(inspection.subprocess, "Popen", lambda args, **kw: real_popen([sys.executable, str(script)], **kw))
    result = inspection.inspect_model({"llama_server": sys.executable, "capabilities": {"features": [inspection.FEATURE]}},
        tmp_path / "model", tmp_path, check=lambda: None, track=lambda _: None, stop=lambda child: child.kill())
    assert result == report
    assert "native-inspection" in read_log()
    assert "architecture=dense" in read_log()
    assert inspection.MARKER + json.dumps(report) in (tmp_path / "inspection.log").read_text()


def test_progress_is_throttled_but_stage_changes_are_immediate(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: now[0])
    log = diagnostics.ProgressLog()
    progress = {"phase": "profiling", "stage": "load", "label": "Loading", "pct": 0}
    log.update("profiling", "a", progress)
    log.update("profiling", "a", {**progress, "pct": 10})
    now[0] = 1.1
    log.update("profiling", "a", {**progress, "pct": 20})
    log.update("profiling", "a", {**progress, "stage": "sample", "label": "Sampling"})
    assert read_log().count("Loading") == 2
    assert "Sampling" in read_log()


def test_native_callback_keeps_operation_identity_on_another_thread():
    with diagnostics.scope("solve-a"):
        log = diagnostics.bound_emitter("highs")
    thread = threading.Thread(target=lambda: log("iteration"))
    thread.start()
    thread.join(timeout=2)
    assert "[highs][solve-a] iteration" in read_log()


@pytest.mark.integration
def test_real_highs_callback_and_exact_fallback_agree(monkeypatch):
    from kllm.modules.primacpp.interactors import highs_scheduler as scheduler
    pytest.importorskip("highspy")
    model = scheduler.HighsMilpModel()
    x = model.add_var(lb=0, ub=5, cost=1)
    model.add_row(lb=2, ub=float("inf"), cols=[x], vals=[1])
    native = model.solve()
    assert native.feasible and native.solver_backend == "highspy"
    assert "native solve finished" in read_log()
    assert "HiGHS" in read_log()  # Captured by the per-instance native callback.
    monkeypatch.setattr(scheduler, "_solve_with_highspy", lambda _: None)
    exact = model.solve()
    assert exact.feasible and exact.solver_backend == "highs_exact_milp"
    assert exact.objective == native.objective == 2
    assert "highs_exact_milp" in read_log()
