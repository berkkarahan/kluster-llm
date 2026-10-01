from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import unquote, unquote_plus, urlparse

from huggingface_hub import list_repo_files
from huggingface_hub.errors import HFValidationError
from huggingface_hub.utils import validate_repo_id

from kllm.exceptions import ValidationError


class ModelLookupError(ValidationError):
    """Actionable search or explicit-target failure with secrets removed."""


@dataclass(frozen=True)
class ModelSearchTarget:
    """Normalized model search input without an untyped wire dictionary."""

    raw: str
    kind: Literal["url", "repo", "search"]
    url: str | None = None
    repo_id: str | None = None
    search_term: str | None = None
    suffix: str | None = None
    revision: str | None = None
    folder: str | None = None
    filename: str | None = None


@dataclass(frozen=True)
class EncodedPathSegments:
    """Decoded URL path parts and whether the original path ended in a slash."""

    segments: tuple[str, ...]
    trailing_slash: bool


@dataclass(frozen=True)
class QueryParameter:
    """One decoded URL query key and value."""

    key: str
    value: str


_BARE_QUANT_RE = re.compile(
    r"^[A-Za-z]{0,3}\d+[A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)*$",
    re.IGNORECASE,
)


_FILE_ROUTES = frozenset({"blob", "resolve", "raw"})


_HF_WEBSITE_HOSTS = frozenset(
    {
        "huggingface.co",
        "www.huggingface.co",
        "hf.co",
        "www.hf.co",
    }
)


_NON_MODEL_ROOTS = frozenset(
    {
        "datasets",
        "dataset",
        "spaces",
        "space",
        "collections",
        "collection",
    }
)


_QUANT_TOKEN_RE = re.compile(
    r"(?:^|[._-])((?:IQ|Q|BF|F)\d+(?:[_-][A-Za-z0-9]+)*)(?=[._-]|$)",
    re.IGNORECASE,
)


_SHARD_RE = re.compile(r"-\d{5}-of-", re.IGNORECASE)


_UNSUPPORTED_LINK = (
    "Supply a supported model repository, tree, or file link on huggingface.co or hf.co."
)
_REVISION_QUERY = (
    "Use a /tree/, /blob/, /resolve/, or /raw/ link for the revision. " + _UNSUPPORTED_LINK
)


def _available_quants(files: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for rel in _candidate_ggufs(files):
        for token in quant_tokens_in_name(rel):
            key = normalize_quant(token)
            if key and key not in seen:
                seen[key] = token
    return [seen[k] for k in sorted(seen)]


def _candidate_ggufs(files: list[str]) -> list[str]:
    out: list[str] = []
    for rel in files:
        if not rel.lower().endswith(".gguf"):
            continue
        if _is_skipped_gguf(rel):
            continue
        out.append(rel)
    return out


def _decode_segment(segment: str) -> str:
    if _malformed_percent(segment):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return unquote(segment)


def _encoded_segments(path: str) -> EncodedPathSegments:
    pieces = path.split("/")
    if pieces and pieces[0] == "":
        pieces = pieces[1:]
    trailing = bool(pieces) and pieces[-1] == ""
    if trailing:
        pieces = pieces[:-1]
    if any(piece == "" for piece in pieces):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return EncodedPathSegments(
        tuple(_decode_segment(piece) for piece in pieces), trailing
    )


def _has_controls(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def _hf_show_file_info(query: str) -> str | None:
    """Return one decoded show_file_info path, or None when the parameter is absent."""
    values = [item.value for item in _query_items(query) if item.key == "show_file_info"]
    if not values:
        return None
    if len(values) != 1 or not values[0].strip():
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return _validate_repo_path(values[0].strip())


def _is_hf_host(url: str) -> bool:
    return _is_hf_website(url)


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


def _is_http_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _is_skipped_gguf(name: str) -> bool:
    lower = name.lower()
    if "mmproj" in lower or "imatrix" in lower:
        return True
    if _SHARD_RE.search(name):
        return True
    return False


def _malformed_percent(text: str) -> bool:
    index = 0
    while index < len(text):
        if text[index] != "%":
            index += 1
            continue
        pair = text[index + 1 : index + 3]
        if len(pair) < 2 or any(ch not in "0123456789abcdefABCDEF" for ch in pair):
            return True
        index += 3
    return False


def _parse_hf_url(url: str) -> ModelSearchTarget:
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
    if any(item.key == "revision" for item in _query_items(parsed.query)):
        raise ModelLookupError(_REVISION_QUERY)
    show_file = _hf_show_file_info(parsed.query)
    path = _encoded_segments(parsed.path)
    segments = list(path.segments)
    trailing = path.trailing_slash
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
    return ModelSearchTarget(
        raw=url,
        kind="url",
        url=url,
        repo_id=repo_id,
        revision=revision,
        folder=folder,
        filename=filename,
    )


def _query_items(query: str) -> list[QueryParameter]:
    if not query:
        return []
    items: list[QueryParameter] = []
    for part in query.split("&"):
        if not part:
            continue
        key, sep, value = part.partition("=")
        if _malformed_percent(key) or _malformed_percent(value):
            raise ModelLookupError(_UNSUPPORTED_LINK)
        decoded_key = unquote_plus(key)
        decoded_value = unquote_plus(value) if sep else ""
        items.append(QueryParameter(decoded_key, decoded_value))
    return items


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


def _validate_repo_path(path: str) -> str:
    if not path or path.startswith("/") or "\\" in path or _has_controls(path):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return path


def _validate_revision(revision: str) -> str:
    if not revision or "\\" in revision or _has_controls(revision):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    if any(part in {"", ".", ".."} for part in revision.split("/")):
        raise ModelLookupError(_UNSUPPORTED_LINK)
    return revision


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


def looks_like_quant_tag(value: str) -> bool:
    s = value.strip()
    if s.lower().endswith(".gguf"):
        s = s[:-5]
    if not s or "/" in s or "\\" in s or "." in s:
        return False
    return bool(_BARE_QUANT_RE.fullmatch(s))


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


def normalize_quant(value: str) -> str:
    """Strip separators and optional .gguf so Q4_K_M / Q_4_K_M / q4-k-m match."""
    s = value.strip()
    if s.lower().endswith(".gguf"):
        s = s[:-5]
    return re.sub(r"[^A-Za-z0-9]+", "", s).upper()


def parse_model_search_query(query: str) -> ModelSearchTarget:
    """Split a Models search box into url / repo / Hub-search parts."""
    raw = query.strip()
    if not raw:
        raise ValueError("query is empty")
    if _is_http_url(raw):
        if _is_hf_website(raw):
            return _parse_hf_url(raw)
        return ModelSearchTarget(raw=raw, kind="url", url=raw)
    suffix = None
    left = raw
    if ":" in raw:
        left, suffix = raw.split(":", 1)
        left = left.strip()
        suffix = suffix.strip() or None
    if not left:
        raise ValueError("query is empty")
    if "/" in left:
        return ModelSearchTarget(raw=raw, kind="repo", repo_id=left, suffix=suffix)
    return ModelSearchTarget(raw=raw, kind="search", search_term=left, suffix=suffix)


def quant_tokens_in_name(filename: str) -> list[str]:
    name = Path(filename).name
    if name.lower().endswith(".gguf"):
        name = name[:-5]
    return [m.group(1) for m in _QUANT_TOKEN_RE.finditer(name)]


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


def resolve_hf_filename(
    repo_id: str,
    suffix: str,
    *,
    token: str | None = None,
    list_files: Callable[..., list[str]] = list_repo_files,
) -> str:
    """Exact Hub path/*.gguf filename, or unique file matching a quant tag."""
    suffix = suffix.strip()
    if not suffix:
        raise ValueError("Hugging Face ref is missing a filename or quant tag after ':'")
    if is_exact_hf_filename(suffix):
        return suffix
    files = list_files(repo_id, token=token if token else False)
    return select_gguf_for_quant(files, suffix, repo_id=repo_id)


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
