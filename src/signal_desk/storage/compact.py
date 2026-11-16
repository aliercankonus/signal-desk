"""Compress closed raw days in place: name.jsonl -> name.jsonl.gz.

A day file is compressed only after its UTC day has ended and it has been idle for a while.
The .gz is written as .gz.part, verified against the original (size and SHA-256 of the
decompressed bytes), renamed, and only then is the original removed. Nothing is ever deleted
without a verified compressed copy. Readers that glob *.jsonl ignore .gz files; decompress a
day (``gzip -dk``) into a separate directory for replay or research.
"""
from __future__ import annotations

from datetime import date
import gzip
import hashlib
import os
from pathlib import Path
import re
import time

DAY_FILE = re.compile(r"_(\d{4}-\d{2}-\d{2})\.jsonl$")
CHUNK = 1024 * 1024


class CompactionError(RuntimeError):
    pass


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _digest(stream, pause: float) -> tuple[int, bytes]:
    digest, size = hashlib.sha256(), 0
    while chunk := stream.read(CHUNK):
        digest.update(chunk)
        size += len(chunk)
        if pause:
            time.sleep(pause)
    return size, digest.digest()


def _gz_matches(target: Path, size: int, digest: bytes, pause: float) -> bool:
    with gzip.open(target, "rb") as stream:
        return _digest(stream, pause) == (size, digest)


def compress_file(path: str | Path, *, pause: float = 0.0) -> int:
    """Compress one closed file; return bytes saved. The original is removed only after verification."""
    path = Path(path)
    target = path.with_name(path.name + ".gz")
    part = path.with_name(path.name + ".gz.part")
    before = path.stat().st_size
    if target.exists():
        # An earlier run stopped between rename and unlink; keep whichever copy is proven complete.
        with path.open("rb") as src:
            size, digest = _digest(src, pause)
        if not _gz_matches(target, size, digest, pause):
            raise CompactionError(f"{target.name} exists and does not match {path.name}")
    else:
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as src, part.open("xb") as raw_out:
            with gzip.GzipFile(filename=path.name, mode="wb", compresslevel=6, fileobj=raw_out, mtime=0) as out:
                while chunk := src.read(CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
                    if pause:
                        time.sleep(pause)
            raw_out.flush()
            os.fsync(raw_out.fileno())
        digest = digest.digest()
        if size != before or path.stat().st_size != before or not _gz_matches(part, size, digest, pause):
            part.unlink()
            raise CompactionError(f"Verification failed for {path.name}; original kept")
        os.replace(part, target)
        _fsync_dir(path.parent)
    saved = before - target.stat().st_size
    path.unlink()
    _fsync_dir(path.parent)
    return saved


def compress_closed_days(raw_dir: str | Path, today: date, *, min_idle_seconds: float = 600,
                         pause: float = 0.0) -> tuple[int, int]:
    """Compress every *_YYYY-MM-DD.jsonl older than ``today`` and idle long enough.

    Returns (files compressed, bytes saved).
    """
    directory = Path(raw_dir)
    for leftover in directory.glob("*.jsonl.gz.part"):
        leftover.unlink()  # interrupted run; its original is still in place
    files = saved = 0
    for path in sorted(directory.glob("*.jsonl")):
        match = DAY_FILE.search(path.name)
        if not match or date.fromisoformat(match.group(1)) >= today:
            continue
        if time.time() - path.stat().st_mtime < min_idle_seconds:
            continue
        saved += compress_file(path, pause=pause)
        files += 1
    return files, saved
