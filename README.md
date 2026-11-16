# signal-desk

Paper-only research system for Binance BTCUSDT spot: captures the public order book and trades, builds causal features, and evaluates whether a short-horizon strategy can beat trading costs.

**Status:** Phase 0 (clean start). No strategy has been validated; nothing here trades real money. See [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Run locally
```bash
pip install -r requirements.txt
PYTHONPATH=src python -m pytest -q
```

## Deploy
CI publishes `ghcr.io/aliercankonus/signal-desk:main` on every merge to `main`. `docker-compose.yml` uses that image and needs no build context, so it can be deployed directly through the Hostinger Docker manager.

## Origin
Core code carried over from `aliercankonus/signal-desk-prediction` (collector, order book, features, replay, research, paper fill model). Its operator/agent-bridge tooling was intentionally left behind.
