"""Pinned source/build identity and conservative release qualification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def recipe() -> dict:
    return json.loads((ROOT / "recipe.json").read_text(encoding="utf-8"))


def recipe_digest() -> str:
    # Normalize checkout line endings so Windows and Unix pin the same recipe.
    value = hashlib.sha256((ROOT / "recipe.json").read_text(encoding="utf-8").encode())
    value.update((ROOT / "interactors/build.py").read_text(encoding="utf-8").encode())
    for name in recipe()["patches"]:
        value.update(name.encode())
        value.update((ROOT / "patches" / name).read_text(encoding="utf-8").encode())
    return value.hexdigest()


def qualification_errors(runtime: dict, *, context: dict | None = None) -> list[str]:
    if runtime.get("engine", "llamacpp") != "primacpp":
        return []
    if runtime.get("platform") or context is not None:
        from kllm.modules.primacpp.interactors.qualification import qualification_status

        return qualification_status(runtime, context=context)["qualification_errors"]
    # A source-controlled gate cannot be bypassed by editing ~/.kllm manifests.
    required = recipe()["qualification"]
    return [name for name, passed in required.items() if not passed]


def require_qualified(runtime: dict, *, context: dict | None = None) -> None:
    missing = qualification_errors(runtime, context=context)
    if missing:
        raise ValueError("Prima is not qualified for inference: " + ", ".join(missing))
