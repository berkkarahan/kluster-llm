from __future__ import annotations

import threading
from types import SimpleNamespace

from kllm.agent.services.log_relay import LogRelayService


def test_log_relay_tick_loads_master_journal_and_relays_current_identity():
    calls: list[tuple[str, object]] = []
    hub = SimpleNamespace(
        ensure_journal_loaded=lambda: calls.append(("load", None)),
        relay_tick=lambda **kwargs: calls.append(("relay", kwargs)),
    )
    service = LogRelayService(
        state=lambda: SimpleNamespace(
            node_id="master-1", role="master", master_host=None, join_key="secret"
        ),
        stop=threading.Event(),
        master_control_base=lambda: "http://unused",
        hub=hub,  # type: ignore[arg-type]
    )

    service.tick()

    assert calls == [
        ("load", None),
        (
            "relay",
            {
                "node_id": "master-1",
                "role": "master",
                "master_base": None,
                "join_key": "secret",
            },
        ),
    ]


def test_log_relay_worker_uses_master_control_route():
    calls: list[dict[str, object]] = []
    hub = SimpleNamespace(
        ensure_journal_loaded=lambda: (_ for _ in ()).throw(
            AssertionError("workers do not load the master journal")
        ),
        relay_tick=lambda **kwargs: calls.append(kwargs),
    )
    service = LogRelayService(
        state=lambda: SimpleNamespace(
            node_id="worker-1", role="worker", master_host="master", join_key="secret"
        ),
        stop=threading.Event(),
        master_control_base=lambda: "http://master:18765",
        hub=hub,  # type: ignore[arg-type]
    )

    service.tick()

    assert calls == [
        {
            "node_id": "worker-1",
            "role": "worker",
            "master_base": "http://master:18765",
            "join_key": "secret",
        }
    ]
