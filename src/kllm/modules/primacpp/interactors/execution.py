"""Immutable placement and asset contracts, independent of the legacy topology view."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from kllm.modules.primacpp.entities import Record, RankAllocation
from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.registry import file_digest


class Asset(Record):
    name: str = Field(min_length=1, max_length=256)
    kind: Literal["model", "shard", "draft", "projector", "template", "manifest"]
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    size: int = Field(gt=0)

    @field_validator("name")
    @classmethod
    def basename(cls, value: str) -> str:
        if value in (".", "..") or "/" in value or "\\" in value or ":" in value or "\x00" in value:
            raise ValueError("Asset names must be portable basenames")
        stem = value.split(".", 1)[0].upper()
        if value.endswith((".", " ")) or stem in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }:
            raise ValueError("Asset basename is not portable to Windows")
        return value


class Segment(Record):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    node_id: str


class ExecutionManifest(Record):
    schema_version: Literal[1] = 1
    runtime_id: str = Field(pattern="^[0-9a-f]{64}$")
    run_id: str
    plan_digest: str = Field(pattern="^[0-9a-f]{64}$")
    total_layers: int = Field(gt=0)
    split_mode: Literal["layer", "row", "tensor"] = "layer"
    data_port: int = Field(default=9000, ge=1024, le=65504)
    signal_port: int = Field(default=10000, ge=1024, le=65504)
    ranks: list[RankAllocation] = Field(min_length=1, max_length=32)
    assets: list[Asset] = Field(min_length=1)
    settings: dict[str, Any]
    inventory_digest: str = Field(pattern="^[0-9a-f]{64}$")
    network_id: str | None = None
    segments: list[Segment]
    tensor_placements: list[dict[str, Any]] = Field(default_factory=list)
    transfer_edges: list[dict[str, Any]] = Field(default_factory=list)
    backends: dict[str, str]

    @field_validator("run_id")
    @classmethod
    def uuid(cls, value: str) -> str:
        return str(UUID(value))

    @model_validator(mode="after")
    def coverage(self):
        if [rank.rank for rank in self.ranks] != list(range(len(self.ranks))):
            raise ValueError("Ranks must be ordered from zero")
        nodes = {rank.node_id for rank in self.ranks}
        if (
            len(nodes) != len(self.ranks)
            or self.ranks[0].role != "master"
            or any(r.role != "worker" for r in self.ranks[1:])
        ):
            raise ValueError("Unique participants and one rank-zero master are required")
        if set(self.backends) != nodes:
            raise ValueError("Every rank requires an explicit backend")
        if len({a.name for a in self.assets}) != len(self.assets):
            raise ValueError("Asset basenames must be unique")
        if sum(a.kind == "model" for a in self.assets) != 1:
            raise ValueError("Exactly one primary model is required")
        end = 0
        for segment in self.segments:
            if segment.start != end or segment.end <= segment.start or segment.node_id not in nodes:
                raise ValueError("Execution segments have gaps, overlaps or unknown owners")
            end = segment.end
        if end != self.total_layers:
            raise ValueError("Execution segments do not cover the model")
        assigned = sorted(
            (r.start, r.end, rank.node_id) for rank in self.ranks for r in rank.layers
        )
        declared = sorted((s.start, s.end, s.node_id) for s in self.segments)
        if assigned != declared:
            raise ValueError("Segment and rank ownership disagree")
        if self.data_port + len(self.ranks) > 65536 or self.signal_port + len(self.ranks) > 65536:
            raise ValueError("Port range exceeds TCP limits")
        if set(range(self.data_port, self.data_port + len(self.ranks))) & set(
            range(self.signal_port, self.signal_port + len(self.ranks))
        ):
            raise ValueError("Data and signal ports overlap")
        return self

    @property
    def digest(self) -> str:
        return body_digest(self.model_dump())


def inventory_identity(members: dict, network_id: str | None = None) -> str:
    # Liveness timestamps are deliberately omitted: revisions/inventory changes invalidate plans.
    fields = (
        "role",
        "host",
        "rpc_bind",
        "devices",
        "backend",
        "actual_backend",
        "gpu_ids",
        "total_ram_bytes",
        "config_revision",
        "network_revision",
    )
    return body_digest(
        {
            "network_id": network_id,
            "members": {n: {k: m.get(k) for k in fields} for n, m in sorted(members.items())},
        }
    )


def asset_files(model: Path, companions: dict[str, Path] | None = None) -> list[tuple[Asset, Path]]:
    files: list[tuple[str, str, Path]] = [("model", model.name, model)]
    # All pieces of a split GGUF are needed locally, with their original basenames.
    match = re.fullmatch(r"(.+)-\d{5}-of-(\d{5})\.gguf", model.name)
    if match:
        count = int(match[2])
        if count < 1 or count > 4096:
            raise ValueError("Invalid GGUF shard count")
        for index in range(1, count + 1):
            path = model.with_name(f"{match[1]}-{index:05d}-of-{count:05d}.gguf")
            if path != model:
                files.append(("shard", path.name, path))
    files.extend((kind, path.name, path) for kind, path in (companions or {}).items())
    result = []
    for kind, name, path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe required asset: {name}")
        before = path.stat()
        asset = Asset(name=name, kind=kind, sha256=file_digest(path), size=before.st_size)
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError("Asset changed while hashing")
        result.append((asset, path))
    if len({a.name for a, _ in result}) != len(result):
        raise ValueError("Required assets have conflicting basenames")
    return result


def require_execution_capabilities(manifest: ExecutionManifest, capabilities: dict) -> None:
    features = set(capabilities.get("features", []))
    if "execution_manifest_v1" not in features:
        raise ValueError("Upgrade this Prima runtime for explicit execution manifests")
    if manifest.split_mode != "layer" and manifest.split_mode + "_placement" not in features:
        raise ValueError(
            f"Prima {manifest.split_mode} placement is not implemented in this runtime"
        )
    if manifest.tensor_placements and "expert_placement" not in features:
        raise ValueError("Prima remote expert placement is not implemented in this runtime")
    if "segmented_placement" not in features and manifest.split_mode == "layer":
        from kllm.modules.primacpp.interactors.placement import layer_windows

        layer_windows(manifest)
    if any(len(r.layers) > 1 for r in manifest.ranks) and "segmented_placement" not in features:
        raise ValueError("Repeated rank visits are not implemented in this runtime")
