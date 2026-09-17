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
    yield home
    models._reset_download_state()


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
