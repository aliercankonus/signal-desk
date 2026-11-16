# Klines hourly v4 — backcheck result

Spec: [`klines_hourly_v4.json`](klines_hourly_v4.json) (sha256 `c4da5109…a43e86`), runner `research-klines-hourly-v4` at commit `81164d5`.
Rule: BTCUSDT long while close > 2400h SMA x 1.02, cash when close < SMA x 0.98. One configuration, no search.
Run once on 2024-01-01..2026-11-01 (24,840 hourly bars, 0 missing), cost 0.12% per side.

| | Net return | CAGR | Sharpe | Max drawdown | Round trips | Time in market |
|---|---|---|---|---|---|---|
| v4 rule | +114.9% | 31.0% | 0.97 | 26.3% | 10 | 58% |
| Cash (benchmark) | 0% | | | | | |
| Buy-and-hold (information only) | +83.0% | 23.8% | 0.69 | 53.7% | 1 | 100% |

Checks: net return > 0 yes; Sharpe > 0.5 yes; max drawdown < 35% yes; round trips >= 5 yes. **Backcheck verdict: pass.**

## What this does and does not show

- The period was partly known before the rule was fixed (v3 had shown BTC rose 83% and that 30-day momentum made money in it). The rule itself had never been run on it, but this is weaker than a clean out-of-sample test.
- 10 round trips is a small sample; a few trades carry the result.
- The rule was the best of 64 explored variants on 2018-2023.
- This is **not a profitability claim**. Per the spec it makes the rule eligible for the forward phase: 2026-11-17 to 2027-11-17, same runner, paper only, verdict on the full segment (net return > 0, max drawdown < 35%).

The backcheck is spent; the runner refuses a second run.

## Forward reads

```
python -m signal_desk.research.klines_v4 download --out DATA --first-month 2026-07 --last-month YYYY-MM [--extra-day YYYY-MM-DD ...]
python -m signal_desk.research.klines_v4 forward --spec docs/research/klines_hourly_v4.json --data DATA --out forward_YYYYMMDD.json
```

Interim reads report `interim_no_verdict` and change nothing in the spec.
