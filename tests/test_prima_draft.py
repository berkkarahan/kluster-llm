from pathlib import Path
from types import SimpleNamespace

import pytest

from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors import draft, launch
from kllm.modules.primacpp.interactors.execution import Asset, asset_files, require_execution_capabilities
from kllm.modules.primacpp.interactors.qualification import manifest_context
from kllm.modules.runtimes.interactors.registry import file_digest


def settings(**changes):
    # Production resolves the planned head KV reservation before launch.
    return {"speculative": "draft-simple", "draft_model": "draft.gguf", "draft_max": 3,
            "parallel_slots": 1, "prima_kv_sequences": 2, **changes}


def test_draft_aliases_and_disabled_model_are_explicit():
    assert draft.draft_mode(settings(speculative="draft")) == "draft-simple"
    assert draft.draft_mode(settings(speculative="auto")) == "draft-simple"
    assert draft.draft_mode(settings(speculative="none")) == "none"
    assert draft.draft_mode({}) == "none"
    with pytest.raises(ValueError, match="requires a draft model"):
        draft.draft_mode(settings(draft_model=None))
    with pytest.raises(ValueError, match="does not implement"):
        draft.draft_mode(settings(speculative="draft-mtp"))


@pytest.mark.parametrize("maximum", [-1, 1025, True, "3"])
def test_invalid_draft_budget_refuses_before_launch(maximum):
    with pytest.raises(ValueError, match="0..1024"):
        draft.draft_mode(settings(draft_max=maximum))


def test_scalar_draft_launch_is_cpu_local_and_never_applies_to_workers(monkeypatch):
    flags = {"--spec-type", "--model-draft", "--draft-max", "--gpu-layers-draft",
             "--cache-type-k-draft", "--cache-type-v-draft"}
    monkeypatch.setattr(draft, "help_flags", lambda _: flags)
    args = draft.draft_arguments(Path("binary"), settings(draft_cache_type_k="q8_0"), rank=0)
    assert args == ["--spec-type", "draft-simple", "--draft-max", "3", "--gpu-layers-draft", "0",
                    "--cache-type-k-draft", "q8_0", "--cache-type-v-draft", "f16"]
    assert draft.draft_arguments(Path("binary"), settings(), rank=1) == []
    assert draft.draft_arguments(Path("binary"), settings(speculative="none"), rank=0) == []
    monkeypatch.setattr(draft, "help_flags", lambda _: flags - {"--cache-type-k-draft"})
    with pytest.raises(ValueError, match="Upgrade"):
        draft.draft_arguments(Path("binary"), settings(), rank=0)


def test_draft_capability_and_asset_role_refuse_before_placement_checks():
    manifest = SimpleNamespace(settings=settings(swa_full=False), assets=[], ranks=[])
    base = {"features": ["execution_manifest_v1", "logical_context_sequences_v1",
                         "embedding_role_v1", "compact_swa_v1"]}
    with pytest.raises(ValueError, match="Upgrade"):
        require_execution_capabilities(manifest, base)
    with pytest.raises(ValueError, match="verified draft asset"):
        require_execution_capabilities(manifest, {**base, "features": [*base["features"], "draft_simple_v1"]})


def test_launch_uses_verified_staged_draft_bytes_not_request_model_identifier(tmp_path):
    model = tmp_path / "target.gguf"
    model.write_bytes(b"target")
    staged = tmp_path / "actual-draft.gguf"
    staged.write_bytes(b"verified-draft")
    asset = Asset(name=staged.name, kind="draft", sha256=file_digest(staged), size=staged.stat().st_size)
    manifest = SimpleNamespace(settings=settings(draft_model="catalog-model-id"), assets=[asset])
    assert draft.draft_file(manifest, model, rank=0) == staged
    assert draft.draft_file(manifest, model, rank=1) is None
    staged.write_bytes(b"tampered-draft")
    with pytest.raises(ValueError, match="differs"):
        draft.draft_file(manifest, model, rank=0)


def test_missing_or_duplicate_draft_cannot_start(tmp_path):
    manifest = SimpleNamespace(settings=settings(), assets=[])
    with pytest.raises(ValueError, match="one verified"):
        draft.draft_file(manifest, tmp_path / "model.gguf", rank=0)
    a = Asset(name="missing.gguf", kind="draft", sha256="a" * 64, size=8)
    manifest.assets = [a]
    with pytest.raises(ValueError, match="unavailable"):
        draft.draft_file(manifest, tmp_path / "model.gguf", rank=0)
    manifest.assets = [a, a]
    with pytest.raises(ValueError, match="one verified"):
        draft.draft_file(manifest, tmp_path / "model.gguf", rank=0)


def test_draft_shards_are_complete_signed_assets(tmp_path):
    model = tmp_path / "target.gguf"
    model.write_bytes(b"target")
    first = tmp_path / "draft-00001-of-00002.gguf"
    second = tmp_path / "draft-00002-of-00002.gguf"
    first.write_bytes(b"first")
    with pytest.raises(ValueError, match="Missing"):
        asset_files(model, {"draft": first})
    second.write_bytes(b"second")
    assets = asset_files(model, {"draft": first})
    assert [(asset.name, asset.kind) for asset, _ in assets] == [(model.name, "model"), (first.name, "draft"), (second.name, "shard")]
    assert assets[-1][0].sha256 == file_digest(second)


def test_frozen_draft_scope_records_cpu_placement_and_feature(monkeypatch):
    from kllm.modules.primacpp.interactors import qualification

    monkeypatch.setattr(qualification, "execution_host", lambda: {"cpu": "fixture"})
    body = SimpleNamespace(context=512, model_dump=lambda: settings())
    resolved = launch.execution_settings(body, {}, SimpleNamespace(name="target"), 12345)
    assert resolved["draft_gpu_layers"] == 0 and resolved["speculative"] == "draft-simple"
    rank = RankAllocation(node_id="node", role="master", rank=0, address="127.0.0.1", next_node_id="node",
                          layers=[LayerRange(start=0, end=1)], ngl=1, devices=["Vulkan0"], tensor_split=[1.0])
    manifest = SimpleNamespace(settings=resolved, ranks=[rank], assets=[Asset(name="model.gguf", kind="model", sha256="a" * 64, size=8)],
                               total_layers=1, split_mode="layer", segments=[], tensor_placements=[], backends={"node": "vulkan"})
    context = manifest_context(manifest, {"platform": "Windows"}, devices=[])
    assert "speculative:draft-simple" in context["features"] and "draft_model" in context["features"]
    assert context["execution"]["settings"]["draft_gpu_layers"] == 0


def test_local_run_resolves_draft_companion_before_materialization(monkeypatch, tmp_path):
    from kllm.modules.inference.entities import RunBody
    from kllm.modules.primacpp.interactors import runs

    body = RunBody(model="target.gguf", draft_model="draft.gguf", speculative="draft-simple")
    resolved = []
    control = SimpleNamespace(registry=SimpleNamespace(capture=lambda _: {}),
                              resolve_model=lambda value: resolved.append(value) or tmp_path / value)
    runner = runs.LocalPrimaRuns.__new__(runs.LocalPrimaRuns)
    runner.control = control
    monkeypatch.setattr(runs, "snapshot", lambda: {})
    monkeypatch.setattr(runs, "require_qualified", lambda _: None)
    monkeypatch.setattr(runs, "preview", lambda *args, **kwargs: {"plan_digest": "plan"})
    captured = []

    def materialize(*args, **kwargs):
        captured.append(kwargs["companions"])
        raise ValueError("stop before staging")

    monkeypatch.setattr(runs, "materialize", materialize)
    with pytest.raises(ValueError, match="stop before staging"):
        runner._launch(body, tmp_path / "target.gguf", {}, {})
    assert resolved == ["draft.gguf"] and captured == [{"draft": tmp_path / "draft.gguf"}]
    resolved.clear()
    with pytest.raises(ValueError, match="stop before staging"):
        runner._launch(body.model_copy(update={"speculative": "none"}), tmp_path / "target.gguf", {}, {})
    assert resolved == [] and captured[-1] == {}
