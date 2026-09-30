"""Run pytest with collected items reversed (for isolation diagnostics)."""
from __future__ import annotations

import sys

import pytest


class _ReverseItems:
    def pytest_collection_modifyitems(self, items) -> None:
        items.reverse()


if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:] or ["-q"], plugins=[_ReverseItems()]))
