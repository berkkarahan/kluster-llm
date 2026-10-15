"""Digest-addressed, resumable model preparation with atomic publication.

The transport supplies authenticated chunks and revalidates signed authority
through ``check``; no worker accepts a master filesystem path as a destination.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from collections.abc import Callable, Iterable

from kllm.modules.runtimes.interactors.registry import file_digest
from kllm.shared.atomic_io import replace_file


class ModelStager:
    def __init__(self, root: Path, *, disk_free: Callable[[Path], int] | None = None):
        self.root = root
        self.disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)

    def _paths(self, digest: str) -> tuple[Path, Path]:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Model staging requires a SHA-256 identity")
        return self.root / (digest + ".gguf"), self.root / (digest + ".part")

    def offset(self, digest: str) -> int:
        final, partial = self._paths(digest)
        if final.is_symlink() or partial.is_symlink():
            raise ValueError("Model cache entry cannot be a symlink")
        return partial.stat().st_size if partial.exists() else 0

    def prepare(
        self,
        digest: str,
        size: int,
        chunks: Callable[[int], Iterable[bytes]],
        *,
        check: Callable[[], None],
        reserve_bytes: int = 64 * 1024 * 1024,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        if isinstance(size, bool) or size <= 0 or reserve_bytes < 0:
            raise ValueError("Model size and disk reserve must be valid")
        check()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("Model cache cannot be a symlink")
        final, partial = self._paths(digest)
        offset = self.offset(digest)
        if final.exists() and final.stat().st_size == size and file_digest(final) == digest:
            if progress is not None:
                progress(size, size)
            return final
        if offset > size:
            partial.unlink()
            offset = 0
        if self.disk_free(self.root) < size - offset + reserve_bytes:
            raise ValueError("Not enough disk space to stage the selected model")
        if progress is not None:
            progress(offset, size)
        with partial.open("ab") as output:
            for chunk in chunks(offset):
                check()
                if not isinstance(chunk, bytes) or offset + len(chunk) > size:
                    raise ValueError("Model transfer exceeds the declared asset size")
                output.write(chunk)
                offset += len(chunk)
                if progress is not None:
                    progress(offset, size)
        check()
        if offset != size:
            raise ValueError("Model transfer incomplete; resume from the cached offset")
        if file_digest(partial) != digest:
            partial.unlink()
            raise ValueError("Model transfer digest mismatch; retry from the beginning")
        check()
        replace_file(partial, final)
        if progress is not None:
            progress(size, size)
        return final
