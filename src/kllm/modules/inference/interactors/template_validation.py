"""Validate chat templates with the installed llama.cpp command line."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kllm.modules.inference.interactors.chat_templates import MAX_TEMPLATE_BYTES
from kllm.shared.exceptions import ExternalServiceError, ResourceUnavailableError, ValidationError


@dataclass(frozen=True)
class TemplateValidationResult:
    """Public result returned after the engine parses a draft template."""

    valid: bool
    detail: str

    def as_payload(self) -> dict[str, Any]:
        return {"valid": self.valid, "detail": self.detail}


class TemplateValidationService:
    """Validate size and invoke llama.cpp in an isolated temporary directory."""

    def __init__(
        self,
        resolve_server: Callable[[], str | None],
        *,
        run_process: Callable[..., Any] = subprocess.run,
        temporary_directory: Callable[..., Any] = tempfile.TemporaryDirectory,
        creation_flags: int | None = None,
    ) -> None:
        self._resolve_server = resolve_server
        self._run_process = run_process
        self._temporary_directory = temporary_directory
        self._creation_flags = (
            (getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
            if creation_flags is None
            else creation_flags
        )

    def validate(self, text: str) -> TemplateValidationResult:
        """Ask llama.cpp to parse a draft template and return a safe outcome."""
        if not text or len(text.encode("utf-8")) > MAX_TEMPLATE_BYTES:
            raise ValidationError("template must be between 1 byte and 64 KiB")
        binary = self._resolve_server()
        if not binary:
            raise ResourceUnavailableError("llama-server unavailable")

        with self._temporary_directory() as folder:
            path = Path(folder) / "draft.jinja"
            path.write_text(text, encoding="utf-8")
            try:
                result = self._run_process(
                    [binary, "--jinja", "--chat-template-file", str(path), "--help"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=self._creation_flags,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ExternalServiceError(f"template check unavailable: {exc}") from exc
        if result.returncode:
            raise ValidationError((result.stderr or result.stdout)[-2000:])
        return TemplateValidationResult(
            valid=True,
            detail="Engine parsed a sample message; model compatibility is unverified.",
        )
