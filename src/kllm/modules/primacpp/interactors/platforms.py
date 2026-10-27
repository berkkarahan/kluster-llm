"""Operating-system and backend identity shared by Prima planning and qualification."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Canonical `platform.system()` values; aliases are accepted only at the input boundary.
_CANONICAL = {"windows": "Windows", "linux": "Linux", "darwin": "Darwin", "macos": "Darwin"}


def normalize_platform(value) -> str | None:
    """Return Windows/Linux/Darwin, or None when the node's platform is missing or unknown.

    An unknown platform is never assumed to be Windows (spec edge case).
    """
    return _CANONICAL.get(value.strip().lower()) if isinstance(value, str) else None


def scope_platform(value) -> str | None:
    """Lowercase qualification-scope prefix (`windows`, `linux`, `darwin`)."""
    platform = normalize_platform(value)
    return platform.lower() if platform else None


def vulkan_build(platform_key: str | None, build_backend) -> bool:
    """Whether a runtime's build backend can serve a Vulkan ring on this platform.

    macOS builds stamp `metal+vulkan` (Vulkan through MoltenVK); other platforms must be
    exactly `vulkan`, so no Windows outcome changes.
    """
    return build_backend == "vulkan" or (platform_key == "darwin" and build_backend == "metal+vulkan")


def _split_backends(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [part.strip().lower() for part in value.replace(",", "+").split("+") if part.strip()]


def _vulkan_device_name(name: Any, index: int) -> str:
    text = str(name or "").strip()
    if not text:
        return f"Vulkan{index}"
    upper = text.upper()
    if upper.startswith("MTL"):
        suffix = text[3:]
        return f"Vulkan{suffix if suffix.isdigit() else index}"
    if upper.startswith("METAL"):
        suffix = text[5:]
        return f"Vulkan{suffix if suffix.isdigit() else index}"
    return text


def member_supports_vulkan(
    member: Mapping[str, Any] | None,
    runtime: Mapping[str, Any] | None = None,
) -> bool:
    """True when a cluster member has built or installed a Vulkan-capable backend."""
    if not isinstance(member, Mapping):
        return False
    for field in ("actual_backend", "backend"):
        if "vulkan" in _split_backends(member.get(field)):
            return True
    installed = member.get("installed_backends") or ()
    if isinstance(installed, (list, tuple, set, frozenset)):
        for item in installed:
            if "vulkan" in _split_backends(item):
                return True
    runtime_info = member.get("runtime")
    prepared = (runtime_info.get("prepared") or {}) if isinstance(runtime_info, Mapping) else {}
    if isinstance(prepared, Mapping) and "vulkan" in _split_backends(prepared.get("backend")):
        return True
    for entry in member.get("devices") or ():
        if isinstance(entry, Mapping) and "vulkan" in _split_backends(entry.get("backend")):
            return True
    if (
        isinstance(runtime, Mapping)
        and (member.get("role") == "master" or member.get("is_master"))
        and set(_split_backends(runtime.get("backend"))) >= {"metal", "vulkan"}
    ):
        return True
    return False


def prima_member_backend(
    member: Mapping[str, Any] | None,
    *,
    fallback: str | None = None,
    runtime: Mapping[str, Any] | None = None,
) -> str | None:
    """Effective accelerator backend for a member during Prima placement and planning.

    A node that builds both `metal+vulkan` probes `metal` as its llama.cpp default on
    macOS, but runs Prima over its compiled `vulkan` backend unless `metal` was
    explicitly requested.
    """
    if not isinstance(member, Mapping):
        return fallback
    raw = member.get("actual_backend") or member.get("backend") or fallback
    if raw is None:
        return None
    raw_str = str(raw).strip().lower()
    if raw_str == "cpu":
        return "cpu"
    if "vulkan" in _split_backends(raw_str):
        return "vulkan"
    requested = str(member.get("requested_backend") or "").strip().lower()
    if raw_str == "metal" and requested != "metal" and member_supports_vulkan(member, runtime):
        return "vulkan"
    return str(raw)


def prima_member_view(
    member: Mapping[str, Any] | None,
    *,
    runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a non-mutating member view normalized for Prima placement."""
    if not isinstance(member, Mapping):
        return {}
    out = dict(member)
    effective = prima_member_backend(member, runtime=runtime)
    if effective != "vulkan":
        return out
    raw_backends = {
        *_split_backends(member.get("backend")),
        *_split_backends(member.get("actual_backend")),
    }
    installed = member.get("installed_backends") or ()
    if isinstance(installed, (list, tuple, set, frozenset)):
        for item in installed:
            raw_backends.update(_split_backends(item))
    if out.get("unified_memory") is None and "metal" in raw_backends:
        out["unified_memory"] = True
    out["backend"] = "vulkan"
    out["actual_backend"] = "vulkan"
    if isinstance(member.get("devices"), (list, tuple)):
        devices: list[ Any ] = []
        for idx, entry in enumerate(member.get("devices") or ()):
            if not isinstance(entry, Mapping):
                devices.append(entry)
                continue
            dev_idx = int(entry.get("index", idx))
            dev_backend = str(entry.get("backend") or "").strip().lower()
            if dev_backend in ("", "metal", "metal+vulkan", "vulkan"):
                devices.append(
                    {
                        **entry,
                        "device": _vulkan_device_name(entry.get("device"), dev_idx),
                        "backend": "vulkan",
                    }
                )
            else:
                devices.append(dict(entry))
        out["devices"] = devices
    for rpc_field in ("rpc_devices", "rpc_capabilities"):
        if isinstance(member.get(rpc_field), (list, tuple)):
            rpc_items: list[Any] = []
            for idx, entry in enumerate(member.get(rpc_field) or ()):
                if not isinstance(entry, Mapping):
                    rpc_items.append(entry)
                    continue
                if entry.get("memory_kind", "gpu") == "gpu":
                    dev_idx = int(entry.get("index", idx))
                    dev_backend = str(entry.get("backend") or "").strip().lower()
                    if dev_backend in ("", "metal", "metal+vulkan", "vulkan"):
                        rpc_items.append(
                            {
                                **entry,
                                "device": _vulkan_device_name(entry.get("device"), dev_idx),
                                "backend": "vulkan",
                            }
                        )
                        continue
                rpc_items.append(dict(entry))
            out[rpc_field] = rpc_items
    return out


def prima_members_view(
    members: Mapping[str, Any] | None,
    *,
    runtime: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return a non-mutating copy of `members` normalized for Prima placement."""
    if not isinstance(members, Mapping):
        return {}
    return {
        node_id: prima_member_view(member, runtime=runtime)
        for node_id, member in members.items()
    }


def require_shared_ring_backend(ranks: list[tuple[str, str | None, str | None]]) -> None:
    """Reject a ring whose GPU participants use different accelerator backends.

    `ranks` holds (node_id, platform, backend) for GPU-participating ranks only, so a
    coordinator-only CPU master never counts. Ranks without a backend are left to the
    explicit-backend check at preparation.
    """
    named = [(node, platform, str(backend).lower()) for node, platform, backend in ranks if backend]
    if len({backend for _, _, backend in named}) > 1:
        detail = ", ".join(f"{node} ({platform or 'platform unknown'}, {backend})"
                           for node, platform, backend in named)
        raise ValueError("Prima rings require one shared compatible GPU backend; "
                         f"participants disagree: {detail}")
