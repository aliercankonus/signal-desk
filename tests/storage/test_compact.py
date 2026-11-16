from datetime import date
import gzip
import os
import time

import pytest

from signal_desk.storage import compact

TODAY = date(2026, 11, 17)


def raw(tmp_path, day, lines=200, idle=True):
    path = tmp_path / f"binance_btcusdt_depth_{day}.jsonl"
    path.write_bytes(b"".join(b'{"event_id":"e%d","payload":"%s"}\n' % (i, b"x" * 40) for i in range(lines)))
    if idle:
        old = time.time() - 3600
        os.utime(path, (old, old))
    return path


def test_closed_day_is_compressed_verified_and_original_removed(tmp_path):
    path = raw(tmp_path, "2026-11-16")
    original = path.read_bytes()
    files, saved = compact.compress_closed_days(tmp_path, TODAY)
    target = tmp_path / (path.name + ".gz")
    assert (files, saved) == (1, len(original) - target.stat().st_size)
    assert saved > 0 and not path.exists()
    assert gzip.decompress(target.read_bytes()) == original
    assert not list(tmp_path.glob("*.part"))


def test_today_and_recently_written_files_are_left_alone(tmp_path):
    today = raw(tmp_path, "2026-11-17")
    busy = raw(tmp_path, "2026-11-16", idle=False)
    assert compact.compress_closed_days(tmp_path, TODAY) == (0, 0)
    assert today.exists() and busy.exists()
    assert not list(tmp_path.glob("*.gz"))


def test_unrelated_files_are_ignored(tmp_path):
    other = tmp_path / "notes.jsonl"
    other.write_text("{}\n")
    assert compact.compress_closed_days(tmp_path, TODAY) == (0, 0)
    assert other.exists()


def test_interrupted_part_is_discarded_and_redone(tmp_path):
    path = raw(tmp_path, "2026-11-16")
    original = path.read_bytes()
    (tmp_path / (path.name + ".gz.part")).write_bytes(b"truncated")
    assert compact.compress_closed_days(tmp_path, TODAY)[0] == 1
    assert gzip.decompress((tmp_path / (path.name + ".gz")).read_bytes()) == original


def test_existing_matching_gz_finishes_the_interrupted_unlink(tmp_path):
    path = raw(tmp_path, "2026-11-16")
    target = tmp_path / (path.name + ".gz")
    target.write_bytes(gzip.compress(path.read_bytes()))
    assert compact.compress_closed_days(tmp_path, TODAY)[0] == 1
    assert not path.exists() and target.exists()


def test_mismatched_gz_keeps_the_original(tmp_path):
    path = raw(tmp_path, "2026-11-16")
    target = tmp_path / (path.name + ".gz")
    target.write_bytes(gzip.compress(b"different\n"))
    with pytest.raises(compact.CompactionError):
        compact.compress_closed_days(tmp_path, TODAY)
    assert path.exists()


def test_failed_verification_keeps_the_original(tmp_path, monkeypatch):
    path = raw(tmp_path, "2026-11-16")
    monkeypatch.setattr(compact, "_gz_matches", lambda *a: False)
    with pytest.raises(compact.CompactionError):
        compact.compress_closed_days(tmp_path, TODAY)
    assert path.exists()
    assert not list(tmp_path.glob("*.gz*"))
