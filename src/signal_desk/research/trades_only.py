"""Trades-only BTCUSDT research runner for docs/research/trades_only_v1.json.

Offline, paper-only, research code. Two phases:

* ``validation``: loads train+validation days only, evaluates the 12 predeclared
  configurations and baselines, selects one on validation.
* ``test``: evaluates the selected configuration once on the test days. A marker
  file next to the data is created before evaluation; a second test run is refused.

Prices are last-trade references with a fixed adverse half-spread+slippage
adjustment because the trade files contain no bid/ask (see the spec caveat).
"""
from __future__ import annotations

import argparse
from array import array
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import zipfile

from signal_desk.research.binance_trades_download import days, verify_local

RUNNER_VERSION = "research-trades-only-v1"
PINNED_SPEC_SHA256 = "75219c3c89e2e0179a867de93396a376d2b6bc76edb4878fc1a407faa0422955"
TEST_MARKER = "trades_only_v1.test_run"
FLOW_WINDOW = 30
MOMENTUM_LOOKBACK = 60


class TradesOnlyError(ValueError):
    pass


def _ts(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def load_spec(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != PINNED_SPEC_SHA256:
        raise TradesOnlyError(f"spec sha256 {digest} != pinned {PINNED_SPEC_SHA256}; "
                              "a changed spec needs a new version")
    return json.loads(data), digest


def iter_trades(zip_path: Path):
    """Yield (time_us, price, qty, buyer_is_maker) from a Binance daily trades zip."""
    with zipfile.ZipFile(zip_path) as zf:
        members = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(members) != 1:
            raise TradesOnlyError(f"{zip_path.name}: expected one CSV, found {members}")
        with zf.open(members[0]) as raw:
            for line in io.TextIOWrapper(raw, encoding="ascii"):
                fields = line.rstrip("\r\n").split(",")
                if not fields[0].isdigit():  # optional header row
                    continue
                t = int(fields[4])
                yield (t if t >= 10**14 else t * 1000, float(fields[1]), float(fields[2]),
                       fields[5].strip().lower() == "true")


class Seconds:
    """Per-second causal state for [start, end) UTC seconds.

    Index b covers trades with time in (start+b-1, start+b]. Arrays are
    cumulative (buy qty, sell qty, count) and forward-filled (last price, index
    of the last bucket with a trade; 0 means none yet).
    """

    def __init__(self, start: int, end: int):
        self.start, self.n = start, end - start
        size = self.n + 1
        self.buy = array("d", bytes(8 * size))
        self.sell = array("d", bytes(8 * size))
        self.count = array("q", bytes(8 * size))
        self.price = array("d", bytes(8 * size))
        self.last = array("q", bytes(8 * size))
        self._prev_us = None

    def add(self, trades, max_trades: int | None = None) -> int:
        start_us, used = self.start * 1_000_000, 0
        for i, (t_us, price, qty, buyer_is_maker) in enumerate(trades):
            if max_trades is not None and i >= max_trades:
                raise TradesOnlyError(f"more than {max_trades} trades in one file")
            if self._prev_us is not None and t_us < self._prev_us:
                raise TradesOnlyError(f"trade time decreased at {t_us}")
            self._prev_us = t_us
            b = -(-(t_us - start_us) // 1_000_000)
            if b <= 0 or b > self.n:
                continue
            if buyer_is_maker:
                self.sell[b] += qty
            else:
                self.buy[b] += qty
            self.count[b] += 1
            self.price[b] = price
            self.last[b] = b
            used += 1
        return used

    def finish(self) -> "Seconds":
        for b in range(1, self.n + 1):
            self.buy[b] += self.buy[b - 1]
            self.sell[b] += self.sell[b - 1]
            self.count[b] += self.count[b - 1]
            if not self.last[b]:
                self.price[b], self.last[b] = self.price[b - 1], self.last[b - 1]
        return self

    def age(self, b: int) -> float:
        return b - self.last[b] if self.last[b] else math.inf


def load_range(data_dir: Path, symbol: str, start: int, end: int, max_trades: int) -> Seconds:
    secs = Seconds(start, end)
    first = datetime.fromtimestamp(start, timezone.utc).date()
    last = datetime.fromtimestamp(end - 1, timezone.utc).date()
    for day in days(first, date.fromordinal(last.toordinal() + 1)):
        secs.add(iter_trades(verify_local(data_dir, symbol, day)), max_trades)
    return secs.finish()


def score(secs: Seconds, feature: str, t: int):
    if feature == "always_long":
        return math.inf
    if feature == "no_change":
        return 0.0
    if feature == "signed_aggressor_qty_ratio_30s":
        a = t - FLOW_WINDOW
        if secs.count[t] - secs.count[a] < 2:
            return None
        buy, sell = secs.buy[t] - secs.buy[a], secs.sell[t] - secs.sell[a]
        return (buy - sell) / (buy + sell) if buy + sell > 0 else None
    if feature == "momentum_60s":
        a = t - MOMENTUM_LOOKBACK
        if a < 1 or not secs.last[a]:
            return None
        return secs.price[t] / secs.price[a] - 1.0
    raise TradesOnlyError(f"unknown feature {feature}")


def _costs(spec):
    c = spec["execution_and_costs"]
    return c["fee_per_side"], c["adverse_price_adjustment_bps_per_side"] / 10_000, c["execution_delay_seconds"]


def net_return(spec, entry_ref: float, exit_ref: float) -> tuple[float, float]:
    fee, adverse, _ = _costs(spec)
    gross = exit_ref / entry_ref - 1.0
    net = exit_ref * (1 - adverse) * (1 - fee) / (entry_ref * (1 + adverse) * (1 + fee)) - 1.0
    return gross, net


def eligible_bounds(spec, secs: Seconds, seg_start: int, seg_end: int):
    """Relative [first grid t, last allowed exit index] for a segment."""
    clock, split = spec["clock"], spec["chronological_split"]
    lead = clock["warmup_seconds"] if seg_start == _ts(spec["window_utc"]["start"]) \
        else split["embargo_seconds_after_each_boundary"]
    cadence = clock["prediction_cadence_seconds"]
    first = seg_start - secs.start + lead
    first += (-first) % cadence
    return first, seg_end - secs.start - split["purge_seconds_before_each_boundary"]


def evaluate(spec, secs: Seconds, seg_start: int, seg_end: int, feature: str,
             threshold: float, horizon: int) -> dict:
    _, _, delay = _costs(spec)
    max_age = spec["clock"]["max_trade_age_seconds"]
    cadence = spec["clock"]["prediction_cadence_seconds"]
    first, exit_limit = eligible_bounds(spec, secs, seg_start, seg_end)
    gross, net, stale_exits, free_at = [], [], 0, -1
    t = first
    while t + horizon + delay <= exit_limit:
        if t >= free_at and secs.age(t) <= max_age:
            s = score(secs, feature, t)
            entry = t + delay
            if s is not None and s > threshold and secs.age(entry) <= max_age:
                exit_ = entry + horizon
                stale_exits += secs.age(exit_) > max_age
                g, r = net_return(spec, secs.price[entry], secs.price[exit_])
                gross.append(g)
                net.append(r)
                free_at = exit_
        t += cadence
    return metrics(gross, net, stale_exits)


def buy_and_hold(spec, secs: Seconds, seg_start: int, seg_end: int) -> dict:
    _, _, delay = _costs(spec)
    first, exit_limit = eligible_bounds(spec, secs, seg_start, seg_end)
    entry = first + delay
    if entry >= exit_limit or not secs.last[entry]:
        return metrics([], [], 0)
    g, r = net_return(spec, secs.price[entry], secs.price[exit_limit])
    return metrics([g], [r], int(secs.age(exit_limit) > spec["clock"]["max_trade_age_seconds"]))


def metrics(gross: list, net: list, stale_exits: int) -> dict:
    n = len(net)
    out = {"trades": n, "stale_exit_count": stale_exits, "hit_rate": None,
           "mean_gross_return": None, "mean_net_return": None, "t_stat": None,
           "total_net_return": 0.0, "max_drawdown": 0.0}
    if not n:
        return out
    mean = sum(net) / n
    equity = peak = 1.0
    drawdown = 0.0
    for r in net:
        equity *= 1 + r
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
    std = math.sqrt(sum((r - mean) ** 2 for r in net) / (n - 1)) if n > 1 else 0.0
    out.update(hit_rate=sum(r > 0 for r in net) / n, mean_gross_return=sum(gross) / n,
               mean_net_return=mean, t_stat=mean / (std / math.sqrt(n)) if std > 0 else None,
               total_net_return=equity - 1, max_drawdown=drawdown)
    return out


def configurations(spec):
    for name, cand in spec["decision_rule"]["candidates"].items():
        for threshold in cand["thresholds"]:
            for horizon in spec["horizons_seconds"]:
                yield {"candidate": name, "feature": cand["feature"],
                       "threshold": threshold, "horizon_seconds": horizon}


def baselines(spec, secs, seg_start, seg_end, horizons):
    zero = metrics([], [], 0)
    out = {"no_trade": zero, "no_change": evaluate(spec, secs, seg_start, seg_end, "no_change", 0.0, horizons[0]),
           "buy_and_hold": buy_and_hold(spec, secs, seg_start, seg_end)}
    for h in horizons:
        out[f"always_long_{h}s"] = evaluate(spec, secs, seg_start, seg_end, "always_long", 0.0, h)
    return out


def select(spec, results: list) -> dict | None:
    rule = spec["selection"]
    ok = [r for r in results if r["validation"]["trades"] >= rule["min_validation_trades"]]
    if not ok:
        return None
    best = min(ok, key=lambda r: (-r["validation"]["mean_net_return"], -r["validation"]["trades"],
                                  r["config"]["threshold"], r["config"]["horizon_seconds"]))
    return best["config"]


def run_validation(spec, spec_sha, data_dir: Path) -> dict:
    w = spec["window_utc"]
    start, val_start, test_start = _ts(w["start"]), _ts(w["validation_start"]), _ts(w["test_start"])
    secs = load_range(data_dir, spec["symbol"], start, test_start, spec["limits"]["max_trades_per_day"])
    results = [{"config": c,
                "train": evaluate(spec, secs, start, val_start, c["feature"], c["threshold"], c["horizon_seconds"]),
                "validation": evaluate(spec, secs, val_start, test_start, c["feature"], c["threshold"], c["horizon_seconds"])}
               for c in configurations(spec)]
    horizons = spec["horizons_seconds"]
    return {"runner_version": RUNNER_VERSION, "phase": "validation", "spec_version": spec["experiment_version"],
            "spec_sha256": spec_sha, "git_commit": _git_commit(),
            "window": {"train": [w["start"], w["validation_start"]], "validation": [w["validation_start"], w["test_start"]]},
            "configurations": results,
            "baselines": {"train": baselines(spec, secs, start, val_start, horizons),
                          "validation": baselines(spec, secs, val_start, test_start, horizons)},
            "selected": select(spec, results),
            "note": "validation-only selection; no test data loaded; not a profitability claim"}


def run_test(spec, spec_sha, data_dir: Path, selection: dict) -> dict:
    if selection.get("phase") != "validation" or selection.get("spec_sha256") != spec_sha:
        raise TradesOnlyError("selection file is not a validation result for this spec")
    chosen = selection.get("selected")
    if not chosen:
        raise TradesOnlyError("validation selected nothing; verdict is fail and test must not run")
    try:
        (data_dir / TEST_MARKER).open("x").write(json.dumps({"selected": chosen}) + "\n")
    except FileExistsError:
        raise TradesOnlyError(f"test set already used ({data_dir / TEST_MARKER}); it is spent") from None
    w = spec["window_utc"]
    test_start, end = _ts(w["test_start"]), _ts(w["end"])
    secs = load_range(data_dir, spec["symbol"], test_start, end, spec["limits"]["max_trades_per_day"])
    h = chosen["horizon_seconds"]
    result = evaluate(spec, secs, test_start, end, chosen["feature"], chosen["threshold"], h)
    base = baselines(spec, secs, test_start, end, [h])
    always = base[f"always_long_{h}s"]["mean_net_return"]
    checks = {
        "trades_ge_300": result["trades"] >= 300,
        "mean_net_positive": (result["mean_net_return"] or 0.0) > 0,
        "total_net_positive": result["total_net_return"] > 0,
        "beats_always_long_same_horizon": result["mean_net_return"] is not None
            and (always is None or result["mean_net_return"] > always),
    }
    return {"runner_version": RUNNER_VERSION, "phase": "test", "spec_version": spec["experiment_version"],
            "spec_sha256": spec_sha, "git_commit": _git_commit(), "window": {"test": [w["test_start"], w["end"]]},
            "selected": chosen, "test": result, "baselines": base, "checks": checks,
            "verdict": "pass" if all(checks.values()) else "fail",
            "caveat": spec["execution_and_costs"]["caveat"]}


def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=("validation", "test"))
    parser.add_argument("--selection", type=Path, help="validation output (required for --phase test)")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise TradesOnlyError(f"output {args.output} exists; refusing to overwrite")
    spec, sha = load_spec(args.spec)
    if args.phase == "validation":
        report = run_validation(spec, sha, args.data_dir)
    else:
        if not args.selection:
            raise TradesOnlyError("--selection is required for --phase test")
        report = run_test(spec, sha, args.data_dir, json.loads(args.selection.read_text()))
    with args.output.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(json.dumps({"phase": args.phase, "selected": report.get("selected"),
                      "verdict": report.get("verdict")}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
