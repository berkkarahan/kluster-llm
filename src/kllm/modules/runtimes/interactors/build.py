"""Managed installer mode: isolated runtimes that never replace vendor/bin.

Preparation prefers a matching prebuilt GitHub release pack and falls back to a
source build when no pack resolves to the pinned commit or the pack cannot
satisfy the platform's backend contract. A source build is also what produces
the verified TCP-only RPC evidence the managed private network consumes.
"""

from __future__ import annotations

import json
from pathlib import Path
import platform
import shutil
import subprocess
import time

from kllm.modules.runtimes.entities import PRESET_VERSION, SourceRequest, runtime_id
from kllm.modules.runtimes.interactors.packs import PrebuiltIneligible, release_for_commit
from kllm.modules.runtimes.interactors.registry import atomic_json, file_digest
from kllm.modules.runtimes.interactors.source import git_environment


def _git_failure_detail(stderr: str) -> str:
    """Pick the most actionable line from a failed git invocation."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    notable = [line for line in lines if line.startswith(("fatal:", "error:"))]
    if notable:
        return notable[-1][:300]
    if lines:
        return lines[-1][:300]
    return "verify the public repository, network access and resolved commit"


def _materialize_source(vendor: Path, repository: str, commit: str) -> None:
    """Clone, pin and check out one public commit into vendor."""
    env = git_environment()

    def git(*args):
        result = subprocess.run(
            ["git", "-c", "core.longpaths=true", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode:
            raise ValueError(f"Source fetch failed: {_git_failure_detail(result.stderr)}")
        return result.stdout.strip()

    git("clone", "--filter=blob:none", "--no-checkout", "--", repository, str(vendor))
    git("-C", str(vendor), "fetch", "--depth", "1", "origin", commit)
    git("-C", str(vendor), "checkout", "--detach", commit)
    if git("-C", str(vendor), "rev-parse", "HEAD") != commit:
        raise ValueError("Downloaded source does not match the pinned commit")


def _vulkan_build_environment(install, backend: str) -> dict[str, str] | None:
    """Toolchain env for source builds that compile the Vulkan backend."""
    if backend == "cpu":
        return None
    if install._is_macos():
        return install.ensure_vulkan_macos()
    if backend == "vulkan" and install._is_windows():
        return install.ensure_windows_vulkan()
    return None


def managed_build(install, repository: str, commit: str, destination: Path, backend: str) -> dict:
    source = SourceRequest(repository=repository, ref=commit, ref_type="commit")
    commit = source.ref.lower()
    destination.mkdir(parents=True, exist_ok=True)
    vendor, build_dir, binaries = (
        destination / "source",
        destination / "source/build",
        destination / "bin",
    )
    resolved = install.detect_backend(backend)
    expected = install.stamp_backend_for_install(resolved)
    if not install.ensure_cmake():
        raise ValueError("CMake is required; follow docs/agent-install.md")
    install.ensure_git()
    print("KLLM_RUNTIME_PHASE=downloading", flush=True)
    _materialize_source(vendor, source.repository, commit)
    flags = [*install._cmake_backend_flags(resolved), "-DGGML_RPC=ON", "-DGGML_RPC_RDMA=OFF"]
    cmake_env = _vulkan_build_environment(install, resolved)
    signature = install._build_signature(commit, flags, cmake_env)
    # These globals belong to the isolated installer subprocess, never the agent.
    install.VENDOR_LLAMA, install.BIN_DIR = vendor, binaries
    install.STAMP_FILE = binaries / "llama-release.txt"
    print("KLLM_RUNTIME_PHASE=building", flush=True)
    ok, https = install.install_llama_source.compile_configured_tree(
        build_dir=build_dir,
        vendor=vendor,
        flags=flags,
        cmake_env=cmake_env,
        signature=signature,
        reset_flags=[],
        https_off_flag=install._HTTPS_OFF_FLAG,
        rpc_names=install._RPC_UPSTREAM_NAMES,
        run=install._run,
        warn=install._warn,
        write_signature=install._write_build_signature,
        remove_binaries=install._remove_stale_build_binaries,
        find_binary=install._find_build_tree_binary,
        probe_backends=install._probe_llama_backends,
        expected_stamp=expected,
        backends_ok=install.backends_satisfied,
        parse_backends=install.parse_stamp_backends,
        jobs=4,
    )
    if not ok:
        raise ValueError(
            "Fork cannot satisfy the selected Kluster backend/build preset; inspect the build log"
        )
    install.stage_built_binaries()
    print("KLLM_RUNTIME_PHASE=verifying", flush=True)
    llama, rpc = (
        install._resolve_runtime_binary("llama-server"),
        install._resolve_runtime_binary("rpc-server"),
    )
    if not llama or not rpc or llama.parent != binaries or rpc.parent != binaries:
        raise ValueError("Both llama-server and rpc-server must be produced by this build")
    install._write_rpc_transport_evidence(commit)
    evidence_path = binaries / "rpc-transport.json"
    evidence = json.loads(evidence_path.read_text())
    if evidence["ggml_rpc"] != "ON" or evidence["ggml_rpc_rdma"] != "OFF":
        raise ValueError("Fork did not produce verified TCP-only RPC binaries")
    artifacts = {
        p.name: file_digest(p)
        for p in binaries.iterdir()
        if p.is_file() and p.name != "rpc-transport.json"
    }
    identity = runtime_id(source.repository, commit)
    evidence.update(runtime_id=identity, commit=commit, repository=source.repository)
    atomic_json(evidence_path, evidence)
    manifest = {
        "schema": 1,
        "runtime_id": identity,
        "commit": commit,
        "repository": source.repository,
        "build_id": destination.name,
        "preset_version": PRESET_VERSION,
        "backend": expected,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "https": https,
        "installed_at": time.time(),
        "artifact_dir": str(binaries.resolve()),
        "llama_server": str(llama.resolve()),
        "rpc_server": str(rpc.resolve()),
        "mode": "source",
        "ggml_rpc": "ON",
        "ggml_rpc_rdma": "OFF",
        "artifacts": artifacts,
        "size_bytes": sum(p.stat().st_size for p in binaries.iterdir() if p.is_file()),
    }
    atomic_json(destination / "manifest.json", manifest)
    return manifest


def managed_prebuilt(
    install, repository: str, commit: str, destination: Path, backend: str
) -> dict:
    """Install a matching prebuilt GitHub release pack as a managed runtime.

    Raises :class:`PrebuiltIneligible` (never a partial publish) when no pack
    matches the pinned commit or the pack cannot satisfy the backend contract,
    so the caller can fall back to a source build. Packed binaries are staged
    outside ``destination/bin`` first: a rejected pack must never leave a
    binary that a later source build could be mistaken for.
    """
    source = SourceRequest(repository=repository, ref=commit, ref_type="commit")
    commit = source.ref.lower()
    destination.mkdir(parents=True, exist_ok=True)
    binaries = destination / "bin"
    resolved = install.detect_backend(backend)
    expected = install.stamp_backend_for_install(resolved)
    print("KLLM_RUNTIME_PHASE=downloading", flush=True)
    try:
        release = release_for_commit(source.repository, commit)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise PrebuiltIneligible(f"release lookup failed: {error}") from error
    if release is None:
        raise PrebuiltIneligible(f"no published release tag resolves to {commit[:12]}")
    assets = release.get("assets") or []
    asset = install.pick_release_asset(assets, resolved) or install.pick_release_asset(
        assets, "cpu"
    )
    if not asset or not asset.get("browser_download_url"):
        raise PrebuiltIneligible("release has no installable asset for this platform/backend")
    name = str(asset.get("name") or "pack")
    archive = destination / "prebuilt-download" / name
    archive.parent.mkdir(parents=True, exist_ok=True)
    size = asset.get("size")
    install._download(
        asset["browser_download_url"],
        archive,
        expected_size=size if isinstance(size, int) else None,
        expected_kind=install._expected_archive_kind(name),
    )
    staging = destination / "prebuilt"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    # Isolated installer globals: copy/extraction land in staging, not vendor/bin.
    install.VENDOR_LLAMA, install.BIN_DIR = destination / "source", staging
    install.STAMP_FILE = staging / "llama-release.txt"
    try:
        if not install._install_llama_archive(
            archive,
            tag=str(release.get("tag_name") or commit),
            backend=resolved,
            asset_name=name,
            stamp_backend=expected,
        ):
            raise PrebuiltIneligible(f"asset {name!r} did not contain usable llama binaries")
        # The release stamp is bootstrap bookkeeping; the manifest is evidence.
        (staging / "llama-release.txt").unlink(missing_ok=True)
        llama = staging / ("llama-server.exe" if install._is_windows() else "llama-server")
        rpc = staging / ("rpc-server.exe" if install._is_windows() else "rpc-server")
        if not llama.is_file() or not rpc.is_file():
            raise PrebuiltIneligible(f"asset {name!r} needs both llama-server and rpc-server")
        print("KLLM_RUNTIME_PHASE=verifying", flush=True)
        probed = install._probe_llama_backends(llama)
        if not install.backends_satisfied(expected, probed):
            raise PrebuiltIneligible(
                f"asset {name!r} reports backends {probed!r}, expected {install.parse_stamp_backends(expected)!r}"
            )
        binaries.mkdir(parents=True, exist_ok=True)
        for stale in binaries.iterdir():
            if stale.is_file():
                stale.unlink()
        for item in staging.iterdir():
            shutil.move(str(item), str(binaries / item.name))
        shutil.rmtree(staging, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    install.BIN_DIR = binaries
    llama, rpc = (
        install._resolve_runtime_binary("llama-server"),
        install._resolve_runtime_binary("rpc-server"),
    )
    if not llama or not rpc or llama.parent != binaries or rpc.parent != binaries:
        raise PrebuiltIneligible(f"asset {name!r} did not stage both runtime binaries")
    artifacts = {p.name: file_digest(p) for p in binaries.iterdir() if p.is_file()}
    identity = runtime_id(source.repository, commit)
    manifest = {
        "schema": 1,
        "runtime_id": identity,
        "commit": commit,
        "repository": source.repository,
        "build_id": destination.name,
        "preset_version": PRESET_VERSION,
        "backend": expected,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "https": "on",
        "installed_at": time.time(),
        "artifact_dir": str(binaries.resolve()),
        "llama_server": str(llama.resolve()),
        "rpc_server": str(rpc.resolve()),
        "mode": "prebuilt",
        "prebuilt": True,
        "asset": name,
        "tag": release.get("tag_name"),
        "artifacts": artifacts,
        "size_bytes": sum(p.stat().st_size for p in binaries.iterdir() if p.is_file()),
    }
    atomic_json(destination / "manifest.json", manifest)
    return manifest
