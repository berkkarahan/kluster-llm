from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from huggingface_hub import hf_hub_url, list_models, model_info
from huggingface_hub.errors import (
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)

from kllm.hf_token import resolve_hf_token
from .schemas import ModelSearchHit, ModelSearchResults
from .query import (
    ModelLookupError,
    ModelSearchTarget,
    _UNSUPPORTED_LINK,
    _candidate_ggufs,
    _has_controls,
    _is_skipped_gguf,
    is_exact_hf_filename,
    looks_like_quant_tag,
    match_gguf_for_quant,
    normalize_quant,
    parse_model_search_query,
    quant_tokens_in_name,
    rank_hub_repo_ids,
    vram_fit,
)

SEARCH_HUB_LIMIT = 20
SEARCH_REPO_CAP = 8
SEARCH_FILE_CAP = 40
_METADATA_TIMEOUT = 15.0
HubUrlFactory = Callable[..., str]

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
        return ModelLookupError(f"Repository {repo_id} is unavailable or access is required.")
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


def _option_source(
    repo_id: str,
    rel: str,
    revision: str,
    *,
    canonical: bool,
    hub_url_client: HubUrlFactory,
) -> str:
    if canonical or not is_exact_hf_filename(rel):
        return hub_url_client(repo_id, rel, revision=revision)
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
) -> ModelSearchHit:
    rel = filename
    return ModelSearchHit(
        repo_id=repo_id,
        filename=_basename(rel),
        path=path,
        revision=revision,
        quant=_quant_label(rel, suffix),
        size_bytes=size_bytes,
        gated=gated,
        source=source or f"{repo_id}:{rel}",
    )


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
    model_info_client: Any = model_info,
    hub_url_client: HubUrlFactory = hf_hub_url,
) -> list[ModelSearchHit]:
    try:
        info = model_info_client(
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
    rows: list[ModelSearchHit] = []
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
                source=_option_source(
                    repo_id,
                    rel,
                    revision,
                    canonical=canonical,
                    hub_url_client=hub_url_client,
                ),
            )
        )
    return rows


def _content_length(headers: Any) -> int:
    try:
        raw = headers.get("Content-Length")
    except Exception:
        raw = None
    try:
        return int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0


def _url_size_bytes(url: str, *, headers: dict[str, str] | None = None) -> int | None:
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            resp = client.head(url, headers=headers or {})
            length = _content_length(resp.headers)
            return length if length > 0 else None
    except Exception:
        return None


def _synthetic_url_hit(
    url: str, *, token: str | None, parsed: ModelSearchTarget, url_size_client: Any
) -> ModelSearchHit:
    del token  # Direct URLs never receive configured Hub credentials.
    size = url_size_client(url, headers={}) or 0
    filename = _basename(urlparse(url).path) or "model.gguf"
    return _hit_row(
        urlparse(url).netloc,
        filename,
        size_bytes=size,
        gated=False,
        suffix=parsed.suffix,
        source=url,
        path=None,
        revision=None,
    )


def _hub_search_repo_ids(
    search_term: str, *, token: str | None, list_models_client: Any
) -> list[str]:
    hits = list(
        list_models_client(
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
    list_models_client: Any = list_models,
    model_info_client: Any = model_info,
    url_size_client: Any = None,
    hub_url_client: HubUrlFactory = hf_hub_url,
) -> ModelSearchResults:
    """Resolve a search box query to GGUF hits with size and VRAM fit."""
    parsed = parse_model_search_query(query)
    if url_size_client is None:
        url_size_client = _url_size_bytes
    if token is None:
        token = resolve_hf_token()
    results: list[ModelSearchHit] = []
    warnings: list[str] = []
    kind = parsed.kind
    if kind == "url" and not parsed.repo_id:
        assert parsed.url is not None
        results = [_synthetic_url_hit(
            parsed.url, token=token, parsed=parsed, url_size_client=url_size_client
        )]
    elif kind in {"url", "repo"}:
        assert parsed.repo_id is not None
        revision = parsed.revision or "main"
        results = _inspect_repo_ggufs(
            parsed.repo_id,
            suffix=None if kind == "url" else parsed.suffix,
            filename=parsed.filename if kind == "url" else None,
            folder=parsed.folder if kind == "url" else None,
            revision=revision,
            token=token,
            model_info_client=model_info_client,
            hub_url_client=hub_url_client,
            canonical=kind == "url",
        )
    else:
        try:
            assert parsed.search_term is not None
            repo_ids = _hub_search_repo_ids(
                parsed.search_term, token=token, list_models_client=list_models_client
            )
        except ModelLookupError:
            raise
        except Exception as exc:
            raise _lookup_error(exc) from exc
        failures = 0
        for repo_id in repo_ids[:SEARCH_REPO_CAP]:
            try:
                rows = _inspect_repo_ggufs(
                    repo_id, suffix=parsed.suffix, token=token, revision="main",
                    model_info_client=model_info_client,
                    hub_url_client=hub_url_client,
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
    return ModelSearchResults(
        query=parsed.raw,
        total_vram_bytes=total_vram_bytes,
        results=tuple(
            row.model_copy(update={"fit": vram_fit(row.size_bytes, total_vram_bytes)})
            for row in results
        ),
        warnings=tuple(warnings),
    )
