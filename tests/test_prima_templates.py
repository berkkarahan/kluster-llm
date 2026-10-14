from types import SimpleNamespace

import pytest

from kllm.modules.primacpp.interactors import launch
from kllm.modules.primacpp.interactors.execution import require_execution_capabilities


def test_preset_bytes_are_frozen_before_manifest_and_only_master_renders(monkeypatch):
    monkeypatch.setattr(launch, "selected_template_text", lambda *_: "{{ messages[0].content }}")
    body = SimpleNamespace(context=512, model_dump=lambda: {
        "chat_template": "preset:qwen3-0.6b", "parallel_slots": 1, "prima_kv_sequences": 2})
    settings = launch.execution_settings(body, {}, SimpleNamespace(name="test"), 8080)
    monkeypatch.setattr(launch, "selected_template_text", lambda *_: "changed")
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--parallel", "--alias", "--chat-template", "--chat-template-file", "--jinja"})
    assert launch.rank_arguments(None, settings)[-3:] == ["--chat-template", "{{ messages[0].content }}", "--jinja"]
    assert launch.rank_arguments(None, settings, rank=1) == ["--parallel", "1"]
    with pytest.raises(ValueError, match="Freeze"):
        launch.rank_arguments(None, {"chat_template": "preset:qwen3-0.6b"})


def test_custom_template_preserves_unicode_and_requires_new_native_parser(monkeypatch):
    source = "{{ messages[0].content }} 🐾"
    settings = {"chat_template": "custom", "chat_template_text": source}
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--chat-template"})
    with pytest.raises(ValueError, match="Upgrade"):
        launch.rank_arguments(None, settings)
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--chat-template", "--chat-template-file", "--jinja"})
    assert launch.rank_arguments(None, settings) == ["--chat-template", source, "--jinja"]
    with pytest.raises(ValueError, match="64 KiB"):
        launch.rank_arguments(None, {**settings, "chat_template_text": "🐾" * 16385})


def test_template_capability_refuses_before_other_execution_checks():
    manifest = SimpleNamespace(settings={"prima_chat_template_source": "{{ messages }}",
        "parallel_slots": 1, "prima_kv_sequences": 2, "swa_full": False}, ranks=[], assets=[])
    with pytest.raises(ValueError, match="Jinja"):
        require_execution_capabilities(manifest, {"features": ["execution_manifest_v1",
            "logical_context_sequences_v1", "embedding_role_v1", "compact_swa_v1"]})


def test_legacy_and_default_launch_remain_supported(monkeypatch):
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--chat-template"})
    assert launch.rank_arguments(None, {"chat_template": "chatml"}) == ["--chat-template", "chatml"]
    assert launch.rank_arguments(None, {"chat_template": "default"}) == []


def test_large_unicode_source_uses_owned_file_and_refuses_changed_retry(tmp_path, monkeypatch):
    source = "{{ messages[0].content }}" + "🐾" * 16000
    settings = {"chat_template": "custom", "chat_template_text": source}
    path = launch.materialize_template(settings, tmp_path)
    assert path.read_bytes() == source.encode("utf-8")
    assert launch.materialize_template(settings, tmp_path) == path
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--chat-template", "--chat-template-file", "--jinja"})
    args = launch.rank_arguments(None, settings, template_file=path)
    assert args == ["--chat-template-file", str(path), "--jinja"]
    assert source not in args
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="signed source"):
        launch.materialize_template(settings, tmp_path)
    assert path.read_text() == "changed"
