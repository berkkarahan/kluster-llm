"""Tests for RunAdmission 30s timeout and async cancellation bridge."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from kllm.modules.managed_network.interactors.run_admission import (
    RunAdmission,
)
from kllm.shared.platform.base import PlatformRefusal


class MockLoopThread:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2.0)


@pytest.fixture
def mock_service():
    loop_thread = MockLoopThread()
    service = MagicMock()
    service.loop = loop_thread.loop
    yield service
    loop_thread.stop()


def test_call_success(mock_service):
    admission = RunAdmission(mock_service)

    async def quick_coro():
        return "success_val"

    result = admission.call(quick_coro())
    assert result == "success_val"


def test_call_timeout_cancels_and_raises_504(mock_service):
    admission = RunAdmission(mock_service, timeout=0.1)
    cancelled = False

    async def slow_coro():
        nonlocal cancelled
        try:
            await asyncio.sleep(5.0)
            return "too_late"
        except asyncio.CancelledError:
            cancelled = True
            raise

    with pytest.raises(HTTPException) as exc_info:
        admission.call(slow_coro())

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail.get("code") == "ADMISSION_TIMEOUT"
    assert "timed out after 0.1s" in exc_info.value.detail.get("message", "")

    # Allow event loop thread to process the cancellation
    time.sleep(0.1)
    assert cancelled is True


def test_call_raises_409_on_refusal(mock_service):
    admission = RunAdmission(mock_service)

    async def refusing_coro():
        raise PlatformRefusal("STALE_REVISION", "Selected participant is no longer enrolled")

    with pytest.raises(HTTPException) as exc_info:
        admission.call(refusing_coro())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail.get("code") == "STALE_REVISION"
