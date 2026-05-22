# TradingView Bot v2

Replay-first Python migration of the legacy TradingView ProjectX bot.

TradingView Bot v2 is the active home for the trading-research process learned from the Kalshi and Polymarket bots:

```text
local TradingView/Supabase feed
  -> canonical append-only tape
  -> replay engine
  -> brokerless simulation
  -> immutable run artifacts
  -> reports + Hermes cron watchdogs
```

## Safety boundary

MVP is research/simulation only:

- no Topstep/ProjectX execution
- no SignalR
- no n8n runtime dependency
- no live or paper broker order paths
- no secrets committed or printed

Kalshi and Polymarket are reference archives/process libraries, not parallel active bots to keep expanding.

## Local data source

The local self-hosted Supabase API base is configured by env/config. The known local API base is:

```text
http://192.168.0.35:8000
```

Do not commit service-role keys, database passwords, `.env`, or raw exports.

## First milestones

1. Safe config and redaction.
2. Local Supabase inspector.
3. Export `tv_datafeed_5m`, `tv_datafeed_15m`, `tv_datafeed_30m` into `feed/tradingview.sqlite3`.
4. Replay baseline strategy with conservative bar-level fills.
5. Brokerless simulation and reports.
