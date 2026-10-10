"""Node/operation-scoped chunk grants over the paired signed command channel."""

from __future__ import annotations

import base64
import threading
import time
from pathlib import Path
from uuid import UUID

from kllm.modules.primacpp.interactors.execution import Asset
from kllm.modules.primacpp.interactors.staging import ModelStager
from kllm.modules.runtimes.interactors.registry import file_digest

CHUNK_BYTES = 24 * 1024  # Base64 plus the signed envelope stays below MAX_FRAME.


class TransferGrants:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.grants: dict[tuple[str, str, str], dict] = {}

    def grant(self, node: str, operation: str, asset: Asset, path: Path, *, ttl_s=600):
        UUID(operation)
        if ttl_s <= 0 or ttl_s > 600:
            raise ValueError("Invalid asset grant lifetime")
        before = path.stat()
        if (
            path.is_symlink()
            or not path.is_file()
            or before.st_size != asset.size
            or file_digest(path) != asset.sha256
        ):
            raise ValueError("Transfer source does not match declared asset")
        with self.lock:
            self.grants[(node, operation, asset.sha256)] = {
                "asset": asset,
                "path": path,
                "deadline": self.clock() + ttl_s,
                "identity": (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
            }

    def chunk(self, node: str, operation: str, digest: str, offset: int) -> dict:
        with self.lock:
            grant = self.grants.get((node, operation, digest))
            if grant is None or grant["deadline"] <= self.clock():
                raise ValueError("Asset grant is missing or expired")
            asset, path = grant["asset"], grant["path"]
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or not 0 <= offset <= asset.size
            ):
                raise ValueError("Asset offset is outside the authorized size")
            with path.open("rb") as source:
                import os

                current = os.fstat(source.fileno())
                if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != grant[
                    "identity"
                ]:
                    raise ValueError("Authorized transfer asset changed")
                source.seek(offset)
                chunk = source.read(min(CHUNK_BYTES, asset.size - offset))
            return {
                "sha256": digest,
                "offset": offset,
                "size": asset.size,
                "data": base64.b64encode(chunk).decode("ascii"),
            }

    def revoke(self, operation: str):
        with self.lock:
            for key in list(self.grants):
                if key[1] == operation:
                    self.grants.pop(key)


class AssetReceiver:
    def __init__(self, root: Path, *, disk_free=None):
        self.stager = ModelStager(root, disk_free=disk_free)

    def receive(self, asset: Asset, fetch, check) -> Path:
        def chunks(offset):
            while offset < asset.size:
                check()
                reply = fetch(asset.sha256, offset)
                if (reply.get("sha256"), reply.get("offset"), reply.get("size")) != (
                    asset.sha256,
                    offset,
                    asset.size,
                ):
                    raise ValueError("Asset chunk identity/offset mismatch")
                try:
                    chunk = base64.b64decode(reply["data"], validate=True)
                except (KeyError, ValueError, TypeError) as error:
                    raise ValueError("Malformed asset chunk") from error
                if not chunk or len(chunk) > CHUNK_BYTES:
                    raise ValueError("Asset chunk exceeds transport bounds")
                offset += len(chunk)
                yield chunk

        return self.stager.prepare(asset.sha256, asset.size, chunks, check=check)


class PushReceiver:
    """Accept only signed master pushes for assets declared by a preparation job."""

    def __init__(self, root: Path):
        self.stager = ModelStager(root)
        self.lock = threading.RLock()

    def offset(self, asset: Asset) -> int:
        final, _ = self.stager._paths(asset.sha256)
        if (
            final.exists()
            and not final.is_symlink()
            and final.stat().st_size == asset.size
            and file_digest(final) == asset.sha256
        ):
            return asset.size
        offset = self.stager.offset(asset.sha256)
        return offset if offset < asset.size else 0

    def push(self, asset: Asset, reply: dict, check) -> int:
        from kllm.shared.atomic_io import replace_file

        check()
        with self.lock:
            offset = self.offset(asset)
            if (reply.get("sha256"), reply.get("offset"), reply.get("size")) != (
                asset.sha256,
                offset,
                asset.size,
            ):
                raise ValueError("Asset push does not match the prepared identity/offset")
            try:
                chunk = base64.b64decode(reply["data"], validate=True)
            except (KeyError, ValueError, TypeError) as error:
                raise ValueError("Malformed asset push") from error
            if not chunk or len(chunk) > CHUNK_BYTES or offset + len(chunk) > asset.size:
                raise ValueError("Asset push exceeds transport bounds")
            self.stager.root.mkdir(parents=True, exist_ok=True)
            if self.stager.root.is_symlink():
                raise ValueError("Asset cache cannot be a symlink")
            if self.stager.disk_free(self.stager.root) < asset.size - offset + 64 * 1024 * 1024:
                raise ValueError("Not enough disk space for prepared asset")
            final, partial = self.stager._paths(asset.sha256)
            check()
            if partial.exists() and partial.stat().st_size != offset:
                partial.unlink()
            with partial.open("ab") as output:
                output.write(chunk)
            end = offset + len(chunk)
            if end == asset.size:
                if file_digest(partial) != asset.sha256:
                    partial.unlink()
                    raise ValueError("Asset push digest mismatch")
                check()
                replace_file(partial, final)
            return end
