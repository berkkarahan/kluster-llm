from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kllm import hf_token, models


@pytest.fixture()
def kllm_home(tmp_path, monkeypatch):
    home = tmp_path / "kllm-home"
    monkeypatch.setenv("KLLM_HOME", str(home))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    home.mkdir()
    (home / "models").mkdir()
    models._reset_download_state()
    models._reset_distribute_state()
    models._reset_receive_state()
    yield home
    models._reset_download_state()
    models._reset_distribute_state()
    models._reset_receive_state()


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

    monkeypatch.setattr(models, "list_repo_files", fake_list)
    monkeypatch.setattr(
        models,
        "hf_hub_url",
        lambda repo_id, filename: f"https://huggingface.co/{repo_id}/resolve/main/{filename}",
    )
    stream_headers: dict = {}

    def fake_stream(*_a, **kwargs):
        stream_headers.update(kwargs.get("headers") or {})
        return _FakeResp(b"gguf")

    monkeypatch.setattr(models.httpx, "stream", fake_stream)
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
    monkeypatch.setattr(models, "list_repo_files", lambda repo_id, token=None: REPO_FILES)
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

    monkeypatch.setattr(models.httpx, "stream", fake_stream)
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
    assert models._DOWNLOAD_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(models.DownloadInProgressError):
            models.download_model("https://example.test/toy.gguf")
    finally:
        models._DOWNLOAD_LOCK.release()


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
    assert repo["kind"] == "repo"
    assert repo["repo_id"] == "orcarouter/Qwen3.8-27B-Uncensored-GGUF"
    assert repo["suffix"] == "Q4_K_M"

    named = models.parse_model_search_query("Qwen3.8-27B-Uncensored-GGUF:Q4_K_M")
    assert named["kind"] == "search"
    assert named["search_term"] == "Qwen3.8-27B-Uncensored-GGUF"
    assert named["suffix"] == "Q4_K_M"

    all_quants = models.parse_model_search_query("Qwen3.8-27B-Uncensored-GGUF")
    assert all_quants["kind"] == "search"
    assert all_quants["suffix"] is None

    url = models.parse_model_search_query(
        "https://huggingface.co/orcarouter/Qwen3.8-27B-Uncensored-GGUF/resolve/main/Qwen3.8-27B-Uncensored-Q4_K_M.gguf"
    )
    assert url["kind"] == "url"
    assert url["repo_id"] == "orcarouter/Qwen3.8-27B-Uncensored-GGUF"
    assert url["suffix"] == "Qwen3.8-27B-Uncensored-Q4_K_M.gguf"


UNSLOTH_REPO = "unsloth/Qwen3.5-122B-A10B-GGUF"
UNSLOTH_FILE = "Qwen3.5-122B-A10B-UD-IQ3_S.gguf"
UNSLOTH_SHOW_FILE_INFO = (
    f"https://huggingface.co/{UNSLOTH_REPO}?show_file_info={UNSLOTH_FILE}"
)


def test_parse_search_query_show_file_info():
    parsed = models.parse_model_search_query(UNSLOTH_SHOW_FILE_INFO)
    assert parsed["kind"] == "url"
    assert parsed["repo_id"] == UNSLOTH_REPO
    assert parsed["suffix"] == UNSLOTH_FILE

    encoded = models.parse_model_search_query(
        f"https://huggingface.co/{UNSLOTH_REPO}?show_file_info=gguf%2F{UNSLOTH_FILE}"
    )
    assert encoded["suffix"] == f"gguf/{UNSLOTH_FILE}"

    repo_only = models.parse_model_search_query(f"https://huggingface.co/{UNSLOTH_REPO}")
    assert repo_only["repo_id"] == UNSLOTH_REPO
    assert repo_only["suffix"] is None

    resolve_wins = models.parse_model_search_query(
        f"https://huggingface.co/{UNSLOTH_REPO}/resolve/main/other.Q8_0.gguf"
        f"?show_file_info={UNSLOTH_FILE}"
    )
    assert resolve_wins["suffix"] == "other.Q8_0.gguf"


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
    assert models.sum_vram_bytes([{"vram_bytes": 24_000_000_000}, {"vram_bytes": 24_000_000_000}]) == 48_000_000_000
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
    monkeypatch.setattr(models, "list_models", _fake_hub_hits)
    monkeypatch.setattr(models, "model_info", _fake_orca_info)
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
    monkeypatch.setattr(models, "list_models", _fake_hub_hits)
    monkeypatch.setattr(models, "model_info", _fake_orca_info)
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

    monkeypatch.setattr(models, "list_models", boom)
    monkeypatch.setattr(models, "model_info", _fake_orca_info)
    out = models.search_hub_ggufs(f"{ORCA_REPO}:Q4_K_M", token=False)
    assert len(out["results"]) == 1
    assert out["results"][0]["repo_id"] == ORCA_REPO


def test_search_url_uses_content_length(monkeypatch):
    monkeypatch.setattr(models, "_url_size_bytes", lambda url, headers=None: 12345)
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

    monkeypatch.setattr(models, "list_models", boom)
    monkeypatch.setattr(models, "model_info", fake_info)
    out = models.search_hub_ggufs(UNSLOTH_SHOW_FILE_INFO, token=False)
    assert len(out["results"]) == 1
    row = out["results"][0]
    assert row["repo_id"] == UNSLOTH_REPO
    assert row["filename"] == UNSLOTH_FILE
    assert row["source"] == f"{UNSLOTH_REPO}:{UNSLOTH_FILE}"
    assert row["size_bytes"] == 48_000_000_000
    assert row["quant"] == "IQ3_S"


def test_download_show_file_info_url_uses_hf_hub_url(kllm_home, monkeypatch):
    streamed: list[str] = []
    payload = b"gguf-show-file-info"

    def fake_hub_url(repo_id, filename):
        return f"https://huggingface.co/{repo_id}/resolve/main/{filename}"

    def fake_stream(method, url, **_kwargs):
        streamed.append(url)
        return _FakeResp(payload)

    monkeypatch.setattr(models, "hf_hub_url", fake_hub_url)
    monkeypatch.setattr(models.httpx, "stream", fake_stream)
    path = models.download_model(UNSLOTH_SHOW_FILE_INFO)
    assert path.name == UNSLOTH_FILE
    assert path.read_bytes() == payload
    assert streamed == [f"https://huggingface.co/{UNSLOTH_REPO}/resolve/main/{UNSLOTH_FILE}"]


def test_download_writes_sha256_sidecar(kllm_home, monkeypatch):
    payload = b"gguf-sidecar-bytes"

    def fake_stream(*_args, **_kwargs):
        return _FakeResp(payload)

    monkeypatch.setattr(models.httpx, "stream", fake_stream)
    path = models.download_model("https://example.test/sidecar.gguf")
    assert models.read_sha256_sidecar(path) == models.sha256_file(path)


def test_distribute_in_progress_rejected(kllm_home):
    path = kllm_home / "models" / "toy.gguf"
    path.write_bytes(b"abc")
    assert models._DISTRIBUTE_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(models.DistributeInProgressError):
            models.distribute_to_workers(path, [], "key")
    finally:
        models._DISTRIBUTE_LOCK.release()


def test_distribute_skip_when_worker_matches(kllm_home, monkeypatch):
    path = kllm_home / "models" / "toy.gguf"
    payload = b"hello-distribute"
    path.write_bytes(payload)
    digest = models.ensure_sha256(path)
    pull_calls: list[object] = []

    def fake_probe(*_a, **_k):
        return {
            "exists": True,
            "filename": path.name,
            "size_bytes": len(payload),
            "sha256": digest,
            "path": str(path),
        }

    def fake_pull(*_a, **_k):
        pull_calls.append(1)
        raise AssertionError("pull should be skipped")

    monkeypatch.setattr(models, "probe_model_on_worker", fake_probe)
    monkeypatch.setattr(models, "request_worker_pull", fake_pull)
    workers = [{"node_id": "w1", "host": "10.0.0.2", "role": "worker", "control_port": 18765}]

    results = models.distribute_to_workers(path, workers, "secret")
    assert results == [
        {
            "host": "10.0.0.2",
            "node_id": "w1",
            "ok": True,
            "result": {
                "ok": True,
                "skipped": True,
                "path": str(path),
                "sha256": digest,
            },
        }
    ]
    assert pull_calls == []
    assert models.get_distribute_progress() is None


def test_distribute_updates_progress_during_pull(kllm_home, monkeypatch):
    path = kllm_home / "models" / "push.gguf"
    payload = b"x" * 100
    path.write_bytes(payload)
    snapshots: list[dict] = []

    def fake_probe(*_a, **_k):
        return {"exists": False, "filename": path.name}

    def fake_pull(*_a, **_k):
        snap = models.get_distribute_progress()
        if snap:
            snapshots.append(snap)
        return {"ok": True, "path": "/remote/push.gguf", "sha256": "deadbeef"}

    monkeypatch.setattr(models, "probe_model_on_worker", fake_probe)
    monkeypatch.setattr(models, "request_worker_pull", fake_pull)
    workers = [
        {"node_id": "a", "lan_host": "192.168.1.2", "role": "worker", "control_port": 18765},
        {"node_id": "b", "host": "192.168.1.3", "role": "worker", "control_port": 18765},
    ]
    results = models.distribute_to_workers(
        path,
        workers,
        "key",
        master_host="192.168.1.1",
        master_port=18765,
    )
    assert len(results) == 2
    assert all(r["ok"] for r in results)
    assert any(
        (s.get("workers") or [{}])[0].get("phase") == "pulling" for s in snapshots
    )
    assert models.get_distribute_progress() is None


def test_distribute_tree_second_worker_pulls_from_first(kllm_home, monkeypatch):
    path = kllm_home / "models" / "tree.gguf"
    payload = b"tree-payload"
    path.write_bytes(payload)
    pulls: list[tuple[str, str, int]] = []

    def fake_probe(*_a, **_k):
        return {"exists": False}

    def fake_pull(host, port, join_key, **kwargs):
        pulls.append((host, kwargs["source_host"], int(kwargs["source_port"])))
        return {"ok": True, "path": f"/{host}", "sha256": kwargs["digest"]}

    monkeypatch.setattr(models, "probe_model_on_worker", fake_probe)
    monkeypatch.setattr(models, "request_worker_pull", fake_pull)
    workers = [
        {"node_id": "a", "lan_host": "192.168.1.2", "role": "worker", "control_port": 18765},
        {"node_id": "b", "lan_host": "192.168.1.3", "role": "worker", "control_port": 18765},
    ]
    results = models.distribute_to_workers(
        path,
        workers,
        "key",
        master_host="192.168.1.1",
        master_port=18765,
    )
    assert all(r["ok"] for r in results)
    assert pulls[0] == ("192.168.1.2", "192.168.1.1", 18765)
    assert pulls[1] == ("192.168.1.3", "192.168.1.2", 18765)
    assert results[1]["source"] == {"host": "192.168.1.2", "port": 18765}


def test_parse_bytes_range_and_unsatisfiable():
    assert models.parse_bytes_range(None, 10) == (0, 10, False)
    assert models.parse_bytes_range("bytes=2-5", 10) == (2, 6, True)
    assert models.parse_bytes_range("bytes=8-", 10) == (8, 10, True)
    assert models.parse_bytes_range("bytes=-3", 10) == (7, 10, True)
    with pytest.raises(models.RangeUnsatisfiable):
        models.parse_bytes_range("bytes=10-12", 10)
    with pytest.raises(ValueError, match="multiple"):
        models.parse_bytes_range("bytes=0-1,2-3", 10)


def test_iter_file_range_slice(kllm_home):
    path = kllm_home / "models" / "slice.gguf"
    path.write_bytes(b"abcdefghij")
    assert b"".join(models.iter_file_range(path, 2, 6, chunk=3)) == b"cdef"


def _range_bounds(header: str) -> tuple[int, int]:
    spec = header.split("=", 1)[1]
    start_s, _, end_s = spec.partition("-")
    return int(start_s), int(end_s) + 1


class _FakeStreamResp:
    def __init__(self, data: bytes, status_code: int = 206):
        self._data = data
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def iter_bytes(self, chunk_size=8192):
        yield self._data


def _fake_seed_client(payload: bytes, ranges: list[str]):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stream(self, method, url, headers=None):
            rh = (headers or {}).get("Range") or ""
            ranges.append(rh)
            start, end = _range_bounds(rh)
            response = _FakeStreamResp(payload[start:end])
            response.headers = {"Content-Range": f"bytes {start}-{end-1}/{len(payload)}", "Content-Length": str(end-start)}
            return response

    return FakeClient


def test_pull_from_seed_assembles_parallel_ranges(kllm_home, monkeypatch):
    payload = b"0123456789abcdef"  # 16 bytes, 4 pieces of 4
    digest = __import__("hashlib").sha256(payload).hexdigest()
    ranges: list[str] = []
    monkeypatch.setattr(models.httpx, "Client", _fake_seed_client(payload, ranges))

    out = models.pull_from_seed(
        "assembled.gguf",
        len(payload),
        digest,
        "10.0.0.1",
        18765,
        "secret",
        connections=4,
        piece_size=4,
    )
    dest = kllm_home / "models" / "assembled.gguf"
    assert dest.read_bytes() == payload
    assert out["sha256"] == digest
    assert models.read_sha256_sidecar(dest) == digest
    assert models.get_receive_progress() is None
    assert len(ranges) == 4
    assert not (kllm_home / "models" / "assembled.gguf.partial").exists()
    assert not (kllm_home / "models" / "assembled.gguf.partial.bitmap").exists()


def test_pull_from_seed_resumes_missing_pieces(kllm_home, monkeypatch):
    payload = b"ABCDEFGHIJKLMNOPQRSTUVWX"  # 24 bytes, 3 pieces of 8
    digest = __import__("hashlib").sha256(payload).hexdigest()
    dest = kllm_home / "models" / "resume.gguf"
    tmp = dest.with_suffix(dest.suffix + ".partial")
    tmp.write_bytes(b"\x00" * len(payload))
    with tmp.open("r+b") as f:
        f.seek(8)
        f.write(payload[8:16])
    bitmap = models.empty_piece_bitmap(3)
    models.piece_bit_set(bitmap, 1)
    models.save_piece_bitmap(models.bitmap_path_for_partial(tmp), bitmap)
    models.atomic_json(tmp.with_name(tmp.name + ".resume.json"), {
        "binding": {"sha256": digest, "size": len(payload), "piece_size": 8, "pieces": None},
        "done": {"1": __import__("hashlib").sha256(payload[8:16]).hexdigest()},
    })

    ranges: list[str] = []
    monkeypatch.setattr(models.httpx, "Client", _fake_seed_client(payload, ranges))

    models.pull_from_seed(
        "resume.gguf",
        len(payload),
        digest,
        "10.0.0.1",
        18765,
        "secret",
        connections=2,
        piece_size=8,
    )
    assert dest.read_bytes() == payload
    requested = {_range_bounds(r) for r in ranges}
    assert requested == {(0, 8), (16, 24)}


def test_pull_from_seed_keeps_partial_on_drop(kllm_home, monkeypatch):
    payload = b"keep-partial-bytes!!"  # 20 bytes
    digest = __import__("hashlib").sha256(payload).hexdigest()

    class BoomClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stream(self, *a, **k):
            raise ConnectionError("seed dropped")

    monkeypatch.setattr(models.httpx, "Client", BoomClient)
    with pytest.raises(ConnectionError, match="seed dropped"):
        models.pull_from_seed(
            "keep.gguf",
            len(payload),
            digest,
            "10.0.0.1",
            18765,
            "secret",
            connections=1,
            piece_size=10,
        )
    tmp = kllm_home / "models" / "keep.gguf.partial"
    assert tmp.exists()
    assert models.get_receive_progress() is None
    assert not models._RECEIVE_LOCK.locked()


def test_receive_progress_and_sidecar(kllm_home):
    payload = b"receive-me-please"
    digest = __import__("hashlib").sha256(payload).hexdigest()
    tmp = models.begin_receive("recv.gguf", len(payload))
    assert models.get_receive_progress()["pct"] == 0
    throttle = models._Throttle(interval=0)
    with tmp.open("wb") as f:
        f.write(payload)
    models.finish_receive_chunk(
        filename="recv.gguf", nbytes=len(payload), total=len(payload), throttle=throttle
    )
    assert models.get_receive_progress()["pct"] == 100
    dest = kllm_home / "models" / "recv.gguf"
    out = models.complete_receive(tmp, dest, digest=digest, size=len(payload))
    assert out["ok"] is True
    assert dest.read_bytes() == payload
    assert models.read_sha256_sidecar(dest) == digest
    assert models.get_receive_progress() is None


def test_receive_in_progress_rejected(kllm_home):
    models.begin_receive("a.gguf", 10)
    try:
        with pytest.raises(models.ReceiveInProgressError):
            models.begin_receive("b.gguf", 10)
    finally:
        models.abort_receive()

