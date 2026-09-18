from __future__ import annotations

from kllm.cli_payload import build_plan_run_payload
from kllm.model_defaults import ModelDefaults, merge_defaults_into_extra_args, overlay_user_inference
from kllm.run_settings import RunSettingsFields


def test_run_settings_inference_round_trip():
    body = RunSettingsFields(
        model="toy.gguf",
        temperature=0.4,
        top_p=0.9,
        top_k=40,
        min_p=0.05,
        presence_penalty=1.2,
        repeat_penalty=1.1,
        enable_thinking=False,
        remember=True,
    )
    snap = body.snapshot_for_remember()
    assert snap["temperature"] == 0.4
    assert snap["top_p"] == 0.9
    assert snap["top_k"] == 40
    assert snap["min_p"] == 0.05
    assert snap["presence_penalty"] == 1.2
    assert snap["repeat_penalty"] == 1.1
    assert snap["enable_thinking"] is False

    restored = RunSettingsFields(model="toy.gguf", **{
        k: snap[k]
        for k in (
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "presence_penalty",
            "repeat_penalty",
            "enable_thinking",
            "context",
            "threads",
            "extra_args",
            "remember",
            "advanced_open",
            "cache_type_k",
            "cache_type_v",
            "vram_weight",
            "latency_weight",
        )
    })
    assert restored.temperature == 0.4
    assert restored.enable_thinking is False


def test_run_settings_null_means_auto():
    body = RunSettingsFields(model="toy.gguf")
    snap = body.normalized_auto_fields()
    assert snap["temperature"] is None
    assert snap["top_p"] is None
    assert snap["top_k"] is None
    assert snap["min_p"] is None
    assert snap["presence_penalty"] is None
    assert snap["repeat_penalty"] is None
    assert snap["enable_thinking"] is None


def test_overlay_user_inference_precedence():
    base = ModelDefaults(
        sampling={"temperature": 0.6, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5},
        chat_template_kwargs={"enable_thinking": True},
        sources={
            "temperature": "gguf",
            "top_k": "gguf",
            "min_p": "heuristic",
            "presence_penalty": "heuristic",
            "enable_thinking": "template",
        },
        jinja=True,
        reasoning_format="deepseek",
    )
    # Partial override — None leaves underlying defaults
    over = overlay_user_inference(base, temperature=0.2, enable_thinking=False)
    assert over.sampling["temperature"] == 0.2
    assert over.sources["temperature"] == "user"
    assert over.sampling["top_k"] == 20
    assert over.sources["top_k"] == "gguf"
    assert over.chat_template_kwargs["enable_thinking"] is False
    assert over.sources["enable_thinking"] == "user"

    # None args are no-ops
    same = overlay_user_inference(base)
    assert same.sampling == base.sampling
    assert same.chat_template_kwargs == base.chat_template_kwargs


def test_overlay_then_merge_user_wins_over_extra_args_flag_conflict():
    """User overlay updates sampling; merge still skips flags already in extra_args."""
    base = ModelDefaults(
        sampling={"temperature": 0.6, "top_k": 20},
        chat_template_kwargs={"enable_thinking": True},
        jinja=True,
        reasoning_format="deepseek",
    )
    over = overlay_user_inference(base, temperature=0.3, top_k=8)
    # If user also put --temp in extra_args, that still wins at argv level
    merged = merge_defaults_into_extra_args(["--temp", "0.9"], over)
    assert merged.count("--temp") == 1
    assert merged[merged.index("--temp") + 1] == "0.9"
    # top_k from overlay is injected because not in extra_args
    assert "--top-k" in merged
    assert merged[merged.index("--top-k") + 1] == "8"


def test_cli_payload_includes_inference_overrides():
    payload = build_plan_run_payload(
        model="m.gguf",
        temperature=0.55,
        top_p=0.92,
        top_k=30,
        min_p=0.0,
        presence_penalty=1.5,
        repeat_penalty=1.05,
        enable_thinking=True,
    )
    assert payload["temperature"] == 0.55
    assert payload["top_p"] == 0.92
    assert payload["top_k"] == 30
    assert payload["min_p"] == 0.0
    assert payload["presence_penalty"] == 1.5
    assert payload["repeat_penalty"] == 1.05
    assert payload["enable_thinking"] is True


def test_cli_payload_omits_null_inference():
    payload = build_plan_run_payload(model="m.gguf")
    for key in (
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "presence_penalty",
        "repeat_penalty",
        "enable_thinking",
    ):
        assert key not in payload
