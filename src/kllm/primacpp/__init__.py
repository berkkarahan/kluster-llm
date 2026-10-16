"""Primacpp integration module.

Provides a thin wrapper around the primacpp native library, exposing an API compatible with llama.cpp.
The implementation is currently a placeholder; concrete bindings should be added as the integration progresses.
"""

import importlib.util
import os
from pathlib import Path

def _load_primacpp_lib() -> None:
    """Load the primacpp shared library if available.

    The library is expected at ``~/.kllm/primacpp/libprimacpp.{so,dylib,dll}`` depending on platform.
    This function raises ``FileNotFoundError`` if the library cannot be located.
    """
    home = Path(os.getenv("HOME", "~")).expanduser()
    lib_dir = home / ".kllm" / "primacpp"
    ext = {
        "darwin": "dylib",
        "linux": "so",
        "win32": "dll",
    }.get(os.sys.platform, "so")
    lib_path = lib_dir / f"libprimacpp.{ext}"
    if not lib_path.is_file():
        raise FileNotFoundError(f"Primacpp library not found at {lib_path}")
    spec = importlib.util.spec_from_file_location("_primacpp", str(lib_path))
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(module)  # type: ignore[assignment]
    globals()["_primacpp"] = module

# Load on import; callers can catch the exception if they need graceful fallback.
_load_primacpp_lib()

# Expose low‑level API from the _native module.
# The import runs after the shared-library probe above, so E402 is intentional here.
from ._native import *  # noqa: E402,F401,F403
