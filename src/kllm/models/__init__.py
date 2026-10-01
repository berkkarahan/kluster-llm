from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from huggingface_hub import hf_hub_url as hf_hub_url, list_models, list_repo_files, model_info
from kllm.hf_token import resolve_hf_token as resolve_hf_token

from .store import delete_local_model as delete_local_model
from .store import ensure_sha256 as ensure_sha256
from .store import list_local_models as list_local_models
from .store import local_model_meta as local_model_meta
from .store import read_sha256_sidecar as read_sha256_sidecar
from .store import resolve_local_model as resolve_local_model
from .store import sha256_file as sha256_file
from .store import sha256_sidecar_path as sha256_sidecar_path
from .store import write_sha256_sidecar as write_sha256_sidecar

from .query import ModelLookupError as ModelLookupError
from .query import ModelSearchTarget as ModelSearchTarget
from .query import _BARE_QUANT_RE as _BARE_QUANT_RE
from .query import _FILE_ROUTES as _FILE_ROUTES
from .query import _HF_WEBSITE_HOSTS as _HF_WEBSITE_HOSTS
from .query import _NON_MODEL_ROOTS as _NON_MODEL_ROOTS
from .query import _QUANT_TOKEN_RE as _QUANT_TOKEN_RE
from .query import _REVISION_QUERY as _REVISION_QUERY
from .query import _SHARD_RE as _SHARD_RE
from .query import _UNSUPPORTED_LINK as _UNSUPPORTED_LINK
from .query import _available_quants as _available_quants
from .query import _candidate_ggufs as _candidate_ggufs
from .query import _decode_segment as _decode_segment
from .query import _encoded_segments as _encoded_segments
from .query import _has_controls as _has_controls
from .query import _hf_show_file_info as _hf_show_file_info
from .query import _is_hf_host as _is_hf_host
from .query import _is_hf_website as _is_hf_website
from .query import _is_http_url as _is_http_url
from .query import _is_skipped_gguf as _is_skipped_gguf
from .query import _malformed_percent as _malformed_percent
from .query import _parse_hf_url as _parse_hf_url
from .query import _query_items as _query_items
from .query import _validate_model_repo as _validate_model_repo
from .query import _validate_repo_path as _validate_repo_path
from .query import _validate_revision as _validate_revision
from .query import is_exact_hf_filename as is_exact_hf_filename
from .query import looks_like_quant_tag as looks_like_quant_tag
from .query import match_gguf_for_quant as match_gguf_for_quant
from .query import normalize_quant as normalize_quant
from .query import parse_model_search_query as parse_model_search_query
from .query import quant_tokens_in_name as quant_tokens_in_name
from .query import rank_hub_repo_ids as rank_hub_repo_ids
from .query import resolve_hf_filename as _resolve_hf_filename
from .query import select_gguf_for_quant as select_gguf_for_quant
from .query import sum_vram_bytes as sum_vram_bytes
from .query import vram_fit as vram_fit
from .downloads import (
    DownloadCancelledError as DownloadCancelledError,
    DownloadInProgressError as DownloadInProgressError,
    begin_download as begin_download,
    cancel_download as cancel_download,
    download_job_snapshot as download_job_snapshot,
    download_model as _download_model,
    download_progress_records as download_progress_records,
    fail_download_progress as fail_download_progress,
    finalize_download_progress as finalize_download_progress,
    get_download_progress as get_download_progress,
    set_download_terminal as set_download_terminal,
)

from .catalog import (
    SEARCH_FILE_CAP as SEARCH_FILE_CAP,
    SEARCH_HUB_LIMIT as SEARCH_HUB_LIMIT,
    SEARCH_REPO_CAP as SEARCH_REPO_CAP,
)


def download_model(
    source: str,
    dest_name: str | None = None,
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    """Download through injected Hub adapters while preserving the package API."""
    return _download_model(
        source,
        dest_name,
        on_progress=on_progress,
        hub_url_client=hf_hub_url,
        filename_resolver=resolve_hf_filename,
        token_resolver=resolve_hf_token,
    )

def resolve_hf_filename(repo_id: str, suffix: str, *, token: str | None = None) -> str:
    """Resolve a Hub file while keeping the client replaceable at this boundary."""
    return _resolve_hf_filename(repo_id, suffix, token=token, list_files=list_repo_files)


# Distinct tokens like .Q4_K_M. / -Q4_K_M / _Q4_K_M

def _url_size_bytes(url: str, *, headers: dict[str, str] | None = None) -> int | None:
    """Replaceable boundary for obtaining the size of a direct model URL."""
    from .catalog import _url_size_bytes as lookup_url_size

    return lookup_url_size(url, headers=headers)


def search_hub_ggufs(
    query: str, *, total_vram_bytes: int | None = None, token: str | None = None
) -> dict[str, Any]:
    """Resolve a search query using replaceable Hub clients at this facade."""
    from .catalog import search_hub_ggufs as search_catalog

    result = search_catalog(
        query,
        total_vram_bytes=total_vram_bytes,
        token=token,
        list_models_client=list_models,
        model_info_client=model_info,
        url_size_client=_url_size_bytes,
        hub_url_client=hf_hub_url,
    )
    return result.model_dump(mode="json")
