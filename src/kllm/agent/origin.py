"""Origin helpers for existing ``kllm.agent.origin`` imports.

The implementation lives in ``kllm.shared.origin``. Feature modules import that
module directly so they do not load this package's composition root.
"""

from kllm.shared.origin import (
    _is_loopback as _is_loopback,
    _live_request as _live_request,
    _origin as _origin,
    _origin_allowed as _origin_allowed,
    _request_origin as _request_origin,
    _websocket_origin as _websocket_origin,
)

__all__ = [
    "_is_loopback",
    "_live_request",
    "_origin",
    "_origin_allowed",
    "_request_origin",
    "_websocket_origin",
]
