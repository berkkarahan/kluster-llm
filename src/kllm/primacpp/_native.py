"""Low‑level ctypes bindings for the primacpp shared library.

The library is expected at ``~/.kllm/primacpp/libprimacpp.{so,dylib,dll}``.
This module provides thin wrappers that raise ``NotImplementedError`` until the native implementation is added.
"""

import ctypes
import os
from pathlib import Path

def _load_library() -> ctypes.CDLL:
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
    return ctypes.CDLL(str(lib_path))

# Load the library on import; callers can catch ImportError if needed.
_primacpp_lib = _load_library()

# Example function signatures – adjust to match the actual C API.
# int primacpp_load_model(const char* path, void** out_handle);
_primacpp_lib.primacpp_load_model.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
_primacpp_lib.primacpp_load_model.restype = ctypes.c_int

# int primacpp_infer(void* model, const char* prompt, char* output, size_t out_len);
_primacpp_lib.primacpp_infer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
_primacpp_lib.primacpp_infer.restype = ctypes.c_int

def load_model(path: str):
    """Load a model and return an opaque handle.

    Raises ``NotImplementedError`` if the underlying library does not implement this function.
    """
    out_handle = ctypes.c_void_p()
    result = _primacpp_lib.primacpp_load_model(path.encode("utf-8"), ctypes.byref(out_handle))
    if result != 0:
        raise RuntimeError(f"primacpp_load_model failed with code {result}")
    return out_handle

def infer(model_handle, prompt: str, max_output_len: int = 1024) -> str:
    """Run inference on ``prompt`` using the loaded model.

    Returns the generated text. Raises ``RuntimeError`` on failure.
    """
    output_buf = ctypes.create_string_buffer(max_output_len)
    result = _primacpp_lib.primacpp_infer(model_handle, prompt.encode("utf-8"), output_buf, max_output_len)
    if result != 0:
        raise RuntimeError(f"primacpp_infer failed with code {result}")
    return output_buf.value.decode("utf-8", errors="replace")
