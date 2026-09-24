# MES strategy farm: first offline screen

The first farm stage is a read-only, reproducible directional event study. It does not create demo accounts or place orders. It reuses v2's normalized MES bars, emits closed-bar signals from pure indicator rules, and measures the direction of the move from the **next bar open** to fixed 15, 30, and 60-minute horizons on 5m input. It is a screen for candidates to test with the simulated broker's 1m fill feed, not a profitability claim.

Run it with a Supabase CSV export or the canonical v2 SQLite feed:

```bash
python -m tvbot_v2.replay.strategy_farm --csv /private/path/tv_datafeed_5m_rows.csv --timeframe 5m --output runs/strategy-farm
python -m tvbot_v2.replay.strategy_farm --db feed/tradingview.sqlite3 --timeframe 5m --output runs/strategy-farm
```

Each run creates a new, never-overwritten directory with `config.json` (including source SHA-256), `quality.json`, `summary.json`, `outcomes.jsonl`, and `report.md`. The Supabase export's `ts` is a receipt time; v2 infers the preceding bar only when receipt is within two minutes of its close. Conflicting OHLCV for the same bar is excluded. Labels crossing a missing 5m bar or a chronological 70/15/15 split boundary are excluded. A regime is the latest observed **as of the feed timestamp**, with freshness shown in the report; it is not inferred from wall-clock time alone.

## Version 1 rule definitions

| Candidate | Closed-bar rule |
|---|---|
| `macd_cross` | MACD(12,26) crosses its EMA(9) signal. |
| `rsi_reentry` | RSI(14) crosses back above 30 or below 70. |
| `bollinger_reentry` | Close returns inside 20-period, 2-standard-deviation bands after closing outside. |
| `sma_9_21_cross`, `ema_9_21_cross` | Fast average crosses slow average. |
| `ema_9_touch` | In an EMA21/50-aligned trend, price touches EMA9 and closes back on the trend side. |
| `momentum_20_breakout` | Close breaks the preceding 20-bar high or low. |
| `cci_6_14_pullback_proxy` | CCI(6) crosses zero back in the EMA21/50 trend direction while CCI(14) is on that side of zero. This is an explicit research proxy, not an exact Woodies pattern. |
| `rsi_*_divergence` | Two confirmed, strict three-bar price pivots within 60 bars disagree with RSI(14) at those pivots. Regular and hidden bullish/bearish cases are separate. The signal is timestamped three bars **after** the newest pivot, when confirmation is knowable. |

EMA and RSI use seeded recursive averages; ATR(14) uses Wilder's RMA. The simple regime is up when EMA21 > EMA50 and close > EMA50, down for the reverse, and chop otherwise. Volatility is current ATR(14) divided by the trailing 100-bar ATR median: high above 1.3, low below 0.8. Each strategy has a six-bar cooldown to reduce immediate repeats. These are study choices and must stay versioned during comparison.

Market Cipher B is an [invite-only TradingView script](https://marketciphertrading.com/support-faq/). The farm does not reproduce or label a proxy as its green dot. A licensed alert export can later be evaluated as its own source, or an independently defined WaveTrend signal can be evaluated under its own name. TradingView's [Woodies CCI guide](https://www.tradingview.com/support/solutions/43000594673-woodies-cci/) and [RSI divergence guide](https://www.tradingview.com/support/solutions/43000589127-rsi-divergence-indicator/) are references for evaluating closer definitions.

## Interpreting results and next gate

`positive_rate` is the fraction of directional point moves greater than zero at a fixed horizon. `wilson_lower_95` is a descriptive interval lower bound. Outcomes exclude fees, slippage, stops, targets, position overlap, and Topstep rules. Many variants are screened, and overlapping events are correlated; the intervals do not correct for multiple comparisons. The validation screen is ordered by its lower bound only where there are at least 30 events. Test-window and current-regime rows remain visible in `summary.json`; do not tune rules to the test window.

Issue [#5](https://github.com/thetopham/tradingview-bot-v2/issues/5) tracks the next gate: reconcile a refreshed feed, test fixed candidate definitions through 1m simulated broker fills and fees, then run selected candidates in fresh forward demo accounts. Existing ProDex accounts remain intact. A daily read-only refresh needs an authenticated feed path and an explicit host schedule; this initial run is manual.
