"""Prima model-family support: judged from the runtime, checked before a run starts.

A pinned Prima build implements a fixed set of model architectures. When the runtime
advertises that set, a model whose architecture is missing is refused with a distinct
detail code instead of being launched into an opaque native failure. A runtime that
does not advertise the set cannot be judged, and that is reported as a warning rather
than a refusal so older pins keep working.
"""

from __future__ import annotations

from pathlib import Path

from kllm.modules.models.interactors.gguf_meta import read_inference_metadata

UNSUPPORTED_DETAIL_CODE = "PRIMA_MODEL_UNSUPPORTED"
UNKNOWN_SUPPORT_DETAIL_CODE = "PRIMA_MODEL_SUPPORT_UNKNOWN"

_ARCHITECTURES_KEY = "architectures"


def model_architecture(model: Path) -> str | None:
    """The GGUF-declared architecture name, or None when it cannot be observed."""
    metadata = read_inference_metadata(model)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("architecture")
    return value if isinstance(value, str) and value else None


def architecture_support(architecture: str | None, capabilities: dict) -> dict:
    """Judge one architecture against the runtime's advertised model support.

    Returns ``supported`` (True/False/None when unobservable), plus an ``error`` that
    refuses the deployment and a ``warning`` that only informs.
    """
    advertised = capabilities.get(_ARCHITECTURES_KEY) if isinstance(capabilities, dict) else None
    if advertised is None:
        return {
            "supported": None,
            "error": None,
            "warning": (
                f"{UNKNOWN_SUPPORT_DETAIL_CODE}: this Prima build does not advertise its model "
                f"architectures, so support for '{architecture or 'this model'}' could not be verified"
            ),
        }
    if architecture is None:
        return {
            "supported": None,
            "error": None,
            "warning": (
                f"{UNKNOWN_SUPPORT_DETAIL_CODE}: the model does not declare an architecture, so "
                "Prima support for it could not be verified"
            ),
        }
    names = {str(name) for name in advertised}
    if architecture in names:
        return {"supported": True, "error": None, "warning": None}
    return {
        "supported": False,
        "error": (
            f"{UNSUPPORTED_DETAIL_CODE}: this Prima build does not implement model architecture "
            f"'{architecture}'; it implements: {', '.join(sorted(names)) or 'none'}"
        ),
        "warning": None,
    }


def require_model_support(model: Path, capabilities: dict) -> dict:
    """Refuse a model the runtime cannot load. Safe to call again immediately before spawning."""
    verdict = architecture_support(model_architecture(model), capabilities)
    if verdict["error"]:
        raise ValueError(verdict["error"])
    return verdict
