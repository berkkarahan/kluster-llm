from __future__ import annotations

import hashlib
import os
from kllm.transfer_manifest import atomic_json, read_json, identity
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from huggingface_hub import hf_hub_url, list_models, list_repo_files, model_info
from huggingface_hub.utils import build_hf_headers

from kllm.gguf_meta import read_context_length
from kllm.hf_token import resolve_hf_token
from kllm.model_defaults import resolve_model_defaults
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
_DOWNLOAD_CANCEL: threading.Event | None = None
# Rolling (monotonic_time, bytes) samples for transfer-rate / ETA estimation.
_DOWNLOAD_SAMPLES: deque[tuple[float, int]] = deque()
_RATE_WINDOW = 10.0

_PROGRESS_MIN_INTERVAL = 0.1
_TRANSFER_CHUNK = 1024 * 1024

SEARCH_HUB_LIMIT = 20
SEARCH_REPO_CAP = 8
SEARCH_FILE_CAP = 40

class DownloadInProgressError(RuntimeError):
    """Raised when a second download is requested while one is active."""

class DownloadCancelledError(RuntimeError):
    """Raised inside the download loop when a cancel event is set."""

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
    atomic_json(model_path.with_name(model_path.name + ".sha256.identity"), identity(model_path))

def read_sha256_sidecar(model_path: Path) -> str | None:
    if read_json(model_path.with_name(model_path.name + ".sha256.identity")) != identity(model_path):
        return None
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

def _reset_download_state() -> None:
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_CANCEL
    _DOWNLOAD_PROGRESS = None
    _DOWNLOAD_CANCEL = None
    _DOWNLOAD_SAMPLES.clear()
    if _DOWNLOAD_LOCK.locked():
        try:
            _DOWNLOAD_LOCK.release()
        except RuntimeError:
            pass

def cancel_download() -> bool:
    """Signal the in-flight download loop to stop. True when a job was running."""
    ev = _DOWNLOAD_CANCEL
    if ev is None:
        return False
    ev.set()
    return True

def _set_progress(
    filename: str,
    nbytes: int,
    total: int | None,
    on_progress: ProgressCallback | None = None,
) -> None:
    global _DOWNLOAD_PROGRESS
    now = time.monotonic()
    if total is not None and total > 0:
        while (
            _DOWNLOAD_SAMPLES
            and now - _DOWNLOAD_SAMPLES[0][0] > _RATE_WINDOW
        ):
            _DOWNLOAD_SAMPLES.popleft()
        _DOWNLOAD_SAMPLES.append((now, nbytes))
        rate = _transfer_rate(nbytes)
        eta = (
            max(0.0, (total - nbytes) / rate)
            if rate is not None and rate > 0 and total > nbytes
            else None
        )
    else:
        rate, eta = None, None
    snap = {
        "filename": filename,
        "bytes": nbytes,
        "total": total,
        "pct": _pct(nbytes, total),
        "phase": "downloading",
        "rate_bps": rate,
        "eta_seconds": eta,
    }
    _DOWNLOAD_PROGRESS = snap
    if on_progress is not None:
        on_progress(dict(snap))

def _transfer_rate(current_bytes: int) -> float | None:
    """Rolling transfer rate over the recent sample window; None when unmeasurable."""
    samples = _DOWNLOAD_SAMPLES
    if len(samples) < 2:
        return None
    t0, b0 = samples[0]
    t1, b1 = samples[-1]
    dt = t1 - t0
    if dt <= 0.05 or b1 <= b0:
        return None
    return (b1 - b0) / dt

def finalize_download_progress(filename: str, total: int | None) -> None:
    """Mark the active download complete so pollers see a terminal phase."""
    global _DOWNLOAD_PROGRESS
    if _DOWNLOAD_PROGRESS is None or _DOWNLOAD_PROGRESS.get("filename") != filename:
        return
    snap = dict(_DOWNLOAD_PROGRESS)
    snap.update({"phase": "complete", "rate_bps": None, "eta_seconds": None})
    if total:
        snap["bytes"] = total
        snap["pct"] = 100
    _DOWNLOAD_PROGRESS = snap

def fail_download_progress(filename: str, error: str) -> None:
    """Mark the active download failed with a message."""
    global _DOWNLOAD_PROGRESS
    if _DOWNLOAD_PROGRESS is None or _DOWNLOAD_PROGRESS.get("filename") != filename:
        return
    snap = dict(_DOWNLOAD_PROGRESS)
    snap.update({"phase": "error", "rate_bps": None, "eta_seconds": None, "error": error})
    _DOWNLOAD_PROGRESS = snap

def download_job_snapshot() -> dict[str, Any] | None:
    """Job-level view for /v1/models/download-progress (None when idle)."""
    snap = get_download_progress()
    if snap is None:
        return None
    out = dict(snap)
    out["state"] = "running" if snap.get("phase") == "downloading" else snap.get("phase")
    return out

def set_download_terminal(
    filename: str,
    total: int | None,
    phase: str,
    error: str | None = None,
) -> None:
    """Publish a terminal snapshot for an async (thread-pool) download.

    ``download_model`` clears its progress state on return, so background
    runners publish the terminal state themselves; pollers then see
    complete/error before the runner forgets it.
    """
    global _DOWNLOAD_PROGRESS
    snap: dict[str, Any] = {
        "filename": filename,
        "bytes": total if (total and phase == "complete") else 0,
        "total": total,
        "pct": 100 if phase == "complete" else 0,
        "phase": phase,
        "rate_bps": None,
        "eta_seconds": None,
    }
    if error:
        snap["error"] = error
    _DOWNLOAD_PROGRESS = snap

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
    headers: dict[str, Any] | None = None,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    parsed = urlparse(url)
    name = dest_name or Path(parsed.path).name or "model.gguf"
    filename = _safe_name(name)
    target = models_dir() / filename
    part = target.with_name(target.name + ".part")
    nbytes = 0
    hasher = hashlib.sha256()
    total: int | None = None
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
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelledError("download cancelled")
                    if not chunk:
                        continue
                    f.write(chunk)
                    hasher.update(chunk)
                    nbytes += len(chunk)
                    _set_progress(filename, nbytes, total, on_progress)
        part.replace(target)
        write_sha256_sidecar(target, hasher.hexdigest())
        finalize_download_progress(filename, total)
    except DownloadCancelledError:
        try:
            part.unlink()
        except OSError:
            pass
        raise
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
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_CANCEL
    cancel_event = threading.Event()
    _DOWNLOAD_CANCEL = cancel_event
    keep_snapshot = False
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
                        cancel_event=cancel_event,
                    )
                headers = _hub_headers(token)
            else:
                headers = {}
            return _stream_to_models(
                source, dest_name, headers=headers, on_progress=on_progress,
                cancel_event=cancel_event,
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
                cancel_event=cancel_event,
            )

        raise ValueError(
            "source must be an HTTP(S) URL or 'org/repo:filename.gguf' / "
            "'org/repo:Q4_K_M' Hugging Face ref"
        )
    except DownloadCancelledError:
        snap = get_download_progress()
        if snap is not None:
            fail_download_progress(snap["filename"], "cancelled")
            if _DOWNLOAD_PROGRESS is not None:
                _DOWNLOAD_PROGRESS["phase"] = "cancelled"
        keep_snapshot = True  # let pollers see the terminal cancelled state
    except Exception as exc:
        snap = get_download_progress()
        if snap is not None:
            fail_download_progress(snap["filename"], str(exc))
        raise
    finally:
        if not keep_snapshot:
            _DOWNLOAD_PROGRESS = None
        _DOWNLOAD_CANCEL = None
        _DOWNLOAD_SAMPLES.clear()
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
        try:
            defaults = resolve_model_defaults(path)
            public = defaults.to_public_dict()
            if public.get("sampling") or public.get("chat_template_kwargs") or public.get("reasoning"):
                entry["defaults"] = public
        except Exception:
            pass
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
    identity_path = path.with_name(path.name + ".sha256.identity")
    try:
        identity_path.unlink(missing_ok=True)
    except OSError:
        pass
    return path

