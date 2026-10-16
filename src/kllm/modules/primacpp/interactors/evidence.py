"""Validate completed native work against exact model/rank/device placement."""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from itertools import accumulate
import math
import struct


def execution_reports(output: str) -> list[dict]:
    reports = []
    for line in output.splitlines():
        if line.startswith("KLLM_PRIMA_GPU_EXECUTION="):
            try:
                report = json.loads(line.split("=", 1)[1])
            except ValueError as error:
                raise ValueError("Malformed Prima GPU execution evidence") from error
            if not isinstance(report, dict):
                raise ValueError("Malformed Prima GPU execution evidence")
            reports.append(report)
    return reports


def _verify_adapter_work(report, manifest, rank, names, owners, output_owner, assigned,
                         revision, requested_scales):
    bindings = manifest.settings.get("prima_lora", [])
    if not bindings:
        if report.get("lora_selection") or report.get("executed_adapters"):
            raise ValueError("GPU evidence contains unapproved LoRA selection/work")
        return set(), set()
    targets = manifest.settings.get("prima_lora_targets")
    if not isinstance(targets, dict) or set(targets) != {binding["name"] for binding in bindings}:
        raise ValueError("LoRA evidence requires immutable tensor target bindings")
    scales = {index: binding["scale"] for index, binding in enumerate(bindings)} if requested_scales is None else dict(requested_scales)
    if (type(revision) is not int or revision < 0 or any(type(index) is not int or not 0 <= index < len(bindings)
            or type(value) not in (int, float) or not math.isfinite(value) for index, value in scales.items())):
        raise ValueError("LoRA evidence has an invalid expected selection/revision")
    try:
        scales = {index: struct.unpack("f", struct.pack("f", value))[0] for index, value in scales.items()}
    except (OverflowError, struct.error) as error:
        raise ValueError("LoRA evidence scales exceed a native float") from error
    selected = [[index, scale] for index, scale in sorted(scales.items()) if scale != 0]
    actual = report.get("lora_selection")
    if (type(report.get("lora_revision")) is not int or report["lora_revision"] != revision
            or not isinstance(actual, list) or len(actual) != len(selected)
            or any(not isinstance(row, list) or len(row) != 2 or type(row[0]) is not int
                   or type(row[1]) not in (int, float) for row in actual) or actual != selected):
        raise ValueError("LoRA evidence belongs to another selection/revision")
    output = report.get("output_weight")
    if rank.rank == 0 and output not in ("output.weight", "token_embd.weight"):
        raise ValueError("LoRA output evidence has an invalid base-model projection")
    expected = set()
    for index, scale in selected:
        declared = targets[bindings[index]["name"]]
        if not isinstance(declared, list) or any(not isinstance(t, str) for t in declared) or len(set(declared)) != len(declared):
            raise ValueError("LoRA evidence has malformed tensor targets")
        for target in declared:
            layer = re.fullmatch(r"blk\.(\d+)\..+\.weight", target)
            if layer and int(layer[1]) in assigned:
                owner = owners.get(int(layer[1]))
                backend = names[owner] if owner is not None else "CPU"
                role = "T"
            elif rank.rank == 0 and target == output:
                backend = names[output_owner] if rank.output_gpu else "CPU"
                role = "O"
            else:
                role = None
            if role:
                expected.update((index, target, component, role, backend) for component in ("a", "b"))
            if rank.rank == 0 and target == "token_embd.weight":
                expected.update((index, target, component, "I", "CPU") for component in ("a", "b"))
    work = report.get("executed_adapters")
    if not isinstance(work, dict):
        raise ValueError("Missing completed LoRA matrix evidence")
    completed = set()
    for backend, entries in work.items():
        if not isinstance(backend, str) or not isinstance(entries, list):
            raise ValueError("LoRA evidence has invalid backend work")
        for entry in entries:
            if not isinstance(entry, dict) or type(entry.get("id")) is not int:
                raise ValueError("LoRA evidence has invalid adapter identities")
            index = entry["id"]
            if (index not in scales or scales[index] == 0 or type(entry.get("scale")) not in (int, float)
                    or entry["scale"] != scales[index]
                    or not isinstance(entry.get("target"), str) or entry.get("component") not in ("a", "b")
                    or entry.get("role") not in ("T", "O", "I")):
                raise ValueError("LoRA evidence changed adapter selection, scale or target")
            record = (index, entry["target"], entry["component"], entry["role"], backend)
            if record not in expected:
                raise ValueError("LoRA matrix work differs from its exact rank/backend ownership")
            completed.add(record)
    return expected, completed


def verify_vulkan_execution(reports: list[dict], manifest, rank, devices: list[dict], *, logical_names: list[str] | None = None,
                            lora_revision: int = 0, lora_scales: dict[int, float] | None = None,
                            require_graph_contract: bool = False) -> dict:
    if rank.ngl <= 0 and not rank.output_gpu:
        raise ValueError("Vulkan execution evidence requires transformer or output GPU work")
    assigned = [layer for window in rank.layers for layer in range(window.start, window.end)]
    assigned_gpu = assigned[-rank.ngl:] if rank.ngl else []
    gpu_layers = set(assigned_gpu)
    names = logical_names if logical_names is not None else [f"Vulkan{i}" for i in range(len(devices))]
    if not devices or len(names) != len(devices) or len(set(names)) != len(names) or len(rank.tensor_split) != len(devices):
        raise ValueError("GPU execution evidence has an invalid selected device split")
    try:
        # The native manifest narrows proportions to float before normalizing in
        # double precision. Preserve that boundary for exact layer ownership.
        weights = [struct.unpack("f", struct.pack("f", value))[0] for value in rank.tensor_split]
    except (OverflowError, struct.error) as error:
        raise ValueError("GPU execution evidence has an invalid selected device split") from error
    if any(not math.isfinite(value) or value <= 0 for value in weights):
        raise ValueError("GPU execution evidence has an invalid selected device split")
    splits = [value / sum(weights) for value in accumulate(weights)]
    splits[-1] = 1.0
    count = rank.ngl + int(rank.output_gpu)
    owners = {layer: bisect_right(splits, position / count) for position, layer in enumerate(assigned_gpu)}
    output_owner = bisect_right(splits, rank.ngl / count) if rank.output_gpu else None
    active = set(owners.values()) | ({output_owner} if output_owner is not None else set())
    selected = {(names[i], devices[i]["device_identity"], devices[i]["driver_identity"]) for i in active}
    phases: dict[str, set[str]] = {"prefill": set(), "decode": set()}
    matched = 0
    output_phases = set()
    adapter_expected = {phase: set() for phase in phases}
    adapter_completed = {phase: set() for phase in phases}
    graph_required = {phase: set() for phase in phases}
    graph_phases = set()
    for report in reports:
        if (report.get("schema_version") != 1 or report.get("completed") is not True
                or report.get("runtime_id") != manifest.runtime_id
                or report.get("manifest_digest") != manifest.digest or report.get("rank") != rank.rank):
            raise ValueError("GPU execution evidence disagrees with the execution manifest")
        phase = report.get("phase")
        tokens = report.get("tokens")
        if phase not in phases or type(tokens) is not int or (phase == "prefill" and tokens <= 1) or (phase == "decode" and tokens != 1):
            raise ValueError("GPU execution evidence has an invalid prefill/decode phase")
        expected, completed = _verify_adapter_work(report, manifest, rank, names, owners, output_owner, assigned, lora_revision, lora_scales)
        adapter_expected[phase].update(expected)
        adapter_completed[phase].update(completed)
        reported = report.get("devices")
        if not isinstance(reported, list) or any(not isinstance(d, dict) for d in reported):
            raise ValueError("GPU execution evidence has invalid devices")
        if {(d.get("name"), d.get("device_identity"), d.get("driver_identity")) for d in reported} != selected:
            raise ValueError("GPU execution evidence used a different physical device or driver")
        weights = report.get("executed_weights")
        if not isinstance(weights, dict):
            raise ValueError("GPU execution evidence has invalid tensor ownership")
        graph_contract = report.get("graph_contract_version")
        if require_graph_contract or graph_contract is not None:
            expected = report.get("graph_weights")
            count = report.get("graph_count")
            if (type(graph_contract) is not int or graph_contract != 1
                    or type(count) is not int or count <= 0
                    or type(report.get("completed_graphs")) is not int or report["completed_graphs"] != count
                    or not isinstance(expected, list) or not expected
                    or any(not isinstance(w, str) or not w for w in expected)
                    or len(set(expected)) != len(expected)):
                raise ValueError("Missing complete native graph execution contract")
            completed_weights = set()
            for values in weights.values():
                if not isinstance(values, list) or any(not isinstance(w, str) for w in values):
                    raise ValueError("GPU execution evidence has invalid weights")
                completed_weights.update(values)
            if not set(expected) <= completed_weights:
                raise ValueError("Native graph contains model matrix work that did not complete")
            for weight in expected:
                match = re.fullmatch(r"blk\.(\d+)\..+\.weight", weight)
                if match:
                    if int(match[1]) not in assigned:
                        raise ValueError("Native graph requires another rank's layer")
                    if int(match[1]) in gpu_layers:
                        graph_required[phase].add(weight)
                elif weight not in ("output.weight", "token_embd.weight"):
                    raise ValueError("Native graph has an invalid model tensor name")
            graph_phases.add(phase)
        for backend, values in weights.items():
            if not isinstance(values, list) or any(not isinstance(w, str) for w in values):
                raise ValueError("GPU execution evidence has invalid weights")
            for weight in values:
                if weight in ("output.weight", "token_embd.weight"):
                    if rank.output_gpu:
                        if backend != names[output_owner]:
                            raise ValueError("The planned output projection executed on another backend")
                        output_phases.add(phase)
                    elif backend in names:
                        raise ValueError("GPU execution evidence changed output projection ownership")
                    continue
                match = re.fullmatch(r"blk\.(\d+)\.(.+)\.weight", weight)
                if not match:
                    raise ValueError("GPU execution evidence has invalid tensor names")
                layer = int(match[1])
                if layer not in assigned:
                    raise ValueError("GPU execution evidence executed another rank's layer")
                if layer in gpu_layers:
                    if backend != names[owners[layer]]:
                        raise ValueError("A planned Vulkan layer executed on another backend")
                    phases[phase].add(weight)
                elif backend in names:
                    raise ValueError("GPU execution evidence changed CPU/GPU layer ownership")
        matched += 1
    if manifest.settings.get("prima_lora") and any(adapter_completed[phase] != adapter_expected[phase] for phase in phases):
        raise ValueError("Missing completed LoRA A/B work for this selection and phase")
    required_suffixes = {"attn_q", "attn_k", "attn_v", "attn_output"}
    if rank.output_gpu and output_phases != {"prefill", "decode"}:
        raise ValueError("Missing completed Vulkan output projection work")
    for phase, weights in phases.items():
        for layer in gpu_layers:
            if phase in graph_phases:
                required = {w for w in graph_required[phase] if w.startswith(f"blk.{layer}.")}
                if not required or not required <= weights:
                    raise ValueError(f"Missing completed Vulkan native graph work for {phase} layer {layer}")
                continue
            suffixes = {w.split(".", 2)[2].removesuffix(".weight") for w in weights if w.startswith(f"blk.{layer}.")}
            dense_ffn = {"ffn_gate", "ffn_up", "ffn_down"}
            moe_ffn = {"ffn_gate_inp", "ffn_gate_exps", "ffn_up_exps", "ffn_down_exps"}
            if not required_suffixes <= suffixes or not (dense_ffn <= suffixes or moe_ffn <= suffixes):
                raise ValueError(f"Missing completed Vulkan attention/FFN work for {phase} layer {layer}")
    return {"status": "passed", "kind": "completed Vulkan matrix work; prefill and decode",
            "reports": matched, "gpu_layers": sorted(gpu_layers), "devices": devices,
            "manifest_digest": manifest.digest}
