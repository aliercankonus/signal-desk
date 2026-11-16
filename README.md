# signal-desk

Paper-only research system that captures market data, builds causal features, and evaluates whether a strategy can beat trading costs. Markets are pluggable venues: Binance BTCUSDT spot (crypto) is implemented; stocks are planned (roadmap Phase 5).

**Status:** Phase 0 (clean start). No strategy has been validated; nothing here trades real money. See [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Run locally
```bash
pip install -r requirements.txt
PYTHONPATH=src python -m pytest -q
```

## Deploy
CI publishes `ghcr.io/aliercankonus/signal-desk:main` on every merge to `main`. `docker-compose.yml` uses that image and needs no build context, so it can be deployed directly through the Hostinger Docker manager.

## Operator overview
The `api` service serves read-only pages: the overview at `/` (collector state and health, raw days, disk, alarms, gate progress, backed by `GET /api/status`) and sessions at `/sessions` (one cell per UTC raw day with files, compression, expiry and a clean / reconnects / gap verdict, backed by `GET /api/sessions`). Verdicts come from the collector's state history, `/data/status/collector_events.jsonl` (one JSON line per STARTED, RESYNCING, HALT, STOPPED, HEARTBEAT, COMPACTED or RETENTION event; best-effort, rotated once at 5 MB).

On the VPS the page is also served over HTTPS by Traefik behind basic auth, at the host set in the project environment (`UI_PUBLIC=true`, `UI_HOST`, `UI_BASICAUTH` as a single-quoted htpasswd bcrypt line). Without those variables the route is off. Only the two pages and their `/api/` data are routed; port 8080 stays bound to `127.0.0.1`. Locally, or without the route, use an SSH tunnel and browse to http://localhost:8080:
```bash
ssh -L 8080:127.0.0.1:8080 <user>@<vps-host>
```

## Origin
Core code carried over from `aliercankonus/signal-desk-prediction` (collector, order book, features, replay, research, paper fill model). Its operator/agent-bridge tooling was intentionally left behind.
