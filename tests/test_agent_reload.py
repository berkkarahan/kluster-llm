"""Tests for kllm.agent reload behavior (KLLM-MAINT-002).

Verifies:
1. Ordinary imports of `kllm.agent` do not cause unintended module reloads of `kllm.agent.runtime`.
2. In-flight module state and singletons are preserved across standard imports.
3. `reload_agent_runtime()` explicitly and cleanly reloads `kllm.agent.runtime` when requested.
4. Exposed facades on `kllm.agent` are refreshed after `reload_agent_runtime()`.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch


import kllm.agent
import kllm.agent.runtime
from kllm.shared.state import ClusterState


def test_reload_agent_runtime_exported_in_all():
    """Verify reload_agent_runtime is exposed in __all__ and callable."""
    assert "reload_agent_runtime" in kllm.agent.__all__
    assert callable(kllm.agent.reload_agent_runtime)


def test_import_does_not_reload_runtime_when_already_in_sys_modules():
    """Verify that importing kllm.agent does not call importlib.reload on runtime."""
    assert "kllm.agent.runtime" in sys.modules
    runtime_mod = sys.modules["kllm.agent.runtime"]

    sentinel = object()
    runtime_mod._maint_002_canary = sentinel

    try:
        with patch("importlib.reload", side_effect=AssertionError("importlib.reload should not be called")):
            loaded = kllm.agent._load_runtime()
            assert loaded is runtime_mod
            assert getattr(runtime_mod, "_maint_002_canary", None) is sentinel
    finally:
        if hasattr(runtime_mod, "_maint_002_canary"):
            delattr(runtime_mod, "_maint_002_canary")


def test_state_and_objects_preserved_across_agent_import():
    """Verify in-flight ClusterState and runtime objects are not wiped on ordinary import."""
    test_state = ClusterState(role="master", join_key="sentinel-key-12345")
    kllm.agent._replace_state(test_state)

    try:
        assert kllm.agent.STATE is test_state
        initial_app = kllm.agent.app

        loaded = kllm.agent._load_runtime()
        assert loaded is sys.modules["kllm.agent.runtime"]
        assert kllm.agent.STATE is test_state
        assert kllm.agent.app is initial_app
        assert kllm.agent.create_app() is initial_app
    finally:
        kllm.agent._replace_state(None)


def test_reload_agent_runtime_explicit_call_reloads_runtime():
    """Verify that reload_agent_runtime() explicitly reloads kllm.agent.runtime and recreates top-level instances."""
    runtime_mod = sys.modules["kllm.agent.runtime"]
    initial_app = runtime_mod.app
    initial_scheduler = getattr(runtime_mod, "_capacity_refresh_scheduler", None)

    reloaded = kllm.agent.reload_agent_runtime()

    assert reloaded is sys.modules["kllm.agent.runtime"]
    # Re-execution of runtime.py creates new FastAPI and router instances
    assert reloaded.app is not initial_app, "Expected runtime.app to be recreated on reload"
    if initial_scheduler is not None:
        assert reloaded._capacity_refresh_scheduler is not initial_scheduler
    assert kllm.agent.app is reloaded.app
    assert kllm.agent.create_app() is reloaded.app
    assert kllm.agent.lifespan is reloaded.lifespan
    assert kllm.agent.legacy_v1_router is reloaded.legacy_v1_router


def test_reload_agent_runtime_refreshes_all_facades():
    """Verify all exposed runtime facade attributes are updated on kllm.agent upon reload."""
    reloaded = kllm.agent.reload_agent_runtime()

    facade_attributes = [
        "app",
        "lifespan",
        "legacy_v1_router",
        "health",
        "native",
        "netsec",
        "discovery",
        "http_clients",
        "gateway",
        "membership_mod",
        "httpx",
        "threading",
        "model_settings_store",
        "cluster_update",
    ]

    for attr in facade_attributes:
        assert getattr(kllm.agent, attr) is getattr(reloaded, attr), f"Facade {attr} mismatch after reload"


def test_clean_subprocess_import_and_explicit_reload():
    """Verify clean behavior in an isolated Python interpreter subprocess."""
    code = (
        "import sys\n"
        "import kllm.agent.runtime\n"
        "runtime = sys.modules['kllm.agent.runtime']\n"
        "initial_app = runtime.app\n"
        "# Importing kllm.agent when runtime is already loaded must NOT reload it\n"
        "import kllm.agent\n"
        "assert runtime.app is initial_app, 'runtime was reloaded on kllm.agent import'\n"
        "assert kllm.agent.app is initial_app, 'agent facade does not match initial app'\n"
        "# Explicit reload should construct a new app and update agent facade\n"
        "reloaded = kllm.agent.reload_agent_runtime()\n"
        "assert reloaded.app is not initial_app, 'runtime app was not recreated on explicit reload'\n"
        "assert kllm.agent.app is reloaded.app, 'agent facade was not refreshed to new app'\n"
        "assert kllm.agent.create_app() is reloaded.app\n"
        "print('PASS')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Subprocess failed with stderr:\n{result.stderr}"
    assert "PASS" in result.stdout
