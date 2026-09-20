"""Application-owned connection pools (sync clients are safe across worker threads)."""
import httpx
import threading

control: httpx.Client | None = None
_lock = threading.Lock()


def control_client() -> httpx.Client:
    global control
    with _lock:
        if control is None or control.is_closed:
            control = httpx.Client(timeout=5.0, trust_env=False)
        return control


def close() -> None:
    global control
    with _lock:
        if control is not None:
            control.close()
            control = None
