"""Pinned source/build identity and conservative release qualification."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from kllm.shared.state import home_dir

ROOT = Path(__file__).resolve().parents[1]
RECIPE_METADATA_KEY = "prima_recipe"

DEFAULT_RECIPE: dict[str, Any] = {
    "schema_version": 2,
    "upstream_repository": "https://github.com/OpenCPIL/prima.cpp.git",
    "upstream_commit": "288df6a2cc2120387e12d6f40798f30cc525c9ea",
    "build_recipe_version": 138,
    "dependencies": {
        "libzmq": "622fc6dde99ee172ebaa9c8628d85a7a1995a21d",
        "cppzmq": "c94c20743ed7d4aa37835a5c46567ab0790d4acc",
        "highs": "v1.9.0",
    },
    "dependency_archive_sha256": {
        "libzmq": "65d021968f90d61fd50d8e50409bdd494b040602bfb043e1bd5647ab035db3b4",
        "cppzmq": "6fab1d2522bc3c5f952f176fe7c486f83991fb61f2c5acc63fa33fdb5928ecf3",
        "highs": "dff575df08d88583c109702c7c5c75ff6e51611e6eacca8b5b3fdfba8ecc2cb4",
    },
    "qualification": {
        "vulkan_gpu_inference": True,
        "api_parity": True,
        "platform_backend_parity": True,
        "model_and_placement_parity": True,
        "signed_rank_lifecycle": True,
        "hardware_performance_gate": True,
    },
    "source_repository": "https://github.com/berkkarahan/prima.cpp.git",
    "source_release": "0.0.5",
    "source_release_commit": "7b6d492cbf450f07b598c5613766ecf25cf6520b",
    "source_commit": "7b6d492cbf450f07b598c5613766ecf25cf6520b",
}


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def recipe(home: Path | None = None) -> dict[str, Any]:
    from kllm.modules.managed_network.gateways.store import NetworkStore

    target_home = Path(home) if home is not None else home_dir()
    active = NetworkStore.active_for(target_home)
    if active is not None:
        return active.prima_recipe()
    db_path = target_home / "managed-network.sqlite"
    if db_path.is_file() and not db_path.is_symlink():
        try:
            with sqlite3.connect(db_path, timeout=5.0) as db:
                row = db.execute(
                    "SELECT value FROM metadata WHERE key = ?", (RECIPE_METADATA_KEY,)
                ).fetchone()
                if row:
                    loaded = json.loads(row[0])
                    if isinstance(loaded, dict):
                        version = int(loaded.get("build_recipe_version", 0))
                        default_version = int(DEFAULT_RECIPE["build_recipe_version"])
                        if version > default_version or (
                            version == default_version
                            and loaded.get("qualification") == DEFAULT_RECIPE["qualification"]
                        ):
                            return loaded
                serialized = _canonical_json(DEFAULT_RECIPE)
                db.execute(
                    "INSERT INTO metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (RECIPE_METADATA_KEY, serialized),
                )
                return deepcopy(DEFAULT_RECIPE)
        except sqlite3.Error:
            pass
    return deepcopy(DEFAULT_RECIPE)


def save_recipe(value: dict[str, Any], home: Path | None = None) -> dict[str, Any]:
    from kllm.modules.managed_network.gateways.store import NetworkStore

    target_home = Path(home) if home is not None else home_dir()
    active = NetworkStore.active_for(target_home)
    if active is not None:
        return active.set_prima_recipe(value)
    store = NetworkStore(target_home)
    try:
        return store.set_prima_recipe(value)
    finally:
        store.close()


def recipe_digest(home: Path | None = None) -> str:
    # Normalize representation so Windows and Unix pin the same recipe digest.
    # Qualification gate policy is independent of the CMake/compiler build identity.
    current = deepcopy(recipe() if home is None else recipe(home))
    if isinstance(current.get("qualification"), dict):
        current["qualification"] = {key: False for key in current["qualification"]}
    value = hashlib.sha256(_canonical_json(current).encode("utf-8"))
    value.update((ROOT / "interactors/build.py").read_text(encoding="utf-8").encode("utf-8"))
    return value.hexdigest()


def qualification_errors(runtime: dict, *, context: dict | None = None) -> list[str]:
    if runtime.get("engine", "llamacpp") != "primacpp":
        return []
    if runtime.get("platform") or runtime.get("build_id") or context is not None:
        from kllm.modules.primacpp.interactors.qualification import qualification_status

        return qualification_status(runtime, context=context)["qualification_errors"]
    # A source-controlled gate cannot be bypassed by editing ~/.kllm manifests.
    required = recipe()["qualification"]
    return [name for name, passed in required.items() if not passed]


def require_qualified(runtime: dict, *, context: dict | None = None) -> None:
    missing = qualification_errors(runtime, context=context)
    if missing:
        raise ValueError("Prima is not qualified for inference: " + ", ".join(missing))
