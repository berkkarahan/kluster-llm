"""Compatibility imports for shared CLI utilities."""

from kllm.commands.agent_runtime import (
    _agent_base as _agent_base,
    _agent_healthy as _agent_healthy,
    _client as _client,
    _ensure_agent as _ensure_agent,
    _replace_tracked_instance as _replace_tracked_instance,
    _stop_local as _stop_local,
)
from kllm.commands.gpu_options import (
    _prompt_gpu_ids as _prompt_gpu_ids,
    parse_gpu_ids as parse_gpu_ids,
    resolve_cli_gpus as _resolve_cli_gpus,
)
from kllm.commands.ui import console as console, format_vram as _fmt_vram

__all__ = [
    "_agent_base",
    "_agent_healthy",
    "_client",
    "_ensure_agent",
    "_fmt_vram",
    "_prompt_gpu_ids",
    "_replace_tracked_instance",
    "_resolve_cli_gpus",
    "_stop_local",
    "console",
    "parse_gpu_ids",
]
