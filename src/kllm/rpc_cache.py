"""The pinned RPC server's persistent tensor cache location."""

import os
import sys
from pathlib import Path


def status() -> dict:
    if os.getenv("LLAMA_CACHE"):
        base = Path(os.environ["LLAMA_CACHE"])
    elif sys.platform == "win32":
        base = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "llama.cpp"
        )
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Caches/llama.cpp"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "llama.cpp"
    path = base / "rpc"
    return {"enabled": True, "path": str(path), "exists": path.is_dir(), "persistent": True}
