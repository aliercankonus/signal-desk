from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from pathlib import Path

from signal_desk.api.overview import build_overview
from signal_desk.config.loader import ConfigError, validate_config

OVERVIEW_PAGE = Path(__file__).with_name("static") / "overview.html"


def build_status(config_path: str) -> tuple[int, dict]:
    try:
        config = validate_config(config_path)
    except Exception as exc:  # detail goes to the server log, never to clients
        print(f"health_server config blocked: {exc}", file=sys.stderr, flush=True)
        return 503, {"status": "blocked", "error": "config_invalid", "ready_for_enter": False}
    return 200, {
        "status": "ok",
        "ready_for_enter": False,
        "mode": config["project"]["mode"],
        "execution_enabled": config["project"]["execution_enabled"],
        "venue": config["venue"]["name"],
        "asset_class": config["venue"]["asset_class"],
        "symbol": config["venue"]["symbol"],
        "config_version": config["project"]["config_version"],
        "notes": ["paper-only skeleton", "real orders disabled"],
    }


class HealthHandler(BaseHTTPRequestHandler):
    config_path = "config/config.yaml"
    cached_status: tuple[int, dict] | None = None

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self._send_raw(code, raw, "application/json")

    def _send_raw(self, code: int, raw: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send_raw(200, OVERVIEW_PAGE.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._api_status()
            return
        if path not in ("/health", "/ready"):
            self._send(404, {"error": "not_found"})
            return
        if HealthHandler.cached_status is None:
            HealthHandler.cached_status = build_status(self.config_path)
        code, status = HealthHandler.cached_status
        self._send(code, status)

    def _api_status(self) -> None:
        try:
            config = validate_config(self.config_path)
            payload = build_overview(config)
        except Exception as exc:  # detail goes to the server log, never to clients
            print(f"health_server overview failed: {exc}", file=sys.stderr, flush=True)
            self._send(503, {"error": "overview_unavailable"})
            return
        self._send(200, payload)

    def log_message(self, fmt: str, *args) -> None:  # reduce noisy default logs
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()
    HealthHandler.config_path = args.config
    HealthHandler.cached_status = build_status(args.config)
    server = ThreadingHTTPServer((args.host, args.port), HealthHandler)
    print(f"health_server listening on {args.host}:{args.port} paper_only=true")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
