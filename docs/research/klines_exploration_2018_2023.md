# Klines exploration, 2018-2023 (not a gated test)

After v3 failed against buy-and-hold, the owner ruled out holding BTC. This
exploration looked for the best daily/hourly long-or-cash rule judged against
cash. It is **exploration, not evidence**: 64 variants were tried and the
numbers below were all seen before any rule was fixed. Its only output is the
single rule predeclared in [`klines_hourly_v4.json`](klines_hourly_v4.json).

- Data: Binance spot 1h klines, 2018-01 to 2023-12, sha256-verified. Nothing from 2024 onward was used.
- Coins: BTC, ETH, XRP, BCH, LTC, EOS, BNB, XLM, TRX, ADA (USDT pairs).
- Split: train 2018-2021, validation 2022-2023. Each starts flat.
- Costs: 0.12% per side on every change of position.
- Clock: position during a bar is decided from closes up to the previous bar; close-to-close returns. Missing hours (163 for BTC) were forward-filled.
- Sharpe: hourly rules use hourly returns x sqrt(8760); daily rules use daily returns x sqrt(365).

## Best variant per family (validation 2022-2023, net of costs)

| Family | Best variant | Train net | Val net | Val Sharpe | Val max DD | Round trips / year |
|---|---|---|---|---|---|---|
| Slow BTC trend, hourly bars | SMA 2400h, 2% band | +791% | +66% | 1.04 | 25% | 3 |
| Same, volatility-sized | SMA 1200h, 2% band, 30% vol target | +280% | +44% | 0.97 | 31% | 4 |
| Slow BTC trend, daily bars | SMA 200d | +238% | +94% | 1.27 | 19% | 1 |
| BTC momentum, daily | 30d (the v3 rule) | +758% | +58% | 0.84 | 42% | 13 |
| BTC breakout, hourly | Donchian 1200h | +758% | +12% | 0.35 | 40% | 3 |
| 10-coin basket, daily | 30d momentum, equal weight, BTC filter | +1005% | +18% | 0.46 | 23% | 8 |
| Top-k coin rotation, daily | top 2 by 30d momentum, BTC filter | +3040% | +18% | 0.40 | 41% | 28 |
| Fast BTC momentum, hourly | 24h to 336h | -93% to +334% | -31% to -91% | -0.4 to -3.0 | 59-91% | 117-464 |
| Buy the dip, hourly | -5%/-10% in 24h, hold 24h/72h | -64% to +137% | -7% to -31% | about 0 | 18-55% | 3-28 |
| Buy-and-hold BTC (reference) | | +241% | -10% | 0.18 | 67% | |
| Buy-and-hold basket (reference) | | +352% | -31% | 0.00 | 65% | |

## Reading

- Fast hourly rules lose to costs; the more a rule trades, the worse it does.
- Coin rotation looked spectacular in 2018-2021 and lost money in most variants in 2022-2023.
- The slow BTC trend filter is the only idea positive in both periods across neighbouring settings. SMA 2400h with a 2% band was chosen over SMA 200d because it trades a little more often (SMA 200d made about 2 round trips in validation).

## Caveats

- 64 variants: the top Sharpe is partly luck.
- The best variants made only 2-6 round trips in the two validation years.
- The coin list is the top 10 from about the end of 2019, inside the training period, so the multi-coin training numbers are biased upward.
- BTC daily data for 2024-2026 was already used by v3, so no fully clean BTC backtest remains; v4 adds a forward phase for that reason.
