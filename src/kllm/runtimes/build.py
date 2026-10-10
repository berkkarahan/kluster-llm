"""Stable public import path. Re-exports the owning module."""
from __future__ import annotations

import sys

from kllm.modules.runtimes.interactors import build as _impl

if __name__ == "kllm.runtimes.build":
    sys.modules[__name__] = _impl
