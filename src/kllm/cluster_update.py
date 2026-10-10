"""Stable public import path. Re-exports the owning module."""
from __future__ import annotations

import sys

from kllm.modules.updates.interactors import cluster_update as _impl

if __name__ == "kllm.cluster_update":
    sys.modules[__name__] = _impl
elif __name__ == "__main__":
    raise SystemExit(_impl.main())
