"""Immutable placement and asset contracts, independent of the legacy topology view."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator, model_serializer

from kllm.modules.primacpp.entities import Record, RankAllocation
from kllm.modules.primacpp.interactors.devices import stable_device_inventory
from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.registry import file_digest
from kllm.modules.primacpp.interactors.participants import ParticipantEvidence
from kllm.modules.primacpp.interactors.draft import draft_mode
from kllm.modules.primacpp.interactors.extras import template_kwargs, adapter_requests
from kllm.modules.primacpp.interactors.adapters import adapter_bindings


class Asset(Record):
    name: str = Field(min_length=1, max_length=256)
    kind: Literal["model", "shard", "draft", "projector", "template", "manifest", "adapter"]
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


def validate_asset_batch(assets: list[Asset], *, include_manifest: bool = False) -> None:
    """Keep basename layout and digest-addressed transfer offsets unambiguous."""
    manifests = [asset for asset in assets if asset.kind == "manifest"]
    if (sum(asset.kind == "model" for asset in assets) != 1
            or include_manifest and (len(manifests) != 1 or manifests[0].name != "execution.json")
            or not include_manifest and any(asset.kind == "manifest" for asset in assets)):
        raise ValueError("Prima assets require one model and the expected execution manifest")
    if manifests and manifests[0].size > 4 * 1024 * 1024:
        raise ValueError("Prima execution manifest exceeds the native 4 MiB limit")
    if len({asset.name.casefold() for asset in assets}) != len(assets):
        raise ValueError("Asset basenames must be unique across supported filesystems")
    sizes: dict[str, int] = {}
    for asset in assets:
        prior = sizes.setdefault(asset.sha256, asset.size)
        if prior != asset.size:
            raise ValueError("Identical asset digests cannot declare different sizes")


class Segment(Record):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    node_id: str


class ProfileEdge(Record):
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)


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
    profile_edges: list[ProfileEdge] = Field(default_factory=list, max_length=992)
    backends: dict[str, str]
    participant_evidence: dict[str, ParticipantEvidence] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def wire(self, serialize):
        value = serialize(self)
        # Preserve canonical bytes/digests for existing manifests and local runs.
        if not self.participant_evidence:
            value.pop("participant_evidence", None)
        if not self.profile_edges:
            value.pop("profile_edges", None)
        return value

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
        if self.profile_edges:
            if not self.network_id or len(self.ranks) < 2:
                raise ValueError("Candidate profiling edges require a distributed private network")
            pairs = {(edge.source, edge.target) for edge in self.profile_edges}
            if (len(pairs) != len(self.profile_edges)
                    or any(source == target or source not in nodes or target not in nodes
                           for source, target in pairs)):
                raise ValueError("Candidate profiling edges must be unique directed prepared participants")
        if self.participant_evidence:
            if set(self.participant_evidence) != nodes:
                raise ValueError("Every rank requires observed participant evidence")
            if (len({e.source_commit for e in self.participant_evidence.values()}) != 1
                    or len({e.recipe_digest for e in self.participant_evidence.values()}) != 1):
                raise ValueError("Participant source/recipe identities disagree")
            for rank in self.ranks:
                evidence = self.participant_evidence[rank.node_id]
                if (evidence.node_id != rank.node_id or evidence.runtime_id != self.runtime_id
                        or evidence.backend != self.backends[rank.node_id]
                        or evidence.allocation_digest != body_digest(rank.model_dump())
                        or len(evidence.devices) != len(rank.devices)):
                    raise ValueError("Participant evidence disagrees with its rank/runtime allocation")
                if any(selector not in (device.name, device.device_identity)
                       for selector, device in zip(rank.devices, evidence.devices)):
                    raise ValueError("Participant devices disagree with the planned selection")
        validate_asset_batch(self.assets)
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


def profile_pairs(manifest: ExecutionManifest) -> set[tuple[str, str]]:
    """Measurement authorization adds no native listener or execution edges."""
    return {(edge["source"], edge["target"]) for edge in manifest.transfer_edges} | {
        (edge.source, edge.target) for edge in manifest.profile_edges}


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
            "members": {n: {k: stable_device_inventory(m.get(k)) if k == "devices" else m.get(k)
                            for k in fields} for n, m in sorted(members.items())},
        }
    )


def authority_owner(authority) -> dict:
    fields = ("cluster_binding_id", "master_node_id", "master_key_id", "master_term", "master_session_id")
    return {"sender": str(authority.master_node_id),
            "authority_digest": body_digest({key: str(getattr(authority, key)) for key in fields})}


def asset_files(model: Path, companions: dict[str, Path] | None = None, *, adapters: list[Path] | None = None) -> list[tuple[Asset, Path]]:
    primary = [("model", model), *(companions or {}).items(), *(("adapter", path) for path in adapters or [])]
    files: list[tuple[str, str, Path]] = []
    # All pieces of a split GGUF are needed locally, with their original basenames.
    for kind, primary_path in primary:
        files.append((kind, primary_path.name, primary_path))
        match = re.fullmatch(r"(.+)-(\d{5})-of-(\d{5})\.gguf", primary_path.name) if kind != "adapter" else None
        if match:
            index, count = int(match[2]), int(match[3])
            if count < 1 or count > 4096:
                raise ValueError("Invalid GGUF shard count")
            if index != 1:
                raise ValueError("Select the first GGUF shard for native model loading")
            for index in range(1, count + 1):
                path = primary_path.with_name(f"{match[1]}-{index:05d}-of-{count:05d}.gguf")
                if path != primary_path:
                    files.append(("shard", path.name, path))
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
    if len({a.name.casefold() for a, _ in result}) != len(result):
        raise ValueError("Required assets have conflicting basenames across supported filesystems")
    return result


def require_execution_capabilities(manifest: ExecutionManifest, capabilities: dict) -> None:
    features = set(capabilities.get("features", []))
    if "logical_context_sequences_v1" not in features:
        raise ValueError("Upgrade Prima for consistent logical context slots across rank roles")
    if "embedding_role_v1" not in features:
        raise ValueError("Upgrade Prima to bind embedding/pooling mode to the approved rank role")
    from kllm.modules.primacpp.interactors.extras import head_only_arguments
    head = head_only_arguments(manifest.settings)
    if any(flag in ("--reranking", "--rerank") for flag in head) and "rerank_api_v1" not in features:
        raise ValueError("Upgrade Prima for approved native reranking")
    if any(flag in ("--embedding", "--embeddings") for flag in head) and "embedding_output_v1" not in features:
        raise ValueError("Upgrade Prima for projected native embedding outputs")
    if len(manifest.ranks) > 1 and "decode_metadata_v2" not in features:
        raise ValueError("Upgrade Prima for bounded shared-sequence and output-count batch metadata")
    full = manifest.settings.get("swa_full")
    slots = manifest.settings.get("parallel_slots")
    sequences = manifest.settings.get("prima_kv_sequences")
    if (type(full) is not bool or type(slots) is not int or not 1 <= slots < 2**31 - 1
            or type(sequences) is not int or sequences != slots + 1):
        raise ValueError("Prima execution requires the approved KV storage mode and sequence ceiling")
    storage_capability = "swa_full_v1" if full else "compact_swa_v1"
    if storage_capability not in features:
        raise ValueError("Upgrade Prima for the approved SWA storage layout")
    if any(manifest.settings.get(key) is not None for key in ("reasoning_format", "reasoning_budget", "enable_thinking", "reasoning_effort")) and "reasoning_controls_v1" not in features:
        raise ValueError("Upgrade Prima for deployment reasoning controls")
    if template_kwargs(manifest.settings) is not None and "template_kwargs_defaults_v1" not in features:
        raise ValueError("Upgrade Prima for deployment template keyword defaults")
    adapters = [asset for asset in manifest.assets if asset.kind == "adapter"]
    invocation = manifest.settings.get("prima_alora", {})
    if not isinstance(invocation, dict) or any(
        name not in {asset.name for asset in adapters} or not isinstance(value, dict)
        or set(value) != {"count", "sha256"} or type(value["count"]) is not int
        or not 0 < value["count"] <= 100_000 or not isinstance(value["sha256"], str)
        or not re.fullmatch("[0-9a-f]{64}", value["sha256"])
        for name, value in invocation.items()
    ):
        raise ValueError("Prima activated LoRA bindings require exact bounded invocation identities")
    if manifest.settings.get("prima_alora") and "activated_lora_v1" not in features:
        raise ValueError("Upgrade Prima for activated LoRA invocation handling")
    if adapter_requests(manifest.settings) or adapters:
        if "managed_lora_v1" not in features:
            raise ValueError("Upgrade Prima for verified managed LoRA adapters and rank-wide selection")
        declared = adapter_bindings(manifest.settings)
        for key in ("prima_adapter_bytes", "prima_adapter_scratch_per_token"):
            budget = manifest.settings.get(key)
            if type(budget) is not int or not 0 < budget <= 2**63 - 1:
                raise ValueError("Prima LoRA requires bounded planned storage and graph scratch")
        if (declared != manifest.settings.get("prima_lora")
                or {asset.name for asset in adapters} != {binding["name"] for binding in declared}):
            raise ValueError("Prima LoRA files/scales differ from the execution manifest")
    cache = manifest.settings.get("cache_ram", 0)
    if type(cache) is not int or not 0 <= cache <= 2147483647:
        raise ValueError("Prima cache_ram must be a bounded nonnegative integer")
    if cache and "prompt_cache_v1" not in features:
        raise ValueError("Upgrade Prima for rank-wide prompt caching")
    if cache and manifest.settings.get("prompt_cache_bytes") != cache * 1024**2:
        raise ValueError("Prima prompt cache budget differs from its planned host reservation")
    count = manifest.settings.get("checkpoints", 0)
    slots = manifest.settings.get("parallel_slots", 1)
    if type(count) is not int or not 0 <= count <= 16384:
        raise ValueError("Prima checkpoints require a bounded nonnegative count")
    if count:
        if "context_checkpoints_v1" not in features:
            raise ValueError("Upgrade Prima for rank-wide context checkpoints")
        budget = manifest.settings.get("prima_checkpoint_bytes")
        spacing = manifest.settings.get("checkpoint_min_step")
        if (type(slots) is not int or slots < 1 or count * slots > 16384
                or type(budget) is not int or not 0 < budget <= 2**63 - 1
                or type(spacing) is not int or not 0 <= spacing <= 2147483647
                or manifest.settings.get("prima_checkpoints") is not True):
            raise ValueError("Prima checkpoint budget, spacing or slot count differs from its launch contract")
    if (manifest.settings.get("prima_chat_template_source") or manifest.settings.get("chat_template_text")) and "jinja_template_v1" not in features:
        raise ValueError("Upgrade Prima for custom/preset Jinja template execution")
    if draft_mode(manifest.settings) != "none":
        if "draft_simple_v1" not in features:
            raise ValueError("Upgrade Prima for isolated draft-simple decoding")
        if sum(asset.kind == "draft" for asset in manifest.assets) != 1:
            raise ValueError("Prima draft decoding requires one verified draft asset")
    if any(r.ngl or r.output_gpu for r in manifest.ranks) and "explicit_devices_v1" not in features:
        raise ValueError("Upgrade Prima for explicit selected-device placement")
    if any(r.output_gpu for r in manifest.ranks) and "output_gpu_v1" not in features:
        raise ValueError("Prima output GPU placement requires an updated native runtime")
    if any(r.output_gpu and not r.ngl for r in manifest.ranks) and "output_only_gpu_v1" not in features:
        raise ValueError("Upgrade Prima for output GPU execution without transformer offload")
    if "execution_manifest_v1" not in features:
        raise ValueError("Upgrade this Prima runtime for explicit execution manifests")
    if manifest.split_mode != "layer" and manifest.split_mode + "_placement" not in features:
        raise ValueError(
            f"Prima {manifest.split_mode} placement is not implemented in this runtime"
        )
    if manifest.tensor_placements and "expert_placement" not in features:
        raise ValueError("Prima remote expert placement is not implemented in this runtime")
    if manifest.split_mode == "layer":
        from kllm.modules.primacpp.interactors.placement import require_layer_execution

        require_layer_execution(manifest, features, manifest.settings)
    if any(len(r.layers) > 1 for r in manifest.ranks) and "segmented_placement" not in features:
        raise ValueError("Repeated rank visits are not implemented in this runtime")
