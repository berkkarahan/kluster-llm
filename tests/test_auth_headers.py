"""Unit tests for case-insensitive Bearer authorization header parsing (KLLM-BUG-006)."""

from __future__ import annotations

from unittest.mock import patch

from kllm.shared.auth import verify_api_token


def test_verify_api_token_case_insensitivity():
    test_token = "sk-kllm-abcdef1234567890"

    with patch("kllm.shared.auth.ensure_api_token", return_value=test_token):
        # Standard Bearer prefix
        assert verify_api_token(f"Bearer {test_token}") is True

        # Lowercase bearer prefix (RFC 6750 / RFC 7235 compliance)
        assert verify_api_token(f"bearer {test_token}") is True

        # Uppercase BEARER prefix
        assert verify_api_token(f"BEARER {test_token}") is True

        # Mixed-case Bearer prefix
        assert verify_api_token(f"BeArEr {test_token}") is True

        # Multiple whitespace spaces
        assert verify_api_token(f"Bearer   \t  {test_token}") is True
        assert verify_api_token(f"bearer    {test_token}") is True

        # Raw token without prefix (supported in kluster-llm)
        assert verify_api_token(test_token) is True
        assert verify_api_token(f"  {test_token}  ") is True


def test_verify_api_token_invalid_inputs():
    test_token = "sk-kllm-abcdef1234567890"

    with patch("kllm.shared.auth.ensure_api_token", return_value=test_token):
        # Empty and None
        assert verify_api_token(None) is False
        assert verify_api_token("") is False
        assert verify_api_token("   ") is False

        # Wrong token with Bearer
        assert verify_api_token("Bearer wrong-token") is False
        assert verify_api_token("bearer wrong-token") is False

        # Just the word Bearer
        assert verify_api_token("Bearer") is False
        assert verify_api_token("Bearer ") is False
        assert verify_api_token("bearer") is False
        assert verify_api_token("bearer ") is False

        # Prefix with token that has a single character difference
        assert verify_api_token(f"Bearer {test_token[:-1]}x") is False
