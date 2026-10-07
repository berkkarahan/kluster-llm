"""Installed artifact evidence; a probe's zero capabilities are insufficient."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .platform.base import PlatformRefusal


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def require_tcp_runtime(repo: Path, llama: Path, rpc: Path) -> dict:
    manifest = rpc.parent / 'rpc-transport.json'
    try:
        if manifest.stat().st_size > 65536:
            raise ValueError('oversized manifest')
        evidence = json.loads(manifest.read_text())
        if not isinstance(evidence, dict):
            raise ValueError('invalid manifest object')
        if evidence.get('schema') != 1 or evidence.get('ggml_rpc') != 'ON' or evidence.get('ggml_rpc_rdma') != 'OFF' or evidence.get('source_build') is not True:
            raise ValueError('unknown or RDMA-capable build')
        if evidence.get('runtime_id'):
            from kllm.runtimes.models import SourceRequest, runtime_id
            source = SourceRequest(repository=evidence['repository'], ref=evidence['commit'], ref_type='commit')
            if evidence['runtime_id'] != runtime_id(source.repository, source.ref):
                raise ValueError('source provenance mismatch')
        elif evidence.get('tag') != 'v0.5.0' or manifest.resolve().parent != (repo / 'vendor/bin').resolve():
            raise ValueError('unverified legacy runtime')
        if any(os.environ.get(key) for key in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH')):
            raise ValueError('unverified dynamic-library override')
        installed_at = evidence['installed_at']
        if isinstance(installed_at, bool) or not isinstance(installed_at, (int, float)) or installed_at <= 0:
            raise ValueError('unknown installation timestamp')
        artifacts = evidence['artifacts']
        for binary in (llama, rpc):
            if binary.resolve().parent != manifest.parent.resolve() or binary.name not in artifacts:
                raise ValueError('runtime binary is outside the recorded installation')
        for name, expected in artifacts.items():
            if Path(name).name != name or file_digest(manifest.parent / name) != expected:
                raise ValueError('installed artifact changed')
        if not any('ggml-rpc' in name for name in artifacts):
            raise ValueError('RPC transport library evidence missing')
        return evidence
    except (OSError, ValueError, KeyError, TypeError):
        raise PlatformRefusal('RPC_PROTOCOL_UNSUPPORTED', 'Both runtime ends need a source-built TCP-only runtime; prepare this version from source in Settings → llama.cpp (or install with --llama-only --rpc-tcp-only)') from None
