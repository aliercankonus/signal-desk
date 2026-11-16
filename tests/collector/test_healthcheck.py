from datetime import datetime, timedelta, timezone
import json

import pytest

from signal_desk.collector import healthcheck

NOW = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)


def status(tmp_path, state, age_seconds=5):
    ts = (NOW - timedelta(seconds=age_seconds)).isoformat().replace("+00:00", "Z")
    path = tmp_path / "collector_status.json"
    path.write_text(json.dumps({"status": state, "reason": "x", "ts": ts}))
    return path


@pytest.mark.parametrize("state", ["RUNNING", "SYNCING", "CONNECTING", "RESYNCING"])
def test_live_and_fresh_is_healthy(tmp_path, state):
    assert healthcheck.check(status(tmp_path, state), NOW) is None


@pytest.mark.parametrize("state", ["HALT", "STOPPED", "ERROR", "IDLE"])
def test_terminal_state_is_unhealthy(tmp_path, state):
    assert state in healthcheck.check(status(tmp_path, state), NOW)


def test_stale_status_is_unhealthy(tmp_path):
    assert "old" in healthcheck.check(status(tmp_path, "RUNNING", age_seconds=121), NOW)


def test_missing_or_corrupt_file_is_unhealthy(tmp_path):
    assert "unreadable" in healthcheck.check(tmp_path / "missing.json", NOW)
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert "unreadable" in healthcheck.check(bad, NOW)


def test_main_exit_codes(tmp_path):
    fresh = tmp_path / "fresh.json"
    fresh.write_text(json.dumps({"status": "RUNNING", "ts": datetime.now(timezone.utc).isoformat()}))
    assert healthcheck.main([str(fresh)]) == 0
    assert healthcheck.main([str(status(tmp_path, "HALT"))]) == 1
