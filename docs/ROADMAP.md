# Roadmap

Markets are pluggable: each venue (crypto exchange or stock broker) is registered in `src/signal_desk/venues/` and produces the same normalized market events. Crypto (Binance BTCUSDT) goes first because its data is free and it trades 24/7, which gives the fastest answer to "is there an edge?".

Each phase has an exit gate. Do not start the next phase until the gate is met and the owner agrees.

## Phase 0 — Clean start (current)
- Fresh repository with the working core from `signal-desk-prediction` (collector, order book, features, replay, research, paper fill model).
- CI builds and tests the image on every PR, and publishes `ghcr.io/aliercankonus/signal-desk:main` on merge.
- VPS: firewall to SSH/HTTP/HTTPS only; stop the old Hermes agent and old project containers.
- **Gate:** API and collector from this repo run on the VPS via the Hostinger Docker manager; health is ok.

Status (2026-11-16):
- [x] Repo created, CI green, image published to GHCR
- [x] GHCR package `signal-desk` is public (anonymous pull works)
- [x] VPS reinstalled as "Ubuntu 24.04 with Docker and Traefik"; no old Hermes/project containers remain
- [ ] Old Hermes/ChatGPT credentials revoked (owner to confirm)
- [x] Firewall: inbound TCP 22, 80, 443 only (Hostinger firewall, synced and active)
- [x] Deployed with `vps_docker_create`; api healthy (127.0.0.1:8080 only), collector running
- [x] Collector stream buffer raised 256 -> 4096 (#4) after early overflow resyncs; after redeploy: 0 gaps, peak queue 1012, one stale-feed reconnect in the first 5 min

**Gate met** (API and collector running, health ok). Phase 1 starts once the credentials item is confirmed.

## Phase 1 — Data collection (2–4 weeks)
- Collector runs in continuous mode; daily check of status, gaps, reconnects and disk use.
- Add raw-file retention/compression before the 30 GB raw quota is reached.
- **Gate:** at least 14 days of gap-free sessions with a clean raw audit.

## Phase 2 — Research: does S1 beat costs?
- Run the predeclared S1 experiment (`docs/research/s1_experiment_v1.json`) offline on collected data.
- Add a cost model: 0.10% taker per side (0.20% round trip) unless verified lower fees apply, plus spread/slippage.
- Evaluate once on held-out data after validation-only selection.
- **Gate:** positive expected return after all costs on out-of-sample data, with enough trades to matter. If not met: stop or change the hypothesis. No tuning on the test set.

## Phase 3 — Live paper trading (several weeks)
- Deterministic paper trader using live data, the conservative fill model and a simple ledger; daily P&L report.
- **Gate:** paper results consistent with Phase 2 for several weeks.

## Phase 4 — Small real-money pilot (only with explicit owner approval)
- Separate design review against `.claude/skills/trading-safety`. Hard per-trade and daily limits, kill switch, no AI in the order path.

## Phase 5 — Stocks (after Phase 2 has a result on crypto)
- Choose a broker with a free paper-trading API available in the owner's country (candidates: Alpaca, Interactive Brokers). Confirm data licensing and what real-time data is free.
- Implement a stock venue: collector for its data feed, mapping to `schemas/market_event.schema.json`, market-hours handling (closed markets are normal, not a failure), register it in `venues/`, tests.
- Expect a different strategy horizon: daily or multi-day signals are more realistic than minute-level order flow for a small account. US day trading needs $25k+ (pattern day trader rule).
- Same gates as crypto: Phase 1 data → Phase 2 research gate → Phase 3 paper → Phase 4 pilot.
