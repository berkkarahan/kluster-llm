"""Stable public import path. Re-exports the owning module."""
from __future__ import annotations

import sys

from kllm.shared import log_sink as _impl

if __name__ == "kllm.log_sink":
    sys.modules[__name__] = _impl
elif __name__ == "__main__":
    _impl.main()
    raise SystemExit(0)
