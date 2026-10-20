"""Unit tests for timing-safe cluster join key verification (KLLM-BUG-005)."""

from __future__ import annotations

import secrets
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from kllm.agent.runtime import _require_key
from kllm.shared import state_ref


def test_require_key_bypasses_when_role_not_master():
    state = MagicMock()
    state.role = "idle"
    state.join_key = "cluster-secret"
    with patch.object(state_ref, "current", return_value=state):
        # Should not raise any error even with None or invalid key
        _require_key(None)
        _require_key("wrong-key")


def test_require_key_raises_503_when_no_join_key_configured():
    state = MagicMock()
    state.role = "master"
    state.join_key = ""
    with patch.object(state_ref, "current", return_value=state):
        with pytest.raises(HTTPException) as exc_info:
            _require_key("any-key")
        assert exc_info.value.status_code == 503
        assert "master has no join key" in str(exc_info.value.detail)

    state.join_key = None
    with patch.object(state_ref, "current", return_value=state):
        with pytest.raises(HTTPException) as exc_info:
            _require_key("any-key")
        assert exc_info.value.status_code == 503


def test_require_key_accepts_valid_key_using_compare_digest():
    state = MagicMock()
    state.role = "master"
    state.join_key = "secure-cluster-key-12345"
    with patch.object(state_ref, "current", return_value=state):
        with patch.object(secrets, "compare_digest", wraps=secrets.compare_digest) as spy_compare:
            _require_key("secure-cluster-key-12345")
            spy_compare.assert_called_once_with("secure-cluster-key-12345", "secure-cluster-key-12345")


def test_require_key_rejects_invalid_keys():
    state = MagicMock()
    state.role = "master"
    state.join_key = "secure-cluster-key-12345"
    with patch.object(state_ref, "current", return_value=state):
        # Mismatched key
        with pytest.raises(HTTPException) as exc_info:
            _require_key("wrong-key")
        assert exc_info.value.status_code == 401
        assert "invalid join key" in str(exc_info.value.detail)

        # None header
        with pytest.raises(HTTPException) as exc_info:
            _require_key(None)
        assert exc_info.value.status_code == 401

        # Empty string header
        with pytest.raises(HTTPException) as exc_info:
            _require_key("")
        assert exc_info.value.status_code == 401

        # Same prefix different suffix
        with pytest.raises(HTTPException) as exc_info:
            _require_key("secure-cluster-key-12346")
        assert exc_info.value.status_code == 401
