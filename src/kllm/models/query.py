"""Stable public import path. Re-exports the owning module."""
from __future__ import annotations

import sys

from kllm.modules.models.interactors import query as _impl

if __name__ == "kllm.models.query":
    sys.modules[__name__] = _impl
