"""Tests for the Windows-tolerant atomic replace helper."""

from __future__ import annotations

import errno
import os

import pytest

from kllm.shared import atomic_io


def _permission_error(winerror: int | None) -> PermissionError:
    error = PermissionError(errno.EACCES, "access denied")
    if winerror is not None:
        error.winerror = winerror  # type: ignore[attr-defined]
    return error


def test_replace_file_retries_transient_windows_lock(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "cluster.json"
    destination.write_text("old", encoding="utf-8")
    source = tmp_path / ".cluster-tmp"
    source.write_text("new", encoding="utf-8")

    calls = {"count": 0}
    real_replace = os.replace

    def flaky(src, dst):
        calls["count"] += 1
        if calls["count"] <= 2:
            raise _permission_error(5)
        return real_replace(src, dst)

    monkeypatch.setattr(atomic_io, "_is_transient_lock", lambda _exc: True)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(atomic_io.os, "replace", flaky)

    atomic_io.replace_file(source, destination)

    assert calls["count"] == 3
    assert destination.read_text(encoding="utf-8") == "new"
    assert not source.exists()


def test_replace_file_does_not_retry_nontransient_error(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "cluster.json"
    destination.write_text("old", encoding="utf-8")
    source = tmp_path / ".cluster-tmp"
    source.write_text("new", encoding="utf-8")

    calls = {"count": 0}

    def denied(_src, _dst):
        calls["count"] += 1
        raise _permission_error(None)

    monkeypatch.setattr(atomic_io.os, "replace", denied)

    with pytest.raises(PermissionError):
        atomic_io.replace_file(source, destination)

    assert calls["count"] == 1


def test_replace_file_gives_up_after_deadline(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "cluster.json"
    destination.write_text("old", encoding="utf-8")
    source = tmp_path / ".cluster-tmp"
    source.write_text("new", encoding="utf-8")

    calls = {"count": 0}

    def always_locked(_src, _dst):
        calls["count"] += 1
        raise _permission_error(32)

    monkeypatch.setattr(atomic_io, "_is_transient_lock", lambda _exc: True)
    monkeypatch.setattr(atomic_io, "_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(atomic_io.os, "replace", always_locked)

    with pytest.raises(PermissionError):
        atomic_io.replace_file(source, destination)

    assert calls["count"] == 2
    assert source.exists()


def test_is_transient_lock_only_matches_windows_lock_errors(monkeypatch) -> None:
    monkeypatch.setattr(atomic_io.sys, "platform", "win32")
    assert atomic_io._is_transient_lock(_permission_error(5)) is True
    assert atomic_io._is_transient_lock(_permission_error(32)) is True
    assert atomic_io._is_transient_lock(_permission_error(2)) is False
    assert atomic_io._is_transient_lock(_permission_error(None)) is False

    monkeypatch.setattr(atomic_io.sys, "platform", "linux")
    assert atomic_io._is_transient_lock(_permission_error(5)) is False
