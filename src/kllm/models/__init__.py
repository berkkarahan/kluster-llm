"""Stable import path for the model catalog.

Attribute and explicit imports resolve to kllm.modules.models.
"""
from __future__ import annotations

from kllm.modules import models as _models

def __getattr__(name: str):
    return getattr(_models, name)

def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_models)))
