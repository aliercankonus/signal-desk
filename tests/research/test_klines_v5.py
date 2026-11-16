import copy
import json
from pathlib import Path

import pytest

from signal_desk.research import klines_v4 as k4
from signal_desk.research import klines_v5 as k

SPEC = Path(__file__).resolve().parents[2] / "docs/research/klines_hourly_v5.json"
H = k4.HOUR


def test_spec_pinned_rule_matches_v4_and_universe_fixed():
    spec, sha = k.load_spec(SPEC)
    v4, _ = k4.load_spec(SPEC.with_name("klines_hourly_v4.json"))
    assert sha == k.PINNED_SPEC_SHA256
    assert (spec["rule"]["N_bars"], spec["rule"]["band"]) == (v4["rule"]["N_bars"], v4["rule"]["band"])
    assert spec["execution_and_costs"]["cost_per_side"] == v4["execution_and_costs"]["cost_per_side"]
    syms = spec["universe"]["symbols"]
    assert len(syms) == len(set(syms)) == 20 and "BTCUSDT" not in syms


def test_portfolio_carries_forward_after_sleeve_ends():
    a = [(0, 1.0), (H, 1.2), (2 * H, 1.4)]
    b = [(0, 1.0), (H, 0.8)]  # data ends after one hour
    assert k._portfolio([a, b], 0, 2 * H) == pytest.approx([1.0, 1.0, 1.1])


def _fake(spec, ends):
    start, end = k4._ts(spec["window_utc"]["start"]), k4._ts(spec["window_utc"]["end"])

    def load_bars(data_dir, last_ts=None, symbol=None):
        times = list(range(start - 5 * H, ends.get(symbol, end) + H, H))
        opens = [100 * 1.05 ** i for i in range(len(times))]  # +5%/h: clears the 2% band
        return times, opens, list(opens)
    return load_bars


def test_run_once_with_early_ending_coin(tmp_path, monkeypatch):
    spec, sha = k.load_spec(SPEC)
    spec = copy.deepcopy(spec)
    spec["rule"]["N_bars"] = 3
    spec["window_utc"] = {"start": "2024-01-01", "end": "2024-01-04"}
    early = k4._ts("2024-01-02")
    monkeypatch.setattr(k, "load_bars", _fake(spec, {"MATICUSDT": early}))
    out = k.run(spec, sha, tmp_path)
    assert out["per_coin"]["MATICUSDT"]["data_ends_early_at"].startswith("2024-01-02")
    assert out["per_coin"]["ETHUSDT"]["data_ends_early_at"] is None
    # MATIC's data ends inside the 24h embargo, so only 19 sleeves trade
    assert out["portfolio"]["hours"] == 72 and out["portfolio"]["round_trips"] == 19
    assert out["portfolio"]["total_net_return"] > 0 and out["verdict"] == "fail"  # < 40 round trips
    json.dumps(out)
    with pytest.raises(k.KlinesError, match="already run"):
        k.run(spec, sha, tmp_path)


def test_run_refuses_incomplete_data(tmp_path, monkeypatch):
    spec, sha = k.load_spec(SPEC)
    spec = copy.deepcopy(spec)
    spec["rule"]["N_bars"] = 3
    spec["window_utc"] = {"start": "2024-01-01", "end": "2024-01-04"}
    early = k4._ts("2024-01-03")
    monkeypatch.setattr(k, "load_bars", _fake(spec, {s: early for s in spec["universe"]["symbols"]}))
    with pytest.raises(k.KlinesError, match="incomplete"):
        k.run(spec, sha, tmp_path)
    assert not (tmp_path / k.MARKER).exists()
