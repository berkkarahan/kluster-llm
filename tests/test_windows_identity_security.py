"""Unit tests for Windows System32 absolute path execution in managed network identity (KLLM-BUG-012)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from kllm.modules.managed_network.interactors.identity import _secure_directory, _system32_executable


def test_system32_executable_resolution():
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    expected_whoami = str(system_root / "System32" / "whoami.exe")

    # On actual Windows, whoami.exe exists in System32
    resolved = _system32_executable("whoami")
    if os.name == "nt" and Path(expected_whoami).is_file():
        assert resolved.lower() == expected_whoami.lower()
        assert Path(resolved).is_absolute()
        assert "system32" in resolved.lower()


def test_secure_directory_uses_absolute_paths_on_windows(tmp_path):
    target_dir = tmp_path / "test-network-identity"

    mock_whoami_result = MagicMock()
    mock_whoami_result.stdout = '"user","S-1-5-21-123456789-123456789-123456789-1001"\n'

    calls = []

    def mock_subprocess_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if "whoami" in cmd[0].lower():
            return mock_whoami_result
        return MagicMock(returncode=0)

    with (
        patch("os.name", "nt"),
        patch("subprocess.run", side_effect=mock_subprocess_run),
    ):
        _secure_directory(target_dir)

    assert len(calls) == 2
    whoami_call = calls[0]
    icacls_call = calls[1]

    # Both calls must use absolute paths containing System32, not bare strings
    assert whoami_call[0] != "whoami"
    assert Path(whoami_call[0]).is_absolute()
    assert "system32" in whoami_call[0].lower()
    assert whoami_call[0].lower().endswith("whoami.exe")

    assert icacls_call[0] != "icacls"
    assert Path(icacls_call[0]).is_absolute()
    assert "system32" in icacls_call[0].lower()
    assert icacls_call[0].lower().endswith("icacls.exe")
