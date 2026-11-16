"""Daily-klines BTCUSDT research runner for docs/research/klines_daily_v3.json.

Offline, paper-only, research code. ``download`` fetches checksum-verified 1d kline
zips; ``validate`` runs the 15 predeclared long/flat rules on train and validation
(no test-period data is used) and selects one; ``test`` runs that one choice once on
the test segment, guarded by a marker file next to the data.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import zipfile

from signal_desk.research.binance_trades_download import parse_checksum, sha256_file
from signal_desk.research.trades_only import _git_commit

RUNNER_VERSION = "research-klines-daily-v3"
PINNED_SPEC_SHA256 = "7f9d6e8db1b9a657089d652be9631f0fb9e6f0eb3fe0882475cf4410375b35ce"
TEST_MARKER = "klines_daily_v3.test_run"
BASE_URL = "https://data.binance.vision/data/spot"
SYMBOL = "BTCUSDT"


class KlinesError(RuntimeError):
    pass


def load_spec(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != PINNED_SPEC_SHA256:
        raise KlinesError(f"spec sha256 {digest} != pinned {PINNED_SPEC_SHA256}; "
                          "a changed spec needs a new version")
    return json.loads(data), digest


# ---- data -------------------------------------------------------------------

def download(session, out_dir: Path, first_month: str, last_month: str, extra_days=(),
             interval: str = "1d") -> list[Path]:
    """Monthly kline zips first_month..last_month (YYYY-MM) plus optional daily zips."""
    out_dir.mkdir(parents=True, exist_ok=True)
    y, m = map(int, first_month.split("-"))
    names = []
    while f"{y:04d}-{m:02d}" <= last_month:
        names.append(("monthly", f"{SYMBOL}-{interval}-{y:04d}-{m:02d}.zip"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    names += [("daily", f"{SYMBOL}-{interval}-{d}.zip") for d in extra_days]
    paths = []
    for kind, name in names:
        url = f"{BASE_URL}/{kind}/klines/{SYMBOL}/{interval}/{name}"
        expected = parse_checksum(_get(session, url + ".CHECKSUM").text, name)
        body = _get(session, url).content
        if hashlib.sha256(body).hexdigest() != expected:
            raise KlinesError(f"SHA-256 mismatch for {url}")
        (out_dir / name).write_bytes(body)
        (out_dir / f"{name}.CHECKSUM").write_text(f"{expected}  {name}\n")
        paths.append(out_dir / name)
    return paths


def _get(session, url):
    response = session.get(url, timeout=60)
    if response.status_code != 200:
        raise KlinesError(f"GET {url} -> HTTP {response.status_code}")
    return response


def load_days(data_dir: Path, last_day: date | None = None):
    """Verified (days, opens, closes), consecutive UTC days, optionally up to last_day."""
    rows = {}
    zips = sorted(data_dir.glob(f"{SYMBOL}-1d-*.zip"))
    if not zips:
        raise KlinesError(f"no kline zips in {data_dir}")
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
                    ts = ts // 1000 if ts >= 10**14 else ts  # microseconds from 2025
                    day = datetime.fromtimestamp(ts / 1000, timezone.utc).date()
                    if day in rows:
                        raise KlinesError(f"duplicate day {day}")
                    rows[day] = (float(rec[1]), float(rec[4]))
    days = sorted(d for d in rows if last_day is None or d <= last_day)
    for a, b in zip(days, days[1:]):
        if b - a != timedelta(days=1):
            raise KlinesError(f"gap between {a} and {b}")
    return days, [rows[d][0] for d in days], [rows[d][1] for d in days]


# ---- rules ------------------------------------------------------------------

def configurations(spec) -> list[dict]:
    out = []
    for fam in spec["configurations"]:
        f = fam["family"]
        if f == "sma_trend":
            out += [{"family": f, "N": n} for n in fam["N"]]
        elif f == "momentum":
            out += [{"family": f, "L": n} for n in fam["L"]]
        elif f == "donchian":
            out += [{"family": f, "N": n} for n in fam["N"]]
        elif f == "mean_reversion":
            out += [{"family": f, "X": x, "H": h} for x in fam["X"] for h in fam["H"]]
        else:
            raise KlinesError(f"unknown family {f}")
    return out


def states(cfg, closes) -> list[int]:
    """s[i] = desired position after the close of day i (uses closes[0..i] only)."""
    s, state, count = [], 0, 0
    f = cfg["family"]
    for i, c in enumerate(closes):
        if f == "sma_trend":
            n = cfg["N"]
            state = int(i >= n - 1 and c > sum(closes[i - n + 1:i + 1]) / n)
        elif f == "momentum":
            n = cfg["L"]
            state = int(i >= n and c / closes[i - n] - 1 > 0)
        elif f == "donchian":
            n, m = cfg["N"], cfg["N"] // 2
            if not state and i >= n and c > max(closes[i - n:i]):
                state = 1
            elif state and i >= m and c < min(closes[i - m:i]):
                state = 0
        elif f == "mean_reversion":
            count = max(count - 1, 0)
            if i >= 1 and c / closes[i - 1] - 1 <= -cfg["X"]:
                count = cfg["H"]
            state = int(count > 0)
        s.append(state)
    return s


# ---- simulation -------------------------------------------------------------

def simulate(pos, opens, a: int, b: int, cost: float, embargo: bool = True) -> dict:
    """Days a..b-1; pos[d] is the position for day d; needs opens[b] for the final exit."""
    eq, held, rets, entries, long_days, peak, mdd = 1.0, 0, [], 0, 0, 1.0, 0.0
    for d in range(a, b):
        start = eq
        p = 0 if (embargo and d == a) else pos[d]
        if p != held:
            eq *= 1 - cost
            entries += p
            held = p
        if p:
            eq *= opens[d + 1] / opens[d]
            long_days += 1
        if d == b - 1 and held:
            eq *= 1 - cost
        rets.append(eq / start - 1)
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    n = len(rets)
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    return {"days": n, "total_net_return": eq - 1, "cagr": eq ** (365 / n) - 1,
            "sharpe": mean / sd * math.sqrt(365) if sd > 0 else 0.0,
            "max_drawdown": mdd, "round_trips": entries, "exposure": long_days / n}


def _index(days, day: date) -> int:
    try:
        return days.index(day)
    except ValueError:
        raise KlinesError(f"{day} not in data") from None


def _segments(spec, days, names):
    w = {k: date.fromisoformat(v) for k, v in spec["window_utc"].items()}
    bounds = {"train": ("start", "validation_start"), "validation": ("validation_start", "test_start"),
              "test": ("test_start", "end")}
    return {n: (_index(days, w[bounds[n][0]]), _index(days, w[bounds[n][1]])) for n in names}


def evaluate(spec, days, opens, closes, names) -> tuple[list, dict]:
    cost = spec["execution_and_costs"]["cost_per_side"]
    info_cost = spec["execution_and_costs"]["adverse_price_per_side"] + 0.00075
    segs = _segments(spec, days, names)
    results = []
    for cfg in configurations(spec):
        s = states(cfg, closes)
        pos = [0] + s[:-1]
        results.append({"config": cfg, **{n: {**simulate(pos, opens, a, b, cost),
                                               "fee_0075_info": simulate(pos, opens, a, b, info_cost)}
                                           for n, (a, b) in segs.items()}})
    ones = [1] * len(days)
    baselines = {n: {"buy_and_hold": simulate(ones, opens, a, b, cost, embargo=False),
                     "cash": {"total_net_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}}
                 for n, (a, b) in segs.items()}
    return results, baselines


def select(spec, results, baselines):
    rule = spec["selection"]
    ok = [r for r in results if r["validation"]["round_trips"] >= rule["min_validation_round_trips"]]
    if not ok:
        return None, False
    best = max(ok, key=lambda r: (r["validation"]["sharpe"], r["validation"]["total_net_return"],
                                  -results.index(r)))
    v, bh = best["validation"], baselines["validation"]["buy_and_hold"]
    return best["config"], v["total_net_return"] > 0 and v["sharpe"] > bh["sharpe"]


def run_validation(spec, sha, data_dir: Path) -> dict:
    test_start = date.fromisoformat(spec["window_utc"]["test_start"])
    days, opens, closes = load_days(data_dir, last_day=test_start)  # no test-period data
    results, baselines = evaluate(spec, days, opens, closes, ("train", "validation"))
    selected, gate = select(spec, results, baselines)
    return {"phase": "validation", "spec_version": spec["experiment_version"], "spec_sha256": sha,
            "runner_version": RUNNER_VERSION, "git_commit": _git_commit(),
            "data_last_day": str(days[-1]), "configurations": results, "baselines": baselines,
            "selected": selected, "gate_passed": gate,
            "note": "validation-only selection; no test data loaded; not a profitability claim"}


def run_test(spec, sha, data_dir: Path, selection: dict) -> dict:
    if selection.get("phase") != "validation" or selection.get("spec_sha256") != sha:
        raise KlinesError("selection file is not a validation output of this spec")
    if not selection.get("selected") or not selection.get("gate_passed"):
        raise KlinesError("validation gate not passed; the test must not run")
    try:
        with (data_dir / TEST_MARKER).open("x") as f:
            f.write(datetime.now(timezone.utc).isoformat() + "\n")
    except FileExistsError:
        raise KlinesError(f"test already run ({data_dir / TEST_MARKER} exists); the test set is spent") from None
    days, opens, closes = load_days(data_dir)
    cfg = selection["selected"]
    pos = [0] + states(cfg, closes)[:-1]
    a, b = _segments(spec, days, ("test",))["test"]
    cost = spec["execution_and_costs"]["cost_per_side"]
    info_cost = spec["execution_and_costs"]["adverse_price_per_side"] + 0.00075
    sel = simulate(pos, opens, a, b, cost)
    bh = simulate([1] * len(days), opens, a, b, cost, embargo=False)
    checks = {"total_net_positive": sel["total_net_return"] > 0, "sharpe_beats_buy_and_hold": sel["sharpe"] > bh["sharpe"],
              "drawdown_below_buy_and_hold": sel["max_drawdown"] < bh["max_drawdown"],
              "round_trips_at_least_5": sel["round_trips"] >= 5}
    return {"phase": "test", "spec_version": spec["experiment_version"], "spec_sha256": sha,
            "runner_version": RUNNER_VERSION, "git_commit": _git_commit(), "selected": cfg,
            "test": {**sel, "fee_0075_info": simulate(pos, opens, a, b, info_cost)},
            "baselines": {"buy_and_hold": bh, "cash": {"total_net_return": 0.0}},
            "checks": checks, "verdict": "pass" if all(checks.values()) else "fail"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--first-month", default="2017-08")
    d.add_argument("--last-month", required=True)
    d.add_argument("--extra-day", action="append", default=[])
    for name in ("validate", "test"):
        s = sub.add_parser(name)
        s.add_argument("--spec", type=Path, required=True)
        s.add_argument("--data", type=Path, required=True)
        s.add_argument("--out", type=Path, required=True)
        if name == "test":
            s.add_argument("--selection", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.cmd == "download":
            import requests
            download(requests.Session(), args.out, args.first_month, args.last_month, args.extra_day)
            return 0
        if args.out.exists():
            raise KlinesError(f"{args.out} exists; refusing to overwrite")
        spec, sha = load_spec(args.spec)
        out = (run_validation(spec, sha, args.data) if args.cmd == "validate"
               else run_test(spec, sha, args.data, json.loads(args.selection.read_text())))
    except KlinesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
