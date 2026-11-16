# v5 result: v4 rule on a 20-coin basket (FAIL)

Spec `klines_hourly_v5.json` (sha256 `331b6127…`), runner `research-klines-hourly-v5`, commit `d9872ac`.
This was the only run (marker file). Window: 2024-01-01 to 2026-11-01, 24,840 hours. Cost: 0.12% per side.

| Portfolio (equal 1/20 sleeves) | v4 rule | Cash | Equal-weight buy-and-hold (info only) |
|---|---|---|---|
| Net return | +2.4% | 0% | -18.7% |
| CAGR | +0.8% | 0% | -7.1% |
| Sharpe | 0.24 | – | 0.21 |
| Max drawdown | 49.5% | 0% | 68.2% |
| Round trips | 427 | 0 | – |
| Avg exposure | 42% | 0% | 100% |

Checks:
- net > 0: pass
- Sharpe > 0.5: **fail**
- max drawdown < 35%: **fail**
- round trips ≥ 40: pass

**Verdict: fail.** 9 of 20 coins finished net positive.

Best: BNB +123%, DOGE +106%, TRX +83%, XRP +74%.
Worst: DYDX -83%, FIL -81%, GALA -70%, DOT -63%.
MATIC's data ended 2024-09 and FTM's 2025-01 (both delisted); each then sat in cash.

## Reading
- Across 20 coins, the rule only just beat cash after costs, with a drawdown too deep to trade.
- On alts that kept falling, it cut losses compared with holding (-19% → +2%), but trend whipsaws ate most of the gain.
- BTC's +115% in the v4 backcheck is likely specific to BTC and that period, not general evidence that this trend rule works.
- Per the spec, the rule is not tuned on this data; this altcoin window is now spent.
- v4's BTC forward phase continues unchanged, and it remains the only live check.
