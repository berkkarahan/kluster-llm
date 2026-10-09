from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from kllm.agent.services.template_validation import TemplateValidationService
from kllm.exceptions import ExternalServiceError, ResourceUnavailableError, ValidationError


def test_template_validation_passes_a_temporary_file_to_engine():
    seen: dict[str, object] = {}

    def run_process(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        seen["text"] = Path(command[3]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = TemplateValidationService(
        lambda: "/opt/llama-server", run_process=run_process, creation_flags=17
    ).validate("{{ messages }}")

    assert result.as_payload() == {
        "valid": True,
        "detail": "Engine parsed a sample message; model compatibility is unverified.",
    }
    assert seen["command"][:3] == ["/opt/llama-server", "--jinja", "--chat-template-file"]
    assert seen["command"][-1] == "--help"
    assert seen["text"] == "{{ messages }}"
    assert seen["kwargs"] == {
        "capture_output": True,
        "text": True,
        "timeout": 10,
        "creationflags": 17,
    }


def test_template_validation_rejects_empty_or_oversized_input_before_launch():
    calls = []
    service = TemplateValidationService(
        lambda: "/opt/llama-server", run_process=lambda *_args, **_kwargs: calls.append(True)
    )

    for text in ("", "é" * 32769):
        with pytest.raises(ValidationError, match="between 1 byte and 64 KiB"):
            service.validate(text)
    assert calls == []


def test_template_validation_reports_missing_engine_and_parse_errors():
    with pytest.raises(ResourceUnavailableError, match="llama-server unavailable"):
        TemplateValidationService(lambda: None).validate("template")

    service = TemplateValidationService(
        lambda: "/opt/llama-server",
        run_process=lambda command, **_kwargs: SimpleNamespace(
            returncode=1, stderr="template parse error", stdout=""
        ),
    )
    with pytest.raises(ValidationError, match="template parse error"):
        service.validate("template")


def test_template_validation_wraps_process_failures():
    service = TemplateValidationService(
        lambda: "/opt/llama-server",
        run_process=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("llama-server", 10)
        ),
    )

    with pytest.raises(ExternalServiceError, match="template check unavailable"):
        service.validate("template")
