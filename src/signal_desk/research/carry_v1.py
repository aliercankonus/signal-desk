"""Funding-carry research runner for docs/research/forward_carry_v1.json.

Offline, paper-only, research code; places no orders and needs no keys. Long BTCUSDT spot,
short BTCUSDT USD-M perp (same quantity), on only while the mean of the last 21 funding
rates is clearly positive. ``forward`` reads the predeclared window and gives a verdict only
when the whole window (both kline series and every funding settlement) is present.
``backcheck`` runs the same code on a chosen pre-window range for debugging; its output is
not evidence.

Interim reads stop where the data stops. Funding files are monthly, so the read ends one
hour before the first settlement not yet published. An open position at the end of an
interim read is valued at the last close minus exit costs on both legs.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import zipfile

from signal_desk.research.binance_trades_download import parse_checksum, sha256_file
from signal_desk.research.klines_v3 import BASE_URL as SPOT_URL, SYMBOL, KlinesError, _get
from signal_desk.research.trades_only import _git_commit

RUNNER_VERSION = "research-carry-v1"
PINNED_SPEC_SHA256 = "28b672cfeb972e3ae161d5654b96b431db2ab8fe59c6197d09256a406293284b"
FUTURES_URL = "https://data.binance.vision/data/futures/um"
HOUR = 3600
BARS_PER_YEAR = 8760
LOOKBACK, ENTER, EXIT, LIMIT = 21, 0.0001, 0.00005, 0.5  # spec "rule" (pinned by sha)


def load_spec(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != PINNED_SPEC_SHA256:
        raise KlinesError(f"spec sha256 {digest} != pinned {PINNED_SPEC_SHA256}; "
                          "a changed spec needs a new version")
    return json.loads(data), digest


def _months(first: str, last: str) -> list[str]:
    y, m = map(int, first.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def download(session, out_dir: Path, first_month: str, last_month: str, extra_days=()) -> list[Path]:
    """Spot and perp 1h klines (monthly + daily) and monthly funding into out_dir/{spot,perp,funding}."""
    jobs = []
    for mo in _months(first_month, last_month):
        name = f"{SYMBOL}-1h-{mo}.zip"
        jobs += [(f"{SPOT_URL}/monthly/klines/{SYMBOL}/1h", "spot", name),
                 (f"{FUTURES_URL}/monthly/klines/{SYMBOL}/1h", "perp", name),
                 (f"{FUTURES_URL}/monthly/fundingRate/{SYMBOL}", "funding", f"{SYMBOL}-fundingRate-{mo}.zip")]
    for day in extra_days:
        name = f"{SYMBOL}-1h-{day}.zip"
        jobs += [(f"{SPOT_URL}/daily/klines/{SYMBOL}/1h", "spot", name),
                 (f"{FUTURES_URL}/daily/klines/{SYMBOL}/1h", "perp", name)]
    paths = []
    for base, sub, name in jobs:
        url = f"{base}/{name}"
        expected = parse_checksum(_get(session, url + ".CHECKSUM").text, name)
        body = _get(session, url).content
        if hashlib.sha256(body).hexdigest() != expected:
            raise KlinesError(f"SHA-256 mismatch for {url}")
        target = out_dir / sub
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(body)
        (target / f"{name}.CHECKSUM").write_text(f"{expected}  {name}\n")
        paths.append(target / name)
    return paths


def _verified_records(data_dir: Path, pattern: str):
    zips = sorted(data_dir.glob(pattern))
    if not zips:
        raise KlinesError(f"no {pattern} in {data_dir}")
    for path in zips:
        checksum = path.with_name(path.name + ".CHECKSUM")
        if not checksum.is_file() or sha256_file(path) != parse_checksum(checksum.read_text(), path.name):
            raise KlinesError(f"missing or mismatched CHECKSUM for {path}")
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                for rec in csv.reader(io.TextIOWrapper(zf.open(member), "utf-8")):
                    if rec and rec[0].isdigit():  # skips the header line
                        yield path.name, rec


def load_klines(data_dir: Path) -> dict[int, tuple[float, float, float]]:
    """Verified {bar open epoch s: (open, high, close)}."""
    rows = {}
    for name, rec in _verified_records(data_dir, f"{SYMBOL}-1h-*.zip"):
        ts = int(rec[0])
        ts = (ts // 1000 if ts >= 10**14 else ts) // 1000  # microseconds from 2025
        if ts % HOUR or ts in rows:
            raise KlinesError(f"bad or duplicate bar time {ts} in {name}")
        rows[ts] = (float(rec[1]), float(rec[2]), float(rec[4]))
    return rows


def load_funding(data_dir: Path) -> list[tuple[int, float, int]]:
    """Verified [(settlement epoch s, rate, interval hours)], ascending."""
    rows = {}
    for name, rec in _verified_records(data_dir, f"{SYMBOL}-fundingRate-*.zip"):
        t = (int(rec[0]) // 1000 + HOUR // 2) // HOUR * HOUR  # calc_time is a few ms past the hour
        if t in rows:
            raise KlinesError(f"duplicate funding time {t} in {name}")
        rows[t] = (float(rec[2]), int(rec[1]))
    return [(t, r, ih) for t, (r, ih) in sorted(rows.items())]


def missing_settlements(funding) -> list[int]:
    out = []
    for (t0, _, ih), (t1, _, _) in zip(funding, funding[1:]):
        step = ih * HOUR
        out += [t0 + j * step for j in range(1, round((t1 - t0) / step))]
    return out


def _series_check(rows, start: int, stop: int, label: str) -> int:
    """Missing hours in [start, stop]; error on a gap longer than 24h."""
    times = [t for t in sorted(rows) if start <= t <= stop]
    edges = [start - HOUR] + times + [stop + HOUR]
    for a, b in zip(edges, edges[1:]):
        if b - a > 24 * HOUR + HOUR:
            raise KlinesError(f"{label}: gap longer than 24h after {a}")
    return (stop - start) // HOUR + 1 - len(times)


def simulate(spot, perp, funding, start: int, end: int, cost: float, last: int | None = None) -> dict:
    """Run carry-v1 on bars start..end (complete) or start..last (interim, last < end)."""
    complete = last is None
    stop = end if complete else last
    times = sorted(t for t in spot.keys() & perp.keys() if start <= t <= stop)
    if not times or (complete and times[-1] != end):
        raise KlinesError("no bars for the window, or end bar missing")
    missing_hours = {"spot": _series_check(spot, start, stop, "spot"),
                     "perp": _series_check(perp, start, stop, "perp")}
    _series_check(dict.fromkeys(times), start, stop, "spot&perp")
    ftimes = [f[0] for f in funding]
    rates = [f[1] for f in funding]
    gaps = missing_settlements(funding)
    embargo = start + 24 * HOUR

    st = {"cash": 1.0, "q": 0.0, "on": False, "se": 0.0, "pe": 0.0, "margin": 0.0}
    split = {"funding": 0.0, "spot_leg": 0.0, "perp_leg": 0.0, "costs": 0.0}
    n = {"round_trips": 0, "forced_exits": 0, "settlements_held": 0, "settlements_available": 0,
         "on_bars": 0, "bars": 0, "delayed_executions": 0}

    def enter(so, po):
        q = 0.5 * st["cash"] / so
        c = cost * q * (so + po)
        st.update(q=q, margin=0.5 * st["cash"], se=so, pe=po, on=True)
        st["cash"] -= q * so + st["margin"] + c
        split["costs"] -= c

    def leave(so, po, count=True):
        q = st["q"]
        c = cost * q * (so + po)
        st["cash"] += q * so + st["margin"] + q * (st["pe"] - po) - c
        split["spot_leg"] += q * (so - st["se"])
        split["perp_leg"] += q * (st["pe"] - po)
        split["costs"] -= c
        n["round_trips"] += count
        st.update(q=0.0, margin=0.0, on=False)

    curve, pending, last_decision, prev = [1.0], None, None, start - HOUR
    fi = bisect_left(ftimes, start)
    for t in times:
        so, _, sc = spot[t]
        po, ph, pc = perp[t]
        if complete and t == end:
            if st["on"]:
                leave(so, po)
            curve.append(st["cash"])
            break
        if pending and t >= pending[0]:  # execute at this bar's open
            n["delayed_executions"] += t > pending[0]
            if pending[1] and not st["on"]:
                enter(so, po)
            elif not pending[1] and st["on"]:
                leave(so, po)
                n["forced_exits"] += pending[2]
            pending = None
        if st["on"] and any(prev < g <= t for g in gaps):
            raise KlinesError(f"missing funding settlement while on, before bar {t}")
        while fi < len(funding) and ftimes[fi] <= t:
            ts, rate = ftimes[fi], rates[fi]
            n["settlements_available"] += 1
            if st["on"]:  # funding on perp open of the bar starting at the settlement
                st["cash"] += st["q"] * po * rate
                split["funding"] += st["q"] * po * rate
                n["settlements_held"] += 1
            if fi < LOOKBACK - 1:
                raise KlinesError(f"need {LOOKBACK} funding records up to {ts}")
            m = sum(rates[fi - LOOKBACK + 1:fi + 1]) / LOOKBACK
            target = st["on"]
            if not st["on"] and m >= ENTER and ts >= embargo:
                target = True
            elif st["on"] and m < EXIT:
                target = False
            if target != st["on"] and pending is None:
                pending = (ts + HOUR, target, False)
            last_decision = {"time": _iso(ts), "mean_rate": m, "state": "on" if target else "off"}
            fi += 1
        n["bars"] += 1
        if st["on"]:
            n["on_bars"] += 1
            if pending is None and (ph - st["pe"]) * st["q"] >= LIMIT * st["margin"]:
                pending = (t + HOUR, False, True)
            curve.append(st["cash"] + st["q"] * sc + st["margin"] + st["q"] * (st["pe"] - pc))
        else:
            curve.append(st["cash"])
        prev = t
    open_at_end = st["on"]
    if not complete:
        if open_at_end:
            leave(sc, pc, count=False)  # interim value: exit at the last close
        curve.append(st["cash"])

    rets = [b / a - 1 for a, b in zip(curve, curve[1:])]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    peak, mdd = 1.0, 0.0
    for e in curve:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    eq = st["cash"]
    in_window = [i for i, t in enumerate(ftimes) if start <= t <= stop and t < end]
    return {"first_bar": _iso(times[0]), "exit_bar": _iso(times[-1]), "missing_hours": missing_hours,
            "missing_funding_settlements": sum(start <= g <= stop for g in gaps),
            "total_net_return": eq - 1, "cagr": eq ** (BARS_PER_YEAR / n["bars"]) - 1,
            "sharpe": mean / sd * math.sqrt(BARS_PER_YEAR) if sd > 0 else 0.0,
            "max_drawdown": mdd, **n, "fraction_of_time_on": n["on_bars"] / n["bars"],
            "return_split": split, "last_decision": last_decision,
            "position_open_at_end_of_data": open_at_end and not complete,
            "baselines": _baselines(spot, times, complete, [rates[i] for i in in_window], cost)}


def _baselines(spot, times, complete, window_rates, cost) -> dict:
    s0 = spot[times[0]][0]
    marks = [spot[t][2] for t in (times[:-1] if complete else times)]
    final = (spot[times[-1]][0] if complete else spot[times[-1]][2]) / s0 * (1 - cost) ** 2
    peak, mdd = 1.0, 0.0
    for e in [(1 - cost) * p / s0 for p in marks] + [final]:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    funding = 0.5 * sum(window_rates)  # perp notional fixed at 0.5 of capital
    return {"cash": {"total_net_return": 0.0},
            "buy_and_hold_info_only": {"total_net_return": final - 1, "max_drawdown": mdd},
            "always_on_carry_funding_info_only": {
                "settlements": len(window_rates), "funding_on_capital_before_costs": funding,
                "after_one_round_trip_costs": funding - 4 * cost * 0.5}}


def _iso(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _ts(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp())


def run(spec, sha, data_dir: Path, start: int, end: int, phase: str) -> dict:
    spot, perp = load_klines(data_dir / "spot"), load_klines(data_dir / "perp")
    funding = load_funding(data_dir / "funding")
    f_last, _, f_ih = funding[-1]
    complete = end in spot and end in perp and f_last + f_ih * HOUR >= end
    last = None
    if not complete:
        last = min(end - HOUR, max(t for t in spot if t <= end), max(t for t in perp if t <= end),
                   f_last + f_ih * HOUR - HOUR)
        if last < start:
            raise KlinesError(f"no usable {phase} data yet (data ends {_iso(last)})")
    res = simulate(spot, perp, funding, start, end, spec["costs"]["cost_per_side"], last)
    checks = {"total_net_positive": res["total_net_return"] > 0,
              "max_drawdown_below_35pct": res["max_drawdown"] < 0.35}
    if phase == "backcheck":
        verdict = "debug_not_evidence"
    else:
        verdict = ("pass" if all(checks.values()) else "fail") if complete else "interim_no_verdict"
    return {"phase": phase, "spec_version": spec["experiment_version"], "spec_sha256": sha,
            "runner_version": RUNNER_VERSION, "git_commit": _git_commit(),
            "window": [_iso(start), _iso(end)], "complete": complete, "result": res,
            "checks": checks, "verdict": verdict,
            "caveat": "few round trips and one funding regime: weak evidence, not a profitability claim"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--first-month", default="2026-10")
    d.add_argument("--last-month", required=True)
    d.add_argument("--extra-day", action="append", default=[])
    for name in ("backcheck", "forward"):
        s = sub.add_parser(name)
        s.add_argument("--spec", type=Path, required=True)
        s.add_argument("--data", type=Path, required=True)
        s.add_argument("--out", type=Path, required=True)
        if name == "backcheck":
            s.add_argument("--start", required=True, help="e.g. 2025-01-01T00:00:00Z")
            s.add_argument("--end", required=True)
    args = p.parse_args(argv)
    try:
        if args.cmd == "download":
            import requests
            download(requests.Session(), args.out, args.first_month, args.last_month, args.extra_day)
            return 0
        if args.out.exists():
            raise KlinesError(f"{args.out} exists; refusing to overwrite")
        spec, sha = load_spec(args.spec)
        w = spec["window_utc"]
        start, end = (args.start, args.end) if args.cmd == "backcheck" else (w["start"], w["end"])
        out = run(spec, sha, args.data, _ts(start), _ts(end), args.cmd)
    except KlinesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
