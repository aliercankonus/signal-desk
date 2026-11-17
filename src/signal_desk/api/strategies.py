"""Hourly forward reads of v4, v6 and carry-v1 for the /strategies page (owner-approved 2026-11-17).

Downloads public Binance files into <base_path>/research, runs each runner's interim read with its
pinned spec (rules unchanged), and writes strategies.json. Read-only research: no orders, no keys.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

from signal_desk.research import carry_v1, klines_v4, klines_v6
from signal_desk.research.binance_trades_download import parse_checksum
from signal_desk.research.klines_v3 import BASE_URL as SPOT_URL, KlinesError, _get

SPECS = Path(__file__).resolve().parents[3] / "docs/research"
FUT_URL = carry_v1.FUTURES_URL


def _root(config: dict) -> Path:
    return Path(config["storage"]["base_path"]) / "research"


def _fetch(session, url: str, target: Path) -> bool:
    """Verified download unless already present; False on HTTP 404 (not published yet)."""
    if target.exists():
        return True
    try:
        expected = parse_checksum(_get(session, url + ".CHECKSUM").text, target.name)
        body = _get(session, url).content
    except KlinesError as exc:
        if "HTTP 404" in str(exc):
            return False
        raise
    if hashlib.sha256(body).hexdigest() != expected:
        raise KlinesError(f"SHA-256 mismatch for {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_name(target.name + ".CHECKSUM").write_text(f"{expected}  {target.name}\n")
    target.write_bytes(body)
    return True


def sync(session, base: str, path: str, name: str, out: Path, first_month: str, today, daily=True) -> None:
    """Monthly zips from first_month; for months without one, daily zips up to yesterday.

    Daily zips are deleted once their month's zip lands (the loaders reject duplicate bars)."""
    y, m = map(int, first_month.split("-"))
    while (y, m) <= (today.year, today.month):
        mo = f"{y:04d}-{m:02d}"
        current = (y, m) == (today.year, today.month)
        if not current and _fetch(session, f"{base}/monthly/{path}/{name}-{mo}.zip", out / f"{name}-{mo}.zip"):
            for f in out.glob(f"{name}-{mo}-??.zip*"):
                f.unlink()
        elif daily:
            for d in range(1, 32):
                day = f"{mo}-{d:02d}"
                if day >= today.isoformat():
                    break
                _fetch(session, f"{base}/daily/{path}/{name}-{day}.zip", out / f"{name}-{day}.zip")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def download_all(session, root: Path, today) -> None:
    for sym in ("BTCUSDT", "ETHUSDT"):
        sync(session, SPOT_URL, f"klines/{sym}/1h", f"{sym}-1h", root / "klines" / sym, "2023-05", today)
    carry = root / "carry"
    sync(session, FUT_URL, "klines/BTCUSDT/1h", "BTCUSDT-1h", carry / "perp", "2026-10", today)
    sync(session, FUT_URL, "fundingRate/BTCUSDT", "BTCUSDT-fundingRate", carry / "funding", "2026-10", today,
         daily=False)
    spot = carry / "spot"
    if not spot.exists():
        spot.symlink_to(Path("..") / "klines" / "BTCUSDT")


def _summary(trades: list[dict], total: dict, out: dict) -> dict:
    closed = [t for t in trades if t.get("status") == "closed"]
    return {"trades": trades, "trade_count": len(trades), "closed": len(closed),
            "open": len(trades) - len(closed),
            "winners": sum(t.get("net_return", 0) > 0 for t in trades),
            "losers": sum(t.get("net_return", 0) <= 0 for t in trades),
            "total_net_return": total["total_net_return"], "max_drawdown": total["max_drawdown"],
            "verdict": out["verdict"], "complete": out["complete"]}


def run_all(root: Path) -> dict:
    res = {}

    def guard(name, fn):
        try:
            res[name] = {"status": "ok", **fn()}
        except Exception as exc:  # e.g. "no forward bars in data yet": shown as waiting on the page
            res[name] = {"status": "waiting", "error": str(exc)}

    def v4():
        spec, sha = klines_v4.load_spec(SPECS / "klines_hourly_v4.json")
        out = klines_v4.run(spec, sha, root / "klines" / "BTCUSDT", "forward")
        return {**_summary(out["trades"], out["result"], out), "data_through": out["exit_bar"],
                "position": out["position_at_last_signal"]}

    def v6():
        spec, sha = klines_v6.load_spec(SPECS / "klines_hourly_v6.json")
        out = klines_v6.run(spec, sha, root / "klines", "forward")
        tr = [{**t, "symbol": s} for s, c in out["per_coin"].items() for t in c["trades"]]
        tr.sort(key=lambda t: t["entry_time"])
        exposure = {s: c["exposure_at_last_signal"] for s, c in out["per_coin"].items()}
        return {**_summary(tr, out["portfolio"], out), "data_through": out["window"][1], "position": exposure}

    def carry():
        spec, sha = carry_v1.load_spec(SPECS / "forward_carry_v1.json")
        w = spec["window_utc"]
        out = carry_v1.run(spec, sha, root / "carry", carry_v1._ts(w["start"]), carry_v1._ts(w["end"]), "forward")
        r = out["result"]
        return {**_summary(out["trades"], r, out), "position": int(bool(r.get("position_open_at_end_of_data"))),
                "data_through": r["exit_bar"]}

    guard("v4", v4)
    guard("v6", v6)
    guard("carry-v1", carry)
    return res


def refresh(config: dict, session=None, today=None) -> dict:
    root = _root(config)
    today = today or datetime.now(timezone.utc).date()
    summary = {"updated_at": datetime.now(timezone.utc).isoformat(), "cost_per_side": 0.0012,
               "benchmark": "cash (0%)"}
    try:
        if session is None:
            import requests
            session = requests.Session()
        download_all(session, root, today)
    except Exception as exc:
        summary["download_error"] = str(exc)
    summary["strategies"] = run_all(root)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / "strategies.json.tmp"
    tmp.write_text(json.dumps(summary, sort_keys=True))
    os.replace(tmp, root / "strategies.json")
    return summary


def build_strategies(config: dict) -> dict:
    path = _root(config) / "strategies.json"
    if not path.is_file():
        return {"status": "waiting for first refresh"}
    return json.loads(path.read_text())


def start_refresh_thread(config_path: str, every_s: int = 3600) -> threading.Thread:
    from signal_desk.config.loader import validate_config

    def loop():
        while True:
            try:
                refresh(validate_config(config_path))
            except Exception as exc:
                print(f"strategies refresh failed: {exc}", file=sys.stderr, flush=True)
            time.sleep(every_s)

    t = threading.Thread(target=loop, name="strategies-refresh", daemon=True)
    t.start()
    return t
