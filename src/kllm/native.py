"""Stable public import path. Re-exports the owning module."""
from __future__ import annotations

import sys

from kllm.shared import native as _impl

if __name__ == "kllm.native":
    sys.modules[__name__] = _impl
