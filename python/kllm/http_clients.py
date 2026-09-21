"""Application-owned connection pools (sync clients are safe across worker threads)."""
import httpx
import threading

control: httpx.Client | None = None
_lock = threading.Lock()

# Default httpx keepalive_expiry is 5s. Membership ticks can be farther apart than
# that (capacity probe + two workers), so the pool drops the socket and every
# dashboard "ping" pays a new TCP handshake on Wi-Fi (~100–400ms).
_CONTROL_LIMITS = httpx.Limits(
    max_keepalive_connections=32,
    max_connections=64,
    keepalive_expiry=120.0,
)


def control_client() -> httpx.Client:
    global control
    with _lock:
        if control is None or control.is_closed:
            control = httpx.Client(timeout=5.0, trust_env=False, limits=_CONTROL_LIMITS)
        return control


def close() -> None:
    global control
    with _lock:
        if control is not None:
            control.close()
            control = None
