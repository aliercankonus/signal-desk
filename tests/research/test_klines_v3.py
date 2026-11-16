import json
from datetime import date, timedelta, datetime, timezone
import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from signal_desk.research import klines_v3 as k

SPEC = Path(__file__).resolve().parents[2] / "docs/research/klines_daily_v3.json"


def write_month(out, name, rows, micro=False):
    buf = io.StringIO()
    for day, o, c in rows:
        ms = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
        buf.write(f"{ms * 1000 if micro else ms},{o},{o},{o},{c},1,0,1,1,1,1,0\n")
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as zf:
        zf.writestr(name.replace(".zip", ".csv"), buf.getvalue())
    (out / name).write_bytes(raw.getvalue())
    (out / f"{name}.CHECKSUM").write_text(f"{hashlib.sha256(raw.getvalue()).hexdigest()}  {name}\n")


def test_spec_is_pinned(tmp_path):
    spec, sha = k.load_spec(SPEC)
    assert sha == k.PINNED_SPEC_SHA256 and len(k.configurations(spec)) == spec["configuration_count"]
    bad = tmp_path / "s.json"
    bad.write_bytes(SPEC.read_bytes() + b" ")
    with pytest.raises(k.KlinesError, match="pinned"):
        k.load_spec(bad)


def test_load_days_microseconds_checksum_and_gaps(tmp_path):
    write_month(tmp_path, "BTCUSDT-1d-2024-12.zip", [(date(2024, 12, 31), 1, 2)])
    write_month(tmp_path, "BTCUSDT-1d-2025-01.zip", [(date(2025, 1, 1), 2, 3)], micro=True)
    days, opens, closes = k.load_days(tmp_path)
    assert days == [date(2024, 12, 31), date(2025, 1, 1)] and opens == [1.0, 2.0] and closes == [2.0, 3.0]
    assert k.load_days(tmp_path, last_day=date(2024, 12, 31))[0] == [date(2024, 12, 31)]
    write_month(tmp_path, "BTCUSDT-1d-2025-02.zip", [(date(2025, 2, 3), 1, 1)])
    with pytest.raises(k.KlinesError, match="gap"):
        k.load_days(tmp_path)
    (tmp_path / "BTCUSDT-1d-2025-02.zip.CHECKSUM").write_text("0" * 64 + "  BTCUSDT-1d-2025-02.zip\n")
    with pytest.raises(k.KlinesError, match="CHECKSUM"):
        k.load_days(tmp_path)


def test_rules_are_causal_and_correct():
    closes = [10, 11, 12, 9, 8, 13, 14, 7.0]
    assert k.states({"family": "sma_trend", "N": 2}, closes) == [0, 1, 1, 0, 0, 1, 1, 0]
    assert k.states({"family": "momentum", "L": 1}, closes) == [0, 1, 1, 0, 0, 1, 1, 0]
    # donchian N=2: enter above max of previous 2, exit below min of previous 1
    assert k.states({"family": "donchian", "N": 2}, closes) == [0, 0, 1, 0, 0, 1, 1, 0]
    # 9/12-1 = -25% triggers; hold 2 days -> long after closes 3 and 4 (8/9 is -11%: restarts)
    assert k.states({"family": "mean_reversion", "X": 0.10, "H": 2}, closes) == [0, 0, 0, 1, 1, 1, 0, 1]
    for cfg in [{"family": "sma_trend", "N": 3}, {"family": "donchian", "N": 4},
                {"family": "mean_reversion", "X": 0.05, "H": 3}]:
        full = k.states(cfg, closes)
        assert all(k.states(cfg, closes[:i + 1]) == full[:i + 1] for i in range(len(closes)))


def test_simulate_costs_embargo_and_forced_exit():
    opens = [100, 100, 110, 121, 121]
    r = k.simulate([1, 1, 1, 1, 1], opens, 0, 4, cost=0.01)
    # embargo: flat day 0; long days 1-2 (+21%) and day 3 (0%); entry+exit cost
    assert r["round_trips"] == 1 and r["exposure"] == 0.75
    assert r["total_net_return"] == pytest.approx(0.99 * 1.21 * 0.99 - 1)
    bh = k.simulate([1] * 5, opens, 0, 4, cost=0.0, embargo=False)
    assert bh["total_net_return"] == pytest.approx(0.21) and bh["max_drawdown"] == 0


def _series(tmp_path, closes_fn):
    start, end = date(2017, 8, 17), date(2026, 11, 1)
    by_month, d, i = {}, start, 0
    while d <= end:
        c = closes_fn(i)
        by_month.setdefault(d.strftime("%Y-%m"), []).append((d, c, c))
        d += timedelta(days=1)
        i += 1
    for m, rows in by_month.items():
        write_month(tmp_path, f"BTCUSDT-1d-{m}.zip", rows)


def test_validation_ignores_test_period_and_test_runs_once(tmp_path):
    import math
    _series(tmp_path, lambda i: 100 * math.exp(0.002 * i + 0.2 * math.sin(i / 40)))
    spec, sha = k.load_spec(SPEC)
    val = k.run_validation(spec, sha, tmp_path)
    assert val["data_last_day"] == "2024-01-01" and len(val["configurations"]) == 15
    assert val["selected"] is not None
    val["gate_passed"] = True
    out = k.run_test(spec, sha, tmp_path, val)
    assert out["phase"] == "test" and set(out["checks"]) == {
        "total_net_positive", "sharpe_beats_buy_and_hold", "drawdown_below_buy_and_hold", "round_trips_at_least_5"}
    with pytest.raises(k.KlinesError, match="spent"):
        k.run_test(spec, sha, tmp_path, val)


def test_test_refused_when_gate_failed(tmp_path):
    spec, sha = k.load_spec(SPEC)
    with pytest.raises(k.KlinesError, match="gate"):
        k.run_test(spec, sha, tmp_path, {"phase": "validation", "spec_sha256": sha,
                                         "selected": {"family": "momentum", "L": 7}, "gate_passed": False})
    assert not (tmp_path / k.TEST_MARKER).exists()


def test_cli_refuses_existing_output(tmp_path):
    out = tmp_path / "v.json"
    out.write_text("{}")
    assert k.main(["validate", "--spec", str(SPEC), "--data", str(tmp_path), "--out", str(out)]) == 2
    assert json.loads(out.read_text()) == {}
