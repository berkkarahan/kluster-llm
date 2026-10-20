"""Bounded profile cache and explicit comparisons of already-feasible plans."""

from __future__ import annotations

import json
import time
from pathlib import Path

from kllm.modules.primacpp.entities import DeviceProfile, PrimaTopology
from kllm.modules.primacpp.interactors.devices import stable_device_inventory
from kllm.modules.runtimes.entities import body_digest
from kllm.modules.runtimes.interactors.registry import atomic_json
from kllm.shared.prima_diagnostics import emit


def generation_settings(context, threads, gpu_layers, devices, output_gpu=False, *,
                        cache_type_k=None, cache_type_v=None, flash_attention=None):
    return {"context": context, "threads": threads, "mode": "generation", "gpu_layers": gpu_layers,
            "threads_batch": threads, "batch_size": 512, "micro_batch": 128,
            "devices": [d["device_identity"] for d in devices], "output_gpu": output_gpu,
            "cache_type_k": cache_type_k or "f16", "cache_type_v": cache_type_v or "f16",
            "flash_attention": flash_attention or "off"}


def generation_hardware(member, devices):
    return {**{key: member.get(key) for key in ("backend", "total_ram_bytes", "gpu_ids")},
            "devices": stable_device_inventory(member.get("devices")),
            "selected_devices": stable_device_inventory(devices)}


def profile_key(profile: DeviceProfile) -> str:
    return body_digest(
        {
            key: getattr(profile, key)
            for key in (
                "runtime_id",
                "model_sha256",
                "node_id",
                "hardware_digest",
                "settings_digest",
                "backend",
                "device_identity",
                "driver_identity",
            )
        }
    )


class ProfileCache:
    def __init__(self, root: Path, *, max_entries: int = 256, max_age_s: float = 86400):
        if max_entries < 1 or max_age_s <= 0:
            raise ValueError("Profile cache limits must be positive")
        self.root, self.max_entries, self.max_age_s = root, max_entries, max_age_s

    def put(self, profile: DeviceProfile) -> str:
        key = profile_key(profile)
        atomic_json(self.root / (key + ".json"), profile.model_dump())
        emit("profiling", "profile cached", profile_id=key, backend=profile.backend,
             cpu_layer_ms=profile.cpu_layer_ms, gpu_layer_ms=profile.gpu_layer_ms)
        entries = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for stale in entries[self.max_entries :]:
            stale.unlink(missing_ok=True)
        return key

    def get(self, identity: dict, *, now: float | None = None) -> DeviceProfile | None:
        # Reads never create directories, run a profiler, or delete stale records.
        identity = {"device_identity": "", "driver_identity": "", **identity}
        path = self.root / (body_digest(identity) + ".json")
        try:
            value = DeviceProfile.model_validate(json.loads(path.read_text()))
        except (OSError, ValueError):
            emit("profiling", "profile cache miss", profile_id=path.stem)
            return None
        age = (time.time() if now is None else now) - value.measured_at
        if profile_key(value) != path.stem or not 0 <= age <= self.max_age_s:
            emit("profiling", "profile cache stale or mismatched", profile_id=path.stem)
            return None
        emit("profiling", "profile cache hit", profile_id=path.stem, age_s=round(age, 1))
        return value


def predicted_decode_ms(
    topology: PrimaTopology,
    profiles: dict[str, DeviceProfile],
    *,
    model_sha256: str,
    hardware: dict[str, str],
    settings_digest: str,
    link_ms: dict[tuple[str, str], float],
    now: float | None = None,
    max_age_s: float = 86400,
) -> float | None:
    observed = time.time() if now is None else now
    total = 0.0
    for rank in topology.ranks:
        value = profiles.get(rank.node_id)
        if value is None or (
            value.runtime_id != topology.runtime_id
            or value.model_sha256 != model_sha256
            or value.hardware_digest != hardware.get(rank.node_id)
            or value.settings_digest != settings_digest
            or not 0 <= observed - value.measured_at <= max_age_s
        ):
            return None
        layers = sum(r.end - r.start for r in rank.layers)
        if value.measurement_kind == "end_to_end":
            # Fixed model/output/KV overhead cannot be extrapolated to partial ranks.
            if (len(topology.ranks) != 1 or layers != value.total_layers
                    or rank.ngl != value.gpu_layers or rank.output_gpu != value.output_gpu or value.decode_ms is None):
                return None
            return value.decode_ms
        if rank.ngl and value.gpu_layer_ms is None:
            return None
        total += (layers - rank.ngl) * value.cpu_layer_ms + rank.ngl * (value.gpu_layer_ms or 0)
        if len(topology.ranks) > 1:
            latency = link_ms.get((rank.node_id, rank.next_node_id))
            if latency is None or latency < 0:
                return None
            total += latency
    return total


def choose_profiled_plan(
    candidates: list[PrimaTopology], profiles: dict[str, DeviceProfile], **measurement_context
) -> tuple[PrimaTopology, dict]:
    if not candidates:
        raise ValueError("No feasible Kluster placement candidates")
    scored = [
        (predicted_decode_ms(plan, profiles, **measurement_context), i, plan)
        for i, plan in enumerate(candidates)
    ]
    # A partial profile set cannot safely eliminate an unmeasured candidate.
    if any(score is None for score, _, _ in scored):
        return candidates[0], {"status": "unmeasured", "predicted_decode_ms": None}
    score, _, selected = min(scored, key=lambda row: (row[0], row[1]))
    return selected, {"status": "profiled_estimate", "predicted_decode_ms": score}
