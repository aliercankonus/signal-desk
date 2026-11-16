# Roadmap

Each phase has an exit gate. Do not start the next phase until the gate is met and the owner agrees.

## Phase 0 — Clean start (current)
- Fresh repository with the working core from `signal-desk-prediction` (collector, order book, features, replay, research, paper fill model).
- CI builds and tests the image on every PR, and publishes `ghcr.io/aliercankonus/signal-desk:main` on merge.
- VPS: firewall to SSH/HTTP/HTTPS only; stop the old Hermes agent and old project containers.
- **Gate:** API and collector from this repo run on the VPS via the Hostinger Docker manager; health is ok.

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
