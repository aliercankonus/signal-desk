"""Read-only operator overview: collector status, raw days, disk use and gate progress.

Built from files the collector already writes on the shared /data volume (the status file and
the raw day files). Nothing here writes, restarts or trades; it only reports.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any

from signal_desk.alarms.manager import evaluate_system_alarms
from signal_desk.collector import healthcheck

RAW_DAY = re.compile(r"_(\d{4}-\d{2}-\d{2})\.jsonl(?:\.gz)?$")
PHASE1_GATE_DAYS = 14
S1_WINDOW_DAYS = 60
# Status-file fields shown to the operator; anything else (e.g. error diagnostics) stays out.
COLLECTOR_FIELDS = (
    "status", "reason", "ts", "session_id", "session_synchronized", "consecutive_failures",
    "gap_count", "reconnect_count", "written_events", "depth_events", "trade_events",
    "total_written_events", "total_depth_events", "total_trade_events", "retry_in_seconds",
    "compacted_bytes_saved", "last_compaction_error", "last_error",
)


def _collector(status_path: Path, now: datetime) -> dict[str, Any]:
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out = {key: data[key] for key in COLLECTOR_FIELDS if key in data}
    out.setdefault("status", "UNKNOWN")
    try:
        out["status_age_seconds"] = round((now - datetime.fromisoformat(data["ts"])).total_seconds())
    except (KeyError, TypeError, ValueError):
        out["status_age_seconds"] = None
    out["max_failures"] = (data.get("policy") or {}).get("max_failures")
    problem = healthcheck.check(status_path, now)
    out["healthy"] = problem is None
    out["health_reason"] = problem or "ok"
    return out


def _raw_files(raw_path: Path) -> list[dict[str, Any]]:
    files = []
    if raw_path.is_dir():
        for f in raw_path.rglob("*"):
            if f.is_file():
                match = RAW_DAY.search(f.name)
                files.append({
                    "name": f.name,
                    "day": match.group(1) if match else None,
                    "kind": "depth" if "_depth_" in f.name else "trade" if "_trade_" in f.name else "other",
                    "bytes": f.stat().st_size,
                    "compressed": f.name.endswith(".gz"),
                })
    return sorted(files, key=lambda f: f["name"])


def _raw(raw_path: Path) -> tuple[list[str], int]:
    files = _raw_files(raw_path)
    return sorted({f["day"] for f in files if f["day"]}), sum(f["bytes"] for f in files)


def build_overview(config: dict, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    storage, retention = config["storage"], config["retention"]
    base = Path(storage["base_path"])
    days, raw_bytes = _raw(Path(storage["raw_path"]))
    try:
        usage = shutil.disk_usage(base)
        disk = {"total_bytes": usage.total, "used_bytes": usage.used,
                "used_pct": round(usage.used / usage.total * 100, 1)}
    except OSError:
        disk = {"total_bytes": None, "used_bytes": None, "used_pct": None}
    alarms = evaluate_system_alarms(config, {"disk_pct": disk["used_pct"]})
    keep = retention["raw_target_days"]
    pinned = {str(d) for d in retention.get("raw_pinned_days") or []}
    unpinned = [d for d in days if d not in pinned]
    next_expiry = (str(date.fromisoformat(unpinned[0]) + timedelta(days=keep + 1)) if unpinned else None)
    return {
        "generated_ts": now.isoformat().replace("+00:00", "Z"),
        "project": {
            "mode": config["project"]["mode"],
            "execution_enabled": config["project"]["execution_enabled"],
            "config_version": config["project"]["config_version"],
            "venue": config["venue"]["name"],
            "symbol": config["venue"]["symbol"],
            "product_type": config["venue"]["product_type"],
        },
        "collector": _collector(base / "status" / "collector_status.json", now),
        "raw": {
            "days": days,
            "days_count": len(days),
            "first_day": days[0] if days else None,
            "bytes": raw_bytes,
            "pool_bytes": int(retention["raw_pool_gb"] * 1024 ** 3),
            "keep_days": keep,
            "pinned_days": sorted(pinned),
            "next_expiry_day": next_expiry,
        },
        "disk": disk,
        "alarm_thresholds": {k: config["alarms"][k] for k in ("disk_warn_pct", "disk_enter_block_pct", "disk_halt_pct")},
        "alarms": [{k: a[k] for k in ("severity", "code", "action", "message")} for a in alarms],
        "gates": {
            # Clean-day counting needs the raw audit, which is not wired into the API yet;
            # until then the page shows days with raw data and says so.
            "phase1_required_days": PHASE1_GATE_DAYS,
            "s1_window_days": S1_WINDOW_DAYS,
            "days_with_data": len(days),
            "clean_days_audited": None,
        },
    }


def build_sessions(config: dict, now: datetime | None = None) -> dict[str, Any]:
    """Per-UTC-day view of the raw store: which days exist, their files, compression and expiry.

    Data quality per day (gaps, reconnects, raw audit) is not recorded anywhere yet, so it is
    reported as unknown rather than guessed.
    """
    now = now or datetime.now(timezone.utc)
    retention = config["retention"]
    keep = retention["raw_target_days"]
    pinned = {str(d) for d in retention.get("raw_pinned_days") or []}
    files = _raw_files(Path(config["storage"]["raw_path"]))
    by_day: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        if f["day"]:
            by_day.setdefault(f["day"], []).append(f)
    today = now.date()
    days = []
    if by_day:
        day = date.fromisoformat(min(by_day))
        last = max(today, date.fromisoformat(max(by_day)))
        while day <= last:
            key = str(day)
            day_files = by_day.get(key, [])
            if day == today:
                state = "open"
            elif day_files:
                state = "collected"
            else:
                state = "missing"
            days.append({
                "day": key,
                "state": state,
                "bytes": sum(f["bytes"] for f in day_files),
                "compressed": bool(day_files) and all(f["compressed"] for f in day_files),
                "pinned": key in pinned,
                "expires_day": None if key in pinned else str(day + timedelta(days=keep + 1)),
                "files": [{k: f[k] for k in ("name", "kind", "bytes", "compressed")} for f in day_files],
                "quality": None,  # gaps/reconnects/audit per day are not recorded yet
            })
            day += timedelta(days=1)
    return {
        "generated_ts": now.isoformat().replace("+00:00", "Z"),
        "today": str(today),
        "first_day": days[0]["day"] if days else None,
        "keep_days": keep,
        "s1_window_days": S1_WINDOW_DAYS,
        "phase1_required_days": PHASE1_GATE_DAYS,
        "days": days,
    }
