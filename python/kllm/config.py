"""Persistent kllm operator config (~/.kllm/config.toml)."""

from __future__ import annotations

from typing import Any, Literal

from kllm.accel import SELECTABLE_ACCEL_BACKENDS
from kllm.state import config_path, ensure_home

Device = Literal["cpu", "gpu"]
DEFAULT_DEVICE: Device = "gpu"
VALID_DEVICES = frozenset({"cpu", "gpu"})

DEFAULT_VRAM_PERCENT = 80
DEFAULT_RAM_PERCENT = 80
MIN_MEMORY_PERCENT = 10
MAX_MEMORY_PERCENT = 100


def _normalize_device(value: Any) -> Device:
    if value is None:
        return DEFAULT_DEVICE
    s = str(value).strip().lower()
    if s not in VALID_DEVICES:
        raise ValueError(f"device must be 'cpu' or 'gpu', got {value!r}")
    return s  # type: ignore[return-value]


def _normalize_accel_backend(value: Any) -> str | None:
    """None / empty = auto (use probe). Otherwise metal|cuda|rocm|vulkan."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s in ("auto", "default"):
        return None
    if s not in SELECTABLE_ACCEL_BACKENDS:
        raise ValueError(
            f"accel_backend must be one of {sorted(SELECTABLE_ACCEL_BACKENDS)} or null, got {value!r}"
        )
    return s


def _normalize_percent(value: Any, *, field: str) -> int:
    if value is None:
        return DEFAULT_VRAM_PERCENT if field == "vram_percent" else DEFAULT_RAM_PERCENT
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer, got {value!r}") from exc
    if not (MIN_MEMORY_PERCENT <= n <= MAX_MEMORY_PERCENT):
        raise ValueError(
            f"{field} must be between {MIN_MEMORY_PERCENT} and {MAX_MEMORY_PERCENT}, got {n}"
        )
    return n


def _normalize_gpu_ids(value: Any) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"gpu_ids must be a list of ints, got {value!r}")
    if len(value) == 0:
        raise ValueError("gpu_ids must be non-empty when provided")
    out: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            idx = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid gpu_ids entry {item!r}") from exc
        if idx < 0:
            raise ValueError(f"GPU index must be >= 0, got {idx}")
        if idx not in seen:
            seen.add(idx)
            out.append(idx)
    return out


def default_config() -> dict[str, Any]:
    return {
        "device": DEFAULT_DEVICE,
        "gpu_ids": None,
        "vram_percent": DEFAULT_VRAM_PERCENT,
        "ram_percent": DEFAULT_RAM_PERCENT,
        "accel_backend": None,
    }


def _normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    cfg = default_config()
    if "device" in raw:
        cfg["device"] = _normalize_device(raw["device"])
    if "gpu_ids" in raw:
        cfg["gpu_ids"] = _normalize_gpu_ids(raw["gpu_ids"])
    if "vram_percent" in raw:
        cfg["vram_percent"] = _normalize_percent(raw["vram_percent"], field="vram_percent")
    if "ram_percent" in raw:
        cfg["ram_percent"] = _normalize_percent(raw["ram_percent"], field="ram_percent")
    if "accel_backend" in raw:
        cfg["accel_backend"] = _normalize_accel_backend(raw["accel_backend"])
    return cfg


def _toml_gpu_ids(ids: list[int] | None) -> str:
    if ids is None:
        return "[]"
    return "[" + ", ".join(str(i) for i in ids) + "]"


def _write_toml(cfg: dict[str, Any]) -> str:
    lines = [
        f'device = "{cfg["device"]}"',
        f"vram_percent = {int(cfg['vram_percent'])}",
        f"ram_percent = {int(cfg['ram_percent'])}",
    ]
    gpu_ids = cfg.get("gpu_ids")
    if gpu_ids is not None:
        lines.append(f"gpu_ids = {_toml_gpu_ids(gpu_ids)}")
    accel = cfg.get("accel_backend")
    if accel is not None:
        lines.append(f'accel_backend = "{accel}"')
    return "\n".join(lines) + "\n"


def load_config() -> dict[str, Any]:
    ensure_home()
    path = config_path()
    cfg = default_config()
    if not path.exists():
        return cfg
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py3.10
        import tomli as tomllib  # type: ignore

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    # Empty gpu_ids array in TOML means "unset / all" for backward friendliness
    if "gpu_ids" in raw and raw["gpu_ids"] == []:
        raw = {**raw, "gpu_ids": None}
    return _normalize_config({**cfg, **raw})


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    ensure_home()
    # Merge with existing so partial updates (e.g. device-only) keep percents / gpu_ids.
    current = load_config()
    merged = {**current}
    for key in ("device", "vram_percent", "ram_percent", "gpu_ids", "accel_backend"):
        if key in cfg:
            merged[key] = cfg[key]
    out = _normalize_config(merged)
    path = config_path()
    path.write_text(_write_toml(out), encoding="utf-8")
    return out


def get_device() -> Device:
    return _normalize_device(load_config().get("device"))


def get_gpu_ids() -> list[int] | None:
    return load_config().get("gpu_ids")


def get_accel_backend() -> str | None:
    return _normalize_accel_backend(load_config().get("accel_backend"))


def resolve_device(override: str | None = None) -> Device:
    """Precedence: request override → master config → gpu."""
    if override is not None and str(override).strip() != "":
        return _normalize_device(override)
    return get_device()


def resolve_memory_fraction(
    device: Device | None = None,
    *,
    override_percent: int | None = None,
) -> float:
    """Fraction of VRAM (gpu) or RAM (cpu) to budget. Range 0.10–1.0."""
    cfg = load_config()
    resolved = device if device is not None else _normalize_device(cfg.get("device"))
    if override_percent is not None:
        pct = _normalize_percent(
            override_percent,
            field="vram_percent" if resolved == "gpu" else "ram_percent",
        )
    elif resolved == "gpu":
        pct = int(cfg["vram_percent"])
    else:
        pct = int(cfg["ram_percent"])
    return pct / 100.0
