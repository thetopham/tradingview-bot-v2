# TradingView simulation user guide

This guide describes the **Pi's simulated MES system as checked on 2026-09-24**. The accounts use virtual money and locally simulated fills. The original Topstep/ProjectX order service is disabled.

## Open the dashboard

1. Open [sim.thetopham.com/sim/dashboard](https://sim.thetopham.com/sim/dashboard).
2. Use browser username `dashboard` and the simulator dashboard password you set. The password is stored in the Pi's private simulator environment file; it is not in either GitHub repository.
3. The page refreshes every 30 seconds. It is read-only: viewing it cannot place an order.

Each account card shows balance, equity including an open position's latest mark, realized and open P&L, maximum-loss room, progress toward the simulated profit goal, current position and its stop/target, latest decision, latest one-minute candle, and recent closed trades. The paired comparison shows matching numeric and image decisions after the chart-image trial began. It is a decision comparison; early agreement or an account's lifetime P&L does not prove one model input is better.

The dashboard's candle timestamp is the **start** of a completed minute. A 10:31 candle is normally processed after 10:32. If the market is closed, an old latest-candle timestamp is expected. During market hours, a growing candle age across all accounts is a feed problem worth checking.

## What the ten accounts test

| Decision cadence | Numeric account | Numeric + chart account |
| --- | --- | --- |
| 5 minutes | `alpha`, `beta`, `gamma` | `alpha_vision`, `beta_vision`, `gamma_vision` |
| 15 minutes | `delta` | `delta_vision` |
| 30 minutes | `epsilon` | `epsilon_vision` |

All ten get the same **closed one-minute MES candles** for execution. The five-minute, fifteen-minute, and thirty-minute workflows supply decisions on their respective candles. A decision can be BUY, SELL, HOLD, or FLAT. HOLD makes no new order; FLAT exits a position. A BUY or SELL enters or reverses when a later eligible one-minute candle opens. The one-minute feed never asks ProDex for a decision.

The numeric and image members of a pair share their decision cadence and broker rules. The image workflow fetches a TradingView chart JPEG, passes its binary image to the ProDex vision node, and archives a permanent Google Cloud Storage URL **after** returning the decision. Numeric and image workflows currently use different n8n model wrappers, so this first trial is exploratory. Read the [paired experiment notes](https://github.com/thetopham/tradingview-bot/blob/main/docs/paired-chart-vision-experiment.md) before comparing outcomes.

## How fills and account rules work

The simulated broker receives closed one-minute OHLC candles. It queues a completed model decision for the first eligible one-minute **open after the decision response**. It then checks each completed candle's high and low against an open position's stored stop and target. If both are touched in the same candle, it records the stop. A gap can worsen a stop fill, and the model adds adverse slippage and MES fees. These choices are intentionally conservative; a one-minute candle cannot reveal the actual order of price movements inside that minute.

The running demo profiles use the same bracket choices: size `1` means one MES contract with a 24-tick stop and 48-tick target; size `2` means two contracts at 12/24 ticks; size `3` means three contracts at 8/16 ticks. Each choice has approximately **$30 gross stop / $60 gross target**, before slippage and fees. Here, size chooses bracket distance as well as contract count. Profile-specific brackets are supported for future variants; inspect the account profile before assuming these defaults for a new account.

The configured 50K Combine **proxy** starts at $50,000, uses a $2,000 trailing maximum-loss rule, a $3,000 nominal profit target, a 55% best-day consistency check, a 50-MES-micro maximum, and a 3:10 p.m. Central flat cutoff. Pass and fail are simulated states under these configured assumptions. A passed Combine does not automatically become a funded account. Actual Topstep terms can change; verify them separately before using a real Combine.

## Where decisions and results appear

- The dashboard reads the **local v2 broker ledger**, which is authoritative for simulated positions, fills, fees, and risk.
- Successful n8n decisions are written to Supabase `ai_trading_log`.
- Closed trades enter a durable local outbox, then the results timer writes them to Supabase `trade_results`. Delivery can lag the dashboard.
- `ai_trade_feed` combines decisions with available results. HOLD decisions can have no trade result, and a manually created trade without an AI decision ID may not join the feed.
- Image workflows save permanent chart URLs back to the decision log after responding. A missing URL immediately after a decision can be an archive delay; inspect the n8n execution if it stays missing.

Do not interpret a blank Supabase join as proof that the broker did not trade. Check the ledger/dashboard and the result publisher independently.

## Strategy farm

The [strategy farm](strategy-farm.md) is a separate **read-only** screen of indicator rules. On weekdays near 09:00 Mountain Time, its Pi timer combines an immutable historical five-minute snapshot with newly cached closed bars and writes a dated report under `runs/strategy-farm`. It measures directional moves over fixed horizons. Its results exclude real bracket fills, fees, slippage, position overlap, and account rules. It never creates demo accounts or orders.

Use the report to select candidates for the next research gate: a replay through the broker's one-minute fill rules, followed by a fresh forward demo account if warranted. [Issue #5](https://github.com/thetopham/tradingview-bot-v2/issues/5) tracks this work. Avoid comparing many optimized variants against the same historical test window as if it were fresh evidence.

## Operator checks on the Pi

The runtime checkouts are `/home/thetopham/tradingview-bot-sim` (Flask bridge) and `/home/thetopham/tradingview-bot-v2` (broker). The active ledger is `/home/thetopham/tradingview-bot-v2/data/sim_broker_local.sqlite`. Never reset or replay into it while the bridge is running.

```bash
systemctl is-active tradingview-bot-sim.service tradingview-bot-sim-decisions.timer tradingview-bot-sim-results.timer tvbot-strategy-farm.timer
curl -fsS http://127.0.0.1:5001/healthz
curl -fsS http://127.0.0.1:5678/healthz
systemctl list-timers --all | grep -E 'tradingview-bot-sim|tvbot-strategy-farm'
journalctl -u tradingview-bot-sim.service -n 100 --no-pager
```

The decisions timer checks each minute for a fresh **strategy** candle and invokes each configured account overseer once for that candle. The results timer retries durable trade-result delivery each minute. The old `tradingview_bot.service` is masked and should not be used to operate the simulator. n8n runs in Docker; its local health endpoint is port 5678.

To inspect account state without opening the dashboard, run from the v2 checkout:

```bash
python -m tvbot_v2.simulate --db data/sim_broker_local.sqlite status
python -m tvbot_v2.simulate --db data/sim_broker_local.sqlite history epsilon --limit 20
```

To add a variant, prepare a new named portfolio in a **separate local test database first**. Once its profile and routing are verified, initialize it in the active ledger, configure its private `N8N_OVERSEER_URL_<ACCOUNT>` route, and restart the simulator bridge to refresh the account map. A new account alone does not create or connect an n8n workflow. Keep webhook secrets and dashboard credentials in the private Pi configuration. The [broker runbook](simulated-broker-runbook.md) covers profile format and safe broker commands; the [bridge runbook](https://github.com/thetopham/tradingview-bot/blob/main/docs/sim-broker-bridge.md) covers n8n routing and result delivery.

If the dashboard loads but decisions stop, check the decision timer, latest cached 5m/15m/30m candle, and the corresponding n8n execution. If decisions continue but fills stop, check the one-minute feed and its timestamps. If dashboard trades appear but Supabase results do not, inspect the results timer and its pending outbox. If only chart images or URLs are missing, check Chart-Img fetch, ProDex `imageIncluded`, and the asynchronous Google upload separately. Do not expose credentials or session cookies in screenshots or issue reports.
