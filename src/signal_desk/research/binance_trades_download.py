"""Download Binance daily spot trade zips from data.binance.vision with SHA-256 checks.

Research-only and offline from the collector. Each file is kept only after its
SHA-256 matches the published ``.CHECKSUM``; the checksum text is stored next to
it so the runner can re-verify before use.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import os
from pathlib import Path
import re
import sys

BASE_URL = "https://data.binance.vision/data/spot/daily/trades"
CHUNK = 1024 * 1024
_CHECKSUM_RE = re.compile(r"^([0-9a-f]{64})\s+\*?(\S+)\s*$")


class DownloadError(RuntimeError):
    pass


def zip_name(symbol: str, day: date) -> str:
    return f"{symbol}-trades-{day.isoformat()}.zip"


def zip_url(symbol: str, day: date) -> str:
    return f"{BASE_URL}/{symbol}/{zip_name(symbol, day)}"


def parse_checksum(text: str, expected_name: str) -> str:
    match = _CHECKSUM_RE.match(text.strip())
    if not match:
        raise DownloadError(f"unparseable CHECKSUM for {expected_name}: {text[:100]!r}")
    digest, name = match.groups()
    if name != expected_name:
        raise DownloadError(f"CHECKSUM names {name}, expected {expected_name}")
    return digest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def verify_local(out_dir: Path, symbol: str, day: date) -> Path:
    """Return the zip path if it and its stored CHECKSUM agree; raise otherwise."""
    name = zip_name(symbol, day)
    path, checksum = out_dir / name, out_dir / f"{name}.CHECKSUM"
    if not path.is_file() or not checksum.is_file():
        raise DownloadError(f"missing {name} or its CHECKSUM in {out_dir}")
    expected = parse_checksum(checksum.read_text(), name)
    if sha256_file(path) != expected:
        raise DownloadError(f"SHA-256 mismatch for {path}")
    return path


def _get(session, url: str, stream: bool = False):
    response = session.get(url, stream=stream, timeout=60)
    if response.status_code != 200:
        raise DownloadError(f"GET {url} -> HTTP {response.status_code}")
    return response


def download_day(session, out_dir: Path, symbol: str, day: date, retries: int = 3) -> Path:
    name = zip_name(symbol, day)
    url = zip_url(symbol, day)
    expected = parse_checksum(_get(session, url + ".CHECKSUM").text, name)
    path = out_dir / name
    if path.is_file() and sha256_file(path) == expected:
        (out_dir / f"{name}.CHECKSUM").write_text(f"{expected}  {name}\n")
        return path
    part = out_dir / f"{name}.part"
    last_error = None
    for _ in range(retries):
        h = hashlib.sha256()
        try:
            response = _get(session, url, stream=True)
            with part.open("wb") as f:
                for block in response.iter_content(CHUNK):
                    h.update(block)
                    f.write(block)
        except Exception as exc:  # network errors: retry
            last_error = exc
            continue
        if h.hexdigest() == expected:
            os.replace(part, path)
            (out_dir / f"{name}.CHECKSUM").write_text(f"{expected}  {name}\n")
            return path
        last_error = DownloadError(f"SHA-256 mismatch for {url}")
    part.unlink(missing_ok=True)
    raise last_error


def days(start: date, end: date):
    """UTC days in [start, end)."""
    day = start
    while day < end:
        yield day
        day += timedelta(days=1)


def download_range(session, out_dir: Path, symbol: str, start: date, end: date) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [download_day(session, out_dir, symbol, d) for d in days(start, end)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="first UTC day")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="exclusive UTC day")
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    import requests

    with requests.Session() as session:
        for path in download_range(session, args.out_dir, args.symbol, args.start, args.end):
            print(f"ok {path.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
