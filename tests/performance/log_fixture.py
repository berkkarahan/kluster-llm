"""Deterministic synthetic live-log batch used by the preparation benchmark."""

from uuid import UUID

from kllm.modules.logs.interactors.cluster_log import make_event


def oversized_log_rows(count: int = 500, message_chars: int = 1024) -> list[dict]:
    return [
        make_event("synthetic", "agent", "x" * message_chars, ts=1.0, event_id=str(UUID(int=i + 1)))
        for i in range(count)
    ]
