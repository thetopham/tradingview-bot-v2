# TradingView Bot v2

TradingView Bot v2 supplies the **simulated MES broker and research tools** for the [TradingView bot Flask bridge](https://github.com/thetopham/tradingview-bot). The Pi runs demo accounts only. It does not connect to Topstep/ProjectX or send real orders.

**Start here:** [User guide](documentation/user-guide.md) · [Broker runbook](documentation/simulated-broker-runbook.md) · [Strategy farm](documentation/strategy-farm.md) · [Revival history](documentation/revival-migration-plan-2026-09.md)

## Running system

```text
TradingView closed 1m MES candles -> n8n datafeed -> Flask /sim/feed
                                               -> v2 SQLite broker -> fills, brackets, risk

TradingView closed 5m / 15m / 30m candles -> n8n datafeed -> Flask cache
                                            -> decision timer -> ProDex overseer
                                            -> v2 pending order -> next eligible 1m open

v2 closed trade -> durable result outbox -> Supabase trade_results
ProDex decision -> Supabase ai_trading_log -> ai_trade_feed
v2 ledger -> authenticated /sim/dashboard
```

The one-minute feed advances every configured account's fills, stops, targets, marked equity, and loss rules. It **does not call an LLM every minute**. The decision workflows run on their own closed strategy candles. A decision that arrives after a candle opened cannot fill at that earlier open. When stop and target fall inside one candle, the simulator assumes the stop happened first. One-minute OHLC cannot establish the true intraminute order.

As checked on 2026-09-24, the Pi has five numeric ProDex accounts (`alpha` through `epsilon`) and five paired chart-image accounts (`*_vision`), all using one-minute execution. This is a starting set, not a software account limit. The image experiment is described in the [bridge repo](https://github.com/thetopham/tradingview-bot/blob/main/docs/paired-chart-vision-experiment.md). Account balance and split-test results are experimental simulations, not verified broker P&L.

## Local broker

Install the package with `pip install -e .`, then use a **separate** local database for experiments:

```bash
python -m tvbot_v2.simulate --db data/my-experiment.sqlite init --portfolio profiles/broker-demo-1m.json
python -m tvbot_v2.simulate --db data/my-experiment.sqlite status
python -m tvbot_v2.simulate --db data/my-experiment.sqlite history epsilon --limit 20
```

The Pi production ledger is `/home/thetopham/tradingview-bot-v2/data/sim_broker_local.sqlite`. Do not use that path for tests, resets, or replay. Profile changes to an existing account are rejected; create a new named variant. See the [runbook](documentation/simulated-broker-runbook.md) for the decision contract, bracket settings, conservative fill rules, and account management.

## Research

The [strategy farm](documentation/strategy-farm.md) screens versioned indicator rules against historical and newly cached five-minute bars. Its weekday Pi timer writes immutable reports under `runs/strategy-farm`. It does not create accounts or orders. A candidate needs a separate one-minute broker replay and fresh forward demo account before being treated as promising. [Issue #5](https://github.com/thetopham/tradingview-bot-v2/issues/5) tracks this next gate.

The original May migration plans are kept as historical design notes: [Python migration](documentation/python-codex-v2-migration-plan.md) and [PRD](documentation/tradingview-bot-v2-prd-implementation-plan.md). Their planned architecture is not a description of the current Pi runtime.

## Data and secrets

Local Supabase supplies decision and market-history data. The broker's SQLite ledger is authoritative for simulated positions and fills. Do not commit service keys, Chart-Img sessions, OAuth credentials, dashboard passwords, private environment files, or raw data exports.
