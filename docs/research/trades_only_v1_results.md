# trades_only_v1 results

Spec `docs/research/trades_only_v1.json` (sha256 `75219c3c…0422955`), runner `research-trades-only-v1` at `e451d00`.
Data: Binance BTCUSDT spot daily trades, 2026-08-01 to 2026-09-30, every file checksum-verified.
Returns are per trade in basis points; costs are 0.10%/side fee plus 2 bp/side price adjustment (about 24 bp round trip).

**Verdict: FAIL.** The test set (2026-09-18 to 2026-09-30) is spent.

## Validation (2026-09-06 to 2026-09-18), all 12 configurations

| Candidate | Threshold | Horizon | Trades | Gross bp | Net bp |
|---|---|---|---|---|---|
| flow_30s | 0.2 | 60s | 7610 | +0.12 | −23.85 |
| flow_30s | 0.2 | 300s | 2721 | +0.06 | −23.92 |
| flow_30s | 0.4 | 60s | 6464 | +0.10 | −23.88 |
| flow_30s | 0.4 | 300s | 2528 | −0.01 | −23.98 |
| flow_30s | 0.6 | 60s | 5066 | +0.14 | −23.84 |
| flow_30s | 0.6 | 300s | 2242 | −0.20 | −24.17 |
| momentum_60s | 0.0005 | 60s | 1851 | +0.30 | −23.67 |
| momentum_60s | 0.0005 | 300s | 1157 | −0.32 | −24.29 |
| momentum_60s | 0.001 | 60s | 478 | +0.74 | −23.23 |
| **momentum_60s** | **0.001** | **300s** | **349** | **+1.18** | **−22.80** (selected) |
| momentum_60s | 0.002 | 60s | 77 | +0.05 | −23.92 |
| momentum_60s | 0.002 | 300s | 58 | +6.50 | −17.48 (below 300 trades) |

## Test (run once)

| | Trades | Hit rate | Gross bp | Net bp | Total | Max DD | t |
|---|---|---|---|---|---|---|---|
| Selected (momentum_60s > 0.001, 300s) | 436 | 7.3% | +1.66 | −22.31 | −62.3% | 62.3% | −26.8 |
| Always-long 300s | 3139 | 2.4% | +0.23 | −23.74 | −99.9% | 99.9% | −120.6 |
| Buy-and-hold | 1 | | +951.9 | +925.6 | +9.3% | 0% | |
| No-trade / no-change | 0 | | | 0 | 0% | | |

Checks: trades ≥ 300 ✅, mean net > 0 ❌, total net > 0 ❌, beats always-long ✅.

## Takeaway

The signals pick slightly better moments than random (about +1–2 bp gross), but a round trip costs about 24 bp.
Minute-horizon taker trading at 0.10%/side does not work here; the price approximation does not change that
(fees alone are 20 bp). Next hypothesis: `trades_only_v2.json` (longer holds, rule-based exits).
