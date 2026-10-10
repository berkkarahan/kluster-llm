from __future__ import annotations

from unittest.mock import patch

import pytest

from kllm.modules import models
import kllm.modules.models.api as models_api
from kllm.shared import hf_token


@pytest.fixture()
def kllm_home(tmp_path, monkeypatch):
    home = tmp_path / "kllm-home"
    monkeypatch.setenv("KLLM_HOME", str(home))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    home.mkdir()
    (home / "models").mkdir()
    models.downloads._reset_download_state()
    yield home
    models.downloads._reset_download_state()


def test_normalize_quant_strips_separators_and_gguf():
    assert models.normalize_quant("Q4_K_M") == "Q4KM"
    assert models.normalize_quant("Q_4_K_M") == "Q4KM"
    assert models.normalize_quant("q4-k-m") == "Q4KM"
    assert models.normalize_quant("Q4_K_M.gguf") == "Q4KM"


def test_exact_filename_vs_quant_tag():
    assert models.is_exact_hf_filename("llama.Q4_K_M.gguf")
    assert models.is_exact_hf_filename("gguf/model.Q4_K_M.gguf")
    assert not models.is_exact_hf_filename("Q4_K_M")
    assert not models.is_exact_hf_filename("Q_4_K_M")
    assert not models.is_exact_hf_filename("Q4_K_M.gguf")


REPO_FILES = [
    "README.md",
    "model.Q4_K_M.gguf",
    "model.Q5_K_M.gguf",
    "model.Q8_0.gguf",
    "mmproj-model.Q4_K_M.gguf",
    "model.Q4_K_M-00001-of-00002.gguf",
    "imatrix-model.gguf",
]


def test_match_quant_skips_mmproj_and_shards():
    matches = models.match_gguf_for_quant(REPO_FILES, "Q_4_K_M")
    assert matches == ["model.Q4_K_M.gguf"]


def test_select_quant_unique():
    assert models.select_gguf_for_quant(REPO_FILES, "Q5_K_M") == "model.Q5_K_M.gguf"


def test_select_quant_none_lists_available():
    with pytest.raises(ValueError, match="Available quants") as exc:
        models.select_gguf_for_quant(REPO_FILES, "IQ4_XS", repo_id="org/repo")
    msg = str(exc.value)
    assert "Q4_K_M" in msg
    assert "Q5_K_M" in msg
    assert "Q8_0" in msg
    assert "IQ4_XS" in msg


def test_download_quant_passes_token_to_hub(kllm_home, monkeypatch):
    hf_token.save_hf_token("hf_test_token")
    listed: list[tuple] = []

    def fake_list(repo_id, token=None, **kwargs):
        listed.append((repo_id, token))
        return REPO_FILES

    monkeypatch.setattr(models_api, "list_repo_files", fake_list)
    monkeypatch.setattr(
        models,
        "hf_hub_url",
        lambda repo_id, filename: f"https://huggingface.co/{repo_id}/resolve/main/{filename}",
    )
    stream_headers: dict = {}

    def fake_stream(*_a, **kwargs):
        stream_headers.update(kwargs.get("headers") or {})
        return _FakeResp(b"gguf")

    monkeypatch.setattr(models.downloads.httpx, "stream", fake_stream)
    path = models.download_model("org/repo:q4-k-m")
    assert listed == [("org/repo", "hf_test_token")]
    assert path.name == "model.Q4_K_M.gguf"
    auth = stream_headers.get("authorization") or stream_headers.get("Authorization")
    assert auth == "Bearer hf_test_token"


def test_select_quant_ambiguous_lists_candidates():
    files = ["a.Q4_K_M.gguf", "b.Q4_K_M.gguf"]
    with pytest.raises(ValueError, match="a.Q4_K_M.gguf"):
        models.select_gguf_for_quant(files, "Q4_K_M", repo_id="org/repo")


def test_resolve_hf_filename_exact_skips_listing():
    with patch.object(models, "list_repo_files") as listed:
        name = models.resolve_hf_filename("org/repo", "weights/model.Q4_K_M.gguf")
        assert name == "weights/model.Q4_K_M.gguf"
        listed.assert_not_called()


def test_resolve_hf_filename_quant_lists(monkeypatch):
    monkeypatch.setattr(models_api, "list_repo_files", lambda repo_id, token=None: REPO_FILES)
    assert models.resolve_hf_filename("org/repo", "Q4_K_M") == "model.Q4_K_M.gguf"


class _FakeResp:
    def __init__(self, data: bytes, *, with_length: bool = True):
        self._data = data
        self.headers = {"Content-Length": str(len(data))} if with_length else {}

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        mid = max(1, len(self._data) // 2)
        yield self._data[:mid]
        yield self._data[mid:]

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def test_stream_progress_callback(kllm_home, monkeypatch):
    payload = b"abcdefgh"
    snapshots: list[dict] = []

    def fake_stream(*_args, **_kwargs):
        return _FakeResp(payload)

    monkeypatch.setattr(models.downloads.httpx, "stream", fake_stream)
    path = models.download_model(
        "https://example.test/toy.gguf",
        on_progress=snapshots.append,
    )
    assert path.name == "toy.gguf"
    assert path.read_bytes() == payload
    assert snapshots[0]["bytes"] == 0
    assert snapshots[0]["total"] == len(payload)
    assert snapshots[-1]["bytes"] == len(payload)
    assert snapshots[-1]["pct"] == 100
    assert models.get_download_progress() is None


def test_download_in_progress_rejected(kllm_home):
    assert models.downloads._DOWNLOAD_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(models.DownloadInProgressError):
            models.download_model("https://example.test/toy.gguf")
    finally:
        models.downloads._DOWNLOAD_LOCK.release()


def test_hf_token_file_beats_env(kllm_home, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_env")
    status = hf_token.save_hf_token("hf_file")
    assert status == {"configured": True, "source": "file"}
    assert hf_token.resolve_hf_token() == "hf_file"
    path = hf_token.hf_token_path()
    assert path.is_file()
    if __import__("os").name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600


def test_hf_token_env_fallback(kllm_home, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_env")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_hub_env")
    assert hf_token.hf_token_status() == {"configured": True, "source": "env"}
    assert hf_token.resolve_hf_token() == "hf_env"
    monkeypatch.delenv("HF_TOKEN")
    assert hf_token.resolve_hf_token() == "hf_hub_env"


def test_hf_token_clear(kllm_home):
    hf_token.save_hf_token("hf_secret")
    cleared = hf_token.save_hf_token("")
    assert cleared == {"configured": False, "source": None}
    assert not hf_token.hf_token_path().exists()
    assert "secret" not in str(cleared)


def test_parse_search_query_repo_and_search():
    repo = models.parse_model_search_query("orcarouter/Qwen3.8-27B-Uncensored-GGUF:Q4_K_M")
    assert repo.kind == "repo"
    assert repo.repo_id == "orcarouter/Qwen3.8-27B-Uncensored-GGUF"
    assert repo.suffix == "Q4_K_M"

    named = models.parse_model_search_query("Qwen3.8-27B-Uncensored-GGUF:Q4_K_M")
    assert named.kind == "search"
    assert named.search_term == "Qwen3.8-27B-Uncensored-GGUF"
    assert named.suffix == "Q4_K_M"

    all_quants = models.parse_model_search_query("Qwen3.8-27B-Uncensored-GGUF")
    assert all_quants.kind == "search"
    assert all_quants.suffix is None

    url = models.parse_model_search_query(
        "https://huggingface.co/orcarouter/Qwen3.8-27B-Uncensored-GGUF/resolve/main/Qwen3.8-27B-Uncensored-Q4_K_M.gguf"
    )
    assert url.kind == "url"
    assert url.repo_id == "orcarouter/Qwen3.8-27B-Uncensored-GGUF"
    assert url.filename == "Qwen3.8-27B-Uncensored-Q4_K_M.gguf"
    assert url.revision == "main"
    assert url.suffix is None
    assert url.folder is None


UNSLOTH_REPO = "unsloth/Qwen3.5-122B-A10B-GGUF"
UNSLOTH_FILE = "Qwen3.5-122B-A10B-UD-IQ3_S.gguf"
UNSLOTH_SHOW_FILE_INFO = f"https://huggingface.co/{UNSLOTH_REPO}?show_file_info={UNSLOTH_FILE}"


def test_parse_search_query_show_file_info():
    parsed = models.parse_model_search_query(UNSLOTH_SHOW_FILE_INFO)
    assert parsed.kind == "url"
    assert parsed.repo_id == UNSLOTH_REPO
    assert parsed.filename == UNSLOTH_FILE
    assert parsed.suffix is None
    assert parsed.revision is None

    encoded = models.parse_model_search_query(
        f"https://huggingface.co/{UNSLOTH_REPO}?show_file_info=gguf%2F{UNSLOTH_FILE}"
    )
    assert encoded.filename == f"gguf/{UNSLOTH_FILE}"

    repo_only = models.parse_model_search_query(f"https://huggingface.co/{UNSLOTH_REPO}")
    assert repo_only.repo_id == UNSLOTH_REPO
    assert repo_only.suffix is None
    assert repo_only.filename is None

    resolve_wins = models.parse_model_search_query(
        f"https://huggingface.co/{UNSLOTH_REPO}/resolve/main/other.Q8_0.gguf"
        f"?show_file_info={UNSLOTH_FILE}"
    )
    assert resolve_wins.filename == "other.Q8_0.gguf"


def test_parse_search_query_empty():
    with pytest.raises(ValueError, match="empty"):
        models.parse_model_search_query("  ")


def test_rank_hub_exact_repo_name_first():
    ranked = models.rank_hub_repo_ids(
        [
            "someone/other-gguf",
            "orcarouter/Qwen3.8-27B-Uncensored-GGUF",
            "org/Qwen3.8-27B-Uncensored-GGUF-extra",
        ],
        "Qwen3.8-27B-Uncensored-GGUF",
    )
    assert ranked[0] == "orcarouter/Qwen3.8-27B-Uncensored-GGUF"
    assert ranked[1] == "someone/other-gguf"


def test_vram_fit_and_sum():
    assert models.vram_fit(16_800_000_000, 48_000_000_000) == "fits"
    assert models.vram_fit(50_000_000_000, 48_000_000_000) == "overflow"
    assert models.vram_fit(16_800_000_000, None) == "unknown"
    assert models.vram_fit(0, 48_000_000_000) == "unknown"
    assert (
        models.sum_vram_bytes([{"vram_bytes": 24_000_000_000}, {"vram_bytes": 24_000_000_000}])
        == 48_000_000_000
    )
    assert models.sum_vram_bytes([{"vram_bytes": None}, {"vram_bytes": 0}]) is None


class _FakeSibling:
    def __init__(self, rfilename: str, size: int | None):
        self.rfilename = rfilename
        self.size = size


class _FakeInfo:
    def __init__(self, siblings: list[_FakeSibling], *, gated: bool = False):
        self.siblings = siblings
        self.gated = gated


ORCA_REPO = "orcarouter/Qwen3.8-27B-Uncensored-GGUF"
ORCA_SIBLINGS = [
    _FakeSibling("Qwen3.8-27B-Uncensored-Q4_K_M.gguf", 16_800_000_000),
    _FakeSibling("Qwen3.8-27B-Uncensored-Q8_0.gguf", 30_000_000_000),
    _FakeSibling("mmproj-Q4_K_M.gguf", 1_000),
    _FakeSibling("Qwen3.8-27B-Uncensored-Q4_K_M-00001-of-00002.gguf", 8_000),
]


def _fake_orca_info(repo_id, **_kwargs):
    if repo_id == ORCA_REPO:
        return _FakeInfo(ORCA_SIBLINGS)
    if repo_id == "gated/fails":
        raise OSError("gated")
    return _FakeInfo([_FakeSibling("other.Q5_K_M.gguf", 1_000_000)])


def _fake_hub_hits(**_kwargs):
    return [
        type("M", (), {"id": "someone/other-gguf"})(),
        type("M", (), {"id": ORCA_REPO})(),
        type("M", (), {"id": "gated/fails"})(),
    ]


def test_search_quant_filter_skips_mmproj_and_ranks(monkeypatch):
    monkeypatch.setattr(models_api, "list_models", _fake_hub_hits)
    monkeypatch.setattr(models_api, "model_info", _fake_orca_info)
    out = models.search_hub_ggufs(
        "Qwen3.8-27B-Uncensored-GGUF:Q4_K_M",
        total_vram_bytes=48_000_000_000,
        token=False,
    )
    assert [row["repo_id"] for row in out["results"]] == [ORCA_REPO]
    row = out["results"][0]
    assert row["filename"] == "Qwen3.8-27B-Uncensored-Q4_K_M.gguf"
    assert row["quant"] == "Q4_K_M"
    assert row["size_bytes"] == 16_800_000_000
    assert row["source"] == f"{ORCA_REPO}:Qwen3.8-27B-Uncensored-Q4_K_M.gguf"
    assert row["fit"] == "fits"
    assert "mmproj" not in row["filename"]


def test_search_without_quant_lists_all_candidate_ggufs(monkeypatch):
    monkeypatch.setattr(models_api, "list_models", _fake_hub_hits)
    monkeypatch.setattr(models_api, "model_info", _fake_orca_info)
    out = models.search_hub_ggufs(
        "Qwen3.8-27B-Uncensored-GGUF",
        total_vram_bytes=20_000_000_000,
        token=False,
    )
    names = [row["filename"] for row in out["results"] if row["repo_id"] == ORCA_REPO]
    assert names == [
        "Qwen3.8-27B-Uncensored-Q4_K_M.gguf",
        "Qwen3.8-27B-Uncensored-Q8_0.gguf",
    ]
    by_name = {row["filename"]: row for row in out["results"] if row["repo_id"] == ORCA_REPO}
    assert by_name["Qwen3.8-27B-Uncensored-Q4_K_M.gguf"]["fit"] == "fits"
    assert by_name["Qwen3.8-27B-Uncensored-Q8_0.gguf"]["fit"] == "overflow"


def test_search_direct_repo_skips_hub(monkeypatch):
    def boom(**_kwargs):
        raise AssertionError("list_models should not run for org/repo")

    monkeypatch.setattr(models_api, "list_models", boom)
    monkeypatch.setattr(models_api, "model_info", _fake_orca_info)
    out = models.search_hub_ggufs(f"{ORCA_REPO}:Q4_K_M", token=False)
    assert len(out["results"]) == 1
    assert out["results"][0]["repo_id"] == ORCA_REPO


def test_search_url_uses_content_length(monkeypatch):
    monkeypatch.setattr(models_api, "_url_size_bytes", lambda url, headers=None: 12345)
    out = models.search_hub_ggufs("https://example.test/tiny.Q4_K_M.gguf", token=False)
    assert len(out["results"]) == 1
    row = out["results"][0]
    assert row["filename"] == "tiny.Q4_K_M.gguf"
    assert row["size_bytes"] == 12345
    assert row["source"] == "https://example.test/tiny.Q4_K_M.gguf"
    assert row["quant"] == "Q4_K_M"


def test_search_show_file_info_url_picks_exact_gguf(monkeypatch):
    def boom(**_kwargs):
        raise AssertionError("list_models should not run for Hub file URLs")

    def fake_info(repo_id, **_kwargs):
        if repo_id != UNSLOTH_REPO:
            raise AssertionError(f"unexpected repo {repo_id}")
        return _FakeInfo(
            [
                _FakeSibling(UNSLOTH_FILE, 48_000_000_000),
                _FakeSibling("Qwen3.5-122B-A10B-Q8_0.gguf", 130_000_000_000),
                _FakeSibling("mmproj-Q4_K_M.gguf", 1_000),
            ]
        )

    monkeypatch.setattr(models_api, "list_models", boom)
    monkeypatch.setattr(models_api, "model_info", fake_info)
    out = models.search_hub_ggufs(UNSLOTH_SHOW_FILE_INFO, token=False)
    assert len(out["results"]) == 1
    row = out["results"][0]
    assert row["repo_id"] == UNSLOTH_REPO
    assert row["filename"] == UNSLOTH_FILE
    assert row["source"] == models.hf_hub_url(UNSLOTH_REPO, UNSLOTH_FILE, revision="main")
    assert row["path"] == UNSLOTH_FILE
    assert row["revision"] == "main"
    assert row["size_bytes"] == 48_000_000_000
    assert row["quant"] == "IQ3_S"


def test_download_show_file_info_url_uses_hf_hub_url(kllm_home, monkeypatch):
    streamed: list[str] = []
    payload = b"gguf-show-file-info"

    def fake_hub_url(repo_id, filename, revision=None):
        rev = revision or "main"
        return f"https://huggingface.co/{repo_id}/resolve/{rev}/{filename}"

    def fake_stream(method, url, **_kwargs):
        streamed.append(url)
        return _FakeResp(payload)

    monkeypatch.setattr(models_api, "hf_hub_url", fake_hub_url)
    monkeypatch.setattr(models.downloads.httpx, "stream", fake_stream)
    path = models.download_model(UNSLOTH_SHOW_FILE_INFO)
    assert path.name == UNSLOTH_FILE
    assert path.read_bytes() == payload
    assert streamed == [f"https://huggingface.co/{UNSLOTH_REPO}/resolve/main/{UNSLOTH_FILE}"]


def test_download_writes_sha256_sidecar(kllm_home, monkeypatch):
    payload = b"gguf-sidecar-bytes"

    def fake_stream(*_args, **_kwargs):
        return _FakeResp(payload)

    monkeypatch.setattr(models.downloads.httpx, "stream", fake_stream)
    path = models.download_model("https://example.test/sidecar.gguf")
    assert models.read_sha256_sidecar(path) == models.sha256_file(path)


DEMO_REPO = "demo/model-GGUF"
_SUPPORTED_LINK = "supported model repository"


def _hub_error(cls, status: int, message: str = "upstream"):
    import httpx

    request = httpx.Request("GET", "https://huggingface.co/api/models/demo/model-GGUF")
    response = httpx.Response(
        status, request=request, headers={"X-Error-Message": "hf_SECRET signed"}
    )
    return cls(
        message + " https://huggingface.co/demo/model-GGUF?token=hf_SECRET", response=response
    )


def _release_siblings() -> list[_FakeSibling]:
    return [
        _FakeSibling("model.Q8_0.gguf", 20_000_000_000),
        _FakeSibling("gguf/model.Q4_K_M.gguf", 4_000_000_000),
        _FakeSibling("other/model.Q4_K_M.gguf", 4_000_000_000),
        _FakeSibling("gguf/nested/model.Q5_K_M.gguf", 6_000_000_000),
        _FakeSibling("Q4_K_M.gguf", 1_000),
        _FakeSibling("gguf/mmproj.gguf", 10),
        _FakeSibling("gguf/imatrix.gguf", 10),
        _FakeSibling("gguf/model-00001-of-00002.gguf", 10),
        _FakeSibling("gguf/README.md", 10),
        _FakeSibling("docs/README.md", 10),
        _FakeSibling("gguf/my model.Q6_K.gguf", 3_000),
        _FakeSibling("gguf/a+b.Q6_K.gguf", 3_000),
        _FakeSibling("gguf/a%2Fb.Q6_K.gguf", None),
        _FakeSibling("gguf-extra/model.Q4_K_M.gguf", 4_000),
    ]


def _main_siblings() -> list[_FakeSibling]:
    return [_FakeSibling("main-only.Q4_K_M.gguf", 2_000)]


def _install_demo_hub(monkeypatch, *, mode: str = "ok", calls: list | None = None):
    from huggingface_hub.errors import (
        GatedRepoError,
        RepositoryNotFoundError,
        RevisionNotFoundError,
        HfHubHTTPError,
    )
    import httpx

    def fake_list(**_kwargs):
        raise AssertionError("list_models should not run for an explicit target")

    def fake_info(repo_id, **kwargs):
        if calls is not None:
            calls.append((repo_id, kwargs))
        if mode == "gated":
            raise _hub_error(GatedRepoError, 403)
        if mode == "missing-repo":
            raise _hub_error(RepositoryNotFoundError, 401)
        if mode == "missing-revision":
            raise _hub_error(RevisionNotFoundError, 404)
        if mode == "timeout":
            raise httpx.ReadTimeout("timed out")
        if mode == "rate":
            raise _hub_error(HfHubHTTPError, 429)
        if repo_id != DEMO_REPO:
            raise AssertionError(repo_id)
        revision = kwargs.get("revision")
        if revision == "release/v2":
            return _FakeInfo(_release_siblings())
        if revision == "main":
            return _FakeInfo(_main_siblings())
        raise _hub_error(RevisionNotFoundError, 404)

    monkeypatch.setattr(models_api, "list_models", fake_list)
    monkeypatch.setattr(models_api, "model_info", fake_info)
    monkeypatch.setattr(
        models,
        "_url_size_bytes",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("search must not HEAD files")),
    )


def test_parse_hf_url_grammar_and_rejections():
    tree = models.parse_model_search_query(
        "  https://huggingface.co/demo/model-GGUF/tree/release%2Fv2/gguf/?download=true#files  "
    )
    assert (
        tree.raw
        == "https://huggingface.co/demo/model-GGUF/tree/release%2Fv2/gguf/?download=true#files"
    )
    assert tree.kind == "url"
    assert tree.repo_id == DEMO_REPO
    assert tree.revision == "release/v2"
    assert tree.folder == "gguf"
    assert tree.filename is None
    assert tree.suffix is None

    unencoded = models.parse_model_search_query("https://hf.co/demo/model-GGUF/tree/release/v2")
    assert unencoded.revision == "release"
    assert unencoded.folder == "v2"

    blob = models.parse_model_search_query(
        "HTTPS://www.HuggingFace.co:443/models/demo/model-GGUF/blob/main/gguf/model.Q4_K_M.gguf"
    )
    assert blob.repo_id == DEMO_REPO
    assert blob.filename == "gguf/model.Q4_K_M.gguf"
    assert blob.folder is None

    plus = models.parse_model_search_query(
        "https://huggingface.co/demo/model-GGUF/resolve/main/gguf/a+b.Q6_K.gguf"
    )
    assert plus.filename == "gguf/a+b.Q6_K.gguf"

    literal = models.parse_model_search_query(
        "https://huggingface.co/demo/model-GGUF/raw/main/gguf/a%252Fb.Q6_K.gguf"
    )
    assert literal.filename == "gguf/a%2Fb.Q6_K.gguf"

    info = models.parse_model_search_query(
        "https://huggingface.co/demo/model-GGUF/tree/release%2Fv2/gguf?show_file_info=Q4_K_M.gguf"
    )
    assert info.filename == "Q4_K_M.gguf"
    assert info.folder is None
    assert info.revision == "release/v2"

    query_plus = models.parse_model_search_query(
        "https://huggingface.co/demo/model-GGUF?show_file_info=my+model.Q6_K.gguf"
    )
    assert query_plus.filename == "my model.Q6_K.gguf"
    encoded_plus = models.parse_model_search_query(
        "https://huggingface.co/demo/model-GGUF?show_file_info=a%2Bb.Q6_K.gguf"
    )
    assert encoded_plus.filename == "a+b.Q6_K.gguf"

    direct = models.parse_model_search_query("https://cdn.example/file.gguf?X-Amz-Signature=abc")
    assert direct.kind == "url"
    assert direct.repo_id is None
    assert direct.url == "https://cdn.example/file.gguf?X-Amz-Signature=abc"
    assert direct.filename is None

    for bad in (
        "https://huggingface.co/datasets/demo/model-GGUF",
        "https://huggingface.co/spaces/demo/app",
        "https://huggingface.co/collections/demo/col",
        "https://huggingface.co/demo",
        "https://huggingface.co/demo/model-GGUF/discussions",
        "https://user:hf_SECRET@huggingface.co/demo/model-GGUF",
        "https://huggingface.co:8443/demo/model-GGUF",
        "https://huggingface.co/demo/model-GGUF/blob/main/",
        "https://huggingface.co/demo/model-GGUF/resolve/main",
        "https://huggingface.co/demo/model-GGUF/tree",
        "https://huggingface.co/demo/model-GGUF?show_file_info=",
        "https://huggingface.co/demo/model-GGUF?show_file_info=a&show_file_info=b",
        "https://huggingface.co/demo/model-GGUF?revision=main",
        "https://huggingface.co/demo/model-GGUF/tree/main/%2e%2e/secret.gguf",
        "https://huggingface.co/demo/model-GGUF/resolve/main/foo//bar.gguf",
        "https://huggingface.co/demo/bad%ZZ/model",
    ):
        with pytest.raises(ValueError, match=_SUPPORTED_LINK):
            models.parse_model_search_query(bad)


def test_explicit_tree_folder_lists_eligible_revision_files(monkeypatch):
    calls: list = []
    _install_demo_hub(monkeypatch, calls=calls)
    out = models.search_hub_ggufs(
        "https://huggingface.co/demo/model-GGUF/tree/release%2Fv2/gguf",
        total_vram_bytes=8_000_000_000,
        token="hf_SUPERSECRET",
    )
    paths = [row["path"] for row in out["results"]]
    assert paths == [
        "gguf/model.Q4_K_M.gguf",
        "gguf/nested/model.Q5_K_M.gguf",
        "gguf/my model.Q6_K.gguf",
        "gguf/a+b.Q6_K.gguf",
        "gguf/a%2Fb.Q6_K.gguf",
    ]
    assert "other/model.Q4_K_M.gguf" not in paths
    assert "gguf-extra/model.Q4_K_M.gguf" not in paths
    assert all(row["revision"] == "release/v2" for row in out["results"])
    assert calls[0][1]["revision"] == "release/v2"
    assert calls[0][1]["files_metadata"] is True
    assert calls[0][1]["timeout"] == 15.0
    assert calls[0][1]["token"] == "hf_SUPERSECRET"
    by_path = {row["path"]: row for row in out["results"]}
    q4 = by_path["gguf/model.Q4_K_M.gguf"]
    assert q4["filename"] == "model.Q4_K_M.gguf"
    assert q4["fit"] == "fits"
    assert q4["source"] == models.hf_hub_url(DEMO_REPO, q4["path"], revision="release/v2")
    assert by_path["gguf/nested/model.Q5_K_M.gguf"]["fit"] == "fits"
    unknown = by_path["gguf/a%2Fb.Q6_K.gguf"]
    assert unknown["size_bytes"] == 0
    assert unknown["fit"] == "unknown"
    blob = __import__("json").dumps(out)
    assert "hf_SUPERSECRET" not in blob
    assert out["warnings"] == []
    root = models.search_hub_ggufs(
        "https://huggingface.co/demo/model-GGUF/tree/release%2Fv2",
        total_vram_bytes=8_000_000_000,
        token=False,
    )
    assert "model.Q8_0.gguf" in [row["path"] for row in root["results"]]
    assert root["results"][0]["fit"] == "overflow" or any(
        row["fit"] == "overflow" for row in root["results"]
    )
    main = models.search_hub_ggufs("https://huggingface.co/demo/model-GGUF", token=False)
    assert [row["path"] for row in main["results"]] == ["main-only.Q4_K_M.gguf"]
    assert main["results"][0]["revision"] == "main"


def test_explicit_lookup_is_uncapped_and_empty_folder_differs_from_missing(monkeypatch):
    files = [_FakeSibling(f"models/file-{i}.Q4_K_M.gguf", 10) for i in range(41)]
    files.append(_FakeSibling("docs/README.md", 1))

    def fake_info(repo_id, **kwargs):
        assert kwargs["revision"] == "main"
        return _FakeInfo(files)

    monkeypatch.setattr(models_api, "model_info", fake_info)
    monkeypatch.setattr(
        models, "list_models", lambda **_k: (_ for _ in ()).throw(AssertionError("discovery"))
    )
    out = models.search_hub_ggufs("https://huggingface.co/demo/model-GGUF", token=False)
    assert len(out["results"]) == 41

    def docs_only(repo_id, **_kwargs):
        return _FakeInfo([_FakeSibling("docs/README.md", 1), _FakeSibling("gguf/README.md", 1)])

    monkeypatch.setattr(models_api, "model_info", docs_only)
    empty = models.search_hub_ggufs(
        "https://huggingface.co/demo/model-GGUF/tree/main/gguf", token=False
    )
    assert empty["results"] == []
    assert empty["warnings"] == []
    with pytest.raises(ValueError, match="Folder"):
        models.search_hub_ggufs(
            "https://huggingface.co/demo/model-GGUF/tree/main/missing-folder", token=False
        )


def test_exact_file_urls_do_not_substitute_or_expand_quant(monkeypatch):
    _install_demo_hub(monkeypatch)
    for route in ("blob", "resolve", "raw"):
        out = models.search_hub_ggufs(
            f"https://huggingface.co/demo/model-GGUF/{route}/release%2Fv2/gguf/model.Q4_K_M.gguf",
            token=False,
        )
        assert [row["path"] for row in out["results"]] == ["gguf/model.Q4_K_M.gguf"]
        assert out["results"][0]["revision"] == "release/v2"
    info = models.search_hub_ggufs(
        "https://huggingface.co/demo/model-GGUF/tree/release%2Fv2/gguf?show_file_info=Q4_K_M.gguf",
        token=False,
    )
    assert [row["path"] for row in info["results"]] == ["Q4_K_M.gguf"]
    literal = models.search_hub_ggufs(
        "https://huggingface.co/demo/model-GGUF/resolve/release%2Fv2/Q4_K_M.gguf",
        token=False,
    )
    assert [row["path"] for row in literal["results"]] == ["Q4_K_M.gguf"]
    assert literal["results"][0]["source"] == models.hf_hub_url(
        DEMO_REPO, "Q4_K_M.gguf", revision="release/v2"
    )
    with pytest.raises(ValueError, match="unavailable"):
        models.search_hub_ggufs(
            "https://huggingface.co/demo/model-GGUF/resolve/release%2Fv2/missing.Q4_K_M.gguf",
            token=False,
        )
    with pytest.raises(ValueError, match="unavailable"):
        models.search_hub_ggufs(
            "https://huggingface.co/demo/model-GGUF/resolve/release%2Fv2/gguf/Model.Q4_K_M.gguf",
            token=False,
        )
    for bad in (
        "gguf/mmproj.gguf",
        "gguf/imatrix.gguf",
        "gguf/model-00001-of-00002.gguf",
        "gguf/README.md",
    ):
        with pytest.raises(ValueError, match="not a supported standalone GGUF"):
            models.search_hub_ggufs(
                f"https://huggingface.co/demo/model-GGUF/blob/release%2Fv2/{bad}",
                token=False,
            )


def test_explicit_lookup_errors_are_safe_and_not_empty_results(monkeypatch):
    for mode, match in (
        ("gated", "token"),
        ("missing-repo", "unavailable or access"),
        ("missing-revision", "Revision"),
        ("timeout", "timed out"),
        ("rate", "rate limited"),
    ):
        _install_demo_hub(monkeypatch, mode=mode)
        with pytest.raises(ValueError, match=match) as exc:
            models.search_hub_ggufs(
                "https://huggingface.co/demo/model-GGUF/tree/release%2Fv2",
                token="hf_SUPERSECRET",
            )
        text = str(exc.value)
        assert "hf_SUPERSECRET" not in text
        assert "https://" not in text
        assert "does not exist" not in text.lower()


def test_keyword_partial_warnings_and_legacy_caps(monkeypatch):
    def fake_list(**kwargs):
        assert kwargs["limit"] == models.SEARCH_HUB_LIMIT
        return [type("M", (), {"id": f"org/repo-{i}"})() for i in range(10)]

    def fake_info(repo_id, **kwargs):
        assert kwargs["timeout"] == 15.0
        if repo_id == "org/repo-0":
            return _FakeInfo(
                [
                    _FakeSibling("Q4_K_M.gguf", 10),
                    _FakeSibling("real.Q4_K_M.gguf", 20),
                ]
            )
        if repo_id == "org/repo-1":
            raise OSError("boom hf_SUPERSECRET https://signed.example/x?sig=1")
        return _FakeInfo([])

    monkeypatch.setattr(models_api, "list_models", fake_list)
    monkeypatch.setattr(models_api, "model_info", fake_info)
    out = models.search_hub_ggufs("llama:Q4_K_M", token=False)
    assert out["results"]
    assert any(
        row["source"].startswith("https://") and row["filename"] == "Q4_K_M.gguf"
        for row in out["results"]
    )
    assert any(row["source"] == "org/repo-0:real.Q4_K_M.gguf" for row in out["results"])
    assert out["warnings"]
    blob = __import__("json").dumps(out)
    assert "hf_SUPERSECRET" not in blob
    assert "signed.example" not in blob

    def failing_info(repo_id, **_kwargs):
        raise OSError("all-down")

    monkeypatch.setattr(models_api, "model_info", failing_info)
    with pytest.raises(ValueError, match="all-down"):
        models.search_hub_ggufs("llama", token=False)

    def empty_info(repo_id, **_kwargs):
        return _FakeInfo([_FakeSibling("README.md", 1)])

    monkeypatch.setattr(models_api, "model_info", empty_info)
    empty = models.search_hub_ggufs("llama", token=False)
    assert empty["results"] == []
    assert empty["warnings"] == []


def test_direct_signed_url_keeps_source_and_skips_hub_credentials(kllm_home, monkeypatch):
    seen: dict = {}

    def fake_head(url, headers=None):
        seen["head"] = (url, dict(headers or {}))
        return 42

    def fake_stream(method, url, **kwargs):
        seen["stream"] = (url, dict(kwargs.get("headers") or {}))
        return _FakeResp(b"signed-bytes")

    monkeypatch.setattr(models_api, "_url_size_bytes", fake_head)
    monkeypatch.setattr(models.downloads.httpx, "stream", fake_stream)
    monkeypatch.setattr(models_api, "resolve_hf_token", lambda: "hf_SUPERSECRET")
    monkeypatch.setattr(
        models, "model_info", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no hub"))
    )
    source = "https://cdn.example/file.gguf?X-Amz-Signature=abc"
    out = models.search_hub_ggufs(source, token="hf_SUPERSECRET")
    assert out["results"][0]["source"] == source
    assert out["results"][0]["path"] is None
    assert out["results"][0]["revision"] is None
    assert seen["head"][1] == {}
    assert "hf_SUPERSECRET" not in __import__("json").dumps(out)
    path = models.download_model(source)
    assert path.read_bytes() == b"signed-bytes"
    assert seen["stream"][0] == source
    assert "authorization" not in {k.lower() for k in seen["stream"][1]}


def test_download_hub_url_uses_revision_and_rejects_tree(kllm_home, monkeypatch):
    streamed: list[str] = []

    def fake_stream(method, url, **_kwargs):
        streamed.append(url)
        return _FakeResp(b"rev-bytes")

    monkeypatch.setattr(models.downloads.httpx, "stream", fake_stream)
    monkeypatch.setattr(
        models,
        "list_repo_files",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no listing")),
    )
    source = models.hf_hub_url(DEMO_REPO, "gguf/model.Q4_K_M.gguf", revision="release/v2")
    path = models.download_model(source)
    assert path.name == "model.Q4_K_M.gguf"
    assert path.read_bytes() == b"rev-bytes"
    assert streamed == [source]
    with pytest.raises(ValueError, match="not file downloads"):
        models.download_model("https://huggingface.co/demo/model-GGUF/tree/release%2Fv2/gguf")
    assert streamed == [source]


def test_shorthand_exact_path_does_not_invent_files(monkeypatch):
    def fake_info(repo_id, **_kwargs):
        return _FakeInfo(
            [
                _FakeSibling("other/missing.gguf", 5),
                _FakeSibling("real.Q4_K_M.gguf", 5),
            ]
        )

    monkeypatch.setattr(models_api, "model_info", fake_info)
    out = models.search_hub_ggufs("demo/model-GGUF:real.Q4_K_M.gguf", token=False)
    assert [row["path"] for row in out["results"]] == ["real.Q4_K_M.gguf"]
    with pytest.raises(ValueError, match="unavailable"):
        models.search_hub_ggufs("demo/model-GGUF:gguf/missing.gguf", token=False)
    basename = models.search_hub_ggufs("demo/model-GGUF:missing.gguf", token=False)
    assert [row["path"] for row in basename["results"]] == ["other/missing.gguf"]
