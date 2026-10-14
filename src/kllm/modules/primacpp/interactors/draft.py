"""Translate draft decoding without widening target placement or asset ownership."""

from pathlib import Path

from kllm.modules.primacpp.interactors.compatibility import help_flags
from kllm.modules.runtimes.interactors.registry import file_digest


def draft_mode(settings: dict) -> str:
    mode = settings.get("speculative", "auto")
    model = settings.get("draft_model")
    if mode == "none" or mode == "auto" and not model:
        return "none"
    if mode not in ("auto", "draft", "draft-simple"):
        raise ValueError("Prima does not implement speculative mode " + str(mode))
    if not model:
        raise ValueError("Prima draft-simple decoding requires a draft model")
    maximum = settings.get("draft_max")
    if maximum is None:
        maximum = 3
    if type(maximum) is not int or not 0 <= maximum <= 1024:
        raise ValueError("Prima draft_max requires 0..1024 tokens")
    return "draft-simple"


def draft_arguments(binary: Path, settings: dict, *, rank: int) -> list[str]:
    mode = draft_mode(settings)
    if mode == "none" or rank != 0:
        return []
    flags = help_flags(binary)
    required = {"--spec-type", "--model-draft", "--draft-max", "--gpu-layers-draft",
                "--cache-type-k-draft", "--cache-type-v-draft"}
    if not required <= flags:
        raise ValueError("Upgrade Prima for isolated draft-simple decoding")
    maximum = settings.get("draft_max")
    return ["--spec-type", mode, "--draft-max", str(3 if maximum is None else maximum),
            "--gpu-layers-draft", "0", "--cache-type-k-draft", settings.get("draft_cache_type_k", "f16"),
            "--cache-type-v-draft", settings.get("draft_cache_type_v", "f16")]


def draft_file(manifest, model: Path, *, rank: int) -> Path | None:
    if draft_mode(manifest.settings) == "none" or rank != 0:
        return None
    assets = [asset for asset in manifest.assets if asset.kind == "draft"]
    if len(assets) != 1:
        raise ValueError("Prima draft decoding requires one verified draft asset")
    asset = assets[0]
    path = model.parent / asset.name
    if not path.is_file() or path.is_symlink():
        raise ValueError("Prepared Prima draft asset is unavailable")
    before = path.stat()
    if before.st_size != asset.size or file_digest(path) != asset.sha256:
        raise ValueError("Prepared Prima draft asset differs from the execution manifest")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Prepared Prima draft asset changed during verification")
    return path
