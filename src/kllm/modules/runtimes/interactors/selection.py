"""Per-operation executable selection, without process-wide environment changes."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path

_snapshot: ContextVar[dict | None] = ContextVar("kllm_runtime_snapshot", default=None)


def snapshot() -> dict | None:
    return _snapshot.get()


@contextmanager
def use_runtime(value: dict | None):
    token = _snapshot.set(value)
    try:
        yield
    finally:
        _snapshot.reset(token)


def selected_binary(name: str) -> str | None:
    value = snapshot()
    if value is None:
        # Read-only fallback keeps telemetry on the active process's artifact.
        from kllm.shared.state import home_dir

        path = home_dir() / "runtimes/registry.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        value = data.get("running")
        if value is None and data.get("selected"):
            value = data["versions"][data["selected"]].get("build")
            if value is None:
                raise ValueError("Selected llama.cpp version is not prepared")
        if value is None:
            return None
    binary = value["llama_server" if name == "llama-server" else "rpc_server"]
    if not Path(binary).is_file():
        raise ValueError(
            "Selected runtime executable is unavailable; rebuild it in Settings → llama.cpp"
        )
    return binary
