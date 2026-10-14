"""Source-controlled qualification for a concrete execution scope."""

from __future__ import annotations

import json
import platform
import subprocess
from copy import deepcopy
from pathlib import Path

import psutil
from kllm.modules.primacpp.interactors.draft import draft_mode
from kllm.modules.primacpp.interactors.extras import template_kwargs, adapter_requests, head_only_arguments

ROOT = Path(__file__).resolve().parents[1]
COMMON_GATES = {"vulkan_gpu_inference", "api_parity", "model_and_placement_parity", "signed_rank_lifecycle"}
EXECUTION_SETTINGS = (
    "context", "threads", "threads_batch", "parallel_slots", "batch_size", "micro_batch", "prompt_cache_bytes",
    "cache_type_k", "cache_type_v", "kv_unified", "flash_attention", "mmap_mlock", "checkpoints", "cache_ram",
    "prima_checkpoints", "prima_checkpoint_bytes", "checkpoint_min_step", "prima_graphs", "prima_graph_counts",
    "speculative", "draft_model", "draft_max", "draft_cache_type_k", "draft_cache_type_v", "draft_gpu_layers", "draft_contexts",
    "prima_lora", "prima_lora_targets", "prima_alora", "prima_adapter_bytes", "prima_adapter_scratch_per_token",
    "swa_full", "prima_kv_sequences", "vision", "mmproj", "moe_offload",
    "chat_template", "chat_template_text", "prima_chat_template_source", "extra_args", "enable_thinking", "reasoning_effort",
    "reasoning_budget", "reasoning_format", "temperature", "top_p", "top_k", "min_p",
    "presence_penalty", "repeat_penalty", "frequency_penalty", "seed", "max_output_tokens", "stop",
)


def execution_host() -> dict:
    cpu = platform.processor()
    if platform.system() == "Windows":
        # Windows' platform.processor identifies the family, not the CPU model.
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError as error:
            raise ValueError("Cannot establish the Windows CPU identity for Prima qualification") from error
    elif not cpu and platform.system() == "Linux":
        try:
            values = [line.partition(":") for line in Path("/proc/cpuinfo").read_text().splitlines()]
            cpu = next(value.strip() for key, _, value in values
                       if key.strip() in ("model name", "Hardware", "Processor") and value.strip())
        except (OSError, StopIteration) as error:
            raise ValueError("Cannot establish Linux CPU identity for Prima qualification") from error
    elif not cpu and platform.system() == "Darwin":
        try:
            cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                text=True, check=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("Cannot establish macOS CPU identity for Prima qualification") from error
    return {"os": platform.platform(), "cpu": cpu,
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(), "ram_bytes": psutil.virtual_memory().total}


def execution_scope(manifest, *, devices: list[dict]) -> dict:
    """Bind measured behavior, excluding run IDs, endpoints and planner preferences."""
    nodes = {rank.node_id: rank.rank for rank in manifest.ranks}
    evidence = getattr(manifest, "participant_evidence", {})
    distributed = len(manifest.ranks) > 1
    scope = {
        "model_sha256": next(a.sha256 for a in manifest.assets if a.kind == "model"),
        "assets": [a.model_dump() for a in manifest.assets],
        "host": (evidence[manifest.ranks[0].node_id].host.model_dump()
                 if distributed and evidence else execution_host()),
        "total_layers": manifest.total_layers, "split_mode": manifest.split_mode,
        "settings": {key: manifest.settings.get(key) for key in EXECUTION_SETTINGS},
        "ranks": [{"rank": r.rank, "role": r.role, "layers": [v.model_dump() for v in r.layers],
                   "ngl": r.ngl, "devices": r.devices, "tensor_split": r.tensor_split,
                   "output_gpu": r.output_gpu} for r in manifest.ranks],
        "segments": [{"start": s.start, "end": s.end, "rank": nodes[s.node_id]} for s in manifest.segments],
        "tensor_placements": [{**value, "node_id": nodes[value["node_id"]]}
                              if "node_id" in value else value for value in manifest.tensor_placements],
        "devices": [{key: value.get(key) for key in ("device_identity", "driver_identity", "memory_bytes")}
                    for value in devices],
    }
    if distributed:
        scope["participants"] = [{"rank": rank.rank, **{key: value for key, value in
            evidence[rank.node_id].model_dump().items() if key not in ("node_id", "allocation_digest")}}
            for rank in manifest.ranks if rank.node_id in evidence]
    return deepcopy(scope)


def _matching_execution(record: dict, context: dict) -> dict | None:
    execution = context.get("execution")
    if not isinstance(execution, dict) or not execution:
        return None
    # JSON equality preserves types: booleans must not compare equal to integers.
    encoded = json.dumps(execution, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return next((scope for scope in record.get("execution_scopes", [])
                 if scope.get("receipts") and isinstance(scope.get("execution"), dict)
                 and json.dumps(scope["execution"], sort_keys=True, separators=(",", ":"), allow_nan=False) == encoded), None)


def manifest_context(manifest, runtime: dict, *, devices: list[dict]) -> dict:
    settings = manifest.settings
    head_args = head_only_arguments(settings)
    embedding = any(flag in ("--embedding", "--embeddings") for flag in head_args)
    reranking = any(flag in ("--reranking", "--rerank") for flag in head_args)
    if reranking:
        features = {"rerank"}
    elif embedding:
        features = {"embeddings"}
    else:
        features = {"chat", "streaming", "template:" + str(settings.get("chat_template", "default"))}
    if draft_mode(settings) != "none":
        features.add("speculative:draft-simple")
    if settings.get("cache_ram", 0):
        features.add("prompt_cache")
    if settings.get("checkpoints", 0):
        features.add("context_checkpoints")
    if template_kwargs(settings) is not None:
        features.add("template_kwargs_defaults")
    if adapter_requests(settings):
        features.add("lora")
    if settings.get("prima_alora"):
        features.add("activated_lora")
    for key in ("vision", "mmproj", "draft_model", "moe_offload", "chat_template_text", "extra_args"):
        if settings.get(key):
            features.add(key)
    for key in ("reasoning_effort", "reasoning_budget", "reasoning_format", "enable_thinking"):
        if settings.get(key) is not None:
            features.add(key)
            features.add("reasoning_controls")
    if manifest.tensor_placements:
        features.add("expert_placement")
    evidence = getattr(manifest, "participant_evidence", {})
    distributed = len(manifest.ranks) > 1
    participants = [{"platform": evidence[r.node_id].platform if r.node_id in evidence else
                      runtime.get("platform") if not distributed else None,
                     "backend": manifest.backends[r.node_id], **({"rank": r.rank, "role": r.role,
                      "ngl": r.ngl, "layers": [w.model_dump() for w in r.layers]} if distributed else {})}
                    for r in manifest.ranks]
    if distributed:
        devices = [d.model_dump() for r in manifest.ranks if r.node_id in evidence
                   for d in evidence[r.node_id].devices]
    return {"topology": "local" if len(manifest.ranks) == 1 else "distributed",
            "model_sha256": next(a.sha256 for a in manifest.assets if a.kind == "model"),
            "features": sorted(features), "split_mode": manifest.split_mode,
            "devices": deepcopy(devices), "participants": participants,
            "execution": execution_scope(manifest, devices=devices)}


def policy() -> dict:
    return json.loads((ROOT / "qualification.json").read_text(encoding="utf-8"))


def api_features(body: dict, protocol: str) -> set[str]:
    required = {protocol}
    if protocol == "completion_raw":
        prompt = body.get("prompt")
        if not (isinstance(prompt, str) or
                isinstance(prompt, list) and
                (all(type(token) is int for token in prompt) or
                 all(isinstance(item, str) for item in prompt))):
            raise ValueError("Prima raw completion prompt must be a string, token IDs or prompt strings")
        if "stream" in body and type(body["stream"]) is not bool:
            raise ValueError("Prima raw completion stream must be a boolean")
        if "n_probs" in body and (type(body["n_probs"]) is not int or not 0 <= body["n_probs"] <= 2**31 - 1):
            raise ValueError("Prima raw completion n_probs must be a nonnegative native integer")
        required.update(api_features(body, "chat") - {"chat"})
        if body.get("n_probs", 0):
            required.add("token_logprobs")
        if body.get("grammar") or body.get("json_schema"):
            required.add("structured_output")
        return required
    if protocol == "infill":
        for key in ("input_prefix", "input_suffix"):
            if not isinstance(body.get(key), str):
                raise ValueError(f"Prima infill {key} must be a string")
        if "prompt" in body and not isinstance(body["prompt"], str):
            raise ValueError("Prima infill prompt must be a string")
        extra = body.get("input_extra", [])
        if not isinstance(extra, list) or any(
            not isinstance(chunk, dict) or not isinstance(chunk.get("text"), str)
            or ("filename" in chunk and not isinstance(chunk["filename"], str))
            for chunk in extra
        ):
            raise ValueError("Prima infill input_extra must contain text and optional filename strings")
        if extra:
            required.add("infill:extra")
        if body.get("stream"):
            if type(body["stream"]) is not bool:
                raise ValueError("Prima infill stream must be a boolean")
            required.add("streaming")
        elif "stream" in body and type(body["stream"]) is not bool:
            raise ValueError("Prima infill stream must be a boolean")
        return required
    if protocol == "completions":
        count = body.get("n", 1)
        if type(count) is not int or not 1 <= count <= 16:
            raise ValueError("Prima text completion n must be an integer from 1 to 16")
        if count > 1:
            required.add("completions:multiple_choices")
        prompt = body.get("prompt")
        if isinstance(prompt, list) and prompt and not all(type(value) is int for value in prompt):
            required.add("completions:multiple_prompts")
        if "logprobs" in body and body["logprobs"] is not None:
            if type(body["logprobs"]) is not bool:
                raise ValueError("Prima text completion logprobs must be a boolean")
            if body["logprobs"]:
                required.add("token_logprobs")
        if "top_logprobs" in body and body["top_logprobs"] is not None:
            top = body["top_logprobs"]
            if type(top) is not int or not 0 <= top <= 20 or body.get("logprobs") is not True:
                raise ValueError("Prima text completion top_logprobs requires logprobs and an integer from 0 to 20")
        if "stream" in body and type(body["stream"]) is not bool:
            raise ValueError("Prima text completion stream must be a boolean")
        if body.get("stream"):
            required.add("streaming")
        return required
    if protocol == "tokenize":
        if isinstance(body.get("content"), list):
            required.add("tokenize:mixed_input")
        for key, feature in (("add_special", "tokenize:add_special"),
                             ("with_pieces", "tokenize:pieces")):
            if key in body:
                if type(body[key]) is not bool:
                    raise ValueError(f"Prima {key} must be a boolean")
                if body[key]:
                    required.add(feature)
        if "parse_special" in body:
            if type(body["parse_special"]) is not bool:
                raise ValueError("Prima parse_special must be a boolean")
            if not body["parse_special"]:
                required.add("tokenize:literal_special")
        return required
    if protocol == "apply_template":
        required.update(api_features(body, "chat") - {"chat"})
        return required
    if protocol == "detokenize":
        return required
    if protocol == "embedding_raw":
        required.update(api_features(body, "embeddings") - {"embeddings"})
        return required
    if protocol == "rerank":
        if "top_n" in body and (type(body["top_n"]) is not int or not 0 < body["top_n"] <= 2**31 - 1):
            raise ValueError("Prima rerank top_n must be a positive native integer")
        if "top_n" in body:
            required.add("rerank:top_n")
        if "texts" in body:
            required.add("rerank:tei")
        if "return_text" in body:
            if type(body["return_text"]) is not bool:
                raise ValueError("Prima rerank return_text must be a boolean")
            if body["return_text"] and "texts" in body:
                required.add("rerank:return_text")
        return required
    if protocol == "embeddings":
        encoding = body.get("encoding_format", "float")
        if encoding not in ("float", "base64"):
            raise ValueError("Prima embedding encoding must be float or base64")
        required.add("embedding_encoding:" + encoding)
        if "dimensions" in body:
            if type(body["dimensions"]) is not int or not 0 < body["dimensions"] <= 2**31 - 1:
                raise ValueError("Prima embedding dimensions must be a positive native integer")
            required.add("embedding_dimensions:" + str(body["dimensions"]))
        if "embd_normalize" in body:
            value = body["embd_normalize"]
            if type(value) is not int or not -1 <= value <= 2**31 - 1:
                raise ValueError("Prima embedding normalization must be a bounded native integer")
            required.add("embedding_normalization:" + str(value))
        return required
    if any(key in body for key in ("reasoning_budget_start_tag", "reasoning_budget_end_tag", "reasoning_budget_end_tags")):
        required.update({"reasoning_delimiters", "reasoning_controls"})
    if "reasoning_control" in body:
        if type(body["reasoning_control"]) is not bool:
            raise ValueError("Prima reasoning_control must be a boolean")
        if body["reasoning_control"]:
            required.update({"reasoning_control", "reasoning_controls"})
    if any(key in body for key in ("reasoning_format", "reasoning_effort", "reasoning_budget_tokens", "thinking_budget_tokens", "enable_thinking")) or body.get("reasoning") or body.get("thinking"):
        required.add("reasoning_controls")
    for key in ("reasoning_budget_tokens", "thinking_budget_tokens"):
        if key in body and (type(body[key]) is not int or not -1 <= body[key] <= 2**31 - 1):
            raise ValueError("Prima reasoning budget must be a bounded native integer")
    if "lora" in body:
        if not isinstance(body["lora"], list):
            raise ValueError("Prima per-request LoRA selection must be an array")
        if body["lora"]:
            required.update({"lora", "request_lora"})
    kwargs = body.get("chat_template_kwargs") or {}
    text = body.get("text") or {}
    if not isinstance(kwargs, dict) or not isinstance(text, dict):
        raise ValueError("Prima template kwargs and text options must be objects")
    if body.get("stream"):
        required.add("streaming")
    if body.get("response_format") or text.get("format"):
        required.add("structured_output")
    if kwargs.get("enable_thinking") is not None:
        if type(kwargs["enable_thinking"]) is not bool:
            raise ValueError("Prima enable_thinking must be a boolean")
        required.add("thinking_toggle")
        required.add("thinking:on" if kwargs["enable_thinking"] else "thinking:off")
    if body.get("thinking"):
        thinking = body["thinking"]
        if not isinstance(thinking, dict):
            raise ValueError("Prima thinking options must be an object")
        required.add("thinking:off" if thinking.get("type") == "disabled" else "thinking:on")
    if body.get("reasoning"):
        required.add("reasoning")
    if body.get("logprobs") or body.get("top_logprobs"):
        required.add("token_logprobs")
    choice = body.get("tool_choice")
    no_tools = choice == "none" or (isinstance(choice, dict) and choice.get("type") == "none")
    if body.get("tools") and not no_tools:
        forced = isinstance(choice, dict) and choice.get("type") in ("function", "tool")
        required.add("tools:forced" if forced else "tools:auto")
        parallel = body.get("parallel_tool_calls", True)
        if protocol == "messages" and isinstance(choice, dict):
            parallel = not choice.get("disable_parallel_tool_use", False)
        if not forced and parallel:
            required.add("parallel_tools")
        if body.get("stream"):
            required.add("streaming_tools")
    messages = (body.get("input") if protocol == "responses" else body.get("messages")) or []
    # Responses native parsing owns invalid input-type errors (HTTP 400).
    # Feature discovery should inspect valid item arrays without changing that boundary.
    if protocol == "responses" and not isinstance(messages, list):
        messages = []
    if not isinstance(messages, list):
        raise ValueError("Prima messages must be an array")
    for message in messages:
        if isinstance(message, dict) and (message.get("reasoning_content") or message.get("type") == "reasoning"):
            required.update({"reasoning_history", "reasoning_controls"})
        if isinstance(message, dict) and (message.get("role") == "tool" or message.get("tool_calls")):
            required.add("tool_turns")
        if isinstance(message, dict) and message.get("type") in ("function_call", "function_call_output"):
            required.add("tool_turns")
            if message.get("type") == "function_call_output":
                output = message.get("output")
                if isinstance(output, list) and any(isinstance(p, dict) and p.get("type") not in ("text", "input_text", "output_text") for p in output):
                    required.add("vision")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue  # Native request validation owns malformed block errors.
                kind = part.get("type")
                if protocol == "messages" and kind == "thinking" and message.get("role") == "assistant":
                    required.update({"reasoning_history", "reasoning_controls"})
                elif protocol == "messages" and kind in ("tool_use", "tool_result"):
                    required.add("tool_turns")
                    nested = part.get("content")
                    if kind == "tool_result" and isinstance(nested, list) and any(isinstance(p, dict) and p.get("type") != "text" for p in nested):
                        required.add("vision")
                elif kind not in ("text", "input_text", "output_text"):
                    required.add("vision")
    return required


def require_api_features(body: dict, active_config: dict | None, protocol: str):
    """A qualified deployment does not grant untested request-time features."""
    if not active_config or active_config.get("engine") != "primacpp":
        return
    runtime = active_config.get("runtime") or {}
    context = active_config.get("qualification_context")
    if not isinstance(context, dict) or not context:
        raise ValueError("Prima serving scope has no qualified execution context; restart the run")
    status = qualification_status(runtime, context=context)
    if not status["inference_supported"]:
        raise ValueError("Prima serving scope is no longer qualified; prepare the current runtime")
    allowed = set(status["qualified_features"])
    requested = api_features(body, protocol)
    missing = requested - allowed
    if missing:
        raise ValueError("Prima request features are not qualified: " + ", ".join(sorted(missing)))
    capabilities = set((runtime.get("capabilities") or {}).get("features", []))
    if protocol == "rerank" and "rerank_api_v1" not in capabilities:
        raise ValueError("Upgrade Prima for authenticated rerank requests")
    if protocol == "completions" and "text_completion_v1" not in capabilities:
        raise ValueError("Upgrade Prima for OpenAI text completions")
    if protocol == "completion_raw" and "raw_completion_v1" not in capabilities:
        raise ValueError("Upgrade Prima for native-format completions")
    if protocol == "infill" and "infill_api_v1" not in capabilities:
        raise ValueError("Upgrade Prima for FIM infill")
    if "rerank:tei" in requested and "rerank_tei_v1" not in capabilities:
        raise ValueError("Upgrade Prima for TEI rerank requests")
    if protocol == "apply_template" and "apply_template_v1" not in capabilities:
        raise ValueError("Upgrade Prima for native template application")
    if "tokenize:literal_special" in requested and "tokenize_parse_special_v1" not in capabilities:
        raise ValueError("Upgrade Prima for literal special-token parsing")
    if protocol in ("embeddings", "embedding_raw") and "embedding_output_v1" not in capabilities:
        raise ValueError("Upgrade Prima for projected embedding output contracts")
    if "reasoning_delimiters" in requested and "reasoning_delimiters_v1" not in capabilities:
        raise ValueError("Prima native runtime does not support request reasoning delimiters")
    if "reasoning_control" in requested and "reasoning_control_v1" not in capabilities:
        raise ValueError("Prima native runtime does not support live reasoning control")
    if "reasoning_controls" in requested and "reasoning_controls_v1" not in capabilities:
        raise ValueError("Prima native runtime does not support reasoning controls")
    if protocol in ("responses", "messages") and requested & {"reasoning_history", "reasoning_controls"} and "reasoning_wire_v1" not in capabilities:
        raise ValueError("Upgrade Prima for baseline reasoning content and stream contracts")
    if "request_lora" in requested and "request_lora_v1" not in capabilities:
        raise ValueError("Prima native runtime does not support per-request LoRA selection")
    if "tools:auto" in requested and "automatic_tools_v1" not in capabilities:
        raise ValueError("Prima native runtime does not support automatic tools")
    if {"tools:auto", "streaming_tools"} <= requested and "streaming_automatic_tools_v1" not in capabilities:
        raise ValueError("Prima native runtime does not support automatic tool streams")


def qualification_status(runtime: dict, *, context: dict | None = None) -> dict:
    """Library selection checks preparation; execution additionally checks its plan."""
    if runtime.get("engine", "llamacpp") != "primacpp":
        return {"scope": None, "inference_supported": True, "qualification_errors": []}
    topology = (context or {}).get("topology", "local")
    scope = "windows-vulkan-" + topology if topology in ("local", "distributed") else None
    definition = policy()
    record = definition.get("scopes", {}).get(scope) if definition.get("schema_version") == 1 else None
    errors: list[str] = []
    if (not record or runtime.get("platform") != "Windows" or runtime.get("backend") != "vulkan"):
        return {"scope": scope, "inference_supported": False,
                "qualification_errors": ["platform_backend_not_qualified"]}
    from kllm.modules.primacpp.interactors.recipe import recipe_digest, recipe

    if (record.get("source_commit") != recipe()["source_commit"]
            or runtime.get("commit") != record.get("source_commit")
            or record.get("recipe_digest") != recipe_digest()
            or runtime.get("recipe_digest") != record.get("recipe_digest")):
        errors.append("qualification_identity_mismatch")
    required = COMMON_GATES | {"local_performance_gate" if topology == "local" else "hardware_performance_gate"}
    gates = record.get("gates", {})
    errors.extend(sorted(name for name in required if gates.get(name) is not True))
    if not record.get("receipts"):
        errors.append("qualification_evidence_missing")
    if not record.get("models"):
        errors.append("qualified_model_set_empty")
    if not record.get("devices"):
        errors.append("qualified_device_set_empty")
    if not any(scope.get("receipts") and isinstance(scope.get("execution"), dict) and scope["execution"]
               for scope in record.get("execution_scopes", [])):
        errors.append("qualified_execution_set_empty")
    features = set(record.get("features", []))
    if context is not None:
        execution = _matching_execution(record, context)
        if execution is None:
            errors.append("execution_settings_placement_not_qualified")
        else:
            features &= set(execution.get("features", []))
        identities = {value.get("sha256") for value in record.get("models", [])}
        if context.get("model_sha256") not in identities:
            errors.append("model_not_qualified")
        if not set(context.get("features", [])) <= features:
            errors.append("features_not_qualified")
        if context.get("split_mode") not in record.get("placements", []):
            errors.append("placement_not_qualified")
        approved = {(d.get("device_identity"), d.get("driver_identity")) for d in record.get("devices", [])}
        selected = context.get("devices", [])
        if not selected or any((d.get("device_identity"), d.get("driver_identity")) not in approved for d in selected):
            errors.append("device_driver_not_qualified")
        participants = context.get("participants", [])
        if topology == "local" and len(participants) != 1:
            errors.append("local_qualification_requires_one_node")
        if topology == "distributed" and (len(participants) < 2
                or len(context.get("execution", {}).get("participants", [])) != len(participants)):
            errors.append("participant_hardware_evidence_missing")
        if any(p.get("platform") != "Windows" or (p.get("backend") != "vulkan" and not (
                topology == "distributed" and p.get("role") == "master" and p.get("rank") == 0
                and p.get("backend") == "cpu" and p.get("ngl") == 0 and p.get("layers") == []))
                for p in participants):
            errors.append("participant_platform_backend_not_qualified")
    return {"scope": scope, "inference_supported": not errors, "qualification_errors": errors,
            "qualified_models": record.get("models", []), "qualified_features": sorted(features),
            "qualified_placements": record.get("placements", [])}
