"""File helpers shared by uploads and downloads: hashing from disk, safe names, atomic writes."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import BinaryIO, Iterator

READ_BLOCK = 256 * 1024


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(READ_BLOCK), b""):
            h.update(block)
    return h.hexdigest()


def iter_slice(path: str | os.PathLike, start: int, length: int, block: int = READ_BLOCK) -> Iterator[bytes]:
    """The bytes [start, start+length) of a file, read lazily in blocks (never whole in memory)."""
    with open(path, "rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            data = f.read(min(block, left))
            if not data:
                raise OSError(f"文件在上传过程中变短了：{path}")
            left -= len(data)
            yield data


_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def safe_filename(name: str | None, fallback: str) -> str:
    """A server-supplied file name reduced to one path component that cannot escape a directory."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE.sub("_", name).strip(" .")
    if not name or name in (".", ".."):
        name = fallback
    return name[:200]


class AtomicWriter:
    """Write to a hidden temporary file beside the destination; `commit` renames it into place.

    A crash or a failed checksum leaves the destination untouched (either absent or the previous
    complete file) — never a half-written file under the final name.
    """

    def __init__(self, dest: str | os.PathLike):
        self.dest = Path(dest)
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{self.dest.name[:60]}.", suffix=".part", dir=str(self.dest.parent))
        self.tmp = Path(tmp)
        self.fh: BinaryIO = os.fdopen(fd, "wb")
        self.hash = hashlib.sha256()
        self.size = 0
        self.done = False

    def write(self, data: bytes) -> None:
        self.fh.write(data)
        self.hash.update(data)
        self.size += len(data)

    def reset(self) -> None:
        self.fh.seek(0)
        self.fh.truncate()
        self.hash = hashlib.sha256()
        self.size = 0

    def flush(self) -> None:
        self.fh.flush()

    def commit(self) -> Path:
        self.fh.flush()
        os.fsync(self.fh.fileno())
        self.fh.close()
        os.replace(self.tmp, self.dest)
        self.done = True
        return self.dest

    def discard(self) -> None:
        if self.done:
            return
        self.done = True
        try:
            self.fh.close()
        finally:
            self.tmp.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.discard()                  # no-op after commit; otherwise nothing half-written stays
