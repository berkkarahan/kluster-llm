"""Model package. Importing a submodule does not load the catalog API."""

from __future__ import annotations

import importlib
from typing import Any


_SUBMODULES = {
    "catalog": "kllm.modules.models.interactors.catalog",
    "downloads": "kllm.modules.models.interactors.downloads",
    "download_progress": "kllm.modules.models.interactors.download_progress",
    "download_transfer": "kllm.modules.models.interactors.download_transfer",
    "query": "kllm.modules.models.interactors.query",
    "resolved_settings": "kllm.modules.models.interactors.resolved_settings",
    "schemas": "kllm.modules.models.entities",
    "store": "kllm.modules.models.interactors.store",
}


def __getattr__(name: str) -> Any:
    submodule = _SUBMODULES.get(name)
    if submodule is not None:
        return importlib.import_module(submodule)
    api = importlib.import_module("kllm.modules.models.api")
    return getattr(api, name)


def __dir__() -> list[str]:
    api = importlib.import_module("kllm.modules.models.api")
    return sorted(set(globals()) | set(_SUBMODULES) | set(dir(api)))
