"""Stable public import path. Re-exports the owning module."""
from __future__ import annotations

import sys

from kllm.modules.models.interactors import download_transfer as _impl

if __name__ == "kllm.models.download_transfer":
    sys.modules[__name__] = _impl
