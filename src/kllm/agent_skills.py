"""Stable public import path. Re-exports the owning module."""
from __future__ import annotations

import sys

from kllm.modules.connect.interactors import agent_skills as _impl

if __name__ == "kllm.agent_skills":
    sys.modules[__name__] = _impl
