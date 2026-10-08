"""Private output drain, independent of the lifetime of the controlling agent."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import threading

from kllm.atomic_io import replace_file
from kllm.state import home_dir, state_path


def command(path: str | Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "kllm.log_sink",
        "--log-path",
        str(path),
        "--state-path",
        str(state_path()),
        "--status-dir",
        str(home_dir() / ".log-sinks"),
    ]


class OutputSink:
    """Bounded synchronous writes; dropped output is never queued for replay."""

    def __init__(self, path: Path, policy: Path, status_dir: Path) -> None:
        self.path, self.policy = path, policy
        status_dir.mkdir(parents=True, exist_ok=True)
        self.status_path = status_dir / f"{os.getpid()}.json"
        self.lock = threading.Lock()
        self.enabled = True
        self.generation = ""
        self.file = None
        self.error: str | None = None
        self.last_status: dict | None = None

    def sync(self) -> None:
        try:
            policy = json.loads(self.policy.read_text(encoding="utf-8"))
            enabled, generation = (
                policy.get("logging_enabled", True),
                policy.get("logging_generation", ""),
            )
            if type(enabled) is not bool or not isinstance(generation, str):
                return
        except FileNotFoundError:
            return  # A missing policy must not undo a previously applied stop.
        except (OSError, ValueError, AttributeError):
            return  # Keep the last valid policy on transient read failures.
        with self.lock:
            self.enabled, self.generation = enabled, generation
            if not enabled and self.file:
                self.file.close()
                self.file = None
            if not enabled:
                self.error = None
            self._ack()

    def _ack(self) -> None:
        status = dict(
            pid=os.getpid(),
            path=str(self.path),
            enabled=self.enabled,
            generation=self.generation,
            error=self.error,
        )
        if status != self.last_status:
            temporary = self.status_path.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps(status), encoding="utf-8")
                replace_file(temporary, self.status_path)
                self.last_status = status
            except OSError:
                pass  # Always keep draining the producer; missing ack reports pending.

    def write(self, chunk: bytes) -> None:
        with self.lock:
            if not self.enabled:
                return
            try:
                if self.file is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.file = self.path.open("ab", buffering=0)
                self.file.write(chunk)
                self.error = None
            except OSError as exc:
                self.error = type(exc).__name__
                if self.file:
                    self.file.close()
                    self.file = None
            self._ack()

    def close(self) -> None:
        with self.lock:
            if self.file:
                self.file.close()
            self.status_path.unlink(missing_ok=True)


class PolicyFileHandler(logging.Handler):
    """Terminal agents keep console output while gating their diagnostic file."""

    def __init__(self, filename: str, encoding: str = "utf-8") -> None:
        super().__init__()
        self.encoding = encoding
        self.sink = OutputSink(Path(filename), state_path(), home_dir() / ".log-sinks")
        self.stopped = threading.Event()
        self.sink.sync()
        self.watcher = threading.Thread(target=self._watch, name="terminal-log-policy", daemon=True)
        self.watcher.start()

    def _watch(self) -> None:
        while not self.stopped.wait(0.1):
            self.sink.sync()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.sync()
            self.sink.write((self.format(record) + "\n").encode(self.encoding, errors="replace"))
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self.stopped.set()
        self.watcher.join(timeout=1)
        self.sink.close()
        super().close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    args = parser.parse_args()
    sink = OutputSink(args.log_path, args.state_path, args.status_dir)
    stopped = threading.Event()
    sink.sync()

    def watch() -> None:
        while not stopped.wait(0.1):
            sink.sync()

    watcher = threading.Thread(target=watch, name="log-policy", daemon=True)
    watcher.start()
    try:
        while chunk := sys.stdin.buffer.read1(65536):
            sink.sync()
            sink.write(chunk)
    finally:
        stopped.set()
        watcher.join(timeout=1)
        sink.close()


if __name__ == "__main__":
    main()
