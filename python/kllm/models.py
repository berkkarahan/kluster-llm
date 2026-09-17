from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from huggingface_hub import hf_hub_download

from kllm.state import models_dir


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name)[:180]


def download_model(source: str, dest_name: str | None = None) -> Path:
    """Download a GGUF from Hugging Face repo id, HF URL, or direct HTTP(S) URL."""
    models_dir().mkdir(parents=True, exist_ok=True)

    if source.startswith("http://") or source.startswith("https://"):
        return _download_url(source, dest_name)

    # repo_id or repo_id:filename
    if ":" in source and not source.startswith("http"):
        repo_id, filename = source.split(":", 1)
        path = hf_hub_download(repo_id=repo_id, filename=filename)
        target = models_dir() / (dest_name or Path(filename).name)
        if Path(path).resolve() != target.resolve():
            target.write_bytes(Path(path).read_bytes())
        return target

    raise ValueError(
        "source must be an HTTP(S) URL or 'org/repo:filename.gguf' Hugging Face ref"
    )


def _download_url(url: str, dest_name: str | None) -> Path:
    parsed = urlparse(url)
    name = dest_name or Path(parsed.path).name or "model.gguf"
    target = models_dir() / _safe_name(name)
    with httpx.stream("GET", url, follow_redirects=True, timeout=None) as resp:
        resp.raise_for_status()
        with target.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    return target


def resolve_local_model(name: str) -> Path:
    path = Path(name)
    if path.is_file():
        return path
    candidate = models_dir() / name
    if candidate.is_file():
        return candidate
    matches = list(models_dir().glob(f"*{name}*"))
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"model not found: {name}")


def list_local_models() -> list[dict]:
    """Return local GGUF files under ~/.kllm/models."""
    out: list[dict] = []
    root = models_dir()
    for path in sorted(root.glob("*.gguf")):
        try:
            st = path.stat()
        except OSError:
            continue
        out.append(
            {
                "id": path.name,
                "object": "model",
                "owned_by": "kllm",
                "filename": path.name,
                "path": str(path),
                "size_bytes": st.st_size,
            }
        )
    return out


def push_model_to_worker(
    host: str,
    port: int,
    join_key: str,
    model_path: Path,
    timeout: float = 3600.0,
) -> dict:
    digest = sha256_file(model_path)
    url = f"http://{host}:{port}/v1/models/receive"
    headers = {
        "X-Kllm-Key": join_key,
        "X-Kllm-Filename": model_path.name,
        "X-Kllm-Sha256": digest,
        "X-Kllm-Size": str(model_path.stat().st_size),
        "Content-Type": "application/octet-stream",
    }
    with model_path.open("rb") as f:
        with httpx.Client(timeout=timeout) as client:
            resp = client.put(url, content=f, headers=headers)
            resp.raise_for_status()
            return resp.json()
