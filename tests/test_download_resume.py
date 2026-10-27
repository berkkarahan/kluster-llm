"""Tests for model download HTTP range resume and 60-second read timeout."""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from kllm.modules.models.interactors.download_transfer import (
    DEFAULT_STREAM_TIMEOUT,
    DownloadError,
    stream_download,
)


class DummyStreamResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: dict | None = None,
        chunks: list[bytes] | None = None,
        raise_on_chunk: Exception | None = None,
    ):
        self.status_code = status_code
        self.headers = headers or {"Content-Length": "100"}
        self._chunks = chunks or [b"chunk1", b"chunk2"]
        self._raise_on_chunk = raise_on_chunk

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 416:
            raise httpx.HTTPStatusError(
                "Error", request=MagicMock(), response=MagicMock(status_code=self.status_code)
            )

    def iter_bytes(self, chunk_size: int = 65536):
        for c in self._chunks:
            if self._raise_on_chunk:
                raise self._raise_on_chunk
            yield c


def test_stream_download_uses_60s_read_timeout(tmp_path: Path):
    target = tmp_path / "model.gguf"
    captured_timeout = None

    @contextlib.contextmanager
    def mock_stream(method, url, headers=None, follow_redirects=True, timeout=None):
        nonlocal captured_timeout
        captured_timeout = timeout
        yield DummyStreamResponse(status_code=200, chunks=[b"data"])

    stream_download(
        "https://example.com/model.gguf",
        target,
        headers={},
        on_progress=None,
        cancel_event=None,
        report_progress=lambda *a: None,
        report_complete=lambda *a: None,
        stream_client=mock_stream,
        sidecar_writer=lambda p, s: None,
    )

    assert captured_timeout is not None
    if isinstance(captured_timeout, httpx.Timeout):
        assert captured_timeout.read == 60.0
    else:
        assert captured_timeout == DEFAULT_STREAM_TIMEOUT


def test_stream_download_resumes_with_range_header(tmp_path: Path):
    target = tmp_path / "model.gguf"
    part = tmp_path / "model.gguf.part"

    # Pre-populate part with 1024 bytes
    initial_bytes = b"A" * 1024
    part.write_bytes(initial_bytes)

    captured_headers = None
    remaining_bytes = b"B" * 2048

    @contextlib.contextmanager
    def mock_stream(method, url, headers=None, follow_redirects=True, timeout=None):
        nonlocal captured_headers
        captured_headers = headers
        assert headers is not None
        assert headers.get("Range") == "bytes=1024-"
        yield DummyStreamResponse(
            status_code=206,
            headers={"Content-Length": str(len(remaining_bytes))},
            chunks=[remaining_bytes],
        )

    stream_download(
        "https://example.com/model.gguf",
        target,
        headers={"Authorization": "Bearer test"},
        on_progress=None,
        cancel_event=None,
        report_progress=lambda *a: None,
        report_complete=lambda *a: None,
        stream_client=mock_stream,
        sidecar_writer=lambda p, s: None,
    )

    assert target.exists()
    assert not part.exists()
    content = target.read_bytes()
    assert len(content) == 1024 + 2048
    assert content[:1024] == initial_bytes
    assert content[1024:] == remaining_bytes


def test_stream_download_preserves_part_on_timeout(tmp_path: Path):
    target = tmp_path / "model.gguf"
    part = tmp_path / "model.gguf.part"

    part.write_bytes(b"initial_data")

    @contextlib.contextmanager
    def mock_stream(method, url, headers=None, follow_redirects=True, timeout=None):
        yield DummyStreamResponse(
            status_code=206,
            headers={"Content-Length": "100"},
            chunks=[b"chunk_before_timeout"],
            raise_on_chunk=httpx.ReadTimeout("Read timed out"),
        )

    with pytest.raises(DownloadError):
        stream_download(
            "https://example.com/model.gguf",
            target,
            headers={},
            on_progress=None,
            cancel_event=None,
            report_progress=lambda *a: None,
            report_complete=lambda *a: None,
            stream_client=mock_stream,
            sidecar_writer=lambda p, s: None,
            max_retries=0,  # Fast failure for test
        )

    # The part file must NOT be unlinked
    assert part.exists()
    assert part.read_bytes() == b"initial_data"


def test_stream_download_handles_200_fallback(tmp_path: Path):
    target = tmp_path / "model.gguf"
    part = tmp_path / "model.gguf.part"

    part.write_bytes(b"old_stale_bytes")

    full_payload = b"brand_new_payload_from_zero"

    @contextlib.contextmanager
    def mock_stream(method, url, headers=None, follow_redirects=True, timeout=None):
        # Server ignores Range and returns 200 OK
        yield DummyStreamResponse(
            status_code=200,
            headers={"Content-Length": str(len(full_payload))},
            chunks=[full_payload],
        )

    stream_download(
        "https://example.com/model.gguf",
        target,
        headers={},
        on_progress=None,
        cancel_event=None,
        report_progress=lambda *a: None,
        report_complete=lambda *a: None,
        stream_client=mock_stream,
        sidecar_writer=lambda p, s: None,
    )

    assert target.exists()
    assert target.read_bytes() == full_payload


def test_stream_download_handles_416_range_not_satisfiable(tmp_path: Path):
    target = tmp_path / "model.gguf"
    part = tmp_path / "model.gguf.part"

    part.write_bytes(b"invalid_offset_data")
    full_payload = b"valid_fresh_data"
    attempts = 0

    @contextlib.contextmanager
    def mock_stream(method, url, headers=None, follow_redirects=True, timeout=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # First attempt with Range fails with 416
            yield DummyStreamResponse(status_code=416)
        else:
            # Second attempt without Range succeeds
            assert headers is None or "Range" not in headers
            yield DummyStreamResponse(
                status_code=200,
                headers={"Content-Length": str(len(full_payload))},
                chunks=[full_payload],
            )

    stream_download(
        "https://example.com/model.gguf",
        target,
        headers={},
        on_progress=None,
        cancel_event=None,
        report_progress=lambda *a: None,
        report_complete=lambda *a: None,
        stream_client=mock_stream,
        sidecar_writer=lambda p, s: None,
    )

    assert target.exists()
    assert target.read_bytes() == full_payload


def test_stream_download_preserves_part_on_remote_protocol_error(tmp_path: Path):
    target = tmp_path / "model.gguf"
    part = tmp_path / "model.gguf.part"

    part.write_bytes(b"initial_data_before_drop")

    @contextlib.contextmanager
    def mock_stream(method, url, headers=None, follow_redirects=True, timeout=None):
        yield DummyStreamResponse(
            status_code=206,
            headers={"Content-Length": "100"},
            chunks=[b"chunk_before_disconnect"],
            raise_on_chunk=httpx.RemoteProtocolError("peer closed connection prematurely"),
        )

    with pytest.raises(DownloadError):
        stream_download(
            "https://example.com/model.gguf",
            target,
            headers={},
            on_progress=None,
            cancel_event=None,
            report_progress=lambda *a: None,
            report_complete=lambda *a: None,
            stream_client=mock_stream,
            sidecar_writer=lambda p, s: None,
            max_retries=0,
        )

    # The part file must NOT be unlinked on RemoteProtocolError to allow resume
    assert part.exists()
    assert part.read_bytes() == b"initial_data_before_drop"


def test_stream_download_retries_and_succeeds_after_protocol_error(tmp_path: Path, monkeypatch):
    # Accelerate backoff sleep in tests
    monkeypatch.setattr("time.sleep", lambda *_: None)

    target = tmp_path / "model.gguf"
    part = tmp_path / "model.gguf.part"
    attempts = 0

    @contextlib.contextmanager
    def mock_stream(method, url, headers=None, follow_redirects=True, timeout=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.RemoteProtocolError("server connection dropped mid-transfer")
        yield DummyStreamResponse(
            status_code=200,
            headers={"Content-Length": "11"},
            chunks=[b"full_stream"],
        )

    stream_download(
        "https://example.com/model.gguf",
        target,
        headers={},
        on_progress=None,
        cancel_event=None,
        report_progress=lambda *a: None,
        report_complete=lambda *a: None,
        stream_client=mock_stream,
        sidecar_writer=lambda p, s: None,
        max_retries=2,
    )

    assert target.exists()
    assert not part.exists()
    assert target.read_bytes() == b"full_stream"
    assert attempts == 2

