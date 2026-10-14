"""Bind declared LoRA files/scales to verified assets and owned launch paths."""

import math
import ntpath
import hashlib
import struct
from pathlib import Path

from kllm.modules.models.interactors.gguf_meta import read_adapter_layout
from kllm.modules.primacpp.interactors.extras import adapter_requests
from kllm.modules.runtimes.interactors.registry import file_digest


def adapter_paths(settings: dict) -> list[Path]:
    paths = []
    seen = set()
    for request in adapter_requests(settings):
        path = Path(request["path"]).expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError("Prima LoRA requires a complete regular local adapter file")
        identity = path.resolve()
        if identity not in seen:
            paths.append(path)
            seen.add(identity)
    return paths


def adapter_bindings(settings: dict) -> list[dict]:
    return [{"name": ntpath.basename(request["path"]), "scale": request["scale"]}
            for request in adapter_requests(settings)]


def adapter_reservation(path: Path) -> dict:
    """Conservative full-file storage and per-token LoRA temporary bounds.

    Actual ranks load only owned pairs. Reserve the complete adapter on each
    selected pool until exact ownership-aware pricing consumes these descriptors.
    Eight bytes per element bounds supported GGML tensor storage; file bytes and
    tensor bookkeeping cover metadata/read buffers. This is not allocator evidence.
    """
    before = path.stat()
    layout = read_adapter_layout(path)
    if not layout or layout["confidence"] != "metadata":
        raise ValueError("Prima LoRA requires complete adapter tensor metadata")
    alpha = layout["metadata"].get("adapter.lora.alpha", 0.0)
    if type(alpha) not in (int, float) or not math.isfinite(alpha):
        raise ValueError("Prima LoRA alpha must be finite")
    invocation = layout["metadata"].get("adapter.alora.invocation_tokens", [])
    if not isinstance(invocation, list) or len(invocation) > 100_000 or any(
        type(token) is not int or not 0 <= token < 2**31 for token in invocation
    ):
        raise ValueError("Prima activated LoRA requires a bounded native token array")
    invocation = list(invocation)
    pairs = {}
    elements = 0
    for tensor in layout["tensors"]:
        name = tensor["name"]
        if not name.endswith((".lora_a", ".lora_b")):
            raise ValueError("Prima LoRA tensor has an unexpected suffix")
        dimensions = tuple(tensor["dimensions"]) + (1,) * (4 - len(tensor["dimensions"]))
        if len(tensor["dimensions"]) < 2 or any(type(d) is not int or d <= 0 for d in dimensions):
            raise ValueError("Prima LoRA tensor has invalid dimensions")
        pair = pairs.setdefault(name[:-7], {})
        component = name[-1]
        if component in pair:
            raise ValueError("Prima LoRA contains duplicate tensor components")
        pair[component] = dimensions
        elements += math.prod(dimensions)
    scratch = 0
    for target, pair in pairs.items():
        if set(pair) != {"a", "b"}:
            raise ValueError("Prima LoRA tensor pairs require both components")
        embedding = target == "token_embd.weight"
        rank = pair["a"][0] if embedding else pair["a"][1]
        if rank != pair["b"][0] or pair["a"][2:] != pair["b"][2:]:
            raise ValueError("Prima LoRA tensor pairs have incompatible shapes")
        outputs = max(pair["a"][1], pair["b"][1]) if embedding else pair["b"][1]
        experts = pair["b"][2] * pair["b"][3]
        scratch = max(scratch, 4 * experts * (rank + 2 * outputs))
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Prima LoRA changed during metadata inspection")
    storage = before.st_size + 8 * elements + 1024 * len(layout["tensors"])
    if max(storage, scratch) > 2**63 - 1:
        raise ValueError("Prima LoRA storage bounds exceed native capacity")
    return {"storage_bytes": storage, "scratch_per_token": scratch, "architecture": layout["architecture"],
            "targets": sorted(pairs), "invocation_tokens": invocation}


def adapter_targets(settings: dict) -> dict[str, list[str]]:
    return {path.name: adapter_reservation(path)["targets"] for path in adapter_paths(settings)}


def invocation_identity(tokens: list[int]) -> dict:
    data = struct.pack("<" + "I" * len(tokens), *tokens)
    return {"count": len(tokens), "sha256": hashlib.sha256(data).hexdigest()}


def adapter_invocations(settings: dict) -> dict[str, dict]:
    values = {}
    for path in adapter_paths(settings):
        tokens = adapter_reservation(path)["invocation_tokens"]
        if tokens:
            values[path.name] = invocation_identity(tokens)
    return values


def adapter_arguments(manifest, model: Path) -> list[str]:
    declared = adapter_bindings(manifest.settings)
    if declared != manifest.settings.get("prima_lora", []):
        raise ValueError("Prima LoRA binding differs from the signed execution settings")
    assets = {asset.name: asset for asset in manifest.assets if asset.kind == "adapter"}
    if set(assets) != {binding["name"] for binding in declared}:
        raise ValueError("Prima LoRA assets differ from the declared adapter files")
    verified = {}
    for name, asset in assets.items():
        path = model.parent / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Prepared Prima LoRA asset is unavailable")
        before = path.stat()
        if before.st_size != asset.size or file_digest(path) != asset.sha256:
            raise ValueError("Prepared Prima LoRA bytes differ from the execution manifest")
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Prepared Prima LoRA changed during verification")
        verified[name] = path
    expected_targets = manifest.settings.get("prima_lora_targets")
    if declared and (not isinstance(expected_targets, dict) or set(expected_targets) != set(assets)):
        raise ValueError("Prima LoRA target bindings differ from its verified asset set")
    storage = scratch = 0
    invocations = {}
    for binding in declared:
        reserve = adapter_reservation(verified[binding["name"]])
        if reserve["targets"] != expected_targets[binding["name"]]:
            raise ValueError("Prima LoRA tensor targets differ from the execution settings")
        storage += reserve["storage_bytes"]
        scratch += reserve["scratch_per_token"]
        if reserve["invocation_tokens"]:
            invocations[binding["name"]] = invocation_identity(reserve["invocation_tokens"])
    if invocations != manifest.settings.get("prima_alora", {}):
        raise ValueError("Prima activated LoRA invocation differs from the verified adapter bytes")
    if declared and (manifest.settings.get("prima_adapter_bytes") != storage
                     or manifest.settings.get("prima_adapter_scratch_per_token") != scratch):
        raise ValueError("Prima LoRA memory bounds differ from the verified adapter bytes")
    return [argument for binding in declared
            for argument in ("--lora-scaled", str(verified[binding["name"]]), str(binding["scale"]))]
