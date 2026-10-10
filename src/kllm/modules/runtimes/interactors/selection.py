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


def selected_runtime() -> dict | None:
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
    return value


def selected_environment() -> dict[str, str]:
    """Return only the selected runtime's explicit loader environment."""
    value = selected_runtime()
    environment = value.get("runtime_env", {}) if value else {}
    if not isinstance(environment, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in environment.items()
    ):
        raise ValueError("Selected runtime has an invalid process environment")
    return environment


def selected_binary(name: str) -> str | None:
    value = selected_runtime()
    if value is None:
        return None
    if name == "rpc-server" and value.get("engine") == "primacpp":
        raise ValueError("Prima uses ring workers; it cannot start a llama.cpp RPC worker")
    binary = value["llama_server" if name == "llama-server" else "rpc_server"]
    if not Path(binary).is_file():
        raise ValueError(
            "Selected runtime executable is unavailable; rebuild it in Settings → llama.cpp"
        )
    return binary
