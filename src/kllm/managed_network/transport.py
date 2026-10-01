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
    manifest = repo / 'vendor' / 'bin' / 'rpc-transport.json'
    try:
        if manifest.stat().st_size > 65536:
            raise ValueError('oversized manifest')
        evidence = json.loads(manifest.read_text())
        if not isinstance(evidence, dict):
            raise ValueError('invalid manifest object')
        if evidence.get('schema') != 1 or evidence.get('tag') != 'v0.5.0' or evidence.get('ggml_rpc') != 'ON' or evidence.get('ggml_rpc_rdma') != 'OFF' or evidence.get('source_build') is not True:
            raise ValueError('unknown or RDMA-capable build')
        if any(os.environ.get(key) for key in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH')):
            raise ValueError('unverified dynamic-library override')
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
        raise PlatformRefusal('RPC_PROTOCOL_UNSUPPORTED', 'Both runtime ends need a recorded TCP-only build; install with --llama-only --rpc-tcp-only') from None
