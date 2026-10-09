"""Version-pinned llama.cpp chat template selections shared by UI and CLI."""

from __future__ import annotations

from pathlib import Path

PRESETS = {
    "preset:llama31-8b": ("Llama 3.1 8B Instruct", "meta-llama-Llama-3.1-8B-Instruct.jinja"),
    "preset:qwen25-7b": ("Qwen 2.5 7B Instruct", "Qwen-Qwen2.5-7B-Instruct.jinja"),
    "preset:qwen3-0.6b": ("Qwen 3 0.6B", "Qwen-Qwen3-0.6B.jinja"),
    "preset:qwen35-4b": ("Qwen 3.5 4B", "Qwen3.5-4B.jinja"),
    "preset:mistral-nemo-2407": ("Mistral NeMo 2407", "mistralai-Mistral-Nemo-Instruct-2407.jinja"),
    "preset:gemma2-2b": ("Gemma 2 2B IT", "google-gemma-2-2b-it.jinja"),
}
LEGACY = {"llama3", "phi3", "gemma", "mistral-v3-tekken"}
MAX_TEMPLATE_BYTES = 64 * 1024


def selected_template_text(name: str, text: str | None, embedded: str) -> str:
    """Read the template selected for deployment without writing launch files."""
    if text:
        return text
    if name in PRESETS:
        return (Path(__file__).parent / "templates" / PRESETS[name][1]).read_text(encoding="utf-8")
    return embedded if name == "default" else ""


def validate_selection(name: str, text: str | None) -> None:
    if name not in {"default", "custom", "chatml", *PRESETS, *LEGACY}:
        raise ValueError(f"unknown chat template: {name}")
    if text is not None and len(text.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise ValueError("chat template must be at most 64 KiB")
    if name == "custom" and not text:
        raise ValueError("custom chat template requires Jinja text")
    if text and name not in ("default", "custom"):
        raise ValueError("choose a preset or custom Jinja, not both")


def template_launch(name: str, text: str | None, model: str) -> dict[str, object]:
    validate_selection(name, text)
    if name in PRESETS:
        return {
            "chat_template_file": str(Path(__file__).parent / "templates" / PRESETS[name][1]),
            "jinja": True,
        }
    if text:
        from kllm.state import ensure_home
        import hashlib

        path = (
            ensure_home() / f"chat-template-{hashlib.sha256(model.encode()).hexdigest()[:16]}.jinja"
        )
        path.write_text(text, encoding="utf-8")
        return {"chat_template_file": str(path), "jinja": True}
    if name == "chatml":
        return {"chat_template": "chatml", "jinja": True}
    if name in LEGACY:
        return {"chat_template": name, "jinja": False}
    return {}
