# trades_only_v2 results

Spec `docs/research/trades_only_v2.json` (sha256 `54efe258…2ca13ea9`), runner `research-trades-only-v2` at `cb5bbdb`.
Data: Binance BTCUSDT spot daily trades, train 2026-09-01 to 2026-10-01, validation 2026-10-01 to 2026-10-24, every file checksum-verified.
Returns are per trade in basis points; costs are 0.10%/side fee plus 2 bp/side price adjustment (about 24 bp round trip).

**Verdict: FAIL at validation.** The selected configuration loses money after costs on validation, so the test
was not run. The test window (2026-10-24 to 2026-11-15) is **unused**; keep it unused rather than reopening v2.

## Validation, all 24 configurations

| Candidate | Threshold | Exit | Trades | Gross bp | Net bp | Total |
|---|---|---|---|---|---|---|
| mom15 | 0.003 | tp_sl | 142 | −3.6 | −27.6 | −32.5% |
| mom15 | 0.003 | signal_reversal | 168 | −2.2 | −26.1 | −35.6% |
| mom15 | 0.003 | trailing_stop | 130 | −2.8 | −26.8 | −29.5% |
| mom15 | 0.003 | fixed_4h | 64 | −9.2 | −33.2 | −19.3% |
| mom15 | 0.006 | tp_sl | 34 | +1.9 | −22.1 | −7.3% |
| mom15 | 0.006 | signal_reversal | 26 | +5.9 | −18.1 | −4.6% |
| mom15 | 0.006 | trailing_stop | 28 | +9.4 | −14.5 | −4.0% |
| mom15 | 0.006 | fixed_4h | 19 | +5.9 | −18.1 | −3.5% |
| mom60 | 0.005 | tp_sl | 93 | −4.9 | −28.8 | −23.6% |
| mom60 | 0.005 | signal_reversal | 68 | −2.2 | −26.2 | −16.4% |
| mom60 | 0.005 | trailing_stop | 82 | −0.4 | −24.4 | −18.2% |
| mom60 | 0.005 | fixed_4h | 46 | −13.5 | −37.4 | −16.0% |
| mom60 | 0.01 | tp_sl | 20 | +11.0 | −13.0 | −2.6% |
| mom60 | 0.01 | signal_reversal | 9 | +32.2 | +8.2 | +0.7% |
| mom60 | 0.01 | trailing_stop | 16 | +13.5 | −10.5 | −1.7% |
| mom60 | 0.01 | fixed_4h | 9 | +25.5 | +1.5 | +0.1% |
| flow15 | 0.1 | tp_sl | 227 | −2.8 | −26.8 | −45.7% |
| flow15 | 0.1 | signal_reversal | 725 | −0.8 | −24.8 | −83.5% |
| flow15 | 0.1 | trailing_stop | 219 | −1.6 | −25.6 | −43.1% |
| flow15 | 0.1 | fixed_4h | 129 | −2.7 | −26.7 | −29.3% |
| flow15 | 0.2 | tp_sl | 199 | −0.8 | −24.8 | −39.1% |
| flow15 | 0.2 | signal_reversal | 527 | −0.4 | −24.3 | −72.3% |
| flow15 | 0.2 | trailing_stop | 181 | −0.2 | −24.2 | −35.6% |
| **flow15** | **0.2** | **fixed_4h** | **119** | **−0.0** | **−24.0** | **−25.1%** (selected, ≥100 trades) |

Selected: hit rate 32%, t = −4.24, max drawdown 25.9%. On train the same configuration had 154 trades at −22.0 bp net.
The only positive rows have 9 trades each (t ≈ 0.3 and 0.04): noise, and below the 100-trade minimum.
Baselines on validation: buy-and-hold about −300 bp; always-long with the same exits about −24 to −26 bp per trade.

## Takeaway

Holding for 15 minutes to 4 hours instead of 1–5 minutes did not create gross edge: every configuration with
enough trades is at roughly 0 bp gross, so net is just the cost. Intraday taker strategies on these features are
not worth more versions. Next hypothesis: daily-bar trend and mean-reversion rules over years of data
(`klines_daily_v3.json`), where a single trade can move several percent and the 24 bp cost matters much less.
