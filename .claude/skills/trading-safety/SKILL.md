---
name: trading-safety
description: Safety rules for anything that could move from paper to real orders — limits, kill switch, key handling, keeping AI out of the order path. Use when touching execution, order intents, exchange API keys, or planning Phase 3/4.
---

# Trading safety

Adapted from ECC's `llm-trading-agent-security` and `prediction-market-risk-review` (MIT), cut down to what applies to Binance spot.

## Hard rules
- `execution_enabled=false` stays enforced until the owner explicitly approves Phase 4 in writing.
- No LLM in the order path. Agents may write and review code; deterministic, tested code makes trading decisions.
- No exchange API keys in the repo, images, logs, issues or chat. Keys live only on the VPS as environment variables or Docker secrets, with trade-only scope (no withdrawals) and an IP allowlist.

## Required before any real order (Phase 4 design review)
- Per-trade and daily notional limits enforced in code, independent of strategy output.
- Kill switch: stop on N consecutive losses, daily loss above a threshold, stale data, order-book gap, or any unexpected state.
- Every decision and order logged (including rejected ones), with the config version.
- Dedicated sub-account holding only the pilot funds.
- Dry-run mode that produces the exact orders without sending them, compared against paper results.
- Owner approval for enabling, and a documented way to disable instantly.

## Review output
Return: scope reviewed, pass/warn/fail per item above, what is blocked, required fixes, safe next step.
