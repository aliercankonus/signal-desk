from pathlib import Path

import pytest

from signal_desk.research import klines_v4 as k

SPEC = Path(__file__).resolve().parents[2] / "docs/research/klines_hourly_v4.json"
H = k.HOUR


def test_spec_is_pinned_and_single_rule():
    spec, sha = k.load_spec(SPEC)
    assert sha == k.PINNED_SPEC_SHA256
    assert (spec["rule"]["N_bars"], spec["rule"]["band"]) == (2400, 0.02)
    assert spec["execution_and_costs"]["cost_per_side"] == 0.0012


def test_states_band_hysteresis_and_warmup():
    # n=2: sma of last two closes; 10% band
    closes = [100, 100, 130, 125, 124, 90, 95]
    assert k.states(closes, 2, 0.10) == [0, 0, 1, 1, 1, 0, 0]
    assert k.states([100, 200], 3, 0.0) == [0, 0]  # not enough bars


def test_states_are_causal():
    closes = [100 + (i * 37) % 23 for i in range(60)]
    base = k.states(closes, 10, 0.02)
    changed = k.states(closes[:40] + [1.0] * 20, 10, 0.02)
    assert base[:40] == changed[:40]


def test_simulate_costs_embargo_and_forced_exit():
    times = [i * H for i in range(6)]
    opens = [100, 100, 110, 121, 121, 121]
    res = k.simulate([1] * 6, times, opens, 0, 5, 0.01, embargo_s=H)
    # flat in bar 0, enter at opens[1], hold to opens[5], two costs
    assert res["total_net_return"] == pytest.approx(0.99 * 1.21 * 0.99 - 1)
    assert res["round_trips"] == 1 and res["bars"] == 5 and res["missing_hours"] == 0
    flat = k.simulate([0] * 6, times, opens, 0, 5, 0.01)
    assert flat["total_net_return"] == 0 and flat["sharpe"] == 0


def test_simulate_rejects_long_gap():
    times = [0, H, 30 * H]
    with pytest.raises(k.KlinesError, match="gap"):
        k.simulate([0, 0, 0], times, [1, 1, 1], 0, 2, 0.0)


def _fake(monkeypatch, n_after):
    spec, sha = k.load_spec(SPEC)
    start = k._ts(spec["segments_utc"]["backcheck"]["start"])
    spec["segments_utc"]["backcheck"]["end"] = "2024-01-03"
    spec["segments_utc"]["forward"] = {"start": "2024-01-01", "end": "2024-02-01"}
    times = [start + (i - 2400) * H for i in range(2400 + n_after + 1)]
    px = [100.0 + i * 0.01 for i in range(len(times))]
    monkeypatch.setattr(k, "load_bars", lambda d, last_ts=None: (times, px, px))
    return spec, sha


def test_backcheck_runs_once(monkeypatch, tmp_path):
    spec, sha = _fake(monkeypatch, 48)
    out = k.run(spec, sha, tmp_path, "backcheck")
    assert out["complete"] and out["verdict"] in ("pass", "fail") and out["result"]["bars"] == 48
    with pytest.raises(k.KlinesError, match="already run"):
        k.run(spec, sha, tmp_path, "backcheck")


def test_forward_is_interim_until_segment_end(monkeypatch, tmp_path):
    spec, sha = _fake(monkeypatch, 48)
    out = k.run(spec, sha, tmp_path, "forward")
    assert out["verdict"] == "interim_no_verdict" and not out["complete"]
    assert not (tmp_path / k.BACKCHECK_MARKER).exists()


def test_trades_match_simulate():
    times = [i * H for i in range(12)]
    opens = [100, 101, 103, 102, 99, 98, 101, 104, 106, 105, 103, 107]
    pos = [0, 1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 1]
    res = k.simulate(pos, times, opens, 0, 11, 0.0012, embargo_s=0)
    tr = k.trades(pos, times, opens, 0, 11, 0.0012, embargo_s=0, complete=False)
    assert len(tr) == res["round_trips"] == 3
    assert [t["status"] for t in tr] == ["closed", "closed", "open"]
    eq = 1.0
    for t in tr:
        eq *= 1 + t["net_return"]
    assert eq - 1 == pytest.approx(res["total_net_return"])
