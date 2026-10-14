"""Translate resolved Kluster settings without dropping unsupported Prima behavior."""

import hashlib
from pathlib import Path

from kllm.modules.primacpp.interactors.compatibility import help_flags
from kllm.modules.inference.interactors.chat_templates import (
    PRESETS, selected_template_text, validate_selection,
)
from kllm.modules.primacpp.interactors.devices import remapped_vulkan_names
from kllm.modules.primacpp.interactors.draft import draft_arguments, draft_mode
from kllm.modules.primacpp.interactors.extras import extra_arguments, template_kwargs
from kllm.modules.primacpp.interactors.adapters import adapter_bindings, adapter_targets, adapter_invocations


def device_arguments(binary, runtime: dict, allocation, *, selected: list[dict]) -> list[str]:
    """The visibility mask and ordered native selection describe the same devices."""
    if "explicit_devices_v1" not in (runtime.get("capabilities") or {}).get("features", []):
        if allocation.ngl or allocation.output_gpu:
            raise ValueError("Upgrade Prima for explicit selected-device placement")
        return []
    if "--device" not in help_flags(binary):
        raise ValueError("Prima device capability disagrees with the selected rank binary")
    if not allocation.ngl and not allocation.output_gpu:
        return ["--device", "none"]
    names = remapped_vulkan_names(selected) if selected else list(allocation.devices)
    if not names or len(names) != len(allocation.devices):
        raise ValueError("Prima offload devices disagree with the approved allocation")
    return ["--device", ",".join(names)]


def execution_settings(body, plan: dict, model, port: int) -> dict:
    settings = {**body.model_dump(), **plan.get("resolved_launch_settings", {}),
                "context": plan.get("context_effective", body.context),
                "assessment_port": port, "alias": model.name}
    settings.setdefault("swa_full", False)
    slots = settings.get("parallel_slots")
    sequences = settings.get("prima_kv_sequences")
    if type(slots) is not int or not 1 <= slots < 2**31 - 1 or type(sequences) is not int or sequences != slots + 1:
        raise ValueError("Prima launch requires the planned head KV sequence reservation")
    template, text = settings.get("chat_template", "default"), settings.get("chat_template_text")
    validate_selection(template, text)
    template_kwargs(settings)  # Validate declared extras before preparing assets.
    if settings.get("checkpoints", 0):
        budget = plan.get("checkpoint_estimate_bytes")
        if type(budget) is not int or not 0 < budget <= 2**63 - 1 or not settings.get("prima_checkpoints"):
            raise ValueError("Prima checkpoints require the planned full-state host reservation")
        settings["prima_checkpoint_bytes"] = budget
    if settings.get("prima_graphs") is True:
        counts = plan.get("prima_graph_counts")
        if not isinstance(counts, dict) or any(
                not isinstance(node, str) or type(count) is not int or count < 0
                for node, count in counts.items()):
            raise ValueError("Prima graph reservations require the planner's exact rank visit counts")
        settings["prima_graph_counts"] = dict(counts)
    adapters = adapter_bindings(settings)
    if adapters:
        settings["prima_lora"] = adapters
        settings["prima_lora_targets"] = adapter_targets(settings)
        invocations = adapter_invocations(settings)
        if invocations:
            settings["prima_alora"] = invocations
    mode = draft_mode(settings)
    if mode != "none":
        settings["speculative"] = mode
        settings["draft_gpu_layers"] = 0
    if template in PRESETS or text:
        # Freeze exact preset bytes in the signed manifest before preparation.
        # Native CLI workers consume rank-zero tokens, so only rank zero renders.
        settings["prima_chat_template_source"] = selected_template_text(template, text, "")
    return settings


def materialize_template(settings: dict, directory: Path) -> Path | None:
    source = settings.get("prima_chat_template_source") or settings.get("chat_template_text")
    if not source:
        return None
    if not isinstance(source, str) or len(source.encode("utf-8")) > 64 * 1024:
        raise ValueError("Prima Jinja template must be at most 64 KiB")
    data = source.encode("utf-8")
    path = directory / ("chat-template-" + hashlib.sha256(data).hexdigest() + ".jinja")
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != len(data) or path.read_bytes() != data:
            raise ValueError("Owned Prima template file differs from its signed source")
    return path


def rank_arguments(binary, settings: dict, *, rank: int = 0, template_file: Path | None = None,
                   kwargs_file: Path | None = None) -> list[str]:
    flags = help_flags(binary)
    arguments = []
    for key, flag in (
        ("parallel_slots", "--parallel"), ("batch_size", "--batch-size"),
        ("micro_batch", "--ubatch-size"), ("threads_batch", "--threads-batch"),
        ("cache_type_k", "--cache-type-k"), ("cache_type_v", "--cache-type-v"),
        ("alias", "--alias"), ("seed", "--seed"),
        ("temperature", "--temp"), ("top_p", "--top-p"), ("top_k", "--top-k"),
        ("min_p", "--min-p"), ("presence_penalty", "--presence-penalty"),
        ("repeat_penalty", "--repeat-penalty"), ("frequency_penalty", "--frequency-penalty"),
        ("max_output_tokens", "--predict"),
    ):
        value = settings.get(key)
        # The alias names the rank-zero HTTP endpoint. CLI workers still receive
        # every decoder setting, including the shared parallel sequence count.
        if key == "alias" and rank != 0:
            continue
        if value is not None:
            if flag not in flags:
                raise ValueError(f"Prima does not implement launch setting {key}")
            arguments += [flag, str(value)]
    for key, flag in (("checkpoints", "--ctx-checkpoints"), ("cache_ram", "--cache-ram")):
        value = settings.get(key, 0)
        if value:
            if flag not in flags:
                raise ValueError(f"Prima does not implement {key}; select zero or upgrade its native contract")
            arguments += [flag, str(value)]
    flash = settings.get("flash_attention", "off")
    if settings.get("checkpoints", 0):
        for key, flag in (("prima_checkpoint_bytes", "--checkpoint-cache-bytes"),
                          ("checkpoint_min_step", "--checkpoint-min-step")):
            value = settings.get(key)
            if type(value) is not int or value < 0 or (key == "prima_checkpoint_bytes" and not value) or flag not in flags:
                raise ValueError("Prima checkpoint execution requires an approved byte ceiling and spacing")
            arguments += [flag, str(value)]
    if flash == "on":
        if "--flash-attn" not in flags:
            raise ValueError("Prima does not implement Flash Attention")
        arguments += ["--flash-attn"]
    elif flash != "off":
        raise ValueError("Resolve explicit Flash Attention on/off before Prima launch")
    arguments += draft_arguments(binary, settings, rank=rank)
    full = settings.get("swa_full", False)
    if type(full) is not bool:
        raise ValueError("Prima SWA storage mode must be a boolean")
    storage_flag = "--swa-full" if full else "--swa-compact"
    if storage_flag in flags:
        arguments += [storage_flag]
    elif full:
        # Legacy native binaries always run their compact default; never
        # silently downgrade an explicit full-SWA request.
        raise ValueError("Upgrade Prima for explicit SWA storage selection")
    if settings.get("vision") or settings.get("mmproj"):
        raise ValueError("Prima vision execution is not qualified")
    arguments += extra_arguments(flags, settings, rank=rank, kwargs_file=kwargs_file)
    template = settings.get("chat_template", "default")
    if rank == 0:
        for key, flag in (("reasoning_format", "--reasoning-format"), ("reasoning_budget", "--reasoning-budget"), ("reasoning_effort", "--reasoning-effort")):
            value = settings.get(key)
            if value is not None:
                if flag not in flags:
                    raise ValueError("Upgrade Prima for deployment reasoning controls")
                arguments += [flag, str(value)]
        if settings.get("enable_thinking") is not None:
            if "--reasoning" not in flags:
                raise ValueError("Upgrade Prima for deployment thinking controls")
            arguments += ["--reasoning", "on" if settings["enable_thinking"] else "off"]
        if template == "default":
            # Legacy native binaries already apply their baseline rendering.
            if "--jinja" in flags:
                arguments += ["--jinja"]
        text = settings.get("chat_template_text")
        validate_selection(template, text)
        source = settings.get("prima_chat_template_source") or text
        if source:
            if not isinstance(source, str) or len(source.encode("utf-8")) > 64 * 1024:
                raise ValueError("Prima Jinja template must be at most 64 KiB")
            if not {"--chat-template", "--chat-template-file", "--jinja"} <= flags:
                raise ValueError("Upgrade Prima for custom/preset Jinja templates")
            # Actual spawning supplies an owned file, avoiding Windows command-line limits.
            arguments += (["--chat-template-file", str(template_file)] if template_file else
                          ["--chat-template", source]) + ["--jinja"]
        elif template in PRESETS:
            raise ValueError("Freeze the selected Prima preset before preparation")
        elif template != "default":
            if "--chat-template" not in flags:
                raise ValueError("Prima does not implement this template selection")
            arguments += ["--chat-template", template]
    load = settings.get("mmap_mlock", "mmap")
    if load == "none":
        arguments += ["--no-mmap"]
    elif load == "mlock":
        arguments += ["--no-mmap", "--mlock"]
    elif load == "mmap+mlock":
        arguments += ["--mlock"]
    elif load not in ("mmap", "auto", None):
        raise ValueError("Prima does not implement this model load mode")
    return arguments
