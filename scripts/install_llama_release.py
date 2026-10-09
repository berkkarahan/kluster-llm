"""Release-asset name scoring. Platform facts are passed in by ``install.py``."""

from __future__ import annotations

from typing import Any


def score_release_asset(
    name: str,
    *,
    backend: str,
    windows: bool,
    macos: bool,
    linux: bool,
    arm: bool,
) -> int:
    """Rank one asset name. Negative means the asset cannot be used."""
    n = name.lower()
    score = 0
    if not (n.endswith(".zip") or n.endswith(".tar.gz")):
        return -1
    if "bin" not in n:
        return -1
    if windows:
        if "win" not in n:
            return -1
        if arm and "arm64" not in n:
            return -1
        if not arm and "x64" not in n and "amd64" not in n:
            return -1
    elif macos:
        if "macos" not in n:
            return -1
        if arm and "arm64" not in n:
            return -1
        if not arm and "x64" not in n and "x86_64" not in n:
            return -1
    elif linux:
        if "ubuntu" not in n and "linux" not in n:
            return -1
        if arm and "arm64" not in n:
            return -1
        if not arm and "x64" not in n and "amd64" not in n:
            return -1
    else:
        return -1

    if backend == "cuda" and "cuda" in n:
        score += 50
    elif backend == "vulkan" and "vulkan" in n:
        score += 50
    elif backend == "rocm" and ("rocm" in n or "hip" in n):
        score += 50
    elif backend == "metal" and "macos" in n:
        score += 50
    elif backend == "cpu" and "cpu" in n:
        score += 50
    elif (
        backend == "cpu"
        and not macos
        and ("cuda" not in n and "vulkan" not in n and "rocm" not in n)
    ):
        # Non-macOS: unnamed packs without a GPU tag may be CPU. macOS
        # ``bin-macos-*`` packs are Metal — never treat them as CPU.
        score += 40
    else:
        return -1

    if "cudart" in n:
        return -1
    return score


def pick_best_asset(
    assets: list[dict[str, Any]],
    *,
    backend: str,
    windows: bool,
    macos: bool,
    linux: bool,
    arm: bool,
) -> dict[str, Any] | None:
    ranked = sorted(
        (
            (
                score_release_asset(
                    a.get("name", ""),
                    backend=backend,
                    windows=windows,
                    macos=macos,
                    linux=linux,
                    arm=arm,
                ),
                a,
            )
            for a in assets
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    for score, asset in ranked:
        if score > 0:
            return asset
    return None
