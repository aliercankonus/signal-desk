from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest
import yaml

from signal_desk.api import health_server
from signal_desk.api.overview import build_overview, build_sessions
from signal_desk.config.loader import validate_config

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def config_path(tmp_path):
    config = yaml.safe_load((ROOT / "config/config.example.yaml").read_text())
    base = tmp_path / "data"
    for key in list(config["storage"]):
        if key.endswith("_path"):
            config["storage"][key] = str(base / key.removesuffix("_path"))
    config["storage"]["base_path"] = str(base)
    config["retention"]["raw_pinned_days"] = ["2026-11-17"]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    raw = Path(config["storage"]["raw_path"])
    raw.mkdir(parents=True)
    (raw / "binance_btcusdt_depth_2026-11-16.jsonl.gz").write_bytes(b"x" * 100)
    (raw / "binance_btcusdt_trade_2026-11-16.jsonl.gz").write_bytes(b"x" * 50)
    (raw / "binance_btcusdt_depth_2026-11-17.jsonl.gz").write_bytes(b"x" * 10)
    (raw / "binance_btcusdt_depth_2026-11-18.jsonl").write_bytes(b"x" * 5)
    return path


def write_status(config_path, state, age_seconds=4, **extra):
    config = validate_config(config_path)
    status = Path(config["storage"]["base_path"]) / "status" / "collector_status.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    ts = (NOW - timedelta(seconds=age_seconds)).isoformat()
    status.write_text(json.dumps({"status": state, "reason": "streaming", "ts": ts,
                                  "gap_count": 0, "reconnect_count": 1, "policy": {"max_failures": 5},
                                  "last_error_diagnostics": {"secret": "kept out"}, **extra}))
    return config


def test_overview_summarises_status_raw_days_and_gates(config_path):
    overview = build_overview(write_status(config_path, "RUNNING"), NOW)
    c = overview["collector"]
    assert c["status"] == "RUNNING" and c["healthy"] and c["status_age_seconds"] == 4
    assert c["reconnect_count"] == 1 and c["max_failures"] == 5
    assert "last_error_diagnostics" not in c
    raw = overview["raw"]
    assert raw["days"] == ["2026-11-16", "2026-11-17", "2026-11-18"]
    assert raw["bytes"] == 165
    assert raw["next_expiry_day"] == "2027-01-31"  # 2026-11-16 + 75 keep days + 1
    assert overview["gates"]["days_with_data"] == 3
    assert overview["project"]["execution_enabled"] is False


@pytest.mark.parametrize("state,age", [("HALT", 4), ("RUNNING", 500)])
def test_overview_flags_unhealthy_collector(config_path, state, age):
    c = build_overview(write_status(config_path, state, age), NOW)["collector"]
    assert not c["healthy"] and c["health_reason"] != "ok"


def test_overview_without_status_file(config_path):
    c = build_overview(validate_config(config_path), NOW)["collector"]
    assert c["status"] == "UNKNOWN" and not c["healthy"]


def test_server_serves_page_and_api(config_path):
    write_status(config_path, "RUNNING")
    health_server.HealthHandler.config_path = str(config_path)
    health_server.HealthHandler.cached_status = None
    server = health_server.ThreadingHTTPServer(("127.0.0.1", 0), health_server.HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as resp:
            assert resp.headers["Content-Type"].startswith("text/html")
            assert b"/api/status" in resp.read()
        with urllib.request.urlopen(base + "/api/status") as resp:
            assert json.loads(resp.read())["collector"]["status"] == "RUNNING"
        with urllib.request.urlopen(base + "/sessions") as resp:
            assert b"/api/sessions" in resp.read()
        with urllib.request.urlopen(base + "/api/sessions") as resp:
            assert json.loads(resp.read())["first_day"] == "2026-11-16"
        with urllib.request.urlopen(base + "/health") as resp:
            assert json.loads(resp.read())["status"] == "ok"
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/nope")
        assert err.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_sessions_lists_each_day_with_files_state_and_expiry(config_path):
    config = validate_config(config_path)
    raw = Path(config["storage"]["raw_path"])
    (raw / "binance_btcusdt_depth_2026-11-17.jsonl.gz").unlink()  # leaves 11-17 with no files
    (raw / "binance_btcusdt_trade_2026-11-15.jsonl.gz").write_bytes(b"x" * 7)
    sessions = build_sessions(config, NOW)
    days = {d["day"]: d for d in sessions["days"]}
    assert list(days) == ["2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18"]
    assert sessions["first_day"] == "2026-11-15" and sessions["today"] == "2026-11-18"
    assert days["2026-11-16"]["state"] == "collected" and days["2026-11-16"]["bytes"] == 150
    assert days["2026-11-16"]["compressed"] is True
    assert [f["kind"] for f in days["2026-11-16"]["files"]] == ["depth", "trade"]
    assert days["2026-11-16"]["expires_day"] == "2027-01-31"
    assert days["2026-11-17"]["state"] == "missing" and days["2026-11-17"]["pinned"]
    assert days["2026-11-17"]["expires_day"] is None
    assert days["2026-11-18"]["state"] == "open" and days["2026-11-18"]["compressed"] is False
    assert all(d["quality"] is None for d in days.values())


def test_sessions_without_raw_data(config_path):
    config = validate_config(config_path)
    for f in Path(config["storage"]["raw_path"]).iterdir():
        f.unlink()
    sessions = build_sessions(config, NOW)
    assert sessions["days"] == [] and sessions["first_day"] is None


def write_events(config, *events):
    path = Path(config["storage"]["base_path"]) / "status" / "collector_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"ts": ts, "status": status, **extra}) for ts, status, extra in events]
    path.write_text("\n".join(lines + ["not json"]) + "\n")


def test_sessions_quality_comes_from_the_event_history(config_path):
    config = validate_config(config_path)
    raw = Path(config["storage"]["raw_path"])
    for day in ("2026-11-14", "2026-11-15"):
        (raw / f"binance_btcusdt_depth_{day}.jsonl.gz").write_bytes(b"x")
    write_events(config,
                 ("2026-11-15T10:00:00Z", "STARTED", {}),                      # history starts: partial day
                 ("2026-11-17T03:00:00Z", "RESYNCING", {"gap": True, "policy": {"x": 1}}),
                 ("2026-11-18T09:00:00Z", "RESYNCING", {"gap": False}))
    sessions = build_sessions(config, NOW)
    days = {d["day"]: d for d in sessions["days"]}
    assert sessions["quality_since"] == "2026-11-15"
    assert days["2026-11-14"]["quality"] is None
    assert days["2026-11-15"]["quality"]["partial"] and days["2026-11-15"]["quality"]["verdict"] == "reconnects"
    assert days["2026-11-16"]["quality"] == {"restarts": 0, "reconnects": 0, "gaps": 0, "halts": 0,
                                            "verdict": "clean", "partial": False}
    assert days["2026-11-17"]["quality"]["verdict"] == "gap"
    assert days["2026-11-18"]["quality"]["verdict"] == "reconnects"
    assert sessions["clean_days"] == 1  # only 11-16: 11-15 partial, 11-17 gap, 11-18 open

    overview = build_overview(config, NOW)
    assert overview["gates"]["clean_days"] == 1
    assert [e["status"] for e in overview["events"]] == ["RESYNCING", "RESYNCING", "STARTED"]
    assert "policy" not in overview["events"][1]


def test_no_history_means_quality_unknown(config_path):
    sessions = build_sessions(validate_config(config_path), NOW)
    assert sessions["quality_since"] is None and sessions["clean_days"] is None
    assert all(d["quality"] is None for d in sessions["days"])
