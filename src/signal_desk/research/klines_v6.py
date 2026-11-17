"""v6: BTC+ETH SMA-band ensemble (docs/research/klines_hourly_v6.json). Offline, paper-only research.

Per coin, exposure = mean of four long/flat SMA-band states; two sleeves at 1/2 each, no rebalancing.
``backcheck`` runs once (marker file next to the data); ``forward`` gives a verdict only when complete.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

from signal_desk.research.klines_v3 import KlinesError
from signal_desk.research.klines_v4 import trades, BARS_PER_YEAR, HOUR, _ts, load_bars, states
from signal_desk.research.klines_v5 import _metrics, _portfolio, fetch
from signal_desk.research.trades_only import _git_commit

RUNNER_VERSION = "research-klines-hourly-v6"
PINNED_SPEC_SHA256 = "0f4abd984c236a9cbded19666143b7979f71cc192309059e9643abb861717bfc"
BACKCHECK_MARKER = "klines_hourly_v6.backcheck_run"


def load_spec(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != PINNED_SPEC_SHA256:
        raise KlinesError(f"spec sha256 {digest} != pinned {PINNED_SPEC_SHA256}; a changed spec needs a new version")
    return json.loads(data), digest


def simulate(subs, times, opens, a: int, b: int, cost: float, embargo_s: int = 24 * HOUR, curve=None) -> dict:
    """subs: sub-signal position lists; sub[d] is held from opens[d] to opens[d+1]; exposure = their mean.

    Equity pays cost * |change in exposure|; a fraction x earns x * bar return; exit at opens[b]."""
    k = len(subs)
    eq, held, held_subs, entries, expo_sum, peak, mdd = 1.0, 0.0, [0] * k, 0, 0.0, 1.0, 0.0
    for d in range(a, b):
        if times[d + 1] - times[d] > 24 * HOUR:
            raise KlinesError(f"gap longer than 24h after bar {times[d]}")
        now = [0] * k if times[d] < times[a] + embargo_s else [s[d] for s in subs]
        entries += sum(1 for o, n in zip(held_subs, now) if n and not o)
        held_subs = now
        x = sum(now) / k
        eq *= 1 - cost * abs(x - held)
        held = x
        eq *= 1 + x * (opens[d + 1] / opens[d] - 1)
        if d == b - 1:
            eq *= 1 - cost * held
        expo_sum += x
        if curve is not None:
            curve.append((times[d + 1], eq))
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    n = b - a
    return {"bars": n, "missing_hours": (times[b] - times[a]) // HOUR - n, "net_return": eq - 1,
            "max_drawdown": mdd, "round_trips": entries, "exposure": expo_sum / n}


def run(spec, sha, data_dir: Path, phase: str) -> dict:
    seg, rule = spec["segments_utc"][phase], spec["rule"]
    start, end = _ts(seg["start"]), _ts(seg["end"])
    cost = spec["execution_and_costs"]["cost_per_side"]
    need = max(rule["lengths_bars"])
    loaded = {}
    for sym in spec["symbols"]:
        times, opens, closes = load_bars(data_dir / sym, last_ts=end, symbol=sym)
        a = bisect_left(times, start)
        if a < need or a >= len(times) - 1 or times[a] != start:
            raise KlinesError(f"{sym}: need {need} bars before, a bar at {seg['start']} and at least one after")
        loaded[sym] = (times, opens, closes, a)
    last = min(v[0][-1] for v in loaded.values())
    complete = all(v[0][-1] == end for v in loaded.values())
    if phase == "backcheck":
        if not complete:
            raise KlinesError("backcheck needs bars at both segment bounds for every symbol")
        try:
            with (data_dir / BACKCHECK_MARKER).open("x") as f:
                f.write(datetime.now(timezone.utc).isoformat() + "\n")
        except FileExistsError:
            raise KlinesError(f"backcheck already run ({data_dir / BACKCHECK_MARKER} exists)") from None
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    lag = lambda s: [0] + s[:-1]  # state after close i is held from open i+1
    per_coin, curves, bh_curves, v4_curves = {}, [], [], []
    for sym, (times, opens, closes, a) in loaded.items():
        b = bisect_left(times, last)  # common exit bar (== end when complete)
        subs = [lag(states(closes, n, rule["band"])) for n in rule["lengths_bars"]]
        curve, bh, v4 = [(start, 1.0)], [(start, 1.0)], [(start, 1.0)]
        res = simulate(subs, times, opens, a, b, cost, curve=curve)
        bh_res = simulate([[1] * len(times)], times, opens, a, b, cost, 0, curve=bh)
        v4_res = simulate([lag(states(closes, 2400, 0.02))], times, opens, a, b, cost, curve=v4)
        curves.append(curve)
        bh_curves.append(bh)
        v4_curves.append(v4)
        per_coin[sym] = {**res, "exposure_at_last_signal": sum(s[b] for s in subs) / len(subs),
                         "buy_and_hold_net_return_info": bh_res["net_return"],
                         "v4_rule_net_return_info": v4_res["net_return"],
                         "trades": [{**t, "length_bars": n, "weight": 1 / (len(subs) * len(loaded))}
                                    for n, s in zip(rule["lengths_bars"], subs)
                                    for t in trades(s, times, opens, a, b, cost, complete=complete)]}
    port = _metrics(_portfolio(curves, start, last))
    port["round_trips"] = sum(c["round_trips"] for c in per_coin.values())
    port["avg_exposure"] = sum(c["exposure"] for c in per_coin.values()) / len(per_coin)
    checks = {"total_net_positive": port["total_net_return"] > 0, "max_drawdown_below_35pct": port["max_drawdown"] < 0.35}
    if phase == "backcheck":
        checks["sharpe_above_0_5"] = port["sharpe"] > 0.5
    return {"phase": phase, "spec_version": spec["experiment_version"], "spec_sha256": sha,
            "runner_version": RUNNER_VERSION, "git_commit": _git_commit(),
            "window": [iso(start), iso(last)], "complete": complete, "portfolio": port, "per_coin": per_coin,
            "baselines": {"cash": {"total_net_return": 0.0},
                          "buy_and_hold_50_50_info_only": _metrics(_portfolio(bh_curves, start, last)),
                          "v4_rule_50_50_info_only": _metrics(_portfolio(v4_curves, start, last))},
            "checks": checks,
            "verdict": ("pass" if all(checks.values()) else "fail") if complete else "interim_no_verdict"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--spec", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--first-month", default="2023-05")
    d.add_argument("--last-month", required=True)
    d.add_argument("--extra-day", action="append", default=[])
    for name in ("backcheck", "forward"):
        s = sub.add_parser(name)
        s.add_argument("--spec", type=Path, required=True)
        s.add_argument("--data", type=Path, required=True)
        s.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        spec, sha = load_spec(args.spec)
        if args.cmd == "download":
            import requests
            missing = fetch(requests.Session(), args.out, spec["symbols"], args.first_month,
                            args.last_month, args.extra_day)
            print(json.dumps({"missing": missing}, indent=2))
            return 0
        if args.out.exists():
            raise KlinesError(f"{args.out} exists; refusing to overwrite")
        out = run(spec, sha, args.data, args.cmd)
    except KlinesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
