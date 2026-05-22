# TradingView Bot v2 Migration Plan — Replay-First Python, No n8n / No Topstep

> **For Hermes/Codex implementers:** Build v2 as a Kalshi/Polymarket-style research stack: **datafeed → canonical tape → replay → simulation → reports → only later paper/live gates**. Do not port n8n node-for-node. Do not keep Topstep/ProjectX/SignalR in the core.

**Created:** 2026-05-21  
**Updated for user correction:** local self-hosted Supabase datafeed on i732, replay-first architecture learned from Kalshi/Polymarket processes  
**Reviewed folders:** `/home/matt/workspace/tradingview-bot` and `/home/matt/workspace/tradingview-bot-review`  
**Legacy source inspected:** `674b7a8` plus untracked documentation plan files  
**Goal:** Upgrade the previous TradingView bot into **TradingView Bot v2** and make it the primary home for the useful processes learned from the Kalshi and Polymarket bots: canonical feeds, replay, simulation, immutable runs, metrics, reports, and cron watchdogs. Kalshi/Polymarket become archived/reference systems; their process patterns move into v2 over local TradingView/Supabase data.

**Safety boundary:** v2 is research/dry-run/simulation first. No Topstep/ProjectX execution. No SignalR. No live orders. Any future broker/paper/live adapter is a separate explicit approval gate after replay evidence. Archiving old Kalshi/Polymarket services or repositories is non-destructive until explicitly approved.

---

## 1. Corrected North Star

The goal is not to keep three active trading-bot projects forever. The goal is to **archive Kalshi/Polymarket as process/reference repos** and pivot the learned architecture into the previous TradingView bot as **v2**.

Kalshi and Polymarket taught the right process:

- canonical feed/tape first,
- replay before automation,
- pure strategies/state machines,
- realistic fill assumptions,
- immutable run directories,
- metrics/leaderboards/reports,
- cron as a research/reporting assistant, not a live gambler.

TradingView Bot v2 becomes the new active implementation of that process over the local TradingView datafeed.

The corrected target shape is:

```text
local TradingView/Supabase feed
  -> canonical append-only market tape
  -> replay engine over historical TradingView bars/signals
  -> simulated strategy decisions/fills/positions
  -> immutable run artifacts + reports
  -> Hermes cron for ingestion/replay/report automation
  -> optional Codex/Hermes AI review of run artifacts
  -> future broker adapter only after replay evidence and explicit approval
```

This is the same basic operating model that worked better for Kalshi/Polymarket:

1. Record clean data first.
2. Normalize it into a replayable tape.
3. Build strategy code as pure functions/state machines.
4. Replay over historical windows before any automation claims.
5. Simulate fills and positions locally.
6. Score strategies by deployability metrics, not vibes.
7. Automate reports/watchdogs with Hermes cron only after manual replay is trustworthy.

---

## 2. Archive/Pivot Strategy for Kalshi and Polymarket

Kalshi and Polymarket should not be deleted or forgotten. They should become **reference archives** that preserve the hard-won process lessons while TradingView v2 becomes the active build.

### 2.1 Archive intent

- Keep Kalshi and Polymarket repos/data as read-only references unless an explicit task requires touching them.
- Preserve their docs, replay reports, schema examples, and strategy patterns.
- Do not run old live/paper/order services as part of the TradingView v2 path.
- Do not keep improving three parallel bots. Port patterns into v2 instead.

### 2.2 What to extract as reusable process

From Kalshi:

- canonical `feed/`, `runs/`, `logs/` separation,
- append-only feed DB discipline,
- replay CLI that reads a feed DB and writes immutable run artifacts,
- official/proxy settlement labeling discipline where applicable,
- train/validation/test split discipline,
- run metrics and leaderboard generation,
- watchdogs for feed freshness and stale jobs.

From Polymarket:

- inventory-aware simulation patterns,
- passive/fill realism mindset,
- event-reconstructed state caveats,
- per-window strategy reports,
- explicit separation between raw public data, derived features, strategy decisions, and PnL claims.

Into TradingView v2:

- canonical local TradingView feed/tape,
- pure strategy modules,
- replay runner,
- brokerless simulation ledger,
- immutable `runs/<strategy>/<run-id>/`,
- daily reports and data-quality reports,
- Hermes cron watchdog/report jobs.

### 2.3 Non-destructive archive gate

Before disabling/stopping/removing any old Kalshi or Polymarket service:

1. Inventory active services/processes.
2. Identify canonical DBs and latest run artifacts.
3. Confirm whether any recorder is still intentionally collecting useful data.
4. Write an archive note with paths, service names, DB row counts, and last timestamps.
5. Ask for explicit approval before stopping/disabling services.

No destructive archive action is part of the v2 MVP.

---

## 3. Local Data Source Reality

The TradingView datafeed is no longer hosted Supabase-first. It exists on the local/self-hosted Supabase install on i732.

### 3.1 Local Supabase API

Use this as the migrated project's Supabase API base:

```text
http://192.168.0.35:8000
```

For table operations, Python can use either:

- Supabase REST/PostgREST API at `http://192.168.0.35:8000`
- Direct Postgres connection through the local Supabase pooler when raw SQL/bulk export is needed

**Security note:** Credentials and database passwords must stay in `.env` or local secrets only. Do not commit them and do not include them in Codex prompts, logs, reports, or documentation.

### 3.2 Local tables to treat as source inputs

Expected local Supabase tables include:

- `tv_datafeed_5m`
- `tv_datafeed_15m`
- `tv_datafeed_30m`
- `ai_trading_log`
- `ai_trade_feed`
- `chart_analysis`
- `trade_results`

For v2, the core feed should be derived primarily from TradingView datafeed/chart-analysis tables, not from old ProjectX execution logs. Old AI/trade tables are historical labels/research inputs, not execution authority.

---

## 3. What Exists Today in v1

### 3.1 Legacy runtime shape

Current v1 is:

```text
TradingView / APScheduler webhook
  -> Flask `tradingview_projectx_bot.py`
  -> n8n AI/chart/data workflows
  -> ProjectX/Topstep execution through `api.py` / `strategies.py`
  -> Topstep SignalR close tracking
  -> Supabase logs/dashboard/reports
```

Major files:

- `tradingview_projectx_bot.py`: Flask webhook entrypoint, n8n AI routing, strategy dispatch.
- `config.py`: env config for ProjectX, Supabase, n8n, account maps, risk knobs.
- `api.py`: ProjectX REST, Supabase persistence, current-price lookup, n8n AI call.
- `auth.py`: ProjectX auth and futures session guard.
- `strategies.py`: `run_simple()` ProjectX execution.
- `signalr_listener.py`: Topstep SignalR lifecycle tracking.
- `position_manager.py`: ProjectX/Topstep risk and position context.
- `scheduler.py`: APScheduler chart prefetch, overseer triggers, flatten/report jobs.
- `dashboard.py`, `templates/`, `reports/`: Supabase/ProjectX dashboard and reports.
- `n8n/`: exported workflows.

### 3.2 n8n inventory inspected

The repo contains 36 n8n JSON workflows:

- 4 datafeed workflows.
- 3 chart-fetch workflows.
- 29 overseer/account/timeframe workflows.

Top node types found:

- Supabase: 200
- HTTP request: 166
- Set: 145
- Code: 102
- If: 49
- Webhook: 36
- Respond to Webhook: 32
- OpenAI/Gemini/Anthropic model nodes: 29 each
- Google Cloud Storage: 29

**Conclusion:** n8n is a spec/archive, not something to port literally. The Python replacement should absorb responsibilities, not node graphs.

---

## 4. What to Keep vs Delete

### Keep / rebuild

- TradingView datafeed table semantics: symbol, timeframe, OHLCV, indicators, timestamp.
- Timeframe split: 5m / 15m / 30m.
- The full-loop dataset concept: context → hypothesis → action → result.
- Trace/session IDs for joining bars, signals, decisions, simulated fills, and reports.
- Risk concepts from `position_manager.py`:
  - daily loss/profit limits,
  - consecutive loss guards,
  - drawdown/equity state,
  - missing data warnings,
  - stale/blocked trade reasons.
- Strategy concepts from `strategies.py`:
  - skip duplicate same-direction exposure,
  - handle opposite exposure explicitly,
  - separate signal from execution.
- Scanner ideas from `market_scanner_breakout_retest.py`, but port into tested v2 modules.
- Report ideas from `reports/`, rewritten to read v2 replay/sim artifacts.
- Dashboard only later, read-only, after CLI reports are trustworthy.

### Delete / quarantine from v2 core

- ProjectX/Topstep auth.
- Topstep SignalR.
- ProjectX order/position/trade field names as core model names.
- Futures contract IDs as universal identifiers.
- n8n runtime URLs.
- Supabase hosted assumptions.
- Supabase as the only runtime state; use local feed export + canonical SQLite/tape for replay.
- Any live order submission path.
- Any credential/token logging.

---

## 5. Target v2 Architecture

```text
v2/
  pyproject.toml
  README.md
  config.example.toml
  .env.example
  feed/                         # canonical local feed exports / SQLite tapes
    tradingview.sqlite3
    exports/
  runs/                         # immutable replay/simulation outputs
    <strategy>/<run-id>/
      config.toml
      metrics.json
      results.sqlite3
      report.md
  logs/
  src/tvbot_v2/
    config.py
    db.py
    schema.sql
    models.py
    trace.py
    redaction.py
    supabase_local/
      client.py
      export.py
      inspect.py
    feed/
      normalize.py
      tape_writer.py
      quality.py
    replay/
      engine.py
      fills.py
      metrics.py
      runner.py
      splits.py
    strategy/
      base.py
      simple.py
      breakout_retest.py
      regime.py
    simulate/
      ledger.py
      portfolio.py
      brokerless_executor.py
    ai/
      codex_review.py
      prompts.py
      schemas.py
    reports/
      daily.py
      replay_report.py
      leaderboard.py
      data_quality.py
    cron/
      jobs.py
      watchdog.py
  scripts/
    tvbot-v2-inspect-local-supabase
    tvbot-v2-export-feed
    tvbot-v2-build-tape
    tvbot-v2-replay
    tvbot-v2-simulate-once
    tvbot-v2-daily-report
    tvbot-v2-watchdog
  tests/
    fixtures/
    test_local_supabase_export.py
    test_feed_normalize.py
    test_replay_engine.py
    test_fill_model.py
    test_strategy_simple.py
    test_sim_ledger.py
    test_reports.py
```

---

## 6. Datafeed Layer

### 6.1 Purpose

Replace n8n datafeed/chart fetch and Supabase-hosted assumptions with a Python exporter/normalizer over the local Supabase data.

### 6.2 Source connection modes

Support two Python read paths:

1. **Supabase REST mode**
   - Base URL: `http://192.168.0.35:8000`
   - Good for simple table select/export.
   - Key loaded from env only.

2. **Postgres SQL mode**
   - Good for raw SQL, joins, bulk exports, and table inspection.
   - Credentials loaded from env only.
   - Never print password or full DSN.

### 6.3 Export targets

The exporter should write to local canonical tape storage:

```text
v2/feed/tradingview.sqlite3
```

Core tables:

- `raw_supabase_row`
  - source table, source primary key/hash, fetched_at, raw JSON.
- `bar`
  - symbol, timeframe, ts, open, high, low, close, volume, indicators_json, source_table.
- `chart_snapshot`
  - timeframe, symbol, ts, snapshot JSON/image metadata if available.
- `legacy_ai_label`
  - historical AI signal/reason/action from `ai_trading_log`/`ai_trade_feed`, if useful for supervised analysis.
- `legacy_trade_result`
  - old outcomes from `trade_results`, labeled as legacy/ProjectX-derived.
- `feed_export_run`
  - started_at, finished_at, source, row counts, min/max ts, errors.

### 6.4 Data-quality checks

Every export should report:

- tables reachable,
- row counts by source table,
- min/max timestamps by timeframe,
- gaps by timeframe,
- duplicate bars,
- malformed OHLC rows,
- stale/latest timestamp,
- indicator columns/keys present,
- chart-analysis coverage.

---

## 7. Replay Layer

### 7.1 Replay principle

Strategies should not discover behavior through cron/live automation. They should replay over historical TradingView feed windows first.

Replay input:

```text
feed/tradingview.sqlite3 -> ordered bars/snapshots/signals
```

Replay output:

```text
runs/<strategy>/<run-id>/
  config.toml
  results.sqlite3
  metrics.json
  report.md
```

### 7.2 Replay engine responsibilities

- Load bars by symbol/timeframe/window.
- Step forward chronologically.
- Provide a `MarketState` object to pure strategy code.
- Prevent lookahead: strategy only sees current/past bars.
- Apply fill model on next bar or configurable next-tick equivalent.
- Track simulated cash, equity, positions, realized/unrealized PnL.
- Enforce risk/capital caps at decision time and fill time.
- Reset or roll sessions according to configured trading hours.
- Persist every signal, decision, order intent, fill, position snapshot, and risk block.

### 7.3 Fill realism for TradingView bar data

Because TradingView data is bar-level, not full order book:

- Default fill model should be conservative.
- Market order emitted on bar close fills at next bar open plus configurable slippage.
- Limit order fills only if next/future bar trades through limit; choose conservative price.
- Stops/targets should trigger based on high/low, with ambiguity handling when both hit in same bar.
- Fees/slippage must be configurable and included.
- Any result using bar OHLC path assumptions must label that assumption in `metrics.json`.

### 7.4 Replay metrics

Rank strategies by:

- net PnL after fees/slippage,
- max drawdown,
- worst trade/day/session,
- win rate,
- profit factor,
- average trade,
- exposure time,
- number of trades,
- rejected/blocked signal rate,
- parameter stability across train/validation/test,
- data coverage and gap count.

Do not optimize on raw PnL alone.

---

## 8. Simulation Layer

Simulation is not live trading. It is the persistent brokerless paper ledger over either replay or latest feed snapshots.

### 8.1 Simulated ledger tables

- `sim_account`
- `sim_position`
- `sim_order_intent`
- `sim_fill`
- `sim_trade`
- `sim_risk_event`
- `sim_run`

### 8.2 Sim rules

- Start with fixed simulated capital.
- No broker/API side effects.
- Entries and exits generated by strategy and risk engine only.
- All fills persisted with provenance: replay fill, latest-bar sim fill, manual test fill.
- Reconciliation compares only local ledger to expected state, not Topstep/SignalR.

---

## 9. Python Replacement for n8n and APScheduler

### 9.1 n8n replacement map

- n8n datafeed workflows → `supabase_local/export.py` + `feed/normalize.py`
- n8n chart fetch workflows → optional `chart_snapshot` export/normalizer; chart image fetching later
- n8n overseers → pure Python strategy/replay, optional Codex/Hermes analysis over artifacts
- n8n Supabase nodes → Python Supabase REST/Postgres readers
- n8n code/set/if nodes → tested Python transforms
- n8n model nodes → optional Codex/Hermes review, not order authority

### 9.2 APScheduler replacement map

Use Hermes cron / systemd timers / normal cron to run one-shot scripts:

- `tvbot-v2-export-feed` every 5m/15m/30m or on demand.
- `tvbot-v2-build-tape` after export.
- `tvbot-v2-replay --strategy <name> --window recent` manual first, later scheduled for reports.
- `tvbot-v2-simulate-once` only after replay validates strategy.
- `tvbot-v2-daily-report` after market close.
- `tvbot-v2-watchdog` silent-on-ok.

Cron should not place trades or invent strategies. It should collect data, run known replay/report jobs, and alert on health.

---

## 10. Codex/Hermes Role

Codex subscription should be used mainly for:

1. Implementing and reviewing bite-sized code tasks.
2. Reviewing replay reports/artifacts.
3. Suggesting hypotheses for the next replay experiment.
4. Generating structured commentary from metrics.

Optional later: Codex can act as an offline overseer over a replay context bundle, but:

- It receives redacted context only.
- It outputs JSON only.
- Its output becomes a replay/simulated decision, not a broker order.
- Python validates schema and applies risk gates.
- Invalid/late output becomes `HOLD`.

Codex must not receive local Supabase passwords, service-role keys, broker credentials, webhook secrets, or complete `.env` contents.

---

## 11. Phase Plan

### Phase 0 — Freeze legacy execution, archive old process knowledge, and document local source

**Objective:** Stop thinking in v1 Topstep/n8n terms and stop splitting active development across Kalshi/Polymarket/TradingView. Treat Kalshi/Polymarket as process archives; build the next active system inside TradingView v2.

Deliverables:

- This corrected migration plan.
- README note that v2 is replay-first and does not use Topstep/SignalR/n8n runtime.
- Archive notes for Kalshi and Polymarket reference paths/process lessons, without stopping services yet.
- `.env.example` with placeholders only.

Gate:

- No execution code changed.
- No credentials committed.
- No Kalshi/Polymarket service stopped or disabled without explicit approval.

---

### Phase 1 — Inspect local Supabase safely

**Objective:** Prove the local data source is reachable and understand table shapes.

Files:

- `v2/src/tvbot_v2/supabase_local/inspect.py`
- `v2/scripts/tvbot-v2-inspect-local-supabase`
- `v2/tests/test_local_supabase_export.py` with mocked connection/client

Tasks:

1. Load connection config from env.
2. Support REST and Postgres modes.
3. Print only redacted connection summary.
4. List relevant tables and row counts.
5. Report min/max timestamps for `tv_datafeed_5m`, `tv_datafeed_15m`, `tv_datafeed_30m`.
6. Do not print keys/passwords.

Verification:

```bash
cd /home/matt/workspace/tradingview-bot/v2
pytest -q
python scripts/tvbot-v2-inspect-local-supabase --mode rest --redacted
```

Expected: table inventory and timestamp coverage, no secrets printed.

---

### Phase 2 — Export canonical TradingView tape

**Objective:** Create local replayable feed DB from local Supabase.

Files:

- `v2/src/tvbot_v2/schema.sql`
- `v2/src/tvbot_v2/db.py`
- `v2/src/tvbot_v2/supabase_local/export.py`
- `v2/src/tvbot_v2/feed/normalize.py`
- `v2/src/tvbot_v2/feed/tape_writer.py`
- `v2/scripts/tvbot-v2-export-feed`
- `v2/scripts/tvbot-v2-build-tape`
- `v2/tests/test_feed_normalize.py`

Tasks:

1. Export raw source rows into `raw_supabase_row`.
2. Normalize `tv_datafeed_*` into canonical `bar` rows.
3. Preserve source table and raw JSON.
4. Idempotently upsert by `(symbol,timeframe,ts)`.
5. Export legacy AI/trade rows only as labels/outcomes, not as authority.
6. Write data-quality report.

Verification:

```bash
python scripts/tvbot-v2-export-feed --since 2026-01-01 --db feed/tradingview.sqlite3
python scripts/tvbot-v2-build-tape --db feed/tradingview.sqlite3
python scripts/tvbot-v2-data-quality --db feed/tradingview.sqlite3
```

Expected: canonical feed exists with row counts and min/max timestamps.

---

### Phase 3 — Replay engine MVP

**Objective:** Replay a simple strategy over TradingView bars with no lookahead.

Files:

- `v2/src/tvbot_v2/replay/engine.py`
- `v2/src/tvbot_v2/replay/fills.py`
- `v2/src/tvbot_v2/replay/metrics.py`
- `v2/src/tvbot_v2/replay/runner.py`
- `v2/src/tvbot_v2/strategy/base.py`
- `v2/src/tvbot_v2/strategy/simple.py`
- `v2/scripts/tvbot-v2-replay`
- `v2/tests/test_replay_engine.py`
- `v2/tests/test_fill_model.py`

Tasks:

1. Define `MarketState`, `Signal`, `OrderIntent`, `Fill`, `PortfolioState`.
2. Implement next-bar-open market fill with slippage/fees.
3. Implement basic long/flat strategy from TradingView signals.
4. Persist replay events to run-specific `results.sqlite3`.
5. Write `metrics.json` and `report.md`.

Verification:

```bash
python scripts/tvbot-v2-replay \
  --db feed/tradingview.sqlite3 \
  --strategy simple \
  --symbol MES \
  --timeframe 5m \
  --start 2026-01-01 \
  --end 2026-02-01
```

Expected: run directory under `runs/simple/<run-id>/` with metrics and report.

---

### Phase 4 — Port Kalshi/Polymarket replay discipline

**Objective:** Add train/validation/test splits, parameter sweeps, and deployability metrics.

Files:

- `v2/src/tvbot_v2/replay/splits.py`
- `v2/src/tvbot_v2/replay/optimizer.py`
- `v2/src/tvbot_v2/reports/leaderboard.py`
- `v2/scripts/tvbot-v2-replay-matrix`

Tasks:

1. Chronological split: train 70%, validation 15%, test 15%.
2. Bounded parameter sweep only.
3. Checkpoint sweep progress atomically.
4. Rank by risk-adjusted metrics.
5. Promote validation winners to test once.
6. Report overfit warnings.

Gate:

- No strategy can be called deployable without passing test split and data-quality checks.

---

### Phase 5 — Strategy ports

**Objective:** Port useful v1 ideas into replayable, pure strategy modules.

Strategies:

- `simple`: signal-following baseline.
- `breakout_retest`: from `market_scanner_breakout_retest.py`.
- `regime`: fixed/ported version of `market_regime.py` concepts after syntax issues are resolved in v2 tests.
- `ai_label_baseline`: compare old `ai_trading_log` decisions against outcomes as historical label study.

Rules:

- Strategies are pure/replayable.
- No direct DB writes inside strategy.
- No broker/API calls inside strategy.
- Every blocked signal records reason.

---

### Phase 6 — Brokerless simulation over latest feed

**Objective:** Run the best replay-tested strategy in local simulation mode over latest exported feed.

Files:

- `v2/src/tvbot_v2/simulate/ledger.py`
- `v2/src/tvbot_v2/simulate/portfolio.py`
- `v2/src/tvbot_v2/simulate/brokerless_executor.py`
- `v2/scripts/tvbot-v2-simulate-once`
- `v2/tests/test_sim_ledger.py`

Tasks:

1. Maintain persistent simulated account/positions.
2. Process only new bars since last sim run.
3. Apply same fill model as replay or a clearly labeled live-bar approximation.
4. Persist simulated decisions/fills.
5. Produce daily sim report.

Gate:

- Sim mode only after replay engine and at least one strategy report exists.
- Still no broker order calls.

---

### Phase 7 — Hermes cron operations

**Objective:** Replace APScheduler/n8n scheduling with safe one-shot jobs.

Suggested jobs:

- Every 5m: export latest local Supabase feed rows.
- Every 5m after export: build/update tape and data-quality summary.
- Manual/nightly: replay selected strategies over recent window.
- After market close: daily sim/replay report.
- Watchdog: silent if feed fresh and last jobs succeeded; alert if stale/errors.

Gate:

- Cron jobs are data/report jobs only.
- No broker submission job exists.

---

### Phase 8 — Optional read-only dashboard

Only after reports are trustworthy:

- Read-only over `feed/tradingview.sqlite3` and selected `runs/*` artifacts.
- No broker routes.
- No order routes.
- No refresh-time external API calls except local feed DB reads.

---

### Phase 9 — Future broker adapter, separate plan only

If replay + simulation produce evidence and the user explicitly asks for broker integration later, write a new plan. It must include:

- exact broker target,
- paper-only mode first,
- hard config/CLI gates,
- size/notional caps,
- kill switch,
- reconciliation,
- paper evidence review,
- live activation plan separate from paper.

Not part of this v2 migration.

---

## 12. Immediate Implementation Slice

Smallest useful implementation slice:

1. Create `v2/` skeleton.
2. Add safe config and `.env.example` with local Supabase placeholders.
3. Add local Supabase inspector with redacted output.
4. Add schema for canonical feed DB.
5. Add exporter/normalizer for `tv_datafeed_5m`, `tv_datafeed_15m`, `tv_datafeed_30m`.
6. Add data-quality report.
7. Add tests with mocked Supabase/Postgres rows.
8. Run inspector against i732 only after env credentials are present locally.

Suggested commands after approval:

```bash
cd /home/matt/workspace/tradingview-bot
mkdir -p v2/src/tvbot_v2/{supabase_local,feed,replay,strategy,simulate,ai,reports,cron} v2/scripts v2/tests/fixtures v2/feed v2/runs v2/logs
cd v2
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install pytest pydantic python-dotenv requests psycopg[binary]
```

Do **not** run legacy ProjectX bot. Do **not** call order endpoints. Do **not** put local Supabase keys/passwords into the repo.

---

## 13. Definition of Done for v2 MVP

v2 MVP is done when:

- TradingView Bot v2 is the active project shape for the learned trading-research process.
- Kalshi and Polymarket are treated as reference archives/process libraries, not parallel active bots to keep expanding.
- Python can inspect local Supabase with redacted output.
- Python exports local TradingView datafeed tables into `feed/tradingview.sqlite3`.
- The canonical tape has data-quality metrics.
- At least one pure strategy replays over the tape without lookahead.
- Replay writes immutable run artifacts under `runs/`.
- Metrics include fees/slippage assumptions and deployability stats.
- Simulation can process latest bars into a brokerless local ledger.
- Hermes cron can run export/report/watchdog jobs.
- n8n is not required at runtime.
- Topstep/ProjectX/SignalR are not in the v2 core.
- No live/paper broker order path exists in MVP.
