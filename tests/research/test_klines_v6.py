import copy
import json
from pathlib import Path

import pytest

from signal_desk.research import klines_v4 as k4
from signal_desk.research import klines_v6 as k

SPEC = Path(__file__).resolve().parents[2] / "docs/research/klines_hourly_v6.json"
H = k4.HOUR


def test_spec_pinned_and_rule_fixed():
    spec, sha = k.load_spec(SPEC)
    assert sha == k.PINNED_SPEC_SHA256
    assert spec["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert spec["rule"]["lengths_bars"] == [480, 1200, 2400, 4800] and spec["rule"]["band"] == 0.02
    assert spec["execution_and_costs"]["cost_per_side"] == 0.0012


def test_simulate_fractional_exposure_costs_and_entries():
    times = [i * H for i in range(4)]
    opens = [100.0, 110.0, 110.0, 121.0]
    subs = [[1, 1, 1, 1], [0, 1, 0, 0]]  # exposure 0.5, 1.0, 0.5
    res = k.simulate(subs, times, opens, 0, 3, 0.01, embargo_s=0)
    eq = (1 - 0.005) * 1.05          # enter 0.5, +10% bar
    eq *= (1 - 0.005) * 1.0          # 0.5 -> 1.0, flat bar
    eq *= (1 - 0.005) * 1.05 * (1 - 0.005)  # 1.0 -> 0.5, +10% bar, exit 0.5
    assert res["net_return"] == pytest.approx(eq - 1)
    assert res["round_trips"] == 2 and res["exposure"] == pytest.approx(2 / 3)


def test_simulate_embargo_and_gap():
    times = [i * H for i in range(30)]
    res = k.simulate([[1] * 30], times, [100.0] * 30, 0, 29, 0.01)
    assert res["round_trips"] == 1 and res["exposure"] == pytest.approx(5 / 29)
    with pytest.raises(k.KlinesError, match="gap"):
        k.simulate([[0, 0, 0]], [0, H, 30 * H], [1.0] * 3, 0, 2, 0.0)


def _fake(start, end_by_sym):
    def load_bars(data_dir, last_ts=None, symbol=None):
        times = list(range(start - 5 * H, end_by_sym[symbol] + H, H))
        opens = [100 * 1.05 ** i for i in range(len(times))]
        return times, opens, list(opens)
    return load_bars


def _small(spec, phase):
    spec = copy.deepcopy(spec)
    spec["rule"]["lengths_bars"] = [2, 3]
    spec["segments_utc"][phase] = {"start": "2024-01-01", "end": "2024-01-04"}
    return spec


def test_backcheck_once(tmp_path, monkeypatch):
    spec, sha = k.load_spec(SPEC)
    spec = _small(spec, "backcheck")
    end = k4._ts("2024-01-04")
    monkeypatch.setattr(k, "load_bars", _fake(k4._ts("2024-01-01"), {"BTCUSDT": end, "ETHUSDT": end}))
    out = k.run(spec, sha, tmp_path, "backcheck")
    assert out["complete"] and out["verdict"] == "pass" and out["portfolio"]["round_trips"] == 4
    json.dumps(out)
    with pytest.raises(k.KlinesError, match="already run"):
        k.run(spec, sha, tmp_path, "backcheck")


def test_backcheck_refuses_incomplete_and_forward_is_interim(tmp_path, monkeypatch):
    spec, sha = k.load_spec(SPEC)
    for phase in ("backcheck", "forward"):
        spec = _small(spec, phase)
    ends = {"BTCUSDT": k4._ts("2024-01-04"), "ETHUSDT": k4._ts("2024-01-03")}
    monkeypatch.setattr(k, "load_bars", _fake(k4._ts("2024-01-01"), ends))
    with pytest.raises(k.KlinesError, match="both segment bounds"):
        k.run(spec, sha, tmp_path, "backcheck")
    assert not (tmp_path / k.BACKCHECK_MARKER).exists()
    out = k.run(spec, sha, tmp_path, "forward")
    assert out["verdict"] == "interim_no_verdict" and out["window"][1].startswith("2024-01-03")
