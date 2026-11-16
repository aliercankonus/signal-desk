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
from signal_desk.collector.continuous import EVENTS_FILE

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


EVENT_FIELDS = ("ts", "status", "reason", "gap", "retry_in_seconds", "uptime_seconds", "files",
                "saved_bytes", "deleted_files", "freed_bytes")


def _events(status_dir: Path) -> list[dict[str, Any]]:
    """The collector's state history (oldest first); unreadable lines are skipped."""
    out = []
    for path in (status_dir / (EVENTS_FILE + ".1"), status_dir / EVENTS_FILE):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
                date.fromisoformat(event["ts"][:10])
            except (ValueError, KeyError, TypeError):
                continue
            out.append({k: event[k] for k in EVENT_FIELDS if k in event})
    return out


def _quality_by_day(events: list[dict[str, Any]]) -> tuple[date | None, dict[str, dict[str, Any]]]:
    """Per-UTC-day counts from the history, and the first day the history covers."""
    days: dict[str, dict[str, Any]] = {}
    for e in events:
        q = days.setdefault(e["ts"][:10], {"restarts": 0, "reconnects": 0, "gaps": 0, "halts": 0})
        if e["status"] == "STARTED":
            q["restarts"] += 1
        elif e["status"] == "RESYNCING":
            q["reconnects"] += 1
            q["gaps"] += int(bool(e.get("gap")))
        elif e["status"] == "HALT":
            q["halts"] += 1
    since = date.fromisoformat(events[0]["ts"][:10]) if events else None
    return since, days


def _verdict(q: dict[str, Any]) -> str:
    if q["gaps"] or q["halts"]:
        return "gap"
    if q["reconnects"] or q["restarts"]:
        return "reconnects"
    return "clean"


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
    events = _events(base / "status")
    sessions = build_sessions(config, now)
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
            # Clean days come from the collector's event history; the raw audit is not included.
            "phase1_required_days": PHASE1_GATE_DAYS,
            "s1_window_days": S1_WINDOW_DAYS,
            "days_with_data": len(days),
            "clean_days": sessions["clean_days"],
            "quality_since": sessions["quality_since"],
        },
        "events": list(reversed(events[-30:])),
    }


def build_sessions(config: dict, now: datetime | None = None) -> dict[str, Any]:
    """Per-UTC-day view of the raw store: which days exist, their files, compression, expiry,
    and quality counted from the collector's event history (None before the history began).
    The raw audit is not part of the quality verdict.
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
    since, counts = _quality_by_day(_events(Path(config["storage"]["base_path"]) / "status"))

    def quality(day: date) -> dict[str, Any] | None:
        if since is None or day < since:
            return None  # before the collector kept a history
        q = dict(counts.get(str(day), {"restarts": 0, "reconnects": 0, "gaps": 0, "halts": 0}))
        q["verdict"] = _verdict(q)
        q["partial"] = day == since  # history started during this day
        return q

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
                "quality": quality(day),
            })
            day += timedelta(days=1)
    return {
        "generated_ts": now.isoformat().replace("+00:00", "Z"),
        "today": str(today),
        "first_day": days[0]["day"] if days else None,
        "keep_days": keep,
        "s1_window_days": S1_WINDOW_DAYS,
        "phase1_required_days": PHASE1_GATE_DAYS,
        "quality_since": str(since) if since else None,
        "clean_days": _clean_days(days, today) if since else None,
        "days": days,
    }


def _clean_days(days: list[dict[str, Any]], today: date) -> int:
    """Closed, fully recorded days with raw data and no gap, halt, reconnect or restart."""
    return sum(1 for d in days if d["day"] != str(today) and d["state"] == "collected"
               and d["quality"] and not d["quality"]["partial"] and d["quality"]["verdict"] == "clean")
