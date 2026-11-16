"""Docker healthcheck for the continuous collector.

Reads the status file the collector already rewrites every few seconds while streaming and on
every state change. Unhealthy when the file is missing, older than MAX_AGE_SECONDS, or shows a
terminal state. Healthy states include RESYNCING: brief recoveries are normal, and a recovery
loop ends in HALT through the recovery policy. Nothing restarts the collector on failure; this
only makes a stall or HALT visible as "unhealthy" in Docker.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

DEFAULT_PATH = "/data/status/collector_status.json"
MAX_AGE_SECONDS = 120
LIVE_STATES = {"RUNNING", "SYNCING", "CONNECTING", "RESYNCING"}


def check(path: str | Path, now: datetime | None = None) -> str | None:
    """Return None when healthy, else the reason."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data["ts"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"status unreadable: {exc}"
    age = ((now or datetime.now(timezone.utc)) - ts).total_seconds()
    if data.get("status") not in LIVE_STATES:
        return f"status {data.get('status')}: {data.get('reason')}"
    if age > MAX_AGE_SECONDS:
        return f"status {data['status']} is {age:.0f}s old"
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    reason = check(argv[0] if argv else DEFAULT_PATH)
    print(reason or "ok")
    return 1 if reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
