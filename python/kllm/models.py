from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from huggingface_hub import hf_hub_url, list_models, list_repo_files, model_info
from huggingface_hub.utils import build_hf_headers

from kllm.gguf_meta import read_context_length
from kllm.hf_token import resolve_hf_token
from kllm.state import models_dir

ProgressCallback = Callable[[dict[str, Any]], None]

_SHARD_RE = re.compile(r"-\d{5}-of-", re.IGNORECASE)
_BARE_QUANT_RE = re.compile(
    r"^[A-Za-z]{0,3}\d+[A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)*$",
    re.IGNORECASE,
)
# Distinct tokens like .Q4_K_M. / -Q4_K_M / _Q4_K_M
_QUANT_TOKEN_RE = re.compile(
    r"(?:^|[._-])((?:IQ|Q|BF|F)\d+(?:[_-][A-Za-z0-9]+)*)(?=[._-]|$)",
    re.IGNORECASE,
)

_DOWNLOAD_LOCK = threading.Lock()
_DOWNLOAD_PROGRESS: dict[str, Any] | None = None

_DISTRIBUTE_LOCK = threading.Lock()
_DISTRIBUTE_PROGRESS: dict[str, Any] | None = None
_DISTRIBUTE_PROGRESS_LOCK = threading.Lock()

_RECEIVE_LOCK = threading.Lock()
_RECEIVE_PROGRESS: dict[str, Any] | None = None

_PROGRESS_MIN_INTERVAL = 0.1
_TRANSFER_CHUNK = 1024 * 1024
_BLOB_CHUNK = 8 * 1024 * 1024
_PIECE_SIZE = 8 * 1024 * 1024
_PULL_CONNECTIONS = 4
_PULL_MAX_ATTEMPTS = 3
_PULL_POLL_INTERVAL = 0.25

SEARCH_HUB_LIMIT = 20
SEARCH_REPO_CAP = 8
SEARCH_FILE_CAP = 40


class DownloadInProgressError(RuntimeError):
    """Raised when a second download is requested while one is active."""


class DistributeInProgressError(RuntimeError):
    """Raised when a second distribute is requested while one is active."""


class ReceiveInProgressError(RuntimeError):
    """Raised when a second receive is requested while one is active."""


class RangeUnsatisfiable(ValueError):
    """HTTP Range cannot be satisfied for this file size."""


def sha256_file(path: Path, chunk: int = _TRANSFER_CHUNK) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_sidecar_path(model_path: Path) -> Path:
    return model_path.with_name(model_path.name + ".sha256")


def write_sha256_sidecar(model_path: Path, digest: str) -> None:
    sha256_sidecar_path(model_path).write_text(digest.strip() + "\n", encoding="utf-8")


def read_sha256_sidecar(model_path: Path) -> str | None:
    side = sha256_sidecar_path(model_path)
    if not side.is_file():
        return None
    try:
        text = side.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text.lower()):
        return None
    return text.lower()


def local_model_meta(model_path: Path) -> dict[str, Any]:
    """Size + sidecar digest for skip-if-identical probes (does not hash)."""
    st = model_path.stat()
    return {
        "exists": True,
        "filename": model_path.name,
        "size_bytes": st.st_size,
        "sha256": read_sha256_sidecar(model_path),
    }


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name)[:180]


def _pct(nbytes: int, total: int | None) -> int:
    if not total:
        return 0
    return min(100, int(nbytes * 100 / total))


class _Throttle:
    """Rate-limit progress snapshots (~4–10 Hz) unless forced."""

    def __init__(self, interval: float = _PROGRESS_MIN_INTERVAL) -> None:
        self.interval = interval
        self._last = 0.0

    def allow(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        if force or (now - self._last) >= self.interval:
            self._last = now
            return True
        return False


def get_download_progress() -> dict[str, Any] | None:
    """In-memory snapshot, or None when idle. Not persisted."""
    snap = _DOWNLOAD_PROGRESS
    return dict(snap) if snap is not None else None


def get_distribute_progress() -> dict[str, Any] | None:
    with _DISTRIBUTE_PROGRESS_LOCK:
        snap = _DISTRIBUTE_PROGRESS
        if snap is None:
            return None
        out = dict(snap)
        workers = snap.get("workers")
        if isinstance(workers, list):
            out["workers"] = [dict(w) for w in workers]
        return out


def get_receive_progress() -> dict[str, Any] | None:
    snap = _RECEIVE_PROGRESS
    return dict(snap) if snap is not None else None


def _reset_download_state() -> None:
    global _DOWNLOAD_PROGRESS
    _DOWNLOAD_PROGRESS = None
    if _DOWNLOAD_LOCK.locked():
        try:
            _DOWNLOAD_LOCK.release()
        except RuntimeError:
            pass


def _reset_distribute_state() -> None:
    global _DISTRIBUTE_PROGRESS
    with _DISTRIBUTE_PROGRESS_LOCK:
        _DISTRIBUTE_PROGRESS = None
    if _DISTRIBUTE_LOCK.locked():
        try:
            _DISTRIBUTE_LOCK.release()
        except RuntimeError:
            pass


def _reset_receive_state() -> None:
    global _RECEIVE_PROGRESS
    _RECEIVE_PROGRESS = None
    if _RECEIVE_LOCK.locked():
        try:
            _RECEIVE_LOCK.release()
        except RuntimeError:
            pass


def _set_progress(
    filename: str,
    nbytes: int,
    total: int | None,
    on_progress: ProgressCallback | None = None,
) -> None:
    global _DOWNLOAD_PROGRESS
    snap = {
        "filename": filename,
        "bytes": nbytes,
        "total": total,
        "pct": _pct(nbytes, total),
    }
    _DOWNLOAD_PROGRESS = snap
    if on_progress is not None:
        on_progress(dict(snap))


def _set_receive_progress(
    filename: str,
    nbytes: int,
    total: int | None,
    *,
    phase: str = "receiving",
) -> None:
    global _RECEIVE_PROGRESS
    _RECEIVE_PROGRESS = {
        "filename": filename,
        "bytes": nbytes,
        "total": total,
        "pct": _pct(nbytes, total),
        "phase": phase,
    }


def _clear_receive_progress() -> None:
    global _RECEIVE_PROGRESS
    _RECEIVE_PROGRESS = None


def _init_distribute_progress(filename: str, total: int) -> None:
    global _DISTRIBUTE_PROGRESS
    with _DISTRIBUTE_PROGRESS_LOCK:
        _DISTRIBUTE_PROGRESS = {
            "filename": filename,
            "phase": "hashing",
            "bytes": 0,
            "total": total,
            "pct": 0,
            "workers": [],
        }


def _update_distribute_hash(nbytes: int, total: int) -> None:
    with _DISTRIBUTE_PROGRESS_LOCK:
        snap = _DISTRIBUTE_PROGRESS
        if snap is None:
            return
        snap["phase"] = "hashing"
        snap["bytes"] = nbytes
        snap["total"] = total
        snap["pct"] = _pct(nbytes, total)


def _set_distribute_workers(workers: list[dict[str, Any]]) -> None:
    with _DISTRIBUTE_PROGRESS_LOCK:
        snap = _DISTRIBUTE_PROGRESS
        if snap is None:
            return
        snap["phase"] = "pulling"
        snap["workers"] = workers
        snap["pct"] = _mean_worker_pct(workers)


def _update_worker_progress(
    index: int,
    *,
    phase: str | None = None,
    nbytes: int | None = None,
    total: int | None = None,
    error: str | None = None,
    force: bool = False,
    throttle: _Throttle | None = None,
) -> None:
    if throttle is not None and not throttle.allow(force=force or phase in ("done", "skipped", "error")):
        return
    with _DISTRIBUTE_PROGRESS_LOCK:
        snap = _DISTRIBUTE_PROGRESS
        if snap is None:
            return
        workers = snap.get("workers")
        if not isinstance(workers, list) or index < 0 or index >= len(workers):
            return
        row = workers[index]
        if phase is not None:
            row["phase"] = phase
        if total is not None:
            row["total"] = total
        if nbytes is not None:
            row["bytes"] = nbytes
            row["pct"] = _pct(nbytes, row.get("total"))
        if phase in ("done", "skipped"):
            row["pct"] = 100
            if row.get("total"):
                row["bytes"] = row["total"]
        if error is not None:
            row["error"] = error
        if phase == "error":
            row["pct"] = row.get("pct") or 0
        snap["pct"] = _mean_worker_pct(workers)


def _mean_worker_pct(workers: list[dict[str, Any]]) -> int:
    if not workers:
        return 0
    return int(sum(int(w.get("pct") or 0) for w in workers) / len(workers))


def _clear_distribute_progress() -> None:
    global _DISTRIBUTE_PROGRESS
    with _DISTRIBUTE_PROGRESS_LOCK:
        _DISTRIBUTE_PROGRESS = None


def normalize_quant(value: str) -> str:
    """Strip separators and optional .gguf so Q4_K_M / Q_4_K_M / q4-k-m match."""
    s = value.strip()
    if s.lower().endswith(".gguf"):
        s = s[:-5]
    return re.sub(r"[^A-Za-z0-9]+", "", s).upper()


def looks_like_quant_tag(value: str) -> bool:
    s = value.strip()
    if s.lower().endswith(".gguf"):
        s = s[:-5]
    if not s or "/" in s or "\\" in s or "." in s:
        return False
    return bool(_BARE_QUANT_RE.fullmatch(s))


def is_exact_hf_filename(suffix: str) -> bool:
    """True for Hub paths and *.gguf filenames that are not a bare quant tag."""
    s = suffix.strip()
    if not s:
        return False
    if "/" in s or "\\" in s:
        return True
    if s.lower().endswith(".gguf"):
        return not looks_like_quant_tag(s[:-5])
    return False


def _is_skipped_gguf(name: str) -> bool:
    lower = name.lower()
    if "mmproj" in lower or "imatrix" in lower:
        return True
    if _SHARD_RE.search(name):
        return True
    return False


def quant_tokens_in_name(filename: str) -> list[str]:
    name = Path(filename).name
    if name.lower().endswith(".gguf"):
        name = name[:-5]
    return [m.group(1) for m in _QUANT_TOKEN_RE.finditer(name)]


def _candidate_ggufs(files: list[str]) -> list[str]:
    out: list[str] = []
    for rel in files:
        if not rel.lower().endswith(".gguf"):
            continue
        if _is_skipped_gguf(rel):
            continue
        out.append(rel)
    return out


def _available_quants(files: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for rel in _candidate_ggufs(files):
        for token in quant_tokens_in_name(rel):
            key = normalize_quant(token)
            if key and key not in seen:
                seen[key] = token
    return [seen[k] for k in sorted(seen)]


def match_gguf_for_quant(files: list[str], quant: str) -> list[str]:
    target = normalize_quant(quant)
    if not target:
        return []
    matches: list[str] = []
    for rel in _candidate_ggufs(files):
        for token in quant_tokens_in_name(rel):
            if normalize_quant(token) == target:
                matches.append(rel)
                break
    return matches


def select_gguf_for_quant(files: list[str], quant: str, *, repo_id: str = "org/repo") -> str:
    matches = match_gguf_for_quant(files, quant)
    if not matches:
        available = _available_quants(files)
        listed = ", ".join(available) if available else "(none)"
        raise ValueError(
            f"no GGUF files matching quant {quant!r} in {repo_id}. Available quants: {listed}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"multiple GGUF files match quant {quant!r} in {repo_id}: {', '.join(matches)}"
        )
    return matches[0]


def resolve_hf_filename(repo_id: str, suffix: str, *, token: str | None = None) -> str:
    """Exact Hub path/*.gguf filename, or unique file matching a quant tag."""
    suffix = suffix.strip()
    if not suffix:
        raise ValueError("Hugging Face ref is missing a filename or quant tag after ':'")
    if is_exact_hf_filename(suffix):
        return suffix
    files = list_repo_files(repo_id, token=token if token else False)
    return select_gguf_for_quant(files, suffix, repo_id=repo_id)


def parse_model_search_query(query: str) -> dict[str, Any]:
    """Split a Models search box into url / repo / Hub-search parts."""
    raw = query.strip()
    if not raw:
        raise ValueError("query is empty")
    if raw.startswith("http://") or raw.startswith("https://"):
        repo_id, filename = (None, None)
        if _is_hf_host(raw):
            repo_id, filename = _parse_hf_url(raw)
        return {
            "raw": raw,
            "kind": "url",
            "url": raw,
            "repo_id": repo_id,
            "search_term": None,
            "suffix": filename,
        }
    suffix = None
    left = raw
    if ":" in raw:
        left, suffix = raw.split(":", 1)
        left = left.strip()
        suffix = suffix.strip() or None
    if not left:
        raise ValueError("query is empty")
    if "/" in left:
        return {
            "raw": raw,
            "kind": "repo",
            "url": None,
            "repo_id": left,
            "search_term": None,
            "suffix": suffix,
        }
    return {
        "raw": raw,
        "kind": "search",
        "url": None,
        "repo_id": None,
        "search_term": left,
        "suffix": suffix,
    }


def rank_hub_repo_ids(repo_ids: list[str], search_term: str) -> list[str]:
    """Exact repo-name matches first (case-insensitive), preserving relative order."""
    needle = search_term.strip().lower()
    exact: list[str] = []
    rest: list[str] = []
    seen: set[str] = set()
    for repo_id in repo_ids:
        if not repo_id or repo_id in seen:
            continue
        seen.add(repo_id)
        name = repo_id.rsplit("/", 1)[-1]
        if name.lower() == needle:
            exact.append(repo_id)
        else:
            rest.append(repo_id)
    return exact + rest


def sum_vram_bytes(entries: Any) -> int | None:
    """Sum ``vram_bytes`` over mappings. ``None`` when no positive value is known."""
    total = 0
    known = False
    if entries is None:
        return None
    for entry in entries:
        if isinstance(entry, dict):
            raw = entry.get("vram_bytes")
        else:
            raw = getattr(entry, "vram_bytes", None)
        if raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            total += n
            known = True
    return total if known else None


def vram_fit(size_bytes: int | None, total_vram_bytes: int | None) -> str:
    """Weights-vs-total-VRAM heuristic: fits / overflow / unknown."""
    if total_vram_bytes is None or total_vram_bytes <= 0:
        return "unknown"
    if size_bytes is None or size_bytes <= 0:
        return "unknown"
    return "fits" if size_bytes <= total_vram_bytes else "overflow"


def _hf_show_file_info(query: str) -> str | None:
    values = parse_qs(query, keep_blank_values=False).get("show_file_info") or []
    if not values:
        return None
    name = unquote(values[0]).strip()
    return name or None


def _parse_hf_url(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url)
    parts = [unquote(p) for p in parsed.path.strip("/").split("/") if p]
    if parts and parts[0] in {"models", "model"}:
        parts = parts[1:]
    if len(parts) < 2:
        return None, None
    repo_id = f"{parts[0]}/{parts[1]}"
    rest = parts[2:]
    filename = None
    if rest and rest[0] in {"blob", "resolve", "raw"}:
        file_parts = rest[2:] if len(rest) > 2 else []
        filename = "/".join(file_parts) if file_parts else None
    if not filename:
        filename = _hf_show_file_info(parsed.query)
    return repo_id, filename


def _quant_label(filename: str, suffix: str | None) -> str | None:
    tokens = quant_tokens_in_name(filename)
    if suffix and looks_like_quant_tag(suffix):
        target = normalize_quant(suffix)
        for token in tokens:
            if normalize_quant(token) == target:
                return token
    return tokens[-1] if tokens else None


def _files_for_suffix(files: list[str], suffix: str | None) -> list[str]:
    if not suffix:
        return _candidate_ggufs(files)
    if is_exact_hf_filename(suffix):
        if suffix in files:
            return [suffix]
        base = Path(suffix).name.lower()
        matches = [rel for rel in files if Path(rel).name.lower() == base]
        return matches or [suffix]
    return match_gguf_for_quant(files, suffix)


def _sibling_size(sibling: Any) -> int:
    raw = getattr(sibling, "size", None)
    if raw is None and isinstance(sibling, dict):
        raw = sibling.get("size")
    try:
        return int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0


def _hit_row(
    repo_id: str,
    filename: str,
    *,
    size_bytes: int,
    gated: bool,
    suffix: str | None,
    source: str | None = None,
) -> dict[str, Any]:
    rel = filename
    return {
        "repo_id": repo_id,
        "filename": Path(rel).name,
        "quant": _quant_label(rel, suffix),
        "size_bytes": size_bytes,
        "gated": gated,
        "source": source or f"{repo_id}:{rel}",
    }


def _inspect_repo_ggufs(
    repo_id: str,
    *,
    suffix: str | None,
    token: str | None,
) -> list[dict[str, Any]]:
    try:
        info = model_info(repo_id, files_metadata=True, token=token if token else False)
    except Exception:
        return []
    gated = bool(getattr(info, "gated", False))
    siblings = list(getattr(info, "siblings", None) or [])
    files: list[str] = []
    sizes: dict[str, int] = {}
    for sibling in siblings:
        rel = getattr(sibling, "rfilename", None)
        if not rel and isinstance(sibling, dict):
            rel = sibling.get("rfilename")
        if not rel:
            continue
        files.append(rel)
        sizes[rel] = _sibling_size(sibling)
    rows: list[dict[str, Any]] = []
    for rel in _files_for_suffix(files, suffix):
        rows.append(
            _hit_row(
                repo_id,
                rel,
                size_bytes=sizes.get(rel, 0),
                gated=gated,
                suffix=suffix,
            )
        )
    return rows


def _url_size_bytes(url: str, *, headers: dict[str, str] | None = None) -> int | None:
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            resp = client.head(url, headers=headers or {})
            length = _content_length(resp.headers)
            return length if length > 0 else None
    except Exception:
        return None


def _synthetic_url_hit(url: str, *, token: str | None, parsed: dict[str, Any]) -> dict[str, Any]:
    headers = _hub_headers(token) if _is_hf_host(url) else {}
    size = _url_size_bytes(url, headers=headers) or 0
    repo_id = parsed.get("repo_id") or urlparse(url).netloc
    suffix = parsed.get("suffix")
    filename = Path(suffix).name if suffix else (Path(urlparse(url).path).name or "model.gguf")
    source = f"{parsed['repo_id']}:{suffix}" if parsed.get("repo_id") and suffix else url
    return _hit_row(
        str(repo_id),
        suffix or filename,
        size_bytes=size,
        gated=False,
        suffix=suffix,
        source=source,
    )


def _hub_search_repo_ids(search_term: str, *, token: str | None) -> list[str]:
    hits = list(
        list_models(
            search=search_term,
            filter="gguf",
            limit=SEARCH_HUB_LIMIT,
            token=token if token else False,
        )
    )
    ids: list[str] = []
    for hit in hits:
        repo_id = getattr(hit, "id", None)
        if not repo_id and isinstance(hit, dict):
            repo_id = hit.get("id")
        if repo_id:
            ids.append(str(repo_id))
    return rank_hub_repo_ids(ids, search_term)


def search_hub_ggufs(
    query: str,
    *,
    total_vram_bytes: int | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Resolve a search box query to GGUF hits with size and VRAM fit."""
    parsed = parse_model_search_query(query)
    if token is None:
        token = resolve_hf_token()
    results: list[dict[str, Any]] = []
    kind = parsed["kind"]
    if kind == "url":
        if parsed.get("repo_id") and not parsed.get("suffix"):
            results = _inspect_repo_ggufs(parsed["repo_id"], suffix=None, token=token)
        elif parsed.get("repo_id") and parsed.get("suffix"):
            results = _inspect_repo_ggufs(
                parsed["repo_id"], suffix=parsed.get("suffix"), token=token
            )
            if not results:
                results = [_synthetic_url_hit(parsed["url"], token=token, parsed=parsed)]
        else:
            results = [_synthetic_url_hit(parsed["url"], token=token, parsed=parsed)]
    elif kind == "repo":
        results = _inspect_repo_ggufs(
            parsed["repo_id"], suffix=parsed.get("suffix"), token=token
        )
    else:
        repo_ids = _hub_search_repo_ids(parsed["search_term"], token=token)
        for repo_id in repo_ids[:SEARCH_REPO_CAP]:
            rows = _inspect_repo_ggufs(
                repo_id, suffix=parsed.get("suffix"), token=token
            )
            results.extend(rows)
            if len(results) >= SEARCH_FILE_CAP:
                break
        results = results[:SEARCH_FILE_CAP]
    for row in results:
        row["fit"] = vram_fit(row.get("size_bytes"), total_vram_bytes)
    return {
        "query": parsed["raw"],
        "total_vram_bytes": total_vram_bytes,
        "results": results,
    }


def _is_hf_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return (
        host == "huggingface.co"
        or host.endswith(".huggingface.co")
        or host == "hf.co"
        or host.endswith(".hf.co")
    )


def _hub_headers(token: str | None) -> dict[str, str]:
    return build_hf_headers(token=token if token else False)


def _content_length(headers: Any) -> int:
    raw = None
    try:
        raw = headers.get("Content-Length")
    except Exception:
        raw = None
    if raw is None and isinstance(headers, dict):
        for key, val in headers.items():
            if str(key).lower() == "content-length":
                raw = val
                break
    try:
        return int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0


def _stream_to_models(
    url: str,
    dest_name: str | None,
    *,
    headers: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    parsed = urlparse(url)
    name = dest_name or Path(parsed.path).name or "model.gguf"
    filename = _safe_name(name)
    target = models_dir() / filename
    part = target.with_name(target.name + ".part")
    nbytes = 0
    hasher = hashlib.sha256()
    try:
        with httpx.stream(
            "GET", url, headers=headers or {}, follow_redirects=True, timeout=None
        ) as resp:
            resp.raise_for_status()
            length = _content_length(resp.headers)
            total = length if length > 0 else None
            _set_progress(filename, 0, total, on_progress)
            with part.open("wb") as f:
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    f.write(chunk)
                    hasher.update(chunk)
                    nbytes += len(chunk)
                    _set_progress(filename, nbytes, total, on_progress)
        part.replace(target)
        write_sha256_sidecar(target, hasher.hexdigest())
    except Exception:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    return target


def download_model(
    source: str,
    dest_name: str | None = None,
    *,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Download a GGUF from Hugging Face repo id, HF URL, or direct HTTP(S) URL."""
    if not _DOWNLOAD_LOCK.acquire(blocking=False):
        raise DownloadInProgressError("a download is already in progress")
    global _DOWNLOAD_PROGRESS
    try:
        models_dir().mkdir(parents=True, exist_ok=True)
        token = resolve_hf_token()

        if source.startswith("http://") or source.startswith("https://"):
            if _is_hf_host(source):
                repo_id, filename = _parse_hf_url(source)
                if repo_id and filename:
                    url = hf_hub_url(repo_id, filename)
                    name = dest_name or Path(filename).name
                    return _stream_to_models(
                        url,
                        name,
                        headers=_hub_headers(token),
                        on_progress=on_progress,
                    )
                headers = _hub_headers(token)
            else:
                headers = {}
            return _stream_to_models(
                source, dest_name, headers=headers, on_progress=on_progress
            )

        if ":" in source:
            repo_id, suffix = source.split(":", 1)
            filename = resolve_hf_filename(repo_id, suffix, token=token)
            url = hf_hub_url(repo_id, filename)
            name = dest_name or Path(filename).name
            return _stream_to_models(
                url,
                name,
                headers=_hub_headers(token),
                on_progress=on_progress,
            )

        raise ValueError(
            "source must be an HTTP(S) URL or 'org/repo:filename.gguf' / "
            "'org/repo:Q4_K_M' Hugging Face ref"
        )
    finally:
        _DOWNLOAD_PROGRESS = None
        _DOWNLOAD_LOCK.release()


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
        max_context = read_context_length(path)
        entry: dict = {
            "id": path.name,
            "object": "model",
            "owned_by": "kllm",
            "filename": path.name,
            "path": str(path),
            "size_bytes": st.st_size,
        }
        if max_context is not None:
            entry["max_context"] = max_context
        out.append(entry)
    return out


def ensure_sha256(
    model_path: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Return digest from sidecar or hash the file (optionally with progress)."""
    cached = read_sha256_sidecar(model_path)
    if cached:
        if on_progress is not None:
            total = model_path.stat().st_size
            on_progress(total, total)
        return cached
    total = model_path.stat().st_size
    hasher = hashlib.sha256()
    nbytes = 0
    throttle = _Throttle()
    if on_progress is not None:
        on_progress(0, total)
    with model_path.open("rb") as f:
        while True:
            block = f.read(_TRANSFER_CHUNK)
            if not block:
                break
            hasher.update(block)
            nbytes += len(block)
            if on_progress is not None and throttle.allow(
                force=nbytes >= total
            ):
                on_progress(nbytes, total)
    digest = hasher.hexdigest()
    write_sha256_sidecar(model_path, digest)
    if on_progress is not None:
        on_progress(total, total)
    return digest


def piece_count(size: int, piece_size: int = _PIECE_SIZE) -> int:
    if size <= 0:
        return 0
    return (size + piece_size - 1) // piece_size


def piece_span(index: int, size: int, piece_size: int = _PIECE_SIZE) -> tuple[int, int]:
    start = index * piece_size
    end = min(start + piece_size, size)
    return start, end


def partial_path_for(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".partial")


def bitmap_path_for_partial(tmp: Path) -> Path:
    return tmp.with_name(tmp.name + ".bitmap")


def empty_piece_bitmap(n_pieces: int) -> bytearray:
    return bytearray((n_pieces + 7) // 8)


def load_piece_bitmap(path: Path, n_pieces: int) -> bytearray:
    expected = (n_pieces + 7) // 8
    if n_pieces <= 0:
        return bytearray()
    if not path.is_file():
        return empty_piece_bitmap(n_pieces)
    data = path.read_bytes()
    if len(data) != expected:
        return empty_piece_bitmap(n_pieces)
    return bytearray(data)


def save_piece_bitmap(path: Path, bitmap: bytearray) -> None:
    path.write_bytes(bytes(bitmap))


def piece_bit_get(bitmap: bytearray, index: int) -> bool:
    return bool(bitmap[index // 8] & (1 << (index % 8)))


def piece_bit_set(bitmap: bytearray, index: int) -> None:
    bitmap[index // 8] |= 1 << (index % 8)


def missing_piece_indices(bitmap: bytearray, n_pieces: int) -> list[int]:
    return [i for i in range(n_pieces) if not piece_bit_get(bitmap, i)]


def completed_piece_bytes(
    bitmap: bytearray, size: int, piece_size: int = _PIECE_SIZE
) -> int:
    total = 0
    for i in range(piece_count(size, piece_size)):
        if piece_bit_get(bitmap, i):
            start, end = piece_span(i, size, piece_size)
            total += end - start
    return total


def parse_bytes_range(header: str | None, file_size: int) -> tuple[int, int, bool]:
    """Return (start, end_exclusive, is_partial)."""
    if file_size < 0:
        raise RangeUnsatisfiable("negative file size")
    if not header or not header.strip():
        return 0, file_size, False
    raw = header.strip()
    if not raw.lower().startswith("bytes="):
        raise ValueError(f"unsupported range unit: {header}")
    spec = raw.split("=", 1)[1].strip()
    if "," in spec:
        raise ValueError("multiple ranges are not supported")
    start_s, sep, end_s = spec.partition("-")
    if not sep:
        raise ValueError(f"invalid range: {header}")
    if start_s == "":
        if not end_s:
            raise ValueError(f"invalid range: {header}")
        suffix = int(end_s)
        if suffix <= 0:
            raise RangeUnsatisfiable(header)
        start = max(0, file_size - suffix)
        return start, file_size, True
    start = int(start_s)
    end = int(end_s) + 1 if end_s else file_size
    if start < 0 or start >= file_size or end <= start:
        raise RangeUnsatisfiable(header)
    return start, min(end, file_size), True


def iter_file_range(
    path: Path,
    start: int,
    end: int,
    chunk: int = _BLOB_CHUNK,
) -> Iterator[bytes]:
    """Yield [start, end) from path in large blocks."""
    if end < start:
        return
    remaining = end - start
    with path.open("rb") as f:
        f.seek(start)
        while remaining > 0:
            block = f.read(min(chunk, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def probe_model_on_worker(
    host: str,
    port: int,
    join_key: str,
    filename: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"http://{host}:{port}/v1/models/file"
    headers = {
        "X-Kllm-Key": join_key,
        "X-Kllm-Filename": Path(filename).name,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers=headers)
        if resp.status_code == 404:
            return {"exists": False, "filename": Path(filename).name}
        resp.raise_for_status()
        return resp.json()


def _preallocate_partial(tmp: Path, size: int) -> None:
    if not tmp.exists():
        tmp.touch()
    with tmp.open("r+b") as f:
        f.truncate(size)


def _blob_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/v1/models/blob"


def request_worker_pull(
    host: str,
    port: int,
    join_key: str,
    *,
    filename: str,
    digest: str,
    size: int,
    source_host: str,
    source_port: int,
    timeout: float = 3600.0,
) -> dict[str, Any]:
    """Tell a worker to Range-GET the GGUF from one seed."""
    url = f"http://{host}:{port}/v1/models/pull"
    headers = {"X-Kllm-Key": join_key}
    body = {
        "filename": Path(filename).name,
        "sha256": digest,
        "size": size,
        "source": {"host": source_host, "port": int(source_port)},
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _poll_worker_receive(host: str, port: int, worker_index: int, size: int) -> None:
    try:
        with httpx.Client(timeout=0.4) as client:
            st = client.get(f"http://{host}:{port}/v1/status").json()
        recv = st.get("receive") or {}
        nbytes = int(recv.get("bytes") or 0)
        _update_worker_progress(
            worker_index, phase="pulling", nbytes=nbytes, total=size
        )
    except Exception:
        return


def _download_one_piece(
    *,
    client: httpx.Client,
    tmp: Path,
    filename: str,
    join_key: str,
    source_host: str,
    source_port: int,
    index: int,
    size: int,
    piece_size: int,
    on_bytes: Callable[[int], None] | None = None,
) -> None:
    start, end = piece_span(index, size, piece_size)
    want = end - start
    headers = {
        "X-Kllm-Key": join_key,
        "X-Kllm-Filename": filename,
        "Range": f"bytes={start}-{end - 1}",
    }
    with client.stream("GET", _blob_url(source_host, source_port), headers=headers) as resp:
        if resp.status_code == 416:
            raise RangeUnsatisfiable(f"piece {index} unsatisfiable")
        resp.raise_for_status()
        if resp.status_code != 206:
            raise ValueError(
                f"seed did not return 206 for Range (status {resp.status_code})"
            )
        got = 0
        with tmp.open("r+b") as f:
            f.seek(start)
            for chunk in resp.iter_bytes(chunk_size=_BLOB_CHUNK):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if on_bytes is not None:
                    on_bytes(len(chunk))
            f.flush()
        if got != want:
            raise IOError(f"piece {index}: got {got} bytes expected {want}")


def pull_from_seed(
    filename: str,
    size: int,
    digest: str,
    source_host: str,
    source_port: int,
    join_key: str,
    *,
    connections: int = _PULL_CONNECTIONS,
    piece_size: int = _PIECE_SIZE,
    timeout: float = 3600.0,
) -> dict[str, Any]:
    """Pull a GGUF from one seed with parallel Range GETs and bitmap resume."""
    dest = models_dir() / Path(filename).name
    expected = (digest or "").lower()
    tmp = begin_receive(filename, size, resume=True, piece_size=piece_size)
    bitmap_path = bitmap_path_for_partial(tmp)
    try:
        _preallocate_partial(tmp, size)
        n_pieces = piece_count(size, piece_size)
        bitmap = load_piece_bitmap(bitmap_path, n_pieces)
        missing = missing_piece_indices(bitmap, n_pieces)
        throttle = _Throttle()
        progress_lock = threading.Lock()
        completed = completed_piece_bytes(bitmap, size, piece_size)
        inflight = 0

        def report(*, force: bool = False) -> None:
            finish_receive_chunk(
                filename=dest.name,
                nbytes=completed + inflight,
                total=size,
                throttle=throttle,
                force=force,
            )

        report(force=True)

        if missing:
            work: Queue[int] = Queue()
            for idx in missing:
                work.put(idx)
            errors: list[BaseException] = []
            n_threads = max(1, min(connections, len(missing)))

            def worker() -> None:
                nonlocal inflight, completed
                with httpx.Client(timeout=timeout) as client:
                    while True:
                        try:
                            index = work.get_nowait()
                        except Empty:
                            return
                        piece_got = 0

                        def on_bytes(n: int) -> None:
                            nonlocal inflight, piece_got
                            with progress_lock:
                                inflight += n
                                piece_got += n
                                report()

                        try:
                            _download_one_piece(
                                client=client,
                                tmp=tmp,
                                filename=dest.name,
                                join_key=join_key,
                                source_host=source_host,
                                source_port=source_port,
                                index=index,
                                size=size,
                                piece_size=piece_size,
                                on_bytes=on_bytes,
                            )
                            with progress_lock:
                                inflight -= piece_got
                                piece_bit_set(bitmap, index)
                                save_piece_bitmap(bitmap_path, bitmap)
                                completed = completed_piece_bytes(
                                    bitmap, size, piece_size
                                )
                                report(force=True)
                        except BaseException as exc:
                            with progress_lock:
                                inflight = max(0, inflight - piece_got)
                            errors.append(exc)
                            return

            threads = [
                threading.Thread(target=worker, daemon=True) for _ in range(n_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if errors:
                raise errors[0]

        got = sha256_file(tmp)
        if got != expected:
            abort_receive(tmp)
            raise ValueError("sha256 mismatch")
        return complete_receive(tmp, dest, digest=got, size=size)
    except ReceiveInProgressError:
        raise
    except Exception:
        if _RECEIVE_LOCK.locked() and get_receive_progress() is not None:
            abort_receive(tmp, keep_partial=True)
        raise


def _probe_skip(
    host: str,
    port: int,
    join_key: str,
    filename: str,
    size: int,
    digest: str,
) -> dict[str, Any] | None:
    try:
        meta = probe_model_on_worker(host, port, join_key, filename)
    except Exception:
        return None
    if (
        meta.get("exists")
        and int(meta.get("size_bytes") or 0) == size
        and (meta.get("sha256") or "").lower() == digest.lower()
    ):
        return meta
    return None


def distribute_to_workers(
    model_path: Path,
    workers: Iterable[dict[str, Any]],
    join_key: str,
    *,
    default_port: int = 18765,
    master_host: str | None = None,
    master_port: int | None = None,
) -> list[dict[str, Any]]:
    """Hash once, then tree-pull: one downloader per seed, finished workers seed others."""
    if not _DISTRIBUTE_LOCK.acquire(blocking=False):
        raise DistributeInProgressError("a distribute is already in progress")
    worker_list = [
        {
            "node_id": w.get("node_id"),
            "host": w.get("lan_host") or w.get("host"),
            "port": int(w.get("control_port") or default_port),
        }
        for w in workers
        if (w.get("lan_host") or w.get("host"))
    ]
    seed_port = int(master_port if master_port is not None else default_port)
    try:
        size = model_path.stat().st_size
        _init_distribute_progress(model_path.name, size)
        digest = ensure_sha256(
            model_path,
            on_progress=lambda nbytes, total: _update_distribute_hash(nbytes, total),
        )
        progress_rows = [
            {
                "node_id": w.get("node_id"),
                "host": w["host"],
                "phase": "probing",
                "bytes": 0,
                "total": size,
                "pct": 0,
                "error": None,
            }
            for w in worker_list
        ]
        _set_distribute_workers(progress_rows)
        if not worker_list:
            return []

        results: list[dict[str, Any] | None] = [None] * len(worker_list)
        pending: deque[int] = deque()
        free_seeds: deque[tuple[str, int]] = deque()
        if master_host:
            free_seeds.append((master_host, seed_port))

        for idx, w in enumerate(worker_list):
            meta = _probe_skip(
                w["host"], w["port"], join_key, model_path.name, size, digest
            )
            if meta is not None:
                _update_worker_progress(
                    idx, phase="skipped", nbytes=size, total=size, force=True
                )
                results[idx] = {
                    "host": w["host"],
                    "node_id": w.get("node_id"),
                    "ok": True,
                    "result": {
                        "ok": True,
                        "skipped": True,
                        "path": meta.get("path"),
                        "sha256": digest,
                    },
                }
                free_seeds.append((w["host"], w["port"]))
            else:
                pending.append(idx)

        if pending and not free_seeds:
            raise ValueError("master_host is required to seed a distribute")

        attempts = {idx: 0 for idx in pending}
        in_flight: dict[Any, tuple[int, tuple[str, int], dict[str, Any]]] = {}
        poll_targets: dict[int, tuple[str, int]] = {}
        poll_lock = threading.Lock()
        stop_poll = threading.Event()

        def poll_loop() -> None:
            while not stop_poll.wait(_PULL_POLL_INTERVAL):
                with poll_lock:
                    targets = list(poll_targets.items())
                for pidx, (phost, pport) in targets:
                    _poll_worker_receive(phost, pport, pidx, size)

        poller = threading.Thread(target=poll_loop, daemon=True)
        poller.start()
        try:
            max_workers = max(1, len(worker_list))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                while pending or in_flight:
                    while free_seeds and pending:
                        seed = free_seeds.popleft()
                        idx = pending.popleft()
                        w = worker_list[idx]
                        attempts[idx] = attempts.get(idx, 0) + 1
                        _update_worker_progress(
                            idx, phase="pulling", nbytes=0, total=size, force=True
                        )
                        with poll_lock:
                            poll_targets[idx] = (w["host"], w["port"])
                        fut = pool.submit(
                            request_worker_pull,
                            w["host"],
                            w["port"],
                            join_key,
                            filename=model_path.name,
                            digest=digest,
                            size=size,
                            source_host=seed[0],
                            source_port=seed[1],
                        )
                        in_flight[fut] = (idx, seed, w)
                    if not in_flight:
                        while pending:
                            idx = pending.popleft()
                            err = "no seed available"
                            _update_worker_progress(
                                idx, phase="error", error=err, force=True
                            )
                            results[idx] = {
                                "host": worker_list[idx]["host"],
                                "node_id": worker_list[idx].get("node_id"),
                                "ok": False,
                                "error": err,
                            }
                        break
                    done, _ = wait(
                        tuple(in_flight.keys()), return_when=FIRST_COMPLETED
                    )
                    for fut in done:
                        idx, seed, w = in_flight.pop(fut)
                        with poll_lock:
                            poll_targets.pop(idx, None)
                        try:
                            data = fut.result()
                        except Exception as exc:
                            if attempts.get(idx, 0) < _PULL_MAX_ATTEMPTS:
                                pending.append(idx)
                            else:
                                _update_worker_progress(
                                    idx, phase="error", error=str(exc), force=True
                                )
                                results[idx] = {
                                    "host": w["host"],
                                    "node_id": w.get("node_id"),
                                    "ok": False,
                                    "error": str(exc),
                                }
                            free_seeds.append(seed)
                            continue
                        _update_worker_progress(
                            idx, phase="done", nbytes=size, total=size, force=True
                        )
                        results[idx] = {
                            "host": w["host"],
                            "node_id": w.get("node_id"),
                            "ok": True,
                            "result": data,
                            "source": {"host": seed[0], "port": seed[1]},
                        }
                        # Prefer the new copy as the next seed (binary tree).
                        free_seeds.append((w["host"], w["port"]))
                        free_seeds.append(seed)
        finally:
            stop_poll.set()
            poller.join(timeout=2.0)

        return [r for r in results if r is not None]
    finally:
        _clear_distribute_progress()
        _DISTRIBUTE_LOCK.release()


def begin_receive(
    filename: str,
    total: int,
    *,
    resume: bool = False,
    piece_size: int = _PIECE_SIZE,
) -> Path:
    """Acquire receive lock and prepare a .partial destination path."""
    if not _RECEIVE_LOCK.acquire(blocking=False):
        raise ReceiveInProgressError("a receive is already in progress")
    dest = models_dir() / Path(filename).name
    models_dir().mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    nbytes = 0
    if resume:
        n_pieces = piece_count(total, piece_size)
        bitmap = load_piece_bitmap(bitmap_path_for_partial(tmp), n_pieces)
        nbytes = completed_piece_bytes(bitmap, total, piece_size)
    _set_receive_progress(dest.name, nbytes, total, phase="receiving")
    return tmp


def finish_receive_chunk(
    *,
    filename: str,
    nbytes: int,
    total: int,
    throttle: _Throttle | None = None,
    force: bool = False,
) -> None:
    if throttle is not None and not throttle.allow(force=force or nbytes >= total):
        return
    _set_receive_progress(filename, nbytes, total, phase="receiving")


def complete_receive(
    tmp: Path,
    dest: Path,
    *,
    digest: str,
    size: int,
) -> dict[str, Any]:
    _set_receive_progress(dest.name, size, size, phase="verifying")
    tmp.replace(dest)
    bitmap_path_for_partial(tmp).unlink(missing_ok=True)
    write_sha256_sidecar(dest, digest)
    _clear_receive_progress()
    if _RECEIVE_LOCK.locked():
        _RECEIVE_LOCK.release()
    return {"ok": True, "path": str(dest), "sha256": digest}


def abort_receive(tmp: Path | None = None, *, keep_partial: bool = False) -> None:
    if tmp is not None and not keep_partial:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            bitmap_path_for_partial(tmp).unlink(missing_ok=True)
        except OSError:
            pass
    _clear_receive_progress()
    if _RECEIVE_LOCK.locked():
        try:
            _RECEIVE_LOCK.release()
        except RuntimeError:
            pass


def delete_local_model(name: str) -> Path:
    """Delete a GGUF under models_dir(). Rejects paths outside that directory."""
    path = resolve_local_model(name).resolve()
    root = models_dir().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"model is outside models directory: {path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"model not found: {name}")
    path.unlink()
    side = sha256_sidecar_path(path)
    try:
        side.unlink(missing_ok=True)
    except OSError:
        pass
    return path


def delete_model_on_worker(
    host: str,
    port: int,
    join_key: str,
    filename: str,
    timeout: float = 60.0,
) -> dict:
    url = f"http://{host}:{port}/v1/models/file"
    headers = {
        "X-Kllm-Key": join_key,
        "X-Kllm-Filename": Path(filename).name,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.delete(url, headers=headers)
        resp.raise_for_status()
        return resp.json()
