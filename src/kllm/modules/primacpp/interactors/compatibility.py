"""Executable parity inventory; presence and behavioral qualification are separate."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

BASELINE_COMMIT = "7fe450e19305b828c199d602c23a8337aaa1f03b"
BASELINE_REF = "v0.5.0"


def help_flags(binary: Path) -> set[str]:
    result = subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError("Runtime could not report its launch options")
    return set(re.findall(r"(?<![\w-])--[a-z][\w-]*", result.stdout + result.stderr))


def launch_matrix(prima_binary: Path, baseline_binary: Path) -> dict:
    from kllm.modules.inference.interactors.parameter_catalog import PARAMETERS
    from kllm.modules.inference.interactors.chat_templates import LEGACY, PRESETS
    from kllm.modules.inference.router_http import CONTROL_V2, LEGACY_V1_PROXY
    from kllm.modules.models.interactors.gguf_meta import MOE_ARCHITECTURES

    # Extract actual launch switches as well as user-visible descriptors.
    import inspect
    from kllm.modules.inference.interactors import run_launch

    required = {flag for flag, _, _, _ in PARAMETERS.values()}
    required.update(re.findall(r'"(--[a-z][\w-]*)"', inspect.getsource(run_launch)))
    baseline, prima = help_flags(baseline_binary), help_flags(prima_binary)
    alternatives = {alias: aliases for _, aliases, _, _ in PARAMETERS.values() for alias in aliases}
    missing = {
        flag
        for flag in (required & baseline) - prima
        if not set(alternatives.get(flag, ())) & prima
    }
    return {
        "schema_version": 2,
        "baseline_ref": BASELINE_REF,
        "baseline_commit": BASELINE_COMMIT,
        "launch": [
            {
                "flag": flag,
                "baseline_present": flag in baseline,
                "prima_present": flag in prima,
                "implementation_status": "parser_present" if flag in prima else "parser_absent",
                "parity_check": "paired launch with identical effective settings and model",
                "behavior": "unverified",
            }
            for flag in sorted(required)
        ],
        "parameter_count": len(PARAMETERS),
        "missing_launch_flags": sorted(missing),
        "parameters": [
            {
                "name": name,
                "kluster_cli": flag,
                "engine_aliases": list(aliases),
                "scope": group,
                "implementation_status": "kluster_control_preserved",
                "native_alias_present": bool(set(aliases) & prima) if aliases else None,
                "parity_check": "paired launch and request using " + name,
                "behavior": "unverified",
            }
            for name, (flag, aliases, group, _) in PARAMETERS.items()
        ],
        "api_routes": [
            {
                "path": path,
                "method": method,
                "handler": handler,
                "implementation_status": "kluster_route_preserved; native parity unverified",
                "parity_check": "paired request through authenticated Kluster gateway",
                "behavior": "unverified",
            }
            for path, method, handler in [
                *LEGACY_V1_PROXY,
                *(("/v2" + path, method, handler) for path, method, handler in CONTROL_V2),
            ]
        ],
        "templates": [
            {
                "name": name,
                "implementation_status": "kluster_selection_preserved; native render unverified",
                "parity_check": "compare rendered prompt and token IDs against baseline",
                "behavior": "unverified",
            }
            for name in sorted({"default", "custom", "chatml", *PRESETS, *LEGACY})
        ],
        "moe_architectures": [
            {
                "architecture": name,
                "implementation_status": "metadata_recognized; native execution unverified",
                "parity_check": "model load, prefill/decode and output check on identical GGUF assets",
                "behavior": "unverified",
            }
            for name in sorted(MOE_ARCHITECTURES)
        ],
        "execution_requirements": [
            {
                "name": name,
                "implementation_status": "unverified",
                "parity_check": check,
                "behavior": "unverified",
            }
            for name, check in [
                ("streaming", "SSE event schema, tool deltas, usage and terminal event"),
                ("cancellation", "close stream; verify slot release and subsequent request"),
                ("structured_output", "validate response against supplied JSON schema"),
                ("tool_calls", "force tool call; validate arguments and finish reason"),
                ("batching", "concurrent requests; verify independent slots and outputs"),
                ("kv_cache", "paired cache formats, reuse, context and memory limits"),
                ("speculative_decoding", "draft assets, acceptance and output correctness"),
                ("dense_architectures", "supplied dense model load, tokenization and generation"),
                ("authentication_and_model_identity", "gateway auth rejection and exact served model ID"),
                ("platform_backends", "Windows/Linux/macOS compilation plus physical selected-device execution"),
                ("exact_placement", "manifest ownership and backend execution acknowledgements"),
            ]
        ],
        "api_behavior": "requires model-backed parity assessment",
        "model_behavior": "requires dense/MoE assessment; metadata support is not execution support",
    }
