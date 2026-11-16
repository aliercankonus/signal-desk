"""Trades-only v2 runner: features, exit rules, split bounds, one-time test (offline, synthetic)."""
from datetime import date
import json
import math
from pathlib import Path

import pytest

from signal_desk.research import trades_only as tr
from signal_desk.research import trades_only_v2 as v2
from tests.research.test_trades_only import _ts, write_day

SPEC_PATH = Path(__file__).resolve().parents[2] / "docs/research/trades_only_v2.json"
D0 = date(2026, 1, 1)


def small_spec():
    spec = json.loads(SPEC_PATH.read_text())
    spec["window_utc"].update(start="2026-01-01T00:00:00Z", validation_start="2026-01-02T00:00:00Z",
                              test_start="2026-01-03T00:00:00Z", end="2026-01-04T00:00:00Z")
    return spec


def make_secs(prices, maker=False):
    """One trade per second: prices[i] trades at second i+1 (bucket i+1)."""
    secs = tr.Seconds(0, len(prices) + 1)
    secs.add([((i + 1) * 1_000_000, p, 1.0, maker) for i, p in enumerate(prices)])
    return secs.finish()


def make_window(tmp_path, price_fn, maker=False, step=5):
    data = tmp_path / "data"
    data.mkdir()
    for k in range(3):
        write_day(data, date(2026, 1, 1 + k),
                  [(s, price_fn(k * 86400 + s), 0.01, maker) for s in range(0, 86400, step)])
    return data


def exit_of(secs, rule, feature="momentum_15m", t=10):
    return v2.find_exit(secs, rule, feature, t, t + 1, secs.n - 1, 1, 60)


# --- spec ---------------------------------------------------------------

def test_spec_is_pinned_with_24_configurations(tmp_path):
    spec, sha = v2.load_spec(SPEC_PATH)
    assert sha == v2.PINNED_SPEC_SHA256 and spec["review_status"] == "predeclared_not_executed"
    assert len(list(v2.configurations(spec))) == 24
    changed = tmp_path / "spec.json"
    changed.write_text(SPEC_PATH.read_text().replace("0.0060", "0.0070"))
    with pytest.raises(tr.TradesOnlyError, match="pinned"):
        v2.load_spec(changed)


# --- features -----------------------------------------------------------

def sparse_secs(step, maker=False):
    secs = tr.Seconds(0, 2000)
    secs.add([(b * 1_000_000, 100.0, 1.0, maker) for b in range(step, 2000, step)])
    return secs.finish()


def test_flow_needs_100_trades_and_momentum_needs_history():
    assert v2.score(sparse_secs(10), "flow_15m", 1800) is None           # 90 trades in (900, 1800]
    assert v2.score(sparse_secs(9), "flow_15m", 1800) == 1.0             # 100 trades, all buys
    assert v2.score(sparse_secs(9, maker=True), "flow_15m", 1800) == -1.0
    assert v2.score(sparse_secs(9), "flow_15m", 899) is None             # window starts before data
    secs = make_secs([100.0] * 1000 + [101.0] * 3000)
    assert v2.score(secs, "momentum_15m", 900) is None
    assert v2.score(secs, "momentum_15m", 1500) == pytest.approx(0.01)
    assert v2.score(secs, "momentum_60m", 3700) == pytest.approx(0.01)
    assert v2.score(secs, "no_change", 2000) == 0.0 and v2.score(secs, "always_long", 1) == math.inf


# --- exits --------------------------------------------------------------

def test_price_exits():
    up = make_secs([100.0] * 49 + [100.7] * 100)
    assert exit_of(up, "tp_sl") == (51, "take_profit")
    down = make_secs([100.0] * 49 + [99.6] * 100)
    assert exit_of(down, "tp_sl") == (51, "stop_loss")
    trail = make_secs([100.0] * 29 + [102.0] * 10 + [101.5] * 100)   # 101.5 <= 102 * 0.996
    assert exit_of(trail, "trailing_stop") == (41, "trailing_stop")
    small_dip = make_secs([100.0] * 29 + [99.7] * 100)                 # -0.3% from entry, trail is 0.4%
    assert exit_of(small_dip, "trailing_stop") == (small_dip.n - 1, "forced")


def test_signal_reversal_is_checked_on_the_grid():
    secs = make_secs([100.0] * 999 + [101.0] * 1000 + [100.5] * 1000)
    # momentum_15m first turns negative at grid time 2040 (100.5 / 101 - 1)
    assert exit_of(secs, "signal_reversal", t=1200) == (2041, "signal_reversal")


def test_time_max_hold_and_forced_exits():
    flat = make_secs([100.0] * 89_999)
    assert exit_of(flat, "fixed_4h") == (11 + 14400 + 1, "time")
    assert exit_of(flat, "tp_sl") == (11 + 86400 + 1, "max_hold")
    short = make_secs([100.0] * 1999)
    assert exit_of(short, "fixed_4h") == (1999, "forced")             # segment ends before 4h


def test_warmup_embargo_and_cutoff_bounds():
    spec = small_spec()
    secs = tr.Seconds(_ts(D0), _ts(D0) + 3 * 86400)
    assert v2.segment_bounds(spec, secs, _ts(D0), _ts(D0) + 86400) == (3600, 86400 - 3600, 86399)
    assert v2.segment_bounds(spec, secs, _ts(D0) + 86400, _ts(D0) + 2 * 86400) == \
        (86400 + 3600, 2 * 86400 - 3600, 2 * 86400 - 1)


def test_day_limit_is_enforced(tmp_path):
    spec = small_spec()
    spec["limits"]["max_days_loaded"] = 1
    with pytest.raises(tr.TradesOnlyError, match="max_days_loaded"):
        v2._load(spec, tmp_path, _ts(D0), _ts(D0) + 2 * 86400)


# --- end to end ---------------------------------------------------------

def test_flat_sell_flow_selects_nothing_and_test_refuses(tmp_path):
    data = make_window(tmp_path, lambda s: 100.0, maker=True)
    spec = small_spec()
    val = v2.run_validation(spec, "x", data)
    assert len(val["configurations"]) == 24
    assert all(r["validation"]["trades"] == 0 for r in val["configurations"])
    assert val["selected"] is None
    with pytest.raises(tr.TradesOnlyError, match="selected nothing"):
        v2.run_test(spec, "x", data, val)
    assert not (data / v2.TEST_MARKER).exists()


def test_oscillating_market_validation_then_single_test_run(tmp_path):
    data = make_window(tmp_path, lambda s: 100.0 * (1 + 0.01 * math.sin(2 * math.pi * s / 1200)))
    spec = small_spec()
    val = v2.run_validation(spec, "x", data)
    by = {(r["config"]["candidate"], r["config"]["threshold"], r["config"]["exit_rule"]): r["validation"]
          for r in val["configurations"]}
    always = val["baselines"]["validation"]["always_long_same_exit"]
    # buyer-aggressor flow is always 1.0 after warmup/embargo, so flow rules equal always-long
    assert by[("flow15", 0.2, "tp_sl")] == always["tp_sl"]
    assert by[("flow15", 0.1, "tp_sl")]["trades"] >= 100
    assert by[("flow15", 0.1, "signal_reversal")]["exit_reason_counts"]["max_hold"] + \
        by[("flow15", 0.1, "signal_reversal")]["exit_reason_counts"]["forced"] == \
        by[("flow15", 0.1, "signal_reversal")]["trades"]
    for m in by.values():
        assert sum(m["exit_reason_counts"].values()) == m["trades"]
        assert 0.0 <= m["time_in_market_fraction"] <= 1.0
    assert val["baselines"]["validation"]["no_change"]["trades"] == 0
    assert val["selected"] is not None

    test = v2.run_test(spec, "x", data, val)
    assert test["phase"] == "test" and test["verdict"] in ("pass", "fail")
    assert set(test["checks"]) == {"trades_ge_100", "mean_net_positive", "total_net_positive",
                                   "t_stat_ge_2", "beats_always_long_same_exit"}
    assert len(test["baselines"]["always_long_same_exit"]) == 1
    with pytest.raises(tr.TradesOnlyError, match="spent"):
        v2.run_test(spec, "x", data, val)


def test_foreign_selection_and_existing_output_are_refused(tmp_path):
    spec = small_spec()
    with pytest.raises(tr.TradesOnlyError, match="not a validation result"):
        v2.run_test(spec, "x", tmp_path, {"phase": "validation", "spec_sha256": "y", "selected": {}})
    out = tmp_path / "out.json"
    out.write_text("{}")
    with pytest.raises(tr.TradesOnlyError, match="exists"):
        v2.main(["--spec", str(SPEC_PATH), "--data-dir", str(tmp_path), "--phase", "validation",
                 "--output", str(out)])
