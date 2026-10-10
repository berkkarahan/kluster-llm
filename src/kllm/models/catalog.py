"""Stable public import path. Re-exports the owning module."""
from __future__ import annotations

import sys

from kllm.modules.models.interactors import catalog as _impl

if __name__ == "kllm.models.catalog":
    sys.modules[__name__] = _impl
