"""Hourly-klines BTCUSDT research runner for docs/research/klines_hourly_v4.json.

Offline, paper-only, research code. One fixed rule (long/flat on the 2400-hour SMA with
a 2% band), judged against cash. ``backcheck`` runs once on the predeclared past segment
(marker file next to the data); ``forward`` reads the forward segment and gives a verdict
only when the whole segment is present.
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
from signal_desk.research.klines_v3 import SYMBOL, KlinesError, download
from signal_desk.research.trades_only import _git_commit

RUNNER_VERSION = "research-klines-hourly-v4"
PINNED_SPEC_SHA256 = "c4da5109c54835fb0fe6a6a094d6d8c8cda650324e35b8ddafe7d8ee49a43e86"
BACKCHECK_MARKER = "klines_hourly_v4.backcheck_run"
HOUR = 3600
BARS_PER_YEAR = 8760


def load_spec(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != PINNED_SPEC_SHA256:
        raise KlinesError(f"spec sha256 {digest} != pinned {PINNED_SPEC_SHA256}; "
                          "a changed spec needs a new version")
    return json.loads(data), digest


def load_bars(data_dir: Path, last_ts: int | None = None, symbol: str = SYMBOL):
    """Verified (times, opens, closes); times are UTC epoch seconds of bar opens, ascending."""
    rows = {}
    zips = sorted(data_dir.glob(f"{symbol}-1h-*.zip"))
    if not zips:
        raise KlinesError(f"no 1h kline zips in {data_dir}")
    for path in zips:
        checksum = path.with_name(path.name + ".CHECKSUM")
        if not checksum.is_file() or sha256_file(path) != parse_checksum(checksum.read_text(), path.name):
            raise KlinesError(f"missing or mismatched CHECKSUM for {path}")
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                for rec in csv.reader(io.TextIOWrapper(zf.open(member), "utf-8")):
                    if not rec or not rec[0].isdigit():
                        continue  # header line, if any
                    ts = int(rec[0])
                    ts = (ts // 1000 if ts >= 10**14 else ts) // 1000  # microseconds from 2025
                    if ts % HOUR or ts in rows:
                        raise KlinesError(f"bad or duplicate bar time {ts} in {path.name}")
                    rows[ts] = (float(rec[1]), float(rec[4]))
    times = sorted(t for t in rows if last_ts is None or t <= last_ts)
    return times, [rows[t][0] for t in times], [rows[t][1] for t in times]


def states(closes, n: int, band: float) -> list[int]:
    """s[i] = desired position after the close of bar i (uses closes[0..i] only)."""
    s, state, total = [], 0, 0.0
    for i, c in enumerate(closes):
        total += c
        if i >= n:
            total -= closes[i - n]
        if i >= n - 1:
            sma = total / n
            if c > sma * (1 + band):
                state = 1
            elif c < sma * (1 - band):
                state = 0
        s.append(state)
    return s


def simulate(pos, times, opens, a: int, b: int, cost: float, embargo_s: int = 24 * HOUR,
             curve: list | None = None) -> dict:
    """Bars a..b-1; pos[d] is held from opens[d] to opens[d+1]; exit at opens[b].

    If curve is a list, (time, equity) after each bar is appended to it."""
    eq, held, rets, entries, long_bars, peak, mdd = 1.0, 0, [], 0, 0, 1.0, 0.0
    for d in range(a, b):
        if times[d + 1] - times[d] > 24 * HOUR:
            raise KlinesError(f"gap longer than 24h after bar {times[d]}")
        start = eq
        p = 0 if times[d] < times[a] + embargo_s else pos[d]
        if p != held:
            eq *= 1 - cost
            entries += p
            held = p
        if p:
            eq *= opens[d + 1] / opens[d]
            long_bars += 1
        if d == b - 1 and held:
            eq *= 1 - cost
        rets.append(eq / start - 1)
        if curve is not None:
            curve.append((times[d + 1], eq))
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    n = len(rets)
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    return {"bars": n, "missing_hours": (times[b] - times[a]) // HOUR - n,
            "total_net_return": eq - 1, "cagr": eq ** (BARS_PER_YEAR / n) - 1,
            "sharpe": mean / sd * math.sqrt(BARS_PER_YEAR) if sd > 0 else 0.0,
            "max_drawdown": mdd, "round_trips": entries, "exposure": long_bars / n}


def trades(pos, times, opens, a: int, b: int, cost: float, embargo_s: int = 24 * HOUR,
           complete: bool = True) -> list[dict]:
    """Round trips with the same timing and costs as simulate; the last one is "open" on an interim read."""
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    out, entry = [], None
    for d in range(a, b + 1):
        p = 0 if d == b or times[d] < times[a] + embargo_s else pos[d]
        if p and entry is None:
            entry = d
        elif not p and entry is not None:
            out.append({"entry_time": iso(times[entry]), "entry_price": opens[entry],
                        "exit_time": iso(times[d]), "exit_price": opens[d],
                        "net_return": opens[d] / opens[entry] * (1 - cost) ** 2 - 1,
                        "status": "open" if d == b and not complete else "closed"})
            entry = None
    return out


def _ts(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def run(spec, sha, data_dir: Path, phase: str) -> dict:
    seg, rule = spec["segments_utc"][phase], spec["rule"]
    start, end = _ts(seg["start"]), _ts(seg["end"])
    times, opens, closes = load_bars(data_dir, last_ts=end)
    a = bisect_left(times, start)
    complete = times[-1] == end
    b = len(times) - 1
    if a < rule["N_bars"]:
        raise KlinesError(f"need {rule['N_bars']} bars before {seg['start']}, have {a}")
    if b <= a:
        raise KlinesError(f"no {phase} bars in data yet")
    if phase == "backcheck":
        if times[a] != start or not complete:
            raise KlinesError("backcheck needs bars at both segment bounds")
        try:
            with (data_dir / BACKCHECK_MARKER).open("x") as f:
                f.write(datetime.now(timezone.utc).isoformat() + "\n")
        except FileExistsError:
            raise KlinesError(f"backcheck already run ({data_dir / BACKCHECK_MARKER} exists)") from None
    cost = spec["execution_and_costs"]["cost_per_side"]
    pos = [0] + states(closes, rule["N_bars"], rule["band"])[:-1]
    res = simulate(pos, times, opens, a, b, cost)
    checks = {"total_net_positive": res["total_net_return"] > 0, "max_drawdown_below_35pct": res["max_drawdown"] < 0.35}
    if phase == "backcheck":
        checks.update(sharpe_above_0_5=res["sharpe"] > 0.5, round_trips_at_least_5=res["round_trips"] >= 5)
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    return {"phase": phase, "spec_version": spec["experiment_version"], "spec_sha256": sha,
            "runner_version": RUNNER_VERSION, "git_commit": _git_commit(),
            "first_bar": iso(times[a]), "exit_bar": iso(times[b]), "complete": complete,
            "result": res, "position_at_last_signal": pos[b],
            "trades": trades(pos, times, opens, a, b, cost, complete=complete),
            "baselines": {"cash": {"total_net_return": 0.0},
                          "buy_and_hold_info_only": simulate([1] * len(times), times, opens, a, b, cost, 0)},
            "checks": checks,
            "verdict": ("pass" if all(checks.values()) else "fail") if complete else "interim_no_verdict"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--first-month", default="2023-07")
    d.add_argument("--last-month", required=True)
    d.add_argument("--extra-day", action="append", default=[])
    for name in ("backcheck", "forward"):
        s = sub.add_parser(name)
        s.add_argument("--spec", type=Path, required=True)
        s.add_argument("--data", type=Path, required=True)
        s.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.cmd == "download":
            import requests
            download(requests.Session(), args.out, args.first_month, args.last_month, args.extra_day, "1h")
            return 0
        if args.out.exists():
            raise KlinesError(f"{args.out} exists; refusing to overwrite")
        spec, sha = load_spec(args.spec)
        out = run(spec, sha, args.data, args.cmd)
    except KlinesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
