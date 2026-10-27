"""Best-effort Prima diagnostics, independent of execution and evidence files."""

from __future__ import annotations

import codecs
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial, wraps
import json
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from kllm.shared.state import home_dir, state_path

_context = ContextVar("prima_log_context", default="")
_write_lock = threading.RLock()
MAX_LINE = 4096
MAX_READ = 256 * 1024
MAX_FILE = 8 * 1024 * 1024
_SECRET = re.compile(r"(?i)(authorization|api[_-]?key|access[_-]?token|join[_-]?key)([\s\"':=]+)(?:Bearer\s+)?[^\s,\"}]+")
_REPORT_FIELDS = ("schema_version", "architecture", "stored_blocks", "execution_layers",
                  "backend", "rank", "ngl", "layers", "phase", "readiness_stage",
                  "cpu_q4k_gflops", "memory_available_gib", "prefill_ms", "decode_ms",
                  "peak_memory_bytes", "solver_backend", "objective_step_ms", "fallback_reason")


def policy():
    """Read the same durable policy as native output sinks; fail closed on invalid data."""
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
        return value.get("logging_enabled", True) is True, value.get("logging_generation", "")
    except FileNotFoundError:
        return True, ""
    except (OSError, ValueError, AttributeError):
        return False, ""


def summary(value):
    if not isinstance(value, dict):
        return "report received (full report retained in job log)"
    fields = [f"{key}={value[key]}" for key in _REPORT_FIELDS
              if key in value and isinstance(value[key], (str, int, float, bool))]
    return "; ".join(fields) or "report received (full report retained in job log)"


def clean_line(line):
    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(line)).strip()
    if re.match(r"^KLLM_PRIMA_[A-Z0-9_]+=", line):
        marker, payload = line.split("=", 1)
        try:
            line = f"{marker}: {summary(json.loads(payload))}"
        except ValueError:
            line = f"{marker}: incomplete report (full output retained in job log)"
    # Never mirror native request/response payload dumps into the cluster journal.
    if re.search(r'(?i)["\'](?:prompt|messages|content|completion|api_key)["\']\s*:', line):
        return "request/response payload omitted"
    line = _SECRET.sub(r"\1\2[redacted]", line)
    return line[:MAX_LINE] + (" … [diagnostic truncated]" if len(line) > MAX_LINE else "")


def emit(area, message, *, identity=None, **fields):
    """Serialize only diagnostic values supplied by producers, never execution records."""
    try:
        with _write_lock:
            if not policy()[0]:
                return
            context = identity if identity is not None else _context.get()
            prefix = f"[prima][{area}]" + (f"[{context}]" if context else "")
            details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
            path = home_dir() / "logs" / "prima.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            # Reuse the tailer's truncate detection; diagnostics must remain bounded.
            mode = "ab"
            if path.exists() and path.stat().st_size >= MAX_FILE:
                mode = "wb"
            with path.open(mode) as output:
                if mode == "wb":
                    output.write(b"[prima] diagnostic file rotated; older lines omitted\n")
                for line in (str(message) + (" " + details if details else "")).splitlines():
                    if line.strip():
                        output.write(f"{prefix} {clean_line(line)}\n".encode("utf-8", errors="replace"))
    except Exception:
        pass  # Diagnostics may never change inference, scheduling, or cleanup results.


@contextmanager
def scope(identity):
    token = _context.set(str(identity))
    try:
        yield
    finally:
        _context.reset(token)


def bound_emitter(area):
    """Keep operation identity when a native library invokes a callback on its thread."""
    return partial(emit, area, identity=_context.get())


def operation(area):
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with scope(_context.get() or str(uuid4())):
                start = time.monotonic()
                emit(area, f"{function.__name__} started")
                try:
                    result = function(*args, **kwargs)
                except BaseException as error:
                    emit(area, f"{function.__name__} failed: {error}", elapsed_s=round(time.monotonic() - start, 3))
                    raise
                emit(area, f"{function.__name__} completed", elapsed_s=round(time.monotonic() - start, 3))
                if area == "highs" and result is not None:
                    emit(area, "solver result", **{key: getattr(result, key, None) for key in
                         ("solver_backend", "feasible", "objective", "objective_step_ms", "fallback_reason",
                          "cycle_count_k", "iterations")})
                    for allocation in getattr(result, "node_allocations", []):
                        emit(area, "selected allocation", node=allocation.node_id, rank=allocation.rank,
                             layers=allocation.total_layers, gpu_layers=allocation.total_gpu_layers)
                return result
        return invoke
    return decorate


class ProgressLog:
    """One observer per owning service; repeated progress is limited to 1 Hz."""

    def __init__(self):
        self.previous = {}

    def update(self, area, identity, progress):
        now = time.monotonic()
        stage = (progress.get("phase"), progress.get("stage"), progress.get("label"))
        value = (stage, progress.get("bytes_done"), progress.get("pct"))
        prior = self.previous.get(identity)
        if prior and prior[0] == value:
            return
        if prior and prior[0][0] == stage and now - prior[1] < 1:
            return
        self.previous[identity] = (value, now)
        if len(self.previous) > 256:
            self.previous.pop(next(iter(self.previous)))
        emit(area, progress.get("label", "progress"), identity=identity,
             phase=progress.get("phase"), stage=progress.get("stage"), pct=progress.get("pct"),
             bytes_done=progress.get("bytes_done"), bytes_total=progress.get("bytes_total"))


class FileMirror:
    """Read owned files incrementally without interfering with native evidence consumers."""

    def __init__(self, path, area, *, identity=None, existing=False, callback=emit):
        self.path, self.area = Path(path), area
        self.identity = identity if identity is not None else _context.get()
        self.callback = callback
        self.offset = 0
        self.partial = ""
        self.discard_line = False
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.last_policy = policy()
        self.stopped = threading.Event()
        self.thread = None
        if existing:
            try:
                self.offset = self.path.stat().st_size
            except OSError:
                pass

    def _line(self, line):
        if line.strip():
            self.callback(self.area, clean_line(line), identity=self.identity)

    def poll(self, *, final=False):
        try:
            current_policy = policy()
            size = self.path.stat().st_size
            if not current_policy[0] or current_policy != self.last_policy:
                self.offset = size
                self.partial = ""
                self.decoder.reset()
                self.discard_line = False
            self.last_policy = current_policy
            if not current_policy[0]:
                return
            if size < self.offset:
                self.offset = 0
                self.partial = ""
                self.decoder.reset()
                self.discard_line = False
            # On a burst keep a bounded tail, and make loss visible.
            if size - self.offset > MAX_READ:
                self.offset = size - MAX_READ
                self.partial = ""
                self.decoder.reset()
                self.discard_line = False
                self._line("native diagnostic burst: earlier bytes omitted")
                with self.path.open("rb") as source:
                    source.seek(self.offset)
                    skipped = source.readline(MAX_READ)
                    self.offset += len(skipped)
            with self.path.open("rb") as source:
                source.seek(self.offset)
                data = source.read(MAX_READ)
                self.offset = source.tell()
            text = self.partial + self.decoder.decode(data, final=final)
            if self.discard_line:
                if "\n" not in text:
                    return
                text = text.split("\n", 1)[1]
                self.discard_line = False
            lines = text.split("\n")
            self.partial = lines.pop()
            for line in lines:
                self._line(line)
            if len(self.partial) > MAX_LINE:
                # Reports can span megabytes; do not retain a growing partial line.
                self._line(self.partial[:MAX_LINE] + " [partial diagnostic truncated]")
                self.partial = ""
                self.discard_line = True
            if final and self.partial:
                self._line(self.partial)
                self.partial = ""
        except Exception:
            pass

    def start(self):
        def run():
            while not self.stopped.wait(0.1):
                self.poll()
        self.thread = threading.Thread(target=run, name="prima-log-mirror", daemon=True)
        try:
            self.thread.start()
        except RuntimeError:
            self.thread = None
        return self

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.poll(final=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
