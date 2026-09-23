# TradingView Bot v2

Replay-first Python migration of the legacy TradingView ProjectX bot. The [September 2026 revival plan](documentation/revival-migration-plan-2026-09.md) records the current ProDex-first, five-account simulation scope.

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
- no requirement for n8n to run offline replay; a decision-only ProDex adapter is planned for forward simulation
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

## Offline reconstruction available now

The supplied 30-minute MES CSV and epsilon decision log can be imported and replayed without a broker connection:

```powershell
python -m tvbot_v2.data_cli import-feed --csv <tv_datafeed_30m_rows.csv> --table tv_datafeed_30m --db feed/tradingview.sqlite3
python -m tvbot_v2.data_cli recover-signals --csv <ai_trading_log_rows.csv> --db feed/tradingview.sqlite3 --account epsilon --timeframe 30m --prompt-version simple-30m-epsilon-1-13-2026-optimize --output runs/recovered-epsilon-30m.jsonl
python -m tvbot_v2.cli --db feed/tradingview.sqlite3 --timeframe 30m --signals runs/recovered-epsilon-30m.jsonl --output runs/epsilon-five-variant
```

The five accounts are independently simulated 50K Trading Combine proxies. Old `size` values are retained as bracket-choice metadata. Exported 5-minute bars are needed before the practice workflow can be replayed faithfully.
