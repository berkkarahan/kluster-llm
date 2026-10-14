"""Operating-system and backend identity shared by Prima planning and qualification."""

from __future__ import annotations

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
