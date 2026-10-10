"""Late binding for the process-wide cluster state.

The composition root binds the provider. Feature code reads it at call time so
tests can replace ``kllm.agent.STATE`` without importing the agent package.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from kllm.shared.state import ClusterState

_provider: Callable[[], ClusterState | None] | None = None
_replacer: Callable[[ClusterState], None] | None = None


def bind(provider: Callable[[], ClusterState | None]) -> None:
    """Remember how to read the current cluster state."""
    global _provider
    _provider = provider


def bind_replace(replacer: Callable[[ClusterState], None]) -> None:
    """Remember how to publish a replacement cluster state."""
    global _replacer
    _replacer = replacer


def current() -> ClusterState:
    """Return the cluster state bound by the composition root."""
    if _provider is None:
        raise RuntimeError("cluster state is not bound")
    return cast(ClusterState, _provider())


def replace(state: ClusterState) -> None:
    """Publish a new cluster state through the composition root."""
    if _replacer is None:
        raise RuntimeError("cluster state replacement is not bound")
    _replacer(state)
