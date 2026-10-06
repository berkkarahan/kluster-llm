"""Managed installer mode: source builds that never replace vendor/bin."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import time

from .models import PRESET_VERSION, SourceRequest, runtime_id
from .registry import atomic_json, file_digest
from .source import git_environment


def managed_build(install, repository: str, commit: str, destination: Path, backend: str) -> dict:
    source = SourceRequest(repository=repository, ref=commit, ref_type='commit')
    commit = source.ref.lower()
    destination.mkdir(parents=True, exist_ok=True)
    vendor, build_dir, binaries = destination / 'source', destination / 'source/build', destination / 'bin'
    resolved = install.detect_backend(backend)
    expected = install.stamp_backend_for_install(resolved)
    if not install.ensure_cmake():
        raise ValueError('CMake is required; follow docs/agent-install.md')
    install.ensure_git()
    print('KLLM_RUNTIME_PHASE=downloading', flush=True)
    env = git_environment()
    def git(*args):
        result = subprocess.run(['git', *args], env=env, capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise ValueError('Source fetch failed; verify the public repository and resolved commit')
        return result.stdout.strip()
    git('clone', '--filter=blob:none', '--no-checkout', '--', source.repository, str(vendor))
    git('-C', str(vendor), 'fetch', '--depth', '1', 'origin', commit)
    git('-C', str(vendor), 'checkout', '--detach', commit)
    if git('-C', str(vendor), 'rev-parse', 'HEAD') != commit:
        raise ValueError('Downloaded source does not match the pinned commit')
    flags = [*install._cmake_backend_flags(resolved), '-DGGML_RPC=ON', '-DGGML_RPC_RDMA=OFF']
    cmake_env = install.ensure_vulkan_macos() if install._is_macos() and resolved != 'cpu' else None
    signature = install._build_signature(commit, flags, cmake_env)
    # These globals belong to the isolated installer subprocess, never the agent.
    install.VENDOR_LLAMA, install.BIN_DIR = vendor, binaries
    install.STAMP_FILE = binaries / 'llama-release.txt'
    print('KLLM_RUNTIME_PHASE=building', flush=True)
    ok, https = install.install_llama_source.compile_configured_tree(
        build_dir=build_dir, vendor=vendor, flags=flags, cmake_env=cmake_env, signature=signature,
        reset_flags=[], https_off_flag=install._HTTPS_OFF_FLAG, rpc_names=install._RPC_UPSTREAM_NAMES,
        run=install._run, warn=install._warn, write_signature=install._write_build_signature,
        remove_binaries=install._remove_stale_build_binaries, find_binary=install._find_build_tree_binary,
        probe_backends=install._probe_llama_backends, expected_stamp=expected,
        backends_ok=install.backends_satisfied, parse_backends=install.parse_stamp_backends, jobs=4)
    if not ok:
        raise ValueError('Fork cannot satisfy the selected Kluster backend/build preset; inspect the build log')
    install.stage_built_binaries()
    print('KLLM_RUNTIME_PHASE=verifying', flush=True)
    llama, rpc = install._resolve_runtime_binary('llama-server'), install._resolve_runtime_binary('rpc-server')
    if not llama or not rpc or llama.parent != binaries or rpc.parent != binaries:
        raise ValueError('Both llama-server and rpc-server must be produced by this build')
    install._write_rpc_transport_evidence(commit)
    evidence_path = binaries / 'rpc-transport.json'
    evidence = json.loads(evidence_path.read_text())
    if evidence['ggml_rpc'] != 'ON' or evidence['ggml_rpc_rdma'] != 'OFF':
        raise ValueError('Fork did not produce verified TCP-only RPC binaries')
    artifacts = {p.name: file_digest(p) for p in binaries.iterdir() if p.is_file() and p.name != 'rpc-transport.json'}
    identity = runtime_id(source.repository, commit)
    evidence.update(runtime_id=identity, commit=commit, repository=source.repository)
    atomic_json(evidence_path, evidence)
    manifest = {'schema': 1, 'runtime_id': identity, 'commit': commit, 'repository': source.repository,
                'build_id': destination.name, 'preset_version': PRESET_VERSION, 'backend': expected,
                'platform': platform.system(), 'architecture': platform.machine(), 'https': https,
                'installed_at': time.time(), 'artifact_dir': str(binaries.resolve()),
                'llama_server': str(llama.resolve()), 'rpc_server': str(rpc.resolve()),
                'ggml_rpc': 'ON', 'ggml_rpc_rdma': 'OFF', 'artifacts': artifacts,
                'size_bytes': sum(p.stat().st_size for p in binaries.iterdir() if p.is_file())}
    atomic_json(destination / 'manifest.json', manifest)
    return manifest
