"""Prebuilt llama.cpp release packs for managed runtime snapshots.

A managed runtime is identified by ``(repository, commit)``. A release pack is
only eligible when a published, non-draft GitHub release's tag resolves to that
exact commit, so the installed binary still proves the requested source
identity. There is deliberately no ``nightly-tag.txt`` indirection here: those
binaries come from a different commit, which would break the provenance
equality the runtime registry enforces. The bootstrap installer keeps its own
tag-based resolution because its pin is not commit-exact.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from kllm.modules.runtimes.interactors.source import refs

_API = "https://api.github.com/repos/{repo}/releases?per_page=30"
_HEADERS = {"User-Agent": "kluster-llm", "Accept": "application/vnd.github+json"}


class PrebuiltIneligible(RuntimeError):
    """No prebuilt pack can satisfy the requested source identity/contract."""


def github_repo(repository: str) -> str | None:
    """Return ``owner/name`` for a public GitHub repository, else ``None``."""
    parsed = urlsplit(repository)
    if parsed.hostname != "github.com":
        return None
    path = parsed.path.removesuffix(".git").strip("/")
    return path or None


def list_releases(repository: str) -> list[dict]:
    """Non-draft GitHub releases for ``repository`` (empty when not GitHub)."""
    repo = github_repo(repository)
    if repo is None:
        return []
    request = Request(_API.format(repo=repo), headers=_HEADERS)
    with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed https host
        data = json.loads(response.read(4 * 1024 * 1024))
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and not item.get("draft")]


def tags_for_commit(values: dict[str, str], commit: str) -> set[str]:
    """Tag names whose (possibly peeled) ref resolves to ``commit``."""
    target = commit.lower()
    tags: set[str] = set()
    for name, sha in values.items():
        if not name.startswith("refs/tags/"):
            continue
        base = name[len("refs/tags/") :]
        if base.endswith("^{}"):
            base = base[:-3]
        if sha.lower() == target:
            tags.add(base)
    return tags


def release_for_commit(
    repository: str,
    commit: str,
    *,
    releases: list[dict] | None = None,
    ref_values: dict[str, str] | None = None,
) -> dict | None:
    """The release whose tag peels to ``commit``, or ``None``.

    ``releases``/``ref_values`` are injectable so callers (and tests) can avoid
    network access; by default they are fetched from GitHub and git.
    """
    if github_repo(repository) is None:
        return None
    values = ref_values if ref_values is not None else refs(repository)
    tags = tags_for_commit(values, commit)
    if not tags:
        return None
    candidates = releases if releases is not None else list_releases(repository)
    return next(
        (release for release in candidates if release.get("tag_name") in tags),
        None,
    )
