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


def gz(tmp_path, day, kind="depth"):
    path = tmp_path / f"binance_btcusdt_{kind}_{day}.jsonl.gz"
    path.write_bytes(gzip.compress(b"{}\n" * 100))
    return path


def test_retention_deletes_only_compressed_days_outside_the_window(tmp_path):
    expired = gz(tmp_path, "2026-08-01")          # older than 75 days before TODAY
    kept = gz(tmp_path, "2026-09-03")             # exactly today - 75: still inside
    raw_old = tmp_path / "binance_btcusdt_trade_2026-08-01.jsonl"
    raw_old.write_text("{}\n")                    # uncompressed: never deleted by retention
    size = expired.stat().st_size
    assert compact.delete_expired_days(tmp_path, TODAY, 75) == (1, size)
    assert not expired.exists() and kept.exists() and raw_old.exists()


def test_pinned_days_are_never_deleted(tmp_path):
    pinned = gz(tmp_path, "2026-08-01")
    assert compact.delete_expired_days(tmp_path, TODAY, 75, frozenset({date(2026, 8, 1)})) == (0, 0)
    assert pinned.exists()


@pytest.mark.parametrize("keep", [7, 59, 75.0, None])
def test_keep_window_below_the_60_day_research_need_is_refused(tmp_path, keep):
    old = gz(tmp_path, "2026-01-01")
    with pytest.raises(compact.CompactionError):
        compact.delete_expired_days(tmp_path, TODAY, keep)
    assert old.exists()
