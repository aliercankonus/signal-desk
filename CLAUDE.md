# signal-desk

Market-data capture and strategy research. Markets are pluggable venues; Binance BTCUSDT spot (crypto) is the only one implemented, stocks are planned. **Paper-only**: `execution_enabled=false` is enforced in `config/loader.py`; never add real order placement without an explicit, separate decision from the owner.

## Layout
- `src/signal_desk/venues/` — venue registry (crypto/stock); add a market here first
- `src/signal_desk/collector/` — WebSocket + REST order-book capture, gap detection, continuous mode
- `src/signal_desk/features/` — causal feature pipeline (capture and replay share it)
- `src/signal_desk/replay/`, `research/` — ordered session replay, S1 baseline runner
- `src/signal_desk/paper/fill_model.py`, `decision/` — paper fill rules, decision engine
- `schemas/` — JSON schemas for every persisted record; `config/` — example config + schema
- `docs/ROADMAP.md` — current phase and its exit gate. Read it before starting work.

## Commands
- Tests: `PYTHONPATH=src python -m pytest -q` (about 1 min). Single area: add a path, e.g. `tests/collector`.
- Config check: `PYTHONPATH=src python -m signal_desk.tools.validate_all --config config/config.example.yaml`

## Working rules
- Keep changes small and on a branch; open a PR; CI must pass before merge.
- No new process ceremony (authorization files, digests, handoff docs). Plain code, tests, PRs.
- Paper-only is a hard rule. No AI in any order path; trading logic is deterministic code with hard limits.
- Follow `.claude/skills/token-efficiency` on every task.
- Routine output-heavy work goes to Haiku subagents: `vps-status` for VPS checks, `test-runner` for test runs.
- Coding style: `ponytail` (smallest complete change). Before saying "done": `verification-before-completion`. End of session: `handoff`. Splitting work into issues: `to-tickets`.
- The VPS is managed through the Hostinger API; see `.claude/skills/vps-ops`.
