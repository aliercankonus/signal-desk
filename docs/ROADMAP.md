# Roadmap

Markets are pluggable: each venue (crypto exchange or stock broker) is registered in `src/signal_desk/venues/` and produces the same normalized market events. Crypto (Binance BTCUSDT) goes first because its data is free and it trades 24/7, which gives the fastest answer to "is there an edge?".

Each phase has an exit gate. Do not start the next phase until the gate is met and the owner agrees.

## Phase 0 — Clean start (done 2026-11-16)
- Fresh repository with the working core from `signal-desk-prediction` (collector, order book, features, replay, research, paper fill model).
- CI builds and tests the image on every PR, and publishes `ghcr.io/aliercankonus/signal-desk:main` on merge.
- VPS: firewall to SSH/HTTP/HTTPS only; stop the old Hermes agent and old project containers.
- **Gate:** API and collector from this repo run on the VPS via the Hostinger Docker manager; health is ok.

Status (2026-11-16):
- [x] Repo created, CI green, image published to GHCR
- [x] GHCR package `signal-desk` is public (anonymous pull works)
- [x] VPS reinstalled as "Ubuntu 24.04 with Docker and Traefik"; no old Hermes/project containers remain
- [x] Old Hermes/ChatGPT credentials: owner reviewed and chose to leave them as is (VPS reinstalled, token files gone, no SSH keys on the VPS or account)
- [x] Firewall: inbound TCP 22, 80, 443 only (Hostinger firewall, synced and active)
- [x] Deployed with `vps_docker_create`; api healthy (127.0.0.1:8080 only), collector running
- [x] Collector stream buffer raised 256 -> 4096 (#4) after early overflow resyncs; after redeploy: 0 gaps, peak queue 1012; the remaining reconnects were the trade-lull bug fixed in #9

**Gate met** (API and collector running, health ok). Owner approved moving to Phase 1.

## Phase 1 — Data collection (2–4 weeks, current — started 2026-11-16)
- Collector runs in continuous mode; daily check of status, gaps, reconnects and disk use.
- Add raw-file retention/compression before the 30 GB raw quota is reached.
- **Gate:** at least 14 days of gap-free sessions with a clean raw audit.

Status (2026-11-16):
- [x] Collector reconnect loop fixed (#9). It reconnected about every 2 min ("Timed out waiting for market data") because a 3 s lull in trades triggered a full resync even while depth was fresh. The session now only resyncs after a 60 s trade outage, and stale feeds report their real reason. `stale_trades_ms` (3 s) still governs feature validity. Redeployed 11:29 UTC; 0 reconnects in the first 4 min (the old build had ~2 by then).
- [x] Daily read-only VPS check scheduled at 07:23 UTC in a dedicated cloud session.
- [x] Allow rules for the Hostinger tools in `.claude/settings.json`, under both tool names (`mcp__hostinger__*` and the connector-ID form used in cloud sessions) (#3, #7, #8). The `vps-status` agent now works in cloud sessions.
- [ ] Owner: set the Hostinger connector's Execute and Multi-execute tools to "Always allow" (claude.ai, Connectors). Until then the connector's own policy asks for approval on every `execute` call, and repo allow rules can't override it.
- [ ] Confirm overnight stability from the first daily check: reconnect_count near zero, gap_count 0.
- [ ] Hostinger VM metrics (CPU/RAM/disk) return empty so far, so disk use can't be watched yet. Recheck; if still empty, add a disk check another way.
- [x] Raw compression: the collector gzips each closed UTC day in place (`.jsonl` -> `.jsonl.gz`, verified by SHA-256 before the original is removed), every 15 min. About 6x on synthetic events, so ~5 GB/day of raw data becomes ~0.8 GB/day and the 30 GB quota lasts about 5 weeks instead of about 6 days. No automatic deletion; the quota HALT stays the backstop. Readers glob `*.jsonl`, so `gzip -dk` a day into a separate directory before replay/research.
- [x] Raw retention: compressed raw days are kept 75 days (S1's 60-day window + 15 days margin; the same window serves periodic re-validation in production), then deleted. Days listed in `retention.raw_pinned_days` are never deleted; the collector refuses to start with a keep window under 60 days. Raw quota raised 30 -> 60 GB.
- [ ] Confirm the real compression ratio after the first day closes (look for a `COMPACTED` line in the collector log, 2026-11-17 after 00:10 UTC). If the real ratio is well below ~6x, switch the codec to zstd (measured on synthetic events: zstd-19 8.2x vs gzip-6 6.2x; zstd-3 matches gzip at ~6x the speed). Needs the `zstandard` dependency and `.zst` support in readers.

## Phase 2 — Research: does S1 beat costs?
- Run the predeclared S1 experiment (`docs/research/s1_experiment_v1.json`) offline on collected data.
- Data needed: S1 uses a fixed 60-day window (36 train / 12 validation / 12 final test), so the full run can't happen before about 60 days of clean collection (~2027-01-15 if collection stays clean from 2026-11-16). The Phase 1 14-day gate proves data quality only.
- Storage work before research:
  - Teach replay/audit/research to read compressed day files directly (`.jsonl.gz`); until then, `gzip -dk` a day into a separate directory.
  - Convert the research dataset to Parquet (columnar; the config already plans `feature_format: parquet`) for fast queries. About 7.7x vs raw JSON on synthetic events.
  - Decide whether raw records should keep only the original Binance message and map on replay: ~830 B/event today vs about a third of that, and ~16x compressed with zstd-19 (~0.3 GB/day). This changes the raw schema, the audit and hash verification, so it needs its own design review. Don't change it mid-collection.
- Add a cost model: 0.10% taker per side (0.20% round trip) unless verified lower fees apply, plus spread/slippage.
- Evaluate once on held-out data after validation-only selection.
- **Gate:** positive expected return after all costs on out-of-sample data, with enough trades to matter. If not met: stop or change the hypothesis. No tuning on the test set.

## Phase 3 — Live paper trading (several weeks)
- Deterministic paper trader using live data, the conservative fill model and a simple ledger; daily P&L report.
- Keep the same 75-day raw window for periodic re-validation of the edge (re-run the 36/12/12 check on the latest 60 days, e.g. monthly).
- Store per-trade evidence (decision record, book summary at decision time, fill) in the ledger indefinitely. Pin raw days needed for incident review in `retention.raw_pinned_days`.
- Off-VPS archive for raw days older than 75 days that must be kept (S3-compatible object storage, e.g. Backblaze B2 at about $6/TB/month), so the VPS disk stays bounded.
- **Gate:** paper results consistent with Phase 2 for several weeks.

## Phase 4 — Small real-money pilot (only with explicit owner approval)
- Separate design review against `.claude/skills/trading-safety`. Hard per-trade and daily limits, kill switch, no AI in the order path.

## Phase 5 — Stocks (after Phase 2 has a result on crypto)
- Choose a broker with a free paper-trading API available in the owner's country (candidates: Alpaca, Interactive Brokers). Confirm data licensing and what real-time data is free.
- Implement a stock venue: collector for its data feed, mapping to `schemas/market_event.schema.json`, market-hours handling (closed markets are normal, not a failure), register it in `venues/`, tests.
- Expect a different strategy horizon: daily or multi-day signals are more realistic than minute-level order flow for a small account. US day trading needs $25k+ (pattern day trader rule).
- Same gates as crypto: Phase 1 data → Phase 2 research gate → Phase 3 paper → Phase 4 pilot.
