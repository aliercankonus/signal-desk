import hashlib
import zipfile
from pathlib import Path

import pytest

from signal_desk.research import carry_v1 as cv
from signal_desk.research.klines_v3 import KlinesError

SPEC = Path(__file__).resolve().parents[2] / "docs/research/forward_carry_v1.json"
H = 3600
START = 1_800_000_000 // 86400 * 86400
END = START + 240 * H
COST = 0.0012


def market(price=100.0, high=None):
    bars = {t: (price, price, price) for t in range(START - 48 * H, END + H, H)}
    for t, h in (high or {}).items():
        bars[t] = (price, h, price)
    return bars


def funding(rate=0.0002, skip=()):
    return [(t, rate, 8) for t in range(START - 30 * 8 * H, END + 8 * H, 8 * H) if t not in skip]


def test_spec_is_pinned(tmp_path):
    spec, sha = cv.load_spec(SPEC)
    assert sha == cv.PINNED_SPEC_SHA256 and spec["rule"]["name"] == "carry-v1"
    changed = tmp_path / "s.json"
    changed.write_bytes(SPEC.read_bytes() + b" ")
    with pytest.raises(KlinesError):
        cv.load_spec(changed)


def test_flat_prices_earn_funding_minus_costs():
    r = cv.simulate(market(), market(), funding(), START, END, COST)
    # entry at START+25h (embargo), 26 settlements held (START+32h..END-8h), END not earned
    assert r["round_trips"] == 1 and r["forced_exits"] == 0
    assert r["settlements_held"] == 26
    assert r["return_split"]["funding"] == pytest.approx(26 * 0.005 * 100 * 0.0002)
    assert r["total_net_return"] == pytest.approx(26 * 0.0001 - 4 * COST * 0.5)
    assert r["max_drawdown"] == pytest.approx(0.0012, abs=1e-9)


def test_hard_limit_forces_exit_then_reentry():
    spike = START + 50 * H
    r = cv.simulate(market(), market(high={spike: 151.0}), funding(), START, END, COST)
    assert r["forced_exits"] == 1 and r["round_trips"] == 2


def test_no_entry_when_funding_low():
    r = cv.simulate(market(), market(), funding(0.00008), START, END, COST)
    assert r["round_trips"] == 0 and r["total_net_return"] == 0.0


def test_interim_read_ignores_future_funding():
    last = START + 100 * H
    a = funding()
    b = [(t, -0.01 if t > last else r, ih) for t, r, ih in a]
    ra = cv.simulate(market(), market(), a, START, END, COST, last)
    rb = cv.simulate(market(), market(), b, START, END, COST, last)
    assert ra == rb and ra["position_open_at_end_of_data"]


def test_missing_settlement_while_on_is_error():
    with pytest.raises(KlinesError, match="missing funding"):
        cv.simulate(market(), market(), funding(skip={START + 80 * H}), START, END, COST)


def test_load_funding_rounds_calc_time(tmp_path):
    name = "BTCUSDT-fundingRate-2026-11.zip"
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("f.csv", "calc_time,funding_interval_hours,last_funding_rate\n"
                             "1790812800001,8,0.0001\n1790841599998,8,-0.00002\n")
    (tmp_path / f"{name}.CHECKSUM").write_text(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}\n")
    assert cv.load_funding(tmp_path) == [(1790812800, 0.0001, 8), (1790841600, -0.00002, 8)]


def test_forward_refuses_overwrite(tmp_path):
    out = tmp_path / "out.json"
    out.write_text("{}")
    assert cv.main(["forward", "--spec", str(SPEC), "--data", str(tmp_path), "--out", str(out)]) == 2
    assert out.read_text() == "{}"
