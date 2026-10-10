"""Stable public import path. Re-exports the owning module."""
from __future__ import annotations

import sys

from kllm.shared.platform import inspection as _impl

if __name__ == "kllm.managed_network.platform.inspection":
    sys.modules[__name__] = _impl
