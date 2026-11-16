"""Trades-only BTCUSDT research runner for docs/research/trades_only_v2.json.

Offline, paper-only, research code. Same two phases as v1 (validation selects one
of the 24 predeclared configurations; test runs it once, guarded by a marker
file). v2 changes the trade shape: 60s entry grid on 15-60 minute features, and
rule-based exits (take-profit/stop-loss, signal reversal, trailing stop, fixed
4h), every position capped at 24h and force-closed at its segment end.

Parsing, per-second state and costs are shared with v1 (``trades_only``).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

from signal_desk.research.trades_only import (
    Seconds, TradesOnlyError, _git_commit, _ts, load_range, net_return,
)

RUNNER_VERSION = "research-trades-only-v2"
PINNED_SPEC_SHA256 = "54efe2584b9de6902bd1f47cd8297d5c539db9919c204c5ac4abe8df2ca13ea9"
TEST_MARKER = "trades_only_v2.test_run"
EXIT_RULES = ("tp_sl", "signal_reversal", "trailing_stop", "fixed_4h")
REASONS = ("take_profit", "stop_loss", "trailing_stop", "signal_reversal", "time", "max_hold", "forced")
FLOW_MIN_TRADES = 100
LOOKBACK = {"momentum_15m": 900, "momentum_60m": 3600, "flow_15m": 900}
TAKE_PROFIT, STOP_LOSS, TRAIL, FIXED_HOLD, MAX_HOLD = 0.0060, 0.0030, 0.0040, 14400, 86400


def load_spec(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != PINNED_SPEC_SHA256:
        raise TradesOnlyError(f"spec sha256 {digest} != pinned {PINNED_SPEC_SHA256}; "
                              "a changed spec needs a new version")
    return json.loads(data), digest


def score(secs: Seconds, feature: str, t: int):
    if feature == "always_long":
        return math.inf
    if feature == "no_change":
        return 0.0
    a = t - LOOKBACK[feature]
    if feature == "flow_15m":
        if a < 0 or secs.count[t] - secs.count[a] < FLOW_MIN_TRADES:
            return None
        buy, sell = secs.buy[t] - secs.buy[a], secs.sell[t] - secs.sell[a]
        return (buy - sell) / (buy + sell) if buy + sell > 0 else None
    if a < 1 or not secs.last[a] or not secs.last[t]:
        return None
    return secs.price[t] / secs.price[a] - 1.0


def segment_bounds(spec, secs: Seconds, seg_start: int, seg_end: int):
    """Relative (first grid t, last allowed entry grid t, last price index) for a segment."""
    clock, split = spec["clock"], spec["chronological_split"]
    lead = clock["warmup_seconds"] if seg_start == _ts(spec["window_utc"]["start"]) \
        else split["embargo_seconds_after_each_boundary"]
    grid = clock["entry_grid_seconds"]
    first = seg_start - secs.start + lead
    first += (-first) % grid
    last_idx = seg_end - secs.start - 1          # bucket (end-2, end-1]: strictly inside the segment
    return first, seg_end - secs.start - split["entry_cutoff_seconds_before_segment_end"], last_idx


def find_exit(secs: Seconds, rule: str, exit_feature: str, t: int, entry: int,
              last_idx: int, delay: int, grid: int):
    """Return (exit index, reason) for a position entered at grid time t, filled at index entry."""
    stop = last_idx - delay                       # last trigger whose fill stays inside the segment
    max_trigger = entry + MAX_HOLD
    limit = min(max_trigger, stop)
    trigger, reason = None, None
    if rule == "fixed_4h":
        if entry + FIXED_HOLD <= limit:
            trigger, reason = entry + FIXED_HOLD, "time"
    elif rule == "signal_reversal":
        for g in range(t + grid, limit + 1, grid):
            s = score(secs, exit_feature, g)
            if s is not None and s < 0:
                trigger, reason = g, "signal_reversal"
                break
    elif rule in ("tp_sl", "trailing_stop"):
        price, ref = secs.price, secs.price[entry]
        if rule == "tp_sl":
            up, down = ref * (1 + TAKE_PROFIT), ref * (1 - STOP_LOSS)
            for b in range(entry + 1, limit + 1):
                p = price[b]
                if p >= up or p <= down:
                    trigger, reason = b, "take_profit" if p >= up else "stop_loss"
                    break
        else:
            peak, keep = ref, 1 - TRAIL
            for b in range(entry + 1, limit + 1):
                p = price[b]
                if p > peak:
                    peak = p
                elif p <= peak * keep:
                    trigger, reason = b, "trailing_stop"
                    break
    else:
        raise TradesOnlyError(f"unknown exit rule {rule}")
    if trigger is None:
        if max_trigger <= stop:
            trigger, reason = max_trigger, "max_hold"
        else:
            return last_idx, "forced"
    return trigger + delay, reason


def evaluate(spec, secs: Seconds, seg_start: int, seg_end: int, entry_feature: str,
             threshold: float, rule: str, exit_feature: str | None = None) -> dict:
    """Long-or-flat, one position at a time. exit_feature defaults to entry_feature."""
    delay = spec["execution_and_costs"]["execution_delay_seconds"]
    max_age = spec["clock"]["max_trade_age_seconds_at_entry"]
    grid = spec["clock"]["entry_grid_seconds"]
    exit_feature = exit_feature or entry_feature
    first, last_entry, last_idx = segment_bounds(spec, secs, seg_start, seg_end)
    trades, free_at, t = [], -1, first
    while t < last_entry:
        entry = t + delay
        if t >= free_at and secs.age(t) <= max_age and secs.age(entry) <= max_age:
            s = score(secs, entry_feature, t)
            if s is not None and s > threshold:
                exit_idx, reason = find_exit(secs, rule, exit_feature, t, entry, last_idx, delay, grid)
                gross, net = net_return(spec, secs.price[entry], secs.price[exit_idx])
                trades.append((gross, net, exit_idx - entry, reason, secs.age(exit_idx) > max_age))
                free_at = exit_idx
        t += grid
    return metrics(trades, last_idx - first)


def buy_and_hold(spec, secs: Seconds, seg_start: int, seg_end: int) -> dict:
    """One round trip from the first eligible entry to the segment's last price (its forced-close point)."""
    delay = spec["execution_and_costs"]["execution_delay_seconds"]
    first, _, last_idx = segment_bounds(spec, secs, seg_start, seg_end)
    entry = first + delay
    if entry >= last_idx or not secs.last[entry]:
        return metrics([], last_idx - first)
    gross, net = net_return(spec, secs.price[entry], secs.price[last_idx])
    stale = secs.age(last_idx) > spec["clock"]["max_trade_age_seconds_at_entry"]
    return metrics([(gross, net, last_idx - entry, "forced", stale)], last_idx - first)


def metrics(trades: list, span_seconds: int) -> dict:
    """trades: (gross, net, hold_seconds, exit_reason, stale_exit)."""
    n = len(trades)
    out = {"trades": n, "hit_rate": None, "mean_gross_return": None, "mean_net_return": None,
           "t_stat": None, "total_net_return": 0.0, "max_drawdown": 0.0, "mean_hold_seconds": None,
           "exit_reason_counts": {r: 0 for r in REASONS}, "time_in_market_fraction": 0.0,
           "stale_exit_count": sum(tr[4] for tr in trades)}
    if not n:
        return out
    net = [tr[1] for tr in trades]
    mean = sum(net) / n
    equity = peak = 1.0
    drawdown = 0.0
    for r in net:
        equity *= 1 + r
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
    std = math.sqrt(sum((r - mean) ** 2 for r in net) / (n - 1)) if n > 1 else 0.0
    held = sum(tr[2] for tr in trades)
    for tr in trades:
        out["exit_reason_counts"][tr[3]] += 1
    out.update(hit_rate=sum(r > 0 for r in net) / n, mean_gross_return=sum(tr[0] for tr in trades) / n,
               mean_net_return=mean, t_stat=mean / (std / math.sqrt(n)) if std > 0 else None,
               total_net_return=equity - 1, max_drawdown=drawdown, mean_hold_seconds=held / n,
               time_in_market_fraction=held / span_seconds if span_seconds > 0 else 0.0)
    return out


def configurations(spec):
    for name, cand in spec["entries"]["candidates"].items():
        for threshold in cand["thresholds"]:
            for rule in EXIT_RULES:
                yield {"candidate": name, "feature": cand["feature"], "threshold": threshold, "exit_rule": rule}


def always_long(spec, secs, seg_start, seg_end, config) -> dict:
    return evaluate(spec, secs, seg_start, seg_end, "always_long", 0.0, config["exit_rule"], config["feature"])


def baselines(spec, secs, seg_start, seg_end, configs) -> dict:
    """configs: the configurations whose always-long same-exit baseline is needed."""
    zero = metrics([], 0)
    out = {"no_trade": zero,
           "no_change": evaluate(spec, secs, seg_start, seg_end, "no_change", 0.0, "fixed_4h"),
           "buy_and_hold": buy_and_hold(spec, secs, seg_start, seg_end),
           "always_long_same_exit": {}}
    for c in configs:
        key = c["exit_rule"] if c["exit_rule"] != "signal_reversal" else f"signal_reversal:{c['feature']}"
        if key not in out["always_long_same_exit"]:
            out["always_long_same_exit"][key] = always_long(spec, secs, seg_start, seg_end, c)
    return out


def select(spec, results: list) -> dict | None:
    rule = spec["selection"]
    ok = [r for r in results if r["validation"]["trades"] >= rule["min_validation_trades"]]
    if not ok:
        return None
    best = min(ok, key=lambda r: (-r["validation"]["mean_net_return"], -r["validation"]["trades"],
                                  r["config"]["threshold"], EXIT_RULES.index(r["config"]["exit_rule"])))
    return best["config"]


def _load(spec, data_dir: Path, start: int, end: int) -> Seconds:
    n_days = (datetime.fromtimestamp(end - 1, timezone.utc).date()
              - datetime.fromtimestamp(start, timezone.utc).date()).days + 1
    if n_days > spec["limits"]["max_days_loaded"]:
        raise TradesOnlyError(f"{n_days} days exceeds max_days_loaded")
    return load_range(data_dir, spec["symbol"], start, end, spec["limits"]["max_trades_per_day"])


def run_validation(spec, spec_sha, data_dir: Path) -> dict:
    w = spec["window_utc"]
    start, val_start, test_start = _ts(w["start"]), _ts(w["validation_start"]), _ts(w["test_start"])
    secs = _load(spec, data_dir, start, test_start)
    configs = list(configurations(spec))
    results = [{"config": c,
                "train": evaluate(spec, secs, start, val_start, c["feature"], c["threshold"], c["exit_rule"]),
                "validation": evaluate(spec, secs, val_start, test_start, c["feature"], c["threshold"], c["exit_rule"])}
               for c in configs]
    return {"runner_version": RUNNER_VERSION, "phase": "validation", "spec_version": spec["experiment_version"],
            "spec_sha256": spec_sha, "git_commit": _git_commit(),
            "window": {"train": [w["start"], w["validation_start"]], "validation": [w["validation_start"], w["test_start"]]},
            "configurations": results,
            "baselines": {"train": baselines(spec, secs, start, val_start, configs),
                          "validation": baselines(spec, secs, val_start, test_start, configs)},
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
    secs = _load(spec, data_dir, test_start, end)
    result = evaluate(spec, secs, test_start, end, chosen["feature"], chosen["threshold"], chosen["exit_rule"])
    base = baselines(spec, secs, test_start, end, [chosen])
    always = next(iter(base["always_long_same_exit"].values()))["mean_net_return"]
    mean = result["mean_net_return"]
    checks = {
        "trades_ge_100": result["trades"] >= 100,
        "mean_net_positive": (mean or 0.0) > 0,
        "total_net_positive": result["total_net_return"] > 0,
        "t_stat_ge_2": (result["t_stat"] or 0.0) >= 2.0,
        "beats_always_long_same_exit": mean is not None and (always is None or mean > always),
    }
    return {"runner_version": RUNNER_VERSION, "phase": "test", "spec_version": spec["experiment_version"],
            "spec_sha256": spec_sha, "git_commit": _git_commit(), "window": {"test": [w["test_start"], w["end"]]},
            "selected": chosen, "test": result, "baselines": base, "checks": checks,
            "verdict": "pass" if all(checks.values()) else "fail",
            "caveat": spec["execution_and_costs"]["caveat"]}


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
