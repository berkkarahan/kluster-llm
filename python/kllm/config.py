"""Persistent kllm operator config (~/.kllm/config.toml)."""

from __future__ import annotations

from typing import Any, Literal

from kllm.state import config_path, ensure_home

Device = Literal["cpu", "gpu"]
DEFAULT_DEVICE: Device = "gpu"
VALID_DEVICES = frozenset({"cpu", "gpu"})


def _normalize_device(value: Any) -> Device:
    if value is None:
        return DEFAULT_DEVICE
    s = str(value).strip().lower()
    if s not in VALID_DEVICES:
        raise ValueError(f"device must be 'cpu' or 'gpu', got {value!r}")
    return s  # type: ignore[return-value]


def default_config() -> dict[str, Any]:
    return {"device": DEFAULT_DEVICE}


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
    if "device" in raw:
        cfg["device"] = _normalize_device(raw["device"])
    return cfg


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    ensure_home()
    device = _normalize_device(cfg.get("device", DEFAULT_DEVICE))
    out = {"device": device}
    # Minimal TOML writer — avoid adding a dependency for one key.
    path = config_path()
    path.write_text(f'device = "{device}"\n', encoding="utf-8")
    return out


def get_device() -> Device:
    return _normalize_device(load_config().get("device"))


def resolve_device(override: str | None = None) -> Device:
    """Precedence: request override → master config → gpu."""
    if override is not None and str(override).strip() != "":
        return _normalize_device(override)
    return get_device()
