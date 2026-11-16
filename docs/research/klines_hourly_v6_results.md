# v6 result: BTC+ETH 4-length SMA-band ensemble, backcheck (PASS, contaminated window)

Spec `klines_hourly_v6.json` (sha256 `0f4abd98…`), runner `research-klines-hourly-v6`, commit `5bb52f5`.
This was the only backcheck run (marker file). Window: 2024-01-01 to 2026-11-01, 24,840 hours, no missing hours. Cost: 0.12% per side.

| Portfolio (1/2 BTC, 1/2 ETH) | v6 ensemble | Cash (benchmark) | v4 rule 50/50 (info only) | Buy-and-hold 50/50 (info only) |
|---|---|---|---|---|
| Net return | +74.4% | 0% | +83.3% | +40.8% |
| CAGR | +21.7% | 0% | +23.8% | +12.8% |
| Sharpe | 0.79 | – | 0.80 | 0.49 |
| Max drawdown | 30.4% | 0% | 34.6% | 59.3% |
| Round trips (sub-signal entries) | 168 | 0 | – | – |
| Avg exposure | 52% | 0% | – | 100% |

| Coin | v6 net | Max DD | Round trips | Exposure | v4 rule (info) | Buy-and-hold (info) |
|---|---|---|---|---|---|---|
| BTC | +82.0% | 24.4% | 75 | 56% | +114.9% | +83.0% |
| ETH | +66.8% | 38.8% | 93 | 48% | +51.8% | -1.4% |

Checks:
- net > 0: pass
- Sharpe > 0.5: pass
- max drawdown < 35%: pass

**Verdict: pass (backcheck only).** Exposure at the last signal: BTC 0.50, ETH 0.25.

## Reading
- The rule beats cash after full costs, with a drawdown inside the limit.
- It did not beat the single-length v4 rule on the same 50/50 split: return was a little lower and Sharpe about the same. The ensemble's only gain was a shallower drawdown (30% vs 35%).
- ETH carries the case: it was flat on buy-and-hold but +67% under the rule. BTC trailed both v4 and holding.
- This window is contaminated. v4 and v5 were already studied on it, and the lengths were picked with that knowledge. The pass shows the rule is consistent with that period, not that it has an edge.
- The 168 round trips are sub-signal entries; the number of independent trend calls is far smaller. This is still well short of a few hundred trades, so it is not evidence.
- The real test is the forward segment, 2026-11-17 to 2027-11-17, with no changes to the rule. Until that finishes, there is no claim of profitability.
