from __future__ import annotations

import pytest
import typer

from kllm.commands import gpu_options
from kllm.commands.ui import format_vram


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class FakeClient:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls = 0

    def get(self, _path: str) -> FakeResponse:
        self.calls += 1
        return FakeResponse(self.payload)


def test_parse_gpu_options_uses_domain_parser_and_cli_errors():
    assert gpu_options.parse_gpu_ids("0, 2") == [0, 2]
    with pytest.raises(typer.BadParameter):
        gpu_options.parse_gpu_ids("-1")


def test_explicit_gpu_ids_skip_device_discovery():
    client = FakeClient({})

    assert gpu_options.resolve_cli_gpus(client, "1,2") == [1, 2]
    assert client.calls == 0


def test_gpu_resolver_prompts_with_discovered_device_rows(monkeypatch):
    client = FakeClient(
        {
            "backend": "cuda",
            "gpus": [{"index": 0, "name": "GPU 0"}, {"index": 1, "name": "GPU 1"}],
        }
    )
    selected: list[tuple[list[dict[str, object]], str]] = []

    def prompt(rows: list[dict[str, object]], backend: str) -> list[int]:
        selected.append((rows, backend))
        return [1]

    monkeypatch.setattr(gpu_options, "_prompt_gpu_ids", prompt)

    assert gpu_options.resolve_cli_gpus(client, None) == [1]
    assert selected == [(client.payload["gpus"], "cuda")]
    assert client.calls == 1


def test_gpu_resolver_falls_back_to_agent_defaults_for_bad_response():
    assert gpu_options.resolve_cli_gpus(FakeClient([]), None) is None
    assert format_vram(1024**3) == "1.0 GiB"
    assert format_vram(None) == "—"
