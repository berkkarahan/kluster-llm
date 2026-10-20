"""Unit tests for coroutine cancellation on shutdown timeout in application lifecycle (KLLM-BUG-008)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from kllm.agent.application_lifecycle import AgentLifecycleDependencies, _LifespanSequence
from kllm.shared.exceptions import PersistenceError


@pytest.fixture
def lifespan_sequence():
    deps = MagicMock(spec=AgentLifecycleDependencies)
    app = MagicMock()
    return _LifespanSequence(deps, app)


@pytest.mark.asyncio
async def test_await_until_success_within_deadline(lifespan_sequence):
    async def sample_coro():
        await asyncio.sleep(0.01)
        return "success"

    deadline = time.monotonic() + 1.0
    result = await lifespan_sequence._await_until(sample_coro(), deadline, "timeout error")
    assert result == "success"
    assert len(lifespan_sequence._lifecycle_jobs) == 0


@pytest.mark.asyncio
async def test_await_until_cancels_and_gathers_task_on_timeout(lifespan_sequence):
    cancelled = False
    task_cleaned_up = False

    async def hanging_coroutine():
        nonlocal cancelled, task_cleaned_up
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled = True
            task_cleaned_up = True
            raise

    # Deadline in the past or virtually instantaneous
    deadline = time.monotonic() + 0.02
    with pytest.raises(PersistenceError) as exc_info:
        await lifespan_sequence._await_until(hanging_coroutine(), deadline, "shutdown deadline expired")

    assert "shutdown deadline expired" in str(exc_info.value)
    # The task should have been cancelled and cleanly gathered
    assert cancelled is True
    assert task_cleaned_up is True
    assert len(lifespan_sequence._lifecycle_jobs) == 0
