# klines_daily_v3 results

Spec `docs/research/klines_daily_v3.json` (sha256 `7f9d6e8d…5ce`), runner `research-klines-daily-v3` at `5880fdc`.
Data: Binance BTCUSDT spot 1d klines 2017-08 to 2026-11-01, every file checksum-verified.
Long/flat; costs 0.12%/side (0.10% fee + 2 bp). Sharpe uses √365 scaling.

**Verdict: FAIL** (Sharpe below buy-and-hold). The test window (2024-01-01 to 2026-11-01) is spent.

## Train (2018–2021) and validation (2022–2023), all 15 configurations

| Config | Train net | Train Sharpe | Val net | Val Sharpe | Val max DD | Val round trips |
|---|---|---|---|---|---|---|
| sma_trend 20 | +366.6% | 1.00 | −18.4% | −0.13 | 48.9% | 45 |
| sma_trend 50 | +887.5% | 1.37 | +18.9% | 0.43 | 50.8% | 18 |
| sma_trend 100 | +509.2% | 1.10 | +43.9% | 0.74 | 29.3% | 11 |
| sma_trend 200 | +120.3% | 0.64 | +93.8% | 1.27 | 18.9% | 3 (below 4, not eligible) |
| momentum 7 | +289.4% | 0.90 | −10.5% | −0.00 | 51.7% | 67 |
| **momentum 30** | +497.5% | 1.12 | **+58.0%** | **0.84** | 42.3% | 27 (selected) |
| momentum 90 | +191.8% | 0.76 | +5.8% | 0.25 | 34.2% | 19 |
| momentum 180 | +225.3% | 0.80 | +16.1% | 0.40 | 34.3% | 8 |
| donchian 20 | +337.0% | 1.03 | +10.8% | 0.32 | 44.4% | 16 |
| donchian 55 | +393.4% | 1.05 | +30.6% | 0.62 | 27.9% | 6 |
| donchian 100 | +577.5% | 1.18 | +14.9% | 0.42 | 18.1% | 4 |
| mean_rev 5% / 1d | +56.5% | 0.56 | −11.1% | −0.20 | 21.4% | 28 |
| mean_rev 5% / 3d | −35.9% | −0.04 | −22.2% | −0.33 | 42.9% | 25 |
| mean_rev 10% / 1d | +45.1% | 0.66 | +8.3% | 0.52 | 4.0% | 5 |
| mean_rev 10% / 3d | −10.1% | −0.01 | −5.6% | −0.17 | 15.4% | 5 |
| Buy-and-hold | +236.2% | 0.79 | −8.7% | 0.19 | 66.9% | 1 |

Gate (val net > 0 and val Sharpe > buy-and-hold): passed.

## Test (run once, 2024-01-01 to 2026-11-01)

| | Net total | CAGR | Sharpe | Max DD | Round trips | Exposure |
|---|---|---|---|---|---|---|
| momentum 30 | +28.5% | 9.2% | 0.43 | 36.6% | 54 | 56% |
| momentum 30 at 0.075% fee (info) | +32.0% | 10.3% | 0.46 | 36.4% | 54 | 56% |
| Buy-and-hold | +83.0% | 23.8% | 0.69 | 53.0% | 1 | 100% |
| Cash | 0% | | | | 0 | 0% |

Checks: total net > 0 ✅, Sharpe > buy-and-hold ❌, max DD < buy-and-hold ✅, round trips ≥ 5 ✅.

## Takeaway

The rule made money after costs out of sample and had a smaller drawdown, but it earned less than holding
and had a worse risk-adjusted return, so it fails the predeclared bar. Validation looked strong mainly
because 2022 was a bear market, where any trend filter avoids losses. Known limit: BTC history is
widely known, so rule choices may carry hindsight. No v3 rule is a profitable strategy on this evidence.
