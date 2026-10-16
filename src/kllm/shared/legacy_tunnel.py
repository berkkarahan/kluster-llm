"""One-way cleanup for clusters saved before the LAN-only transition.

Never start or configure a tunnel here. Leave the old config and keys in place
for operator recovery, and refuse to clear cluster state if teardown fails.
"""

import platform
import shutil
import subprocess
from pathlib import Path

from kllm.shared.state import home_dir


def teardown_legacy_tunnel() -> None:
    config = home_dir() / "wg" / "kllm0.conf"
    if platform.system() == "Windows":
        binary = Path(r"C:\Program Files\WireGuard\wireguard.exe")
        if not binary.is_file():
            raise RuntimeError("Cannot stop legacy WireGuard tunnel: wireguard.exe is missing; stop kllm0 manually before retrying")
        command = [str(binary), "/uninstalltunnelservice", "kllm0"]
    else:
        # The old Unix implementation used exactly this config path.
        if not config.exists():
            raise RuntimeError("Cannot verify legacy WireGuard teardown: kllm0.conf is missing; stop kllm0 manually before retrying")
        binary = shutil.which("wg-quick")
        if not binary:
            raise RuntimeError("Cannot stop legacy WireGuard tunnel: wg-quick is missing; stop kllm0 manually before retrying")
        command = [binary, "down", str(config)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"Cannot stop legacy WireGuard tunnel: {detail}; state and keys were retained")
