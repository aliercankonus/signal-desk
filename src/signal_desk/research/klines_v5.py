"""v5: the frozen v4 rule on a fixed multi-coin basket, one run (docs/research/klines_hourly_v5.json)."""

from __future__ import annotations

import argparse
import hashlib
from bisect import bisect_left
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

from signal_desk.research.klines_v3 import KlinesError, download
from signal_desk.research.klines_v4 import BARS_PER_YEAR, HOUR, _ts, load_bars, simulate, states
from signal_desk.research.trades_only import _git_commit

RUNNER_VERSION = "research-klines-hourly-v5"
PINNED_SPEC_SHA256 = "331b612769fff2dd805864df87798007cd14a53b509703c05253eebc065cbaa5"
MARKER = "klines_hourly_v5.run"


def load_spec(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != PINNED_SPEC_SHA256:
        raise KlinesError(f"spec sha256 {digest} != pinned {PINNED_SPEC_SHA256}; a changed spec needs a new version")
    return json.loads(data), digest


def fetch(session, out: Path, symbols, first_month: str, last_month: str, extra_days) -> dict:
    """Download per symbol; months missing (HTTP 404) are skipped and reported (delisted coins)."""
    y, m = map(int, first_month.split("-"))
    months = []
    while f"{y:04d}-{m:02d}" <= last_month:
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    missing = {}
    for sym in symbols:
        for i, mo in enumerate(months + list(extra_days)):
            # an empty month range (first > last) downloads only the daily file
            span = ("9999-12", "0000-01", [mo]) if i >= len(months) else (mo, mo, ())
            try:
                download(session, out / sym, *span, interval="1h", symbol=sym)
            except KlinesError as exc:
                if "HTTP 404" not in str(exc):
                    raise
                missing.setdefault(sym, []).append(mo)
    return missing


def _metrics(eqs) -> dict:
    rets = [eqs[i] / eqs[i - 1] - 1 for i in range(1, len(eqs))]
    n = len(rets)
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))
    peak, mdd = eqs[0], 0.0
    for e in eqs:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    return {"hours": n, "total_net_return": eqs[-1] / eqs[0] - 1,
            "cagr": (eqs[-1] / eqs[0]) ** (BARS_PER_YEAR / n) - 1,
            "sharpe": mean / sd * math.sqrt(BARS_PER_YEAR) if sd > 0 else 0.0, "max_drawdown": mdd}


def _portfolio(curves, start: int, end: int) -> list[float]:
    """Mean sleeve equity on the hourly grid start..end; each sleeve carries forward its last value."""
    idx, last, out = [0] * len(curves), [1.0] * len(curves), []
    for t in range(start, end + 1, HOUR):
        for k, c in enumerate(curves):
            while idx[k] < len(c) and c[idx[k]][0] <= t:
                last[k] = c[idx[k]][1]
                idx[k] += 1
        out.append(sum(last) / len(last))
    return out


def run(spec, sha, data_dir: Path) -> dict:
    win, rule = spec["window_utc"], spec["rule"]
    start, end = _ts(win["start"]), _ts(win["end"])
    cost = spec["execution_and_costs"]["cost_per_side"]
    loaded = {}
    for sym in spec["universe"]["symbols"]:
        times, opens, closes = load_bars(data_dir / sym, last_ts=end, symbol=sym)
        a = bisect_left(times, start)
        if a < rule["N_bars"] or a >= len(times) - 1 or times[a] != start:
            raise KlinesError(f"{sym}: need {rule['N_bars']} bars before and a bar at {win['start']}")
        loaded[sym] = (times, opens, closes, a)
    if not any(v[0][-1] == end for v in loaded.values()):
        raise KlinesError(f"no symbol has a bar at {win['end']}; data incomplete")
    try:
        with (data_dir / MARKER).open("x") as f:
            f.write(datetime.now(timezone.utc).isoformat() + "\n")
    except FileExistsError:
        raise KlinesError(f"v5 already run ({data_dir / MARKER} exists)") from None
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    per_coin, curves, bh_curves = {}, [], []
    for sym, (times, opens, closes, a) in loaded.items():
        b = len(times) - 1
        pos = [0] + states(closes, rule["N_bars"], rule["band"])[:-1]
        curve, bh = [(start, 1.0)], [(start, 1.0)]
        res = simulate(pos, times, opens, a, b, cost, curve=curve)
        bh_res = simulate([1] * len(times), times, opens, a, b, cost, 0, curve=bh)
        curves.append(curve)
        bh_curves.append(bh)
        per_coin[sym] = {"net_return": res["total_net_return"], "max_drawdown": res["max_drawdown"],
                         "round_trips": res["round_trips"], "exposure": res["exposure"],
                         "missing_hours": res["missing_hours"],
                         "data_ends_early_at": None if times[b] == end else iso(times[b]),
                         "buy_and_hold_net_return_info": bh_res["total_net_return"]}
    port = _metrics(_portfolio(curves, start, end))
    port["round_trips"] = sum(c["round_trips"] for c in per_coin.values())
    port["avg_exposure"] = sum(c["exposure"] for c in per_coin.values()) / len(per_coin)
    checks = {"total_net_positive": port["total_net_return"] > 0, "sharpe_above_0_5": port["sharpe"] > 0.5,
              "max_drawdown_below_35pct": port["max_drawdown"] < 0.35,
              "round_trips_at_least_40": port["round_trips"] >= 40}
    return {"spec_version": spec["experiment_version"], "spec_sha256": sha, "runner_version": RUNNER_VERSION,
            "git_commit": _git_commit(), "window": [iso(start), iso(end)], "portfolio": port,
            "coins_net_positive": sum(c["net_return"] > 0 for c in per_coin.values()), "per_coin": per_coin,
            "baselines": {"cash": {"total_net_return": 0.0},
                          "equal_weight_buy_and_hold_info_only": _metrics(_portfolio(bh_curves, start, end))},
            "checks": checks, "verdict": "pass" if all(checks.values()) else "fail"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--spec", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--first-month", default="2023-07")
    d.add_argument("--last-month", default="2026-10")
    d.add_argument("--extra-day", action="append", default=["2026-11-01"])
    r = sub.add_parser("run")
    r.add_argument("--spec", type=Path, required=True)
    r.add_argument("--data", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        spec, sha = load_spec(args.spec)
        if args.cmd == "download":
            import requests
            missing = fetch(requests.Session(), args.out, spec["universe"]["symbols"],
                            args.first_month, args.last_month, args.extra_day)
            print(json.dumps({"missing": missing}, indent=2))
            return 0
        if args.out.exists():
            raise KlinesError(f"{args.out} exists; refusing to overwrite")
        out = run(spec, sha, args.data)
    except KlinesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
