"""Test selection notes: `-m unit` and `-m integration` are convenience subsets only.

The complete `uv run pytest -q` invocation remains the validation command and collects all
non-opt-in tests, including real integration checks.
"""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolated_control_pool():
    """Keep pooled clients and test doubles local to each test."""
    from kllm.shared import http_clients

    previous = http_clients.control
    http_clients.control = None
    yield
    current = http_clients.control
    http_clients.control = previous
    close = getattr(current, "close", None)
    if current is not previous and callable(close):
        close()


@pytest.fixture
def native_inventory_counts(tmp_path, monkeypatch):
    """Install a deterministic native inventory adapter and count expensive acquisitions."""
    import kllm.shared.native as native

    binary = tmp_path / "llama-server"
    binary.write_bytes(b"test binary")
    calls = {"list_gpus": 0, "binary_capabilities": 0}

    class NativeProbe:
        @staticmethod
        def _probe_capacity_and_gpus():
            snapshot = SimpleNamespace(
                backend="cpu",
                total_ram_bytes=16 * 1024**3,
                available_ram_bytes=8 * 1024**3,
                vram_bytes=0,
                hostname="test-host",
            )
            return snapshot, [], "cpu"

    def list_gpus():
        calls["list_gpus"] += 1
        return {"backend": "cpu", "probed_backend": "cpu", "gpus": [], "devices": []}

    def binary_capabilities(_binary, _mtime_ns, _cache_bucket):
        calls["binary_capabilities"] += 1
        return {"devices": [], "binary_version": "test", "metal_init_failed": False}

    monkeypatch.setattr(native, "_NATIVE", NativeProbe())
    monkeypatch.setattr(native, "resolve_llama_server", lambda _repo_root=None: str(binary))
    monkeypatch.setattr(native, "binary_capabilities", binary_capabilities)
    monkeypatch.setattr(native, "list_gpus", list_gpus)
    return calls
