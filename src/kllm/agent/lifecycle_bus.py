"""Lifecycle bus for existing ``kllm.agent.lifecycle_bus`` imports.

The implementation lives in ``kllm.shared.lifecycle_bus``. Feature modules import
that module directly so they do not load this package's composition root.
``lifecycle_bus`` is the same process-wide instance.
"""

from kllm.shared.lifecycle_bus import (
    LifecycleBus as LifecycleBus,
    LifecycleEvent as LifecycleEvent,
    LifecycleNotice as LifecycleNotice,
    lifecycle_bus as lifecycle_bus,
    publish_lifecycle as publish_lifecycle,
)

__all__ = [
    "LifecycleBus",
    "LifecycleEvent",
    "LifecycleNotice",
    "lifecycle_bus",
    "publish_lifecycle",
]
