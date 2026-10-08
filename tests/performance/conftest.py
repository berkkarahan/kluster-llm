"""Opt-in timing and child-process leak helpers for performance tests."""

from __future__ import annotations

import os
import time

import psutil
import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    report.user_properties.append((f"phase_{report.when}_seconds", call.duration))


@pytest.fixture
def no_leaked_child_processes():
    """Fail if a test leaves a new child process running after it returns."""
    if os.getenv("KLLM_PERF_LEAK_CHECK") != "1":
        pytest.skip("set KLLM_PERF_LEAK_CHECK=1 to run process leak probe")
    current = psutil.Process()
    before = {(child.pid, child.create_time()) for child in current.children(recursive=True)}
    yield
    time.sleep(0.05)
    after = {(child.pid, child.create_time()) for child in current.children(recursive=True)}
    leaked = after - before
    assert not leaked, f"test-owned child processes remain: {sorted(pid for pid, _ in leaked)}"
