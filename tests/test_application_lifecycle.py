from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from kllm.agent.application_lifecycle import AgentLifecycleDependencies, AgentLifespan
from kllm.state import ClusterState


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["idle", "worker"])
async def test_agent_lifespan_closes_app_resources_when_application_fails(role, monkeypatch):
    state = ClusterState(node_id="node-1", role=role, members={"old-master": {"role": "master"}})
    monkeypatch.setattr(state, "save", lambda: None)
    calls: list[str] = []
    feed = SimpleNamespace()

    class AsyncClientContext:
        async def __aenter__(self):
            calls.append("client-open")
            return "inference-client"

        async def __aexit__(self, *_args):
            calls.append("client-close")

    async def diagnostics() -> None:
        await asyncio.Event().wait()

    dependencies = AgentLifecycleDependencies(
        state=lambda: state,
        replace_state=lambda _state: calls.append("replace-state"),
        upgrade_pending=lambda: False,
        stop_children=lambda: calls.append("stop-children"),
        load_state=lambda: state,
        reconcile_plan=lambda _state: None,
        reset_usage_state=lambda: calls.append("reset-usage"),
        clear_resources=lambda: calls.append("clear-resources"),
        clear_remote_resources=lambda: calls.append("clear-remote"),
        control_client=lambda: calls.append("control-client"),
        open_usage_store=lambda: calls.append("open-store"),
        background_stop=threading.Event(),
        adopt_child_identity=lambda *_args: False,
        ensure_master_member=lambda **_kwargs: calls.append("ensure-master"),
        create_live_feed=lambda: feed,
        background_enabled=lambda: False,
        start_membership=lambda: pytest.fail("background workers are disabled"),
        start_log_relay=lambda: pytest.fail("background workers are disabled"),
        usage_loop=lambda: pytest.fail("background workers are disabled"),
        thread_factory=lambda **_kwargs: pytest.fail("background workers are disabled"),
        close_resource_probes=lambda: calls.append("close-probes"),
        sample_diagnostics=diagnostics,
        close_clients=lambda: calls.append("close-clients"),
        close_usage_store=lambda _app: calls.append("close-store"),
        stop_live_feed=lambda _feed: calls.append("stop-feed"),
        platform="linux",
        async_client_factory=lambda **_kwargs: AsyncClientContext(),
    )
    app = SimpleNamespace(state=SimpleNamespace())
    lifespan = AgentLifespan(dependencies)

    with pytest.raises(RuntimeError, match="request failed"):
        async with lifespan.run(app):
            assert ("master heartbeat unreachable" in state.netsec_warnings) == (role == "worker")
            assert "old-master" in state.members
            assert app.state.inference_client == "inference-client"
            assert app.state.live_feed is feed
            raise RuntimeError("request failed")

    assert calls == [
        "reset-usage",
        "clear-resources",
        "clear-remote",
        "control-client",
        "open-store",
        "ensure-master",
        "client-open",
        "close-clients",
        "close-store",
        "stop-feed",
        "client-close",
    ]
    assert dependencies.background_stop.is_set()
    assert not hasattr(app.state, "live_feed")
