from __future__ import annotations

import hashlib
import os
from kllm.transfer_manifest import atomic_json, read_json, identity
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, unquote_plus, urlparse

import httpx
from huggingface_hub import hf_hub_url, list_models, list_repo_files, model_info
from huggingface_hub.errors import (
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)
from huggingface_hub.utils import HFValidationError, build_hf_headers, validate_repo_id

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
_DOWNLOAD_LAST_TERMINAL: dict[str, Any] | None = None
_DOWNLOAD_CANCEL: threading.Event | None = None
# Rolling (monotonic_time, bytes) samples for transfer-rate / ETA estimation.
_DOWNLOAD_SAMPLES: deque[tuple[float, int]] = deque()
_RATE_WINDOW = 10.0

_TERMINAL_DOWNLOAD_PHASES = frozenset({"complete", "error", "cancelled"})

_PROGRESS_MIN_INTERVAL = 0.1
_TRANSFER_CHUNK = 1024 * 1024

SEARCH_HUB_LIMIT = 20
SEARCH_REPO_CAP = 8
SEARCH_FILE_CAP = 40
_METADATA_TIMEOUT = 15.0

_HF_WEBSITE_HOSTS = frozenset({
    "huggingface.co",
    "www.huggingface.co",
    "hf.co",
    "www.hf.co",
})
_NON_MODEL_ROOTS = frozenset({
    "datasets",
    "dataset",
    "spaces",
    "space",
    "collections",
    "collection",
})
_FILE_ROUTES = frozenset({"blob", "resolve", "raw"})
_UNSUPPORTED_LINK = (
    "Supply a supported model repository, tree, or file link on huggingface.co or hf.co."
)
_REVISION_QUERY = (
    "Use a /tree/, /blob/, /resolve/, or /raw/ link for the revision. " + _UNSUPPORTED_LINK
)

class DownloadInProgressError(RuntimeError):
    """Raised when a second download is requested while one is active."""

class DownloadCancelledError(RuntimeError):
    """Raised inside the download loop when a cancel event is set."""

class ModelLookupError(ValueError):
    """Actionable search or explicit-target failure with secrets removed."""

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


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def begin_download(source: str, dest_name: str | None = None) -> dict[str, Any]:
    """Reserve a download operation before its background runner starts."""
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_CANCEL
    if _DOWNLOAD_PROGRESS is not None and _DOWNLOAD_PROGRESS.get("phase") not in _TERMINAL_DOWNLOAD_PHASES:
        raise DownloadInProgressError("a download is already in progress")
    if not _DOWNLOAD_LOCK.acquire(blocking=False):
        raise DownloadInProgressError("a download is already in progress")
    stamp = _timestamp()
    filename = _safe_name(dest_name or Path(urlparse(source).path).name or "model.gguf")
    _DOWNLOAD_CANCEL = threading.Event()
    _DOWNLOAD_PROGRESS = {
        "operation_id": str(uuid.uuid4()),
        "filename": filename,
        "bytes": 0,
        "total": None,
        "pct": 0,
        "phase": "queued",
        "rate_bps": None,
        "eta_seconds": None,
        "started_at": stamp,
        "updated_at": stamp,
        "finished_at": None,
    }
    return dict(_DOWNLOAD_PROGRESS)

def _reset_download_state() -> None:
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_CANCEL, _DOWNLOAD_LAST_TERMINAL
    if _DOWNLOAD_PROGRESS is not None and _DOWNLOAD_PROGRESS.get("phase") in _TERMINAL_DOWNLOAD_PHASES:
        _DOWNLOAD_LAST_TERMINAL = dict(_DOWNLOAD_PROGRESS)
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
    previous = _DOWNLOAD_PROGRESS or {}
    stamp = _timestamp()
    snap = {
        "operation_id": previous.get("operation_id") or str(uuid.uuid4()),
        "filename": filename,
        "bytes": nbytes,
        "total": total,
        "pct": _pct(nbytes, total),
        "phase": "downloading",
        "rate_bps": rate,
        "eta_seconds": eta,
        "started_at": previous.get("started_at") or stamp,
        "updated_at": stamp,
        "finished_at": None,
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
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_LAST_TERMINAL
    if _DOWNLOAD_PROGRESS is None or _DOWNLOAD_PROGRESS.get("filename") != filename:
        return
    snap = dict(_DOWNLOAD_PROGRESS)
    stamp = _timestamp()
    snap.update({
        "phase": "complete",
        "rate_bps": None,
        "eta_seconds": None,
        "updated_at": stamp,
        "finished_at": stamp,
    })
    if total:
        snap["bytes"] = total
        snap["pct"] = 100
    _DOWNLOAD_PROGRESS = snap
    _DOWNLOAD_LAST_TERMINAL = dict(snap)

def fail_download_progress(filename: str, error: str) -> None:
    """Mark the active download failed with a message."""
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_LAST_TERMINAL
    if _DOWNLOAD_PROGRESS is None or _DOWNLOAD_PROGRESS.get("filename") != filename:
        return
    snap = dict(_DOWNLOAD_PROGRESS)
    stamp = _timestamp()
    snap.update({
        "phase": "error",
        "rate_bps": None,
        "eta_seconds": None,
        "error": error,
        "updated_at": stamp,
        "finished_at": stamp,
    })
    _DOWNLOAD_PROGRESS = snap
    _DOWNLOAD_LAST_TERMINAL = dict(snap)

def download_job_snapshot() -> dict[str, Any] | None:
    """Job-level view for /v1/models/download-progress (None when idle)."""
    snap = get_download_progress()
    if snap is None and _DOWNLOAD_LAST_TERMINAL is not None:
        snap = dict(_DOWNLOAD_LAST_TERMINAL)
    if snap is None:
        return None
    out = dict(snap)
    out["state"] = "running" if snap.get("phase") in {"queued", "downloading"} else snap.get("phase")
    return out


def download_progress_records() -> dict[str, dict[str, Any] | None]:
    """Return the active operation separately from its retained terminal outcome."""
    global _DOWNLOAD_LAST_TERMINAL
    snap = download_job_snapshot()
    if snap is not None and snap.get("state") in _TERMINAL_DOWNLOAD_PHASES:
        _DOWNLOAD_LAST_TERMINAL = dict(snap)
        snap = None
    return {"current": snap, "last_terminal": dict(_DOWNLOAD_LAST_TERMINAL) if _DOWNLOAD_LAST_TERMINAL else None}

def set_download_terminal(
    filename: str,
    total: int | None,
    phase: str,
    error: str | None = None,
    operation_id: str | None = None,
) -> None:
    """Publish a terminal snapshot for an async (thread-pool) download.

    ``download_model`` clears its progress state on return, so background
    runners publish the terminal state themselves; pollers then see
    complete/error before the runner forgets it.
    """
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_LAST_TERMINAL
    previous = _DOWNLOAD_PROGRESS or {}
    if operation_id is not None and previous.get("operation_id") not in (None, operation_id):
        return
    stamp = _timestamp()
    snap: dict[str, Any] = {
        "operation_id": operation_id or previous.get("operation_id") or str(uuid.uuid4()),
        "filename": filename,
        "bytes": total if (total and phase == "complete") else 0,
        "total": total,
        "pct": 100 if phase == "complete" else 0,
        "phase": phase,
        "rate_bps": None,
        "eta_seconds": None,
        "started_at": previous.get("started_at") or stamp,
        "updated_at": stamp,
        "finished_at": stamp,
    }
    if error:
        snap["error"] = error
    _DOWNLOAD_PROGRESS = snap
    _DOWNLOAD_LAST_TERMINAL = dict(snap)

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

def _search_target(**fields: Any) -> dict[str, Any]:
    target = {
        "raw": "",
        "kind": "search",
        "url": None,
        "repo_id": None,
        "search_term": None,
        "suffix": None,
        "revision": None,
        "folder": None,
        "filename": None,
    }
    target.update(fields)
    return target


def _is_http_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _malformed_percent(text: str) -> bool:
    index = 0
    while index < len(text):
        if text[index] != "%":
            index += 1
            continue
        pair = text[index + 1:index + 3]
        if len(pair) < 2 or any(ch not in "0123456789abcdefABCDEF" for ch in pair):
            return True
        index += 3
    return False


def _has_controls(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def _decode_segment(segment: str) -> str:
    if _malformed_percent(segment):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return unquote(segment)


def _validate_revision(revision: str) -> str:
    if not revision or "\\" in revision or _has_controls(revision):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    if any(part in {"", ".", ".."} for part in revision.split("/")):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return revision


def _validate_repo_path(path: str) -> str:
    if not path or path.startswith("/") or "\\" in path or _has_controls(path):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return path


def _validate_model_repo(org: str, name: str) -> str:
    if (
        not org
        or not name
        or "/" in org
        or "/" in name
        or "\\" in org
        or "\\" in name
        or _has_controls(org)
        or _has_controls(name)
    ):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    repo_id = f"{org}/{name}"
    try:
        validate_repo_id(repo_id)
    except HFValidationError as exc:
        raise ModelLookupError(_UNSUPPORTED_LINK) from exc
    return repo_id


def _query_items(query: str) -> list[tuple[str, str]]:
    if not query:
        return []
    items: list[tuple[str, str]] = []
    for part in query.split("&"):
        if not part:
            continue
        key, sep, value = part.partition("=")
        if _malformed_percent(key) or _malformed_percent(value):
            raise ModelLookupError(_UNSUPPORTED_LINK)
        decoded_key = unquote_plus(key)
        decoded_value = unquote_plus(value) if sep else ""
        items.append((decoded_key, decoded_value))
    return items


def _encoded_segments(path: str) -> tuple[list[str], bool]:
    pieces = path.split("/")
    if pieces and pieces[0] == "":
        pieces = pieces[1:]
    trailing = bool(pieces) and pieces[-1] == ""
    if trailing:
        pieces = pieces[:-1]
    if any(piece == "" for piece in pieces):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return [_decode_segment(piece) for piece in pieces], trailing


def _hf_show_file_info(query: str) -> str | None:
    """Return one decoded show_file_info path, or None when the parameter is absent."""
    values = [value for key, value in _query_items(query) if key == "show_file_info"]
    if not values:
        return None
    if len(values) != 1 or not values[0].strip():
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return _validate_repo_path(values[0].strip())


def _is_hf_website(url: str) -> bool:
    """True for the Hugging Face model website, not CDN or other subdomains."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if (parsed.scheme or "").lower() not in {"http", "https"}:
        return False
    return host in _HF_WEBSITE_HOSTS


def _is_hf_host(url: str) -> bool:
    return _is_hf_website(url)


def _parse_hf_url(url: str) -> dict[str, Any]:
    """Parse a Hugging Face website URL into repo, revision, folder, and file selectors."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ModelLookupError(_UNSUPPORTED_LINK) from exc
    if parsed.username is not None or parsed.password is not None:
        raise ModelLookupError(_UNSUPPORTED_LINK)
    scheme = (parsed.scheme or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ModelLookupError(_UNSUPPORTED_LINK) from exc
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    if any(key == "revision" for key, _value in _query_items(parsed.query)):
        raise ModelLookupError(_REVISION_QUERY)
    show_file = _hf_show_file_info(parsed.query)
    segments, trailing = _encoded_segments(parsed.path)
    if segments and segments[0] in {"models", "model"}:
        segments = segments[1:]
    if not segments or segments[0] in _NON_MODEL_ROOTS:
        raise ModelLookupError(_UNSUPPORTED_LINK)
    if len(segments) < 2:
        raise ModelLookupError(_UNSUPPORTED_LINK)
    repo_id = _validate_model_repo(segments[0], segments[1])
    rest = segments[2:]
    revision = None
    folder = None
    filename = None
    if not rest:
        if trailing:
            pass
    elif rest[0] in _FILE_ROUTES:
        if trailing or len(rest) < 3:
            raise ModelLookupError(_UNSUPPORTED_LINK)
        revision = _validate_revision(rest[1])
        filename = _validate_repo_path("/".join(rest[2:]))
    elif rest[0] == "tree":
        if len(rest) < 2:
            raise ModelLookupError(_UNSUPPORTED_LINK)
        revision = _validate_revision(rest[1])
        if len(rest) > 2:
            folder = _validate_repo_path("/".join(rest[2:]))
    else:
        raise ModelLookupError(_UNSUPPORTED_LINK)
    if filename is None and show_file is not None:
        filename = show_file
        folder = None
    return {
        "repo_id": repo_id,
        "revision": revision,
        "folder": folder,
        "filename": filename,
    }


def parse_model_search_query(query: str) -> dict[str, Any]:
    """Split a Models search box into url / repo / Hub-search parts."""
    raw = query.strip()
    if not raw:
        raise ValueError("query is empty")
    if _is_http_url(raw):
        if _is_hf_website(raw):
            selected = _parse_hf_url(raw)
            return _search_target(
                raw=raw,
                kind="url",
                url=raw,
                repo_id=selected["repo_id"],
                revision=selected["revision"],
                folder=selected["folder"],
                filename=selected["filename"],
            )
        return _search_target(raw=raw, kind="url", url=raw)
    suffix = None
    left = raw
    if ":" in raw:
        left, suffix = raw.split(":", 1)
        left = left.strip()
        suffix = suffix.strip() or None
    if not left:
        raise ValueError("query is empty")
    if "/" in left:
        return _search_target(raw=raw, kind="repo", repo_id=left, suffix=suffix)
    return _search_target(raw=raw, kind="search", search_term=left, suffix=suffix)

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

def _quant_label(filename: str, suffix: str | None) -> str | None:
    tokens = quant_tokens_in_name(filename)
    if suffix and looks_like_quant_tag(suffix):
        target = normalize_quant(suffix)
        for token in tokens:
            if normalize_quant(token) == target:
                return token
    return tokens[-1] if tokens else None

def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _is_standalone_gguf(name: str) -> bool:
    return bool(name) and name.lower().endswith(".gguf") and not _is_skipped_gguf(name)


def _redact_lookup_text(text: str) -> str:
    cleaned = " ".join((text or "").split())
    lowered = cleaned.lower()
    if (
        not cleaned
        or "://" in cleaned
        or "hf_" in lowered
        or "bearer " in lowered
        or "authorization" in lowered
        or "token=" in lowered
        or len(cleaned) > 180
    ):
        return "Lookup failed. Retry or check connectivity and access."
    return cleaned


def _lookup_error(
    exc: Exception,
    *,
    repo_id: str | None = None,
    revision: str | None = None,
) -> ModelLookupError:
    """Map a metadata failure to a safe category. Gated errors win over their superclass."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, GatedRepoError):
        return ModelLookupError(
            f"Repository {repo_id} needs access. Check the configured Hugging Face token."
        )
    if isinstance(exc, RevisionNotFoundError):
        return ModelLookupError(f"Revision {revision} is unavailable in {repo_id}.")
    if isinstance(exc, RepositoryNotFoundError):
        return ModelLookupError(
            f"Repository {repo_id} is unavailable or access is required."
        )
    if isinstance(exc, HfHubHTTPError) and status == 429:
        return ModelLookupError("Lookup was rate limited. Retry or check connectivity and access.")
    if status in {401, 403}:
        return ModelLookupError(
            f"Repository {repo_id} needs access. Check the configured Hugging Face token."
        )
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return ModelLookupError("Lookup timed out. Retry or check connectivity and access.")
    if isinstance(exc, (httpx.RequestError, ConnectionError)):
        return ModelLookupError("Lookup failed. Retry or check connectivity and access.")
    if isinstance(exc, HfHubHTTPError):
        return ModelLookupError("Lookup failed. Retry or check connectivity and access.")
    text = _redact_lookup_text(str(exc))
    if text.startswith("Lookup failed. Retry"):
        return ModelLookupError(text)
    return ModelLookupError(f"Lookup failed: {text}")


def _missing_file(repo_id: str, revision: str, path: str) -> ModelLookupError:
    return ModelLookupError(f"File {path} is unavailable in {repo_id} at revision {revision}.")


def _unsupported_file(path: str) -> ModelLookupError:
    return ModelLookupError(f"File {path} is not a supported standalone GGUF model.")


def _files_for_suffix(
    files: list[str],
    suffix: str | None,
    *,
    repo_id: str,
    revision: str,
) -> list[str]:
    if not suffix:
        return _candidate_ggufs(files)
    if is_exact_hf_filename(suffix):
        if "\\" in suffix or _has_controls(suffix):
            raise ModelLookupError(_UNSUPPORTED_LINK)
        if suffix in files:
            if not _is_standalone_gguf(suffix):
                raise _unsupported_file(suffix)
            return [suffix]
        if "/" in suffix:
            raise _missing_file(repo_id, revision, suffix)
        base = suffix.rsplit("/", 1)[-1].lower()
        matches = [rel for rel in _candidate_ggufs(files) if _basename(rel).lower() == base]
        if not matches:
            raise _missing_file(repo_id, revision, suffix)
        return matches
    return match_gguf_for_quant(files, suffix)

def _sibling_size(sibling: Any) -> int:
    raw = getattr(sibling, "size", None)
    if raw is None and isinstance(sibling, dict):
        raw = sibling.get("size")
    try:
        return int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0

def _option_source(repo_id: str, rel: str, revision: str, *, canonical: bool) -> str:
    if canonical or not is_exact_hf_filename(rel):
        return hf_hub_url(repo_id, rel, revision=revision)
    return f"{repo_id}:{rel}"


def _hit_row(
    repo_id: str,
    filename: str,
    *,
    size_bytes: int,
    gated: bool,
    suffix: str | None,
    source: str | None = None,
    path: str | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    rel = filename
    return {
        "repo_id": repo_id,
        "filename": _basename(rel),
        "path": path,
        "revision": revision,
        "quant": _quant_label(rel, suffix),
        "size_bytes": size_bytes,
        "gated": gated,
        "source": source or f"{repo_id}:{rel}",
    }

def _sibling_names(info: Any) -> tuple[list[str], dict[str, int]]:
    files: list[str] = []
    sizes: dict[str, int] = {}
    for sibling in list(getattr(info, "siblings", None) or []):
        rel = getattr(sibling, "rfilename", None)
        if not rel and isinstance(sibling, dict):
            rel = sibling.get("rfilename")
        if not rel:
            continue
        files.append(str(rel))
        sizes[str(rel)] = _sibling_size(sibling)
    return files, sizes


def _inspect_repo_ggufs(
    repo_id: str,
    *,
    suffix: str | None,
    token: str | None,
    filename: str | None = None,
    folder: str | None = None,
    revision: str = "main",
    canonical: bool = False,
) -> list[dict[str, Any]]:
    try:
        info = model_info(
            repo_id,
            revision=revision,
            files_metadata=True,
            timeout=_METADATA_TIMEOUT,
            token=token if token else False,
        )
    except ModelLookupError:
        raise
    except Exception as exc:
        raise _lookup_error(exc, repo_id=repo_id, revision=revision) from exc
    gated = bool(getattr(info, "gated", False))
    files, sizes = _sibling_names(info)
    if filename:
        if filename not in files:
            raise _missing_file(repo_id, revision, filename)
        if not _is_standalone_gguf(filename):
            raise _unsupported_file(filename)
        selected = [filename]
    elif folder:
        prefix = folder.rstrip("/") + "/"
        if not any(rel.startswith(prefix) for rel in files):
            raise ModelLookupError(
                f"Folder {folder} is unavailable in {repo_id} at revision {revision}."
            )
        selected = _candidate_ggufs([rel for rel in files if rel.startswith(prefix)])
    else:
        selected = _files_for_suffix(files, suffix, repo_id=repo_id, revision=revision)
    rows: list[dict[str, Any]] = []
    for rel in selected:
        rows.append(
            _hit_row(
                repo_id,
                rel,
                size_bytes=sizes.get(rel, 0),
                gated=gated,
                suffix=suffix,
                path=rel,
                revision=revision,
                source=_option_source(repo_id, rel, revision, canonical=canonical),
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
    del token  # Direct URLs never receive configured Hub credentials.
    size = _url_size_bytes(url, headers={}) or 0
    filename = _basename(urlparse(url).path) or "model.gguf"
    return _hit_row(
        urlparse(url).netloc,
        filename,
        size_bytes=size,
        gated=False,
        suffix=parsed.get("suffix"),
        source=url,
        path=None,
        revision=None,
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
    warnings: list[str] = []
    kind = parsed["kind"]
    if kind == "url" and not parsed.get("repo_id"):
        results = [_synthetic_url_hit(parsed["url"], token=token, parsed=parsed)]
    elif kind in {"url", "repo"}:
        revision = parsed.get("revision") or "main"
        results = _inspect_repo_ggufs(
            parsed["repo_id"],
            suffix=None if kind == "url" else parsed.get("suffix"),
            filename=parsed.get("filename") if kind == "url" else None,
            folder=parsed.get("folder") if kind == "url" else None,
            revision=revision,
            token=token,
            canonical=kind == "url",
        )
    else:
        try:
            repo_ids = _hub_search_repo_ids(parsed["search_term"], token=token)
        except ModelLookupError:
            raise
        except Exception as exc:
            raise _lookup_error(exc) from exc
        failures = 0
        for repo_id in repo_ids[:SEARCH_REPO_CAP]:
            try:
                rows = _inspect_repo_ggufs(
                    repo_id, suffix=parsed.get("suffix"), token=token, revision="main"
                )
            except ModelLookupError as exc:
                failures += 1
                warnings.append(str(exc))
                continue
            results.extend(rows)
            if len(results) >= SEARCH_FILE_CAP:
                break
        results = results[:SEARCH_FILE_CAP]
        if not results and failures:
            raise ModelLookupError(warnings[0])
    for row in results:
        row["fit"] = vram_fit(row.get("size_bytes"), total_vram_bytes)
    return {
        "query": parsed["raw"],
        "total_vram_bytes": total_vram_bytes,
        "results": results,
        "warnings": warnings,
    }

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
            "GET", url, headers=headers or {}, follow_redirects=True, timeout=5.0
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
    global _DOWNLOAD_PROGRESS, _DOWNLOAD_CANCEL
    reserved = _DOWNLOAD_LOCK.locked() and (_DOWNLOAD_PROGRESS or {}).get("phase") == "queued"
    if not reserved and not _DOWNLOAD_LOCK.acquire(blocking=False):
        raise DownloadInProgressError("a download is already in progress")
    if reserved:
        cancel_event = _DOWNLOAD_CANCEL
        if cancel_event is None:  # pragma: no cover - reservation always creates one
            raise RuntimeError("download reservation is missing its cancellation event")
    else:
        _DOWNLOAD_PROGRESS = None  # A retained terminal must not become this operation.
        cancel_event = threading.Event()
        _DOWNLOAD_CANCEL = cancel_event
    keep_snapshot = reserved
    try:
        models_dir().mkdir(parents=True, exist_ok=True)
        token = resolve_hf_token()

        if _is_http_url(source):
            if _is_hf_website(source):
                parsed = parse_model_search_query(source)
                repo_id = parsed.get("repo_id")
                filename = parsed.get("filename")
                if not repo_id or not filename:
                    raise ValueError(
                        "Repository and tree links are not file downloads. "
                        "Search for a supported file, then download that result."
                    )
                revision = parsed.get("revision") or "main"
                url = hf_hub_url(repo_id, filename, revision=revision)
                name = dest_name or _basename(filename)
                return _stream_to_models(
                    url,
                    name,
                    headers=_hub_headers(token),
                    on_progress=on_progress,
                    cancel_event=cancel_event,
                )
            return _stream_to_models(
                source,
                dest_name,
                headers={},
                on_progress=on_progress,
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
        raise
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

