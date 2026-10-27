"""Tests for ClusterState concurrency, resilient snapshot, and atomic replacement."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from kllm.shared.state import ClusterState, home_dir


def test_concurrent_save_and_mutation_no_race(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState(node_id="test-node")

    stop_event = threading.Event()
    errors: list[Exception] = []

    def mutator(tid: int):
        idx = 0
        while not stop_event.is_set():
            idx += 1
            # Unlocked mutation simulating rogue background threads
            state.members[f"node_{tid}_{idx}"] = {
                "host": f"192.168.1.{tid}",
                "role": "worker",
                "last_seen": time.time(),
            }
            if len(state.members) > 50:
                # Remove an arbitrary item to trigger size-change during iteration
                try:
                    state.members.pop(next(iter(state.members)))
                except (KeyError, RuntimeError):
                    pass

    def saver():
        while not stop_event.is_set():
            try:
                state.save()
            except Exception as e:
                errors.append(e)

    threads: list[threading.Thread] = []
    # 5 mutator threads
    for i in range(5):
        t = threading.Thread(target=mutator, args=(i,))
        threads.append(t)
    # 5 saver threads
    for _ in range(5):
        t = threading.Thread(target=saver)
        threads.append(t)

    for t in threads:
        t.start()

    time.sleep(1.0)
    stop_event.set()

    for t in threads:
        t.join(timeout=2.0)

    assert errors == [], f"Encountered concurrency errors during save: {errors}"
    assert state.lock is not None


def test_atomic_tempfile_cleanup_on_write_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState(node_id="test-node")

    with patch("kllm.shared.state.replace_file", side_effect=OSError("Disk full")):
        try:
            state.save()
        except OSError:
            pass

    # Ensure no leaked .cluster-* temporary files remain
    tmp_files = list(home_dir().glob(".cluster-*"))
    assert tmp_files == [], f"Leaked temporary state files found: {tmp_files}"


def test_high_concurrency_20_mutators_saves_succeed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState(node_id="test-concurrency")
    state.members = {f"k_{i}": {"val": i, "role": "worker"} for i in range(100)}

    orig_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-7)

    stop_event = threading.Event()
    errors: list[Exception] = []

    def mutator(tid: int):
        i = 1000
        while not stop_event.is_set():
            i += 1
            state.members[f"k_{tid}_{i}"] = {"val": i, "updated_at": time.time()}
            if i > 1010:
                try:
                    state.members.pop(f"k_{tid}_{i-5}", None)
                except KeyError:
                    pass

    threads = [threading.Thread(target=mutator, args=(tid,)) for tid in range(20)]
    try:
        for t in threads:
            t.start()

        saves_completed = 0
        for _ in range(100):
            try:
                state.save()
                saves_completed += 1
            except Exception as e:
                errors.append(e)

        assert errors == [], f"Encountered {len(errors)} errors during 100 concurrent saves: {errors}"
        assert saves_completed == 100
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=3.0)
        sys.setswitchinterval(orig_interval)

    # Verify saved state on disk is valid and loadable
    loaded = ClusterState.load()
    assert loaded.node_id == "test-concurrency"
    assert isinstance(loaded.members, dict)

