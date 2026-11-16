---
name: research-gate
description: Rules for evaluating a trading strategy on collected BTCUSDT data so results are honest — predeclared spec, chronological splits, full trading costs, one-time test evaluation, standard report. Use for any backtest, signal evaluation, or claim that a strategy is profitable.
---

# Research gate

The goal of Phase 2 is a yes/no answer: does the strategy beat costs out of sample? Optimising until something looks good is the main failure mode.

## Before looking at results
- Write or confirm the spec first (`docs/research/s1_experiment_v1.json` for S1): features, horizon, decision rule, data window, splits. Commit it before running.
- Chronological split only: train → validation → test, with a purge/embargo of at least the label horizon between them. No shuffling.
- Use only causal data: features at time t may use information applied at or before t.

## Costs (always included)
- Fees: 0.10% per side (0.20% round trip) unless the owner confirms a lower verified fee tier.
- Entry/exit at the side of the book you'd actually trade (ask to buy, bid to sell), not the midpoint.
- Add slippage for size beyond top-of-book quantity. Use `paper/fill_model.py` conservative rules for paper fills.

## Evaluation
- Select parameters on validation only. Evaluate the test set **once**. If you change anything after seeing test results, the test set is spent; say so.
- Report the number of trades. Fewer than a few hundred trades is not evidence.
- Compare against baselines: no-trade, always-long over the same windows, and the no-change predictor.

## Report format
1. Data window and sessions used (and gaps excluded)
2. Spec version and commit
3. Validation and test results: trades, hit rate, average return per trade after costs, total return after costs, max drawdown
4. Baseline comparison
5. Verdict: pass / fail against the Phase 2 gate in `docs/ROADMAP.md`, plus caveats

Never describe a result as profitable without after-cost, out-of-sample numbers.
