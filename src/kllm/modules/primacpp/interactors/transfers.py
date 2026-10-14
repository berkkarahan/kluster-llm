"""Node/operation-scoped chunk grants over the paired signed command channel."""

from __future__ import annotations

import base64
import hashlib
import math
import threading
import time
from pathlib import Path
from uuid import UUID

from kllm.modules.primacpp.interactors.execution import Asset
from kllm.modules.primacpp.interactors.staging import ModelStager
from kllm.modules.runtimes.interactors.registry import file_digest

CHUNK_BYTES = 24 * 1024  # Base64 plus the signed envelope stays below MAX_FRAME.
TRANSFER_LEASE_SECONDS = 600


class TransferGrants:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.grants: dict[tuple[str, str, str], dict] = {}

    def grant(
        self,
        node: str,
        operation: str,
        asset: Asset,
        path: Path,
        *,
        ttl_s=TRANSFER_LEASE_SECONDS,
        progress=None,
        check=lambda: None,
    ):
        UUID(operation)
        if type(ttl_s) not in (int, float) or not math.isfinite(ttl_s) or ttl_s <= 0 or ttl_s > 600:
            raise ValueError("Invalid asset grant lifetime")
        before = path.stat()
        if path.is_symlink() or not path.is_file() or before.st_size != asset.size:
            raise ValueError("Transfer source does not match declared asset")
        if progress is None:
            digest = file_digest(path)
        else:
            sha = hashlib.sha256()
            with path.open("rb") as source:
                while data := source.read(1024 * 1024):
                    check()
                    sha.update(data)
                    progress(len(data))
            digest = sha.hexdigest()
        after = path.stat()
        if (
            digest != asset.sha256
            or path.is_symlink()
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise ValueError("Transfer source does not match declared asset")
        check()
        with self.lock:
            self.grants[(node, operation, asset.sha256)] = {
                "asset": asset,
                "path": path,
                "deadline": self.clock() + ttl_s,
                "identity": (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
                "ttl_s": ttl_s,
                "acknowledged_offset": None,
                "served_range": None,
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
            if grant["acknowledged_offset"] is None:
                grant["acknowledged_offset"] = (
                    offset  # Resume begins at the receiver's verified offset.
                )
            grant["served_range"] = (offset, offset + len(chunk))
            return {
                "sha256": digest,
                "offset": offset,
                "size": asset.size,
                "data": base64.b64encode(chunk).decode("ascii"),
            }

    def acknowledge(self, node: str, operation: str, digest: str, offset: int):
        with self.lock:
            grant = self.grants.get((node, operation, digest))
            if grant is None or self.clock() >= grant["deadline"]:
                raise ValueError("Asset grant is missing or expired")
            served = grant["served_range"]
            if (
                type(offset) is not int
                or served is None
                or offset != served[1]
                or offset <= served[0]
                or served[0] != grant["acknowledged_offset"]
            ):
                raise ValueError("Asset acknowledgement does not match forward transfer progress")
            path = grant["path"]
            current = path.stat()
            if (
                path.is_symlink()
                or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                != grant["identity"]
            ):
                raise ValueError("Authorized transfer asset changed")
            grant["acknowledged_offset"] = offset
            grant["served_range"] = None
            grant["deadline"] = self.clock() + grant["ttl_s"]

    def revoke(self, operation: str):
        with self.lock:
            for key in list(self.grants):
                if key[1] == operation:
                    self.grants.pop(key)


class PreparationLeases:
    """Renew enrolled workers only as this master-owned operation makes progress."""

    def __init__(self, renew, *, clock=time.monotonic, interval=30):
        self.renew, self.clock, self.interval = renew, clock, interval
        self.nodes = {}
        self.progress = 0
        self.last_progress = -1
        self.next_at = clock() + interval

    def enroll(self, node, job_id):
        self.nodes[node] = job_id

    def advance(self, amount=1):
        if type(amount) is not int or amount <= 0 or self.progress + amount > (1 << 53) - 1:
            raise ValueError("Invalid preparation progress")
        self.progress += amount
        self.check()

    def check(self, *, force=False):
        if (not force and self.clock() < self.next_at) or self.progress <= self.last_progress:
            return
        for node, job_id in self.nodes.items():
            result = self.renew(node, job_id, self.progress)
            if (
                result.get("job_id") != job_id
                or result.get("phase") not in ("preparing", "prepared")
                or type(result.get("lease_progress")) is not int
                or result["lease_progress"] != self.progress
            ):
                raise ValueError("Worker preparation renewal did not acknowledge exact progress")
        self.last_progress = self.progress
        self.next_at = self.clock() + self.interval


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
        self.verified: dict[str, tuple[int, int, int, int, int]] = {}

    @staticmethod
    def _identity(path: Path) -> tuple[int, int, int, int, int]:
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _remember(self, digest: str, identity: tuple[int, int, int, int, int]) -> None:
        if len(self.verified) >= 8192 and digest not in self.verified:
            self.verified.clear()
        self.verified[digest] = identity

    def offset(self, asset: Asset) -> int:
        from kllm.shared.atomic_io import replace_file

        with self.lock:
            final, partial = self.stager._paths(asset.sha256)
            if final.is_symlink() or partial.is_symlink():
                raise ValueError("Model cache entry cannot be a symlink")
            if final.is_file():
                before = self._identity(final)
                if before[2] == asset.size and (self.verified.get(asset.sha256) == before
                        or file_digest(final) == asset.sha256 and before == self._identity(final)):
                    self._remember(asset.sha256, before)
                    return asset.size
                self.verified.pop(asset.sha256, None)
            offset = self.stager.offset(asset.sha256)
            if offset == asset.size:
                # A lost final-chunk acknowledgement must not force a full re-push.
                before = self._identity(partial)
                if file_digest(partial) == asset.sha256 and before == self._identity(partial):
                    replace_file(partial, final)
                    self._remember(asset.sha256, self._identity(final))
                    return asset.size
                partial.unlink()
                return 0
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
                before = self._identity(partial)
                if file_digest(partial) != asset.sha256 or before != self._identity(partial):
                    partial.unlink()
                    raise ValueError("Asset push digest mismatch")
                check()
                replace_file(partial, final)
                self._remember(asset.sha256, self._identity(final))
            return end
