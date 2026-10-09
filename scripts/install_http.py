"""Download and archive checks for the installer.

The facade in ``install.py`` keeps the names tests patch (``_download``,
``_download_once``, ``_DOWNLOAD_ATTEMPTS``) and passes those callables in.
"""

from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ZIP_MAGIC = b"PK\x03\x04"
_GZIP_MAGIC = b"\x1f\x8b"
_TAR_MAGIC_OFFSET = 257
_TAR_MAGIC = b"ustar"


def ssl_contexts() -> list[ssl.SSLContext]:
    """Default trust store, plus certifi when that package is installed."""
    ctxs = [ssl.create_default_context()]
    try:
        import certifi  # Optional CA bundle; a missing certifi keeps the default context.

        ctxs.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    return ctxs


def curl_bytes(
    url: str, *, which: Callable[[str], str | None], user_agent: str, accept: str
) -> bytes:
    curl = which("curl")
    if not curl:
        raise RuntimeError("curl not found")
    result = subprocess.run(
        [curl, "-fsSL", "-A", user_agent, "-H", f"Accept: {accept}", url],
        check=True,
        capture_output=True,
    )
    return result.stdout


def curl_download(
    url: str, dest: Path, *, which: Callable[[str], str | None], user_agent: str, accept: str
) -> None:
    curl = which("curl")
    if not curl:
        raise RuntimeError("curl not found")
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [curl, "-fsSL", "-A", user_agent, "-H", f"Accept: {accept}", "-o", str(dest), url],
        check=True,
    )


def http_json(
    url: str,
    *,
    headers: dict[str, str],
    contexts: Callable[[], list[ssl.SSLContext]],
    curl: Callable[[str], bytes],
) -> Any:
    req = urllib.request.Request(url, headers=headers)
    errors: list[BaseException] = []
    for ctx in contexts():
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            errors.append(exc)
    try:
        return json.loads(curl(url).decode("utf-8"))
    except Exception as exc:
        errors.append(exc)
    raise errors[-1]


def sniff_archive_kind(path: Path) -> str | None:
    """Return 'zip', 'gzip', or 'tar' when magic bytes look like an archive."""
    try:
        with path.open("rb") as handle:
            head = handle.read(512)
    except OSError:
        return None
    if head.startswith(_ZIP_MAGIC):
        return "zip"
    if head.startswith(_GZIP_MAGIC):
        return "gzip"
    if (
        len(head) > _TAR_MAGIC_OFFSET + 5
        and head[_TAR_MAGIC_OFFSET : _TAR_MAGIC_OFFSET + 5] == _TAR_MAGIC
    ):
        return "tar"
    return None


def expected_archive_kind(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return "gzip"
    if lower.endswith(".tar"):
        return "tar"
    return None


def validate_downloaded_file(
    dest: Path,
    *,
    expected_size: int | None = None,
    expected_kind: str | None = None,
    content_length: int | None = None,
    sniff: Callable[[Path], str | None] = sniff_archive_kind,
) -> None:
    if not dest.is_file():
        raise RuntimeError(f"download missing: {dest}")
    size = dest.stat().st_size
    if size <= 0:
        raise RuntimeError(f"download empty: {dest}")
    if content_length is not None and content_length > 0 and size != content_length:
        raise RuntimeError(
            f"download truncated: got {size} bytes, Content-Length was {content_length}"
        )
    if expected_size is not None and expected_size > 0 and size != expected_size:
        raise RuntimeError(f"download size mismatch: got {size} bytes, expected {expected_size}")
    if expected_kind is None:
        return
    kind = sniff(dest)
    if kind != expected_kind:
        preview = dest.read_bytes()[:120]
        raise RuntimeError(
            f"download is not a valid {expected_kind} archive (got {size} bytes, magic={preview!r})"
        )


def download_once(
    url: str,
    dest: Path,
    *,
    headers: dict[str, str],
    contexts: Callable[[], list[ssl.SSLContext]],
    curl: Callable[[str, Path], None],
    validate: Callable[..., None],
    expected_size: int | None = None,
    expected_kind: str | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=headers)
    errors: list[BaseException] = []
    for ctx in contexts():
        try:
            with (
                urllib.request.urlopen(req, context=ctx, timeout=600) as resp,
                dest.open("wb") as out,
            ):
                cl_raw = resp.headers.get("Content-Length")
                content_length = int(cl_raw) if cl_raw and cl_raw.isdigit() else None
                shutil.copyfileobj(resp, out)
            validate(
                dest,
                expected_size=expected_size,
                expected_kind=expected_kind,
                content_length=content_length,
            )
            return
        except Exception as exc:
            errors.append(exc)
            if dest.exists():
                dest.unlink()
    try:
        curl(url, dest)
        validate(dest, expected_size=expected_size, expected_kind=expected_kind)
        return
    except Exception as exc:
        errors.append(exc)
        if dest.exists():
            dest.unlink()
    raise errors[-1]


def download_with_retries(
    url: str,
    dest: Path,
    *,
    attempts: int,
    once: Callable[..., None],
    warn: Callable[[str], None],
    kind_for: Callable[[str], str | None],
    expected_size: int | None = None,
    expected_kind: str | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}", flush=True)
    kind = expected_kind or kind_for(dest.name) or kind_for(url)
    errors: list[BaseException] = []
    for attempt in range(1, attempts + 1):
        try:
            once(url, dest, expected_size=expected_size, expected_kind=kind)
            return
        except Exception as exc:
            errors.append(exc)
            if dest.exists():
                dest.unlink()
            if attempt < attempts:
                warn(f"download attempt {attempt}/{attempts} failed ({exc}); retrying")
    raise errors[-1]
