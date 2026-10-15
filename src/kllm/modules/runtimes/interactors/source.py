"""Read public Git refs and pin them once, on the selecting node."""

from __future__ import annotations

import json
import os
import subprocess
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from kllm.modules.runtimes.entities import UPSTREAM, RuntimeVersion, SourceRequest, runtime_id
from kllm.modules.primacpp.interactors.recipe import recipe_digest, recipe


def git_environment() -> dict[str, str]:
    # Long paths keep staged source checkouts writable when a runtime dir sits
    # deep; dropped credentials keep public fetches from prompting or reusing helpers.
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "core.longpaths",
        "GIT_CONFIG_VALUE_1": "true",
    }


def refs(repository: str) -> dict:
    source = SourceRequest(repository=repository, ref="HEAD")
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "--heads", "--", source.repository],
        env=git_environment(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise ValueError("Cannot read public Git repository; verify its URL and network access")
    found: dict[str, str] = {}
    for line in result.stdout.splitlines():
        pieces = line.split()
        if len(pieces) == 2:
            found[pieces[1]] = pieces[0]
    return found


def resolve(source: SourceRequest) -> RuntimeVersion:
    if source.ref_type == "commit":
        commit = source.ref.lower()
    else:
        name = ("refs/tags/" if source.ref_type == "tag" else "refs/heads/") + source.ref
        values = refs(source.repository)
        commit = values.get(name + "^{}", values.get(name))
        if not commit:
            raise ValueError(
                f"{source.ref_type.title()} {source.ref!r} does not exist in this repository"
            )
    digest = ""
    if source.engine == "primacpp":
        pinned = recipe()
        accept_prima_source(source, commit, pinned)
        digest = recipe_digest()
    return RuntimeVersion(
        **source.model_dump(), commit=commit, recipe_digest=digest,
        runtime_id=runtime_id(source.repository, commit, source.engine, digest),
    )


def accept_prima_source(source: SourceRequest, commit: str, pinned: dict) -> None:
    """Allow tags, branches, and commits on Prima repositories while rejecting unpatched upstream."""
    rejected = {
        UPSTREAM.removesuffix(".git").rstrip("/").lower(),
        str(pinned.get("upstream_repository") or "").removesuffix(".git").rstrip("/").lower(),
    }
    repo = source.repository.removesuffix(".git").rstrip("/").lower()
    if repo in rejected:
        raise ValueError(
            "Prima requires the pinned source repository or a Kluster-compatible Prima fork, not unpatched upstream"
        )
    release = str(pinned.get("source_release") or "")
    release_commit = str(pinned.get("source_release_commit") or pinned.get("source_commit") or "")
    if (
        source.repository == pinned.get("source_repository")
        and source.ref_type == "tag"
        and release
        and source.ref == release
        and release_commit
        and commit != release_commit
    ):
        raise ValueError(
            f"Prima release {release!r} moved; the recipe pin stands at "
            f"{release_commit[:12]}. Use it, or advance the recipe"
        )


def discover(repository: str) -> dict:
    source = SourceRequest(repository=repository, ref="HEAD")
    values = refs(source.repository)
    releases, warning = [], None
    url = urlsplit(source.repository)
    if url.hostname == "github.com":
        repo = url.path.removesuffix(".git").strip("/")
        try:
            request = Request(
                f"https://api.github.com/repos/{repo}/releases?per_page=30",
                headers={"User-Agent": "kluster-llm", "Accept": "application/vnd.github+json"},
            )
            with urlopen(request, timeout=15) as response:
                data = json.loads(response.read(1024 * 1024))
            releases = [
                {
                    "tag": item["tag_name"],
                    "name": item.get("name") or item["tag_name"],
                    "prerelease": bool(item.get("prerelease")),
                }
                for item in data
                if not item.get("draft")
            ]
        except (OSError, ValueError, KeyError, TypeError):
            warning = "Release listing unavailable; Git tags and branches are still available"
    return {
        "repository": source.repository,
        "releases": releases,
        "warning": warning,
        "tags": [
            k.removeprefix("refs/tags/")
            for k in values
            if k.startswith("refs/tags/") and not k.endswith("^{}")
        ],
        "branches": [k.removeprefix("refs/heads/") for k in values if k.startswith("refs/heads/")],
    }
