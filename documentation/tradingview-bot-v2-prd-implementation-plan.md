# TradingView Bot v2 PRD + Migration Implementation Plan

> **For Hermes/Codex implementers:** This is the build spec for upgrading the existing TradingView bot into v2. Treat Kalshi and Polymarket as reference archives/process libraries. Build the learned process — canonical feeds, replay, simulation, metrics, reports, watchdogs — into TradingView Bot v2 over local TradingView/Supabase data. Do not keep expanding three parallel bots.

**Created:** 2026-05-21  
**Primary repo:** `/home/matt/workspace/tradingview-bot`  
**Reference/archive repos:** Kalshi BTC and Polymarket research bots  
**Local data source:** self-hosted Supabase on i732, API base `http://192.168.0.35:8000`  
**Runtime philosophy:** feed → tape → replay → simulate → report → cron watchdogs  
**Safety boundary:** no Topstep/ProjectX execution, no SignalR, no live orders, no broker order paths in MVP.

---

# Part 1 — Product Requirements Document

## 1. Product Summary

TradingView Bot v2 is a replay-first trading research system built from the previous TradingView ProjectX bot, upgraded with the safer process architecture learned from Kalshi and Polymarket work.

The product is **not** a live trading bot in MVP. It is a local research and simulation platform that:

1. Extracts local TradingView data from the self-hosted Supabase instance.
2. Builds a canonical append-only replay tape.
3. Runs pure strategy modules over historical data.
4. Simulates fills, positions, and account state without broker side effects.
5. Produces immutable run artifacts, metrics, leaderboards, and reports.
6. Uses Hermes cron for ingestion/report/watchdog automation.
7. Archives Kalshi/Polymarket as reference systems and ports their process lessons into TradingView v2.

## 2. Background

The previous TradingView bot v1 is a Flask + APScheduler + n8n + ProjectX/Topstep + SignalR + Supabase system.

v1 learned useful concepts:

- TradingView feed/timeframe workflow.
- AI decision logging.
- Context → hypothesis → action → result loop.
- Risk context and account-state concepts.
- Dashboard/report ideas.
- Trace/session metadata.

But v1 has operational problems:

- n8n owns too much orchestration and experiment history.
- ProjectX/Topstep/SignalR tightly couple execution and logging.
- Supabase tables mix feed, AI decisions, outcomes, and dashboard state.
- The system is execution-first rather than replay-first.
- Testing is weak and some modules do not compile cleanly.

Kalshi and Polymarket work taught a better workflow:

- one canonical feed/tape,
- immutable replay runs,
- pure strategies,
- realistic fills,
- explicit PnL provenance,
- train/validation/test discipline,
- cron as reporting/research assistant,
- live execution behind hard approval gates.

TradingView Bot v2 consolidates that learned workflow into one active project.

## 3. Goals

### 3.1 Product goals

- Make TradingView Bot v2 the active home for the trading research process.
- Archive Kalshi/Polymarket as reference/process libraries, not active parallel bot builds.
- Replace n8n runtime workflows with Python modules and one-shot scripts.
- Replace APScheduler with Hermes cron/systemd/cron-friendly one-shot scripts.
- Build a canonical local TradingView tape from self-hosted Supabase.
- Implement a replay engine over TradingView bar/signals data.
- Implement brokerless simulation over replay/latest feed data.
- Generate reproducible run artifacts and reports.
- Keep all broker/live execution out of MVP.

### 3.2 Technical goals

- Use local SQLite for canonical tape and run outputs.
- Support local Supabase REST and/or Postgres export modes.
- Redact secrets in logs, reports, Codex prompts, and error messages.
- Write tests around feed normalization, replay, fills, strategy rules, and sim ledger.
- Make every run reproducible with config + metrics + results DB.
- Make each cron job idempotent and safe to rerun.

## 4. Non-goals

MVP will not:

- Place live trades.
- Place paper broker trades.
- Use Topstep/ProjectX execution.
- Use SignalR.
- Depend on n8n runtime workflows.
- Build a v1-style dashboard before reports are trustworthy.
- Optimize strategies blindly without train/validation/test splits.
- Send secrets to Codex/Hermes prompts.
- Delete or disable Kalshi/Polymarket services without explicit approval.

## 5. Users / Consumers

Primary user:

- Operator/researcher who wants a safe local trading research loop.

System consumers:

- Hermes cron jobs for scheduled export/report/watchdog.
- Codex/Hermes agents for implementation, code review, and offline artifact review.
- Future read-only dashboard after reports mature.

## 6. Source Systems

### 6.1 Previous TradingView bot

Repo:

```text
/home/matt/workspace/tradingview-bot
```

Useful source files:

- `documentation/README.md`
- `documentation/tradingview/*`
- `n8n/` workflow exports as specs/examples
- `market_scanner_breakout_retest.py`
- `position_manager.py`
- `reports/`
- `dashboard.py` only as future reference
- `strategies.py` for baseline execution logic ideas

Quarantine from v2 core:

- `auth.py`
- ProjectX order functions in `api.py`
- `signalr_listener.py`
- ProjectX/Topstep account IDs/field names
- n8n URLs as runtime dependencies

### 6.2 Local Supabase

API base:

```text
http://192.168.0.35:8000
```

Expected source tables:

- `tv_datafeed_5m`
- `tv_datafeed_15m`
- `tv_datafeed_30m`
- `chart_analysis`
- `ai_trading_log`
- `ai_trade_feed`
- `trade_results`

Security:

- Keys/passwords live only in `.env`/local secret storage.
- Documentation must use placeholders only.
- Service-role keys are never included in prompts or reports.

### 6.3 Kalshi and Polymarket references

Use as reference archives for process patterns:

- canonical `feed/`, `runs/`, `logs/` separation,
- replay runner shape,
- immutable artifacts,
- data-quality/watchdog patterns,
- simulation and PnL provenance caveats,
- train/validation/test and bounded optimization discipline.

## 7. Functional Requirements

### FR1 — Local Supabase inspection

The system must inspect local Supabase safely.

Requirements:

- Load connection config from env.
- Support REST/PostgREST mode.
- Support direct Postgres mode if configured.
- Print redacted connection summary.
- List relevant tables.
- Report row counts and timestamp coverage.
- Never print keys/passwords.

Acceptance criteria:

- Running `tvbot-v2-inspect-local-supabase --redacted` produces table inventory without secrets.
- Tests cover redaction and mocked table inspection.

### FR2 — Canonical feed export

The system must export TradingView source tables into a canonical local feed DB.

Requirements:

- Target DB: `v2/feed/tradingview.sqlite3`.
- Preserve raw source rows.
- Normalize bars into canonical schema.
- Upsert idempotently by `(symbol, timeframe, ts)`.
- Store source table and raw JSON hash.
- Record export run metrics.

Acceptance criteria:

- Re-running export does not duplicate bars.
- Data-quality report shows row counts, min/max timestamps, gaps, duplicates, malformed rows.

### FR3 — Canonical tape schema

The feed DB must support replay.

Minimum tables:

- `raw_source_row`
- `bar`
- `chart_snapshot`
- `legacy_ai_label`
- `legacy_trade_result`
- `feed_export_run`
- `data_quality_check`

Acceptance criteria:

- `schema.sql` initializes repeatedly without error.
- Tests confirm required tables and uniqueness constraints.

### FR4 — Replay engine

The system must replay strategies over historical TradingView feed data.

Requirements:

- Chronological replay over selected symbol/timeframe/window.
- No lookahead.
- Pure strategy interface.
- Fill model for bar-level data.
- Persist decisions, intents, fills, positions, risk blocks.
- Write immutable run directory.

Acceptance criteria:

- `tvbot-v2-replay --strategy simple ...` creates `runs/simple/<run-id>/`.
- Run directory contains `config.toml`, `results.sqlite3`, `metrics.json`, `report.md`.
- Tests prove strategy cannot see future bars.

### FR5 — Fill model

The system must label and enforce fill assumptions.

Requirements:

- Default market fill: next bar open plus slippage.
- Limit fill: conservative bar high/low through-limit logic.
- Stop/target ambiguity handling when both hit in same bar.
- Fees and slippage included.
- Fill assumptions persisted in run config/metrics.

Acceptance criteria:

- Tests cover next-bar market fills, limit fills, no-fill cases, fee/slippage accounting, ambiguous stop/target handling.

### FR6 — Strategy modules

The system must support pure strategy modules.

Initial strategies:

- `simple`: baseline signal-following.
- `breakout_retest`: port of useful scanner concepts.
- `regime`: cleaned/ported regime logic after syntax problems are resolved in v2.
- `ai_label_baseline`: evaluates historical v1 AI labels/outcomes as research, not authority.

Acceptance criteria:

- Each strategy can run in replay without direct DB writes or external API calls.
- Each strategy has unit tests for rule gates.

### FR7 — Brokerless simulation

The system must simulate strategy decisions over latest feed snapshots without broker side effects.

Requirements:

- Persistent simulated account and positions.
- Process only new bars since last simulation run.
- Use same or explicitly labeled fill model.
- Persist sim decisions/fills/trades.
- Produce sim report.

Acceptance criteria:

- `tvbot-v2-simulate-once` updates local sim ledger only.
- No broker/order endpoints exist in MVP execution path.

### FR8 — Reports and leaderboards

The system must produce useful reports before dashboard work.

Reports:

- data-quality report,
- replay report,
- run leaderboard,
- daily summary,
- simulation report,
- watchdog health report.

Acceptance criteria:

- Reports are markdown/JSON artifacts.
- Leaderboard ranks by risk-adjusted metrics, not raw PnL alone.

### FR9 — Hermes cron jobs

The system must provide one-shot scripts suitable for Hermes cron.

Jobs:

- inspect/export latest feed,
- build/update tape,
- data-quality check,
- replay selected strategy/window,
- daily report,
- watchdog silent-on-ok.

Acceptance criteria:

- Scripts are idempotent.
- Watchdog exits quietly when healthy and prints compact alert when unhealthy.
- No cron job submits broker orders.

### FR10 — Archive pivot

The system must preserve Kalshi/Polymarket process knowledge while preventing parallel bot sprawl.

Requirements:

- Add archive notes for Kalshi/Polymarket reference paths and useful patterns.
- Do not stop/disable old services without approval.
- Use old bots as references for process, not active build targets.

Acceptance criteria:

- Documentation states TradingView v2 is active project shape.
- Archive notes exist and list reference patterns/paths.

## 8. Non-functional Requirements

### Security

- No secrets in repo/docs/reports/prompts.
- Redact API keys, service-role keys, DB passwords, bearer tokens, webhook secrets, DSNs.
- `.env.example` placeholders only.

### Reliability

- Idempotent exports and cron jobs.
- Atomic writes for run metrics/checkpoints.
- Replay outputs immutable once written.

### Testability

- Unit tests for core transforms and simulation math.
- Mock local Supabase clients in tests.
- No tests require live credentials by default.

### Observability

- Every script writes structured run status.
- Data-quality and replay reports include provenance and assumptions.

### Maintainability

- Keep v2 package cleanly separated from legacy v1 files.
- No direct imports from v1 execution modules unless explicitly wrapped/ported.

## 9. Success Metrics

MVP success:

- Local Supabase export works.
- Canonical feed DB exists and passes data-quality checks.
- At least one strategy replays over historical TradingView data.
- Replay produces immutable artifacts.
- Brokerless simulation processes latest feed bars.
- Reports identify strategy performance and data problems.
- Hermes cron can run export/report/watchdog jobs.
- No n8n, Topstep, ProjectX, or SignalR runtime dependency in v2.

Research success:

- Strategies can be compared across train/validation/test windows.
- Metrics expose overfit, drawdown, worst-session, slippage/fee sensitivity.
- Old v1 AI labels can be evaluated as historical labels, not blindly trusted.

## 10. Risks and Mitigations

### Risk: local Supabase schema differs from assumptions

Mitigation:

- Phase 1 inspection prints columns/sample redacted row shapes.
- Normalizer is schema-tolerant and records malformed rows.

### Risk: TradingView bar data is too coarse for realistic fills

Mitigation:

- Conservative default fill model.
- Persist fill assumptions.
- Label results as bar-replay estimates.

### Risk: old v1 execution concepts leak into v2

Mitigation:

- No imports from `auth.py`, ProjectX order functions, or `signalr_listener.py`.
- Explicit tests/grep checks for forbidden modules in v2.

### Risk: Codex/Hermes receives secrets

Mitigation:

- Redaction module.
- Prompt context builder excludes `.env`, DSNs, keys, raw credentials.

### Risk: three-bot sprawl continues

Mitigation:

- Archive notes for Kalshi/Polymarket.
- V2 docs state TradingView v2 is the active target.
- Any new process improvements go into v2 unless user explicitly asks otherwise.

---

# Part 2 — Migration / Implementation Plan

## 11. Target Directory Structure

Create v2 under the existing TradingView bot repo:

```text
v2/
  pyproject.toml
  README.md
  config.example.toml
  .env.example
  feed/
    tradingview.sqlite3
    exports/
  runs/
  logs/
  docs/
    archive-notes/
      kalshi.md
      polymarket.md
  src/tvbot_v2/
    __init__.py
    config.py
    db.py
    schema.sql
    models.py
    trace.py
    redaction.py
    supabase_local/
      __init__.py
      client.py
      inspect.py
      export.py
    feed/
      __init__.py
      normalize.py
      tape_writer.py
      quality.py
    replay/
      __init__.py
      engine.py
      fills.py
      metrics.py
      runner.py
      splits.py
      optimizer.py
    strategy/
      __init__.py
      base.py
      simple.py
      breakout_retest.py
      regime.py
      ai_label_baseline.py
    simulate/
      __init__.py
      ledger.py
      portfolio.py
      brokerless_executor.py
    reports/
      __init__.py
      daily.py
      replay_report.py
      leaderboard.py
      data_quality.py
    cron/
      __init__.py
      watchdog.py
  scripts/
    tvbot-v2-inspect-local-supabase
    tvbot-v2-export-feed
    tvbot-v2-build-tape
    tvbot-v2-data-quality
    tvbot-v2-replay
    tvbot-v2-replay-matrix
    tvbot-v2-simulate-once
    tvbot-v2-daily-report
    tvbot-v2-watchdog
  tests/
    fixtures/
    test_config_safety.py
    test_redaction.py
    test_schema.py
    test_local_supabase_export.py
    test_feed_normalize.py
    test_data_quality.py
    test_replay_engine.py
    test_fill_model.py
    test_strategy_simple.py
    test_sim_ledger.py
    test_reports.py
```

## 12. Implementation Phases

## Phase 0 — Planning, archive notes, and safety baseline

**Objective:** Freeze legacy execution and document the pivot.

**Files:**

- Already created: `documentation/python-codex-v2-migration-plan.md`
- Create: `v2/README.md`
- Create: `v2/docs/archive-notes/kalshi.md`
- Create: `v2/docs/archive-notes/polymarket.md`
- Create: `v2/.env.example`
- Create: `v2/config.example.toml`

**Steps:**

1. Create `v2/` skeleton directories.
2. Write `v2/README.md` explaining v2 is active replay-first system.
3. Write archive note for Kalshi:
   - repo path,
   - process lessons to port,
   - data/run artifact paths if known,
   - warning: no service stops without approval.
4. Write archive note for Polymarket with same shape.
5. Add `.env.example` with placeholders:
   - `TVBOT_V2_SUPABASE_URL=http://192.168.0.35:8000`
   - `TVBOT_V2_SUPABASE_KEY=`
   - `TVBOT_V2_PGHOST=`
   - `TVBOT_V2_PGPORT=`
   - `TVBOT_V2_PGDATABASE=`
   - `TVBOT_V2_PGUSER=`
   - `TVBOT_V2_PGPASSWORD=`
6. Add `config.example.toml` with safe defaults.

**Safe config defaults:**

```toml
[mode]
dry_run = true
simulation_only = true
broker_orders_enabled = false
live_trading_enabled = false

[data]
supabase_url = "http://192.168.0.35:8000"
feed_db = "feed/tradingview.sqlite3"

[replay]
default_slippage_bps = 1.0
default_fee_per_trade = 0.0
fill_model = "next_bar_open"

[cron]
watchdog_silent_on_ok = true
```

**Verification:**

```bash
cd /home/matt/workspace/tradingview-bot
find v2 -maxdepth 3 -type f | sort
```

**Gate:**

- No legacy execution files changed.
- No secrets committed.
- No old bot services stopped.

---

## Phase 1 — v2 package skeleton and config safety

**Objective:** Establish importable package, config loader, and redaction.

**Files:**

- `v2/pyproject.toml`
- `v2/src/tvbot_v2/__init__.py`
- `v2/src/tvbot_v2/config.py`
- `v2/src/tvbot_v2/redaction.py`
- `v2/tests/test_config_safety.py`
- `v2/tests/test_redaction.py`

**Steps:**

1. Add `pyproject.toml` with pytest and package config.
2. Implement config dataclasses/Pydantic models.
3. Load TOML plus env overrides.
4. Enforce safety invariants:
   - `broker_orders_enabled` must be false in MVP.
   - `live_trading_enabled` must be false in MVP.
   - missing Supabase credentials allowed for tests/dry config, but live inspect should fail cleanly.
5. Implement `redact_secret_text()`.
6. Add tests for config and redaction.

**Verification:**

```bash
cd /home/matt/workspace/tradingview-bot/v2
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
pytest -q
```

**Gate:**

- Tests pass.
- Redaction catches service-role style keys, DB passwords, bearer tokens, URLs with query secrets.

---

## Phase 2 — SQLite schema and DB helpers

**Objective:** Create canonical feed/run schema foundation.

**Files:**

- `v2/src/tvbot_v2/schema.sql`
- `v2/src/tvbot_v2/db.py`
- `v2/src/tvbot_v2/trace.py`
- `v2/tests/test_schema.py`

**Schema v1:**

Feed tables:

- `raw_source_row`
- `bar`
- `chart_snapshot`
- `legacy_ai_label`
- `legacy_trade_result`
- `feed_export_run`
- `data_quality_check`

Replay/run tables:

- `replay_signal`
- `replay_decision`
- `replay_order_intent`
- `replay_fill`
- `replay_position_snapshot`
- `replay_risk_event`
- `replay_metric`

Simulation tables:

- `sim_run`
- `sim_account`
- `sim_position`
- `sim_order_intent`
- `sim_fill`
- `sim_trade`
- `sim_risk_event`

**Steps:**

1. Write `schema.sql` with constraints and indexes.
2. Implement `connect_db(path)`.
3. Implement `init_db(path)` idempotently.
4. Implement atomic JSON write helper for metrics/checkpoints.
5. Implement trace ID helper.

**Verification:**

```bash
pytest -q tests/test_schema.py
python -m tvbot_v2.db --init feed/tradingview.sqlite3
sqlite3 feed/tradingview.sqlite3 '.tables'
```

**Gate:**

- Schema init is repeatable.
- Uniqueness prevents duplicate bars.

---

## Phase 3 — Local Supabase inspector

**Objective:** Safely inspect i732 local Supabase.

**Files:**

- `v2/src/tvbot_v2/supabase_local/client.py`
- `v2/src/tvbot_v2/supabase_local/inspect.py`
- `v2/scripts/tvbot-v2-inspect-local-supabase`
- `v2/tests/test_local_supabase_export.py`

**Steps:**

1. Implement REST client wrapper for Supabase/PostgREST.
2. Implement optional Postgres connection helper.
3. Implement table inventory:
   - table exists,
   - row count,
   - columns/sample redacted shape,
   - timestamp min/max where known.
4. Redact all config in CLI output.
5. Add mocked tests.

**Verification:**

```bash
pytest -q tests/test_local_supabase_export.py
python scripts/tvbot-v2-inspect-local-supabase --mode rest --redacted
```

**Gate:**

- No secret values printed.
- If credentials missing, error explains missing env vars without printing values.

---

## Phase 4 — Export and normalize feed

**Objective:** Build canonical TradingView feed DB from local Supabase.

**Files:**

- `v2/src/tvbot_v2/supabase_local/export.py`
- `v2/src/tvbot_v2/feed/normalize.py`
- `v2/src/tvbot_v2/feed/tape_writer.py`
- `v2/src/tvbot_v2/feed/quality.py`
- `v2/scripts/tvbot-v2-export-feed`
- `v2/scripts/tvbot-v2-build-tape`
- `v2/scripts/tvbot-v2-data-quality`
- `v2/tests/test_feed_normalize.py`
- `v2/tests/test_data_quality.py`

**Steps:**

1. Export from `tv_datafeed_5m`, `tv_datafeed_15m`, `tv_datafeed_30m`.
2. Store raw rows in `raw_source_row` with source table and row hash.
3. Normalize to `bar`.
4. Export `chart_analysis` to `chart_snapshot` if available.
5. Export `ai_trading_log`/`ai_trade_feed` to `legacy_ai_label`.
6. Export `trade_results` to `legacy_trade_result` as legacy ProjectX-derived outcome labels.
7. Generate data-quality checks.

**Verification:**

```bash
python scripts/tvbot-v2-export-feed --since 2026-01-01 --db feed/tradingview.sqlite3
python scripts/tvbot-v2-build-tape --db feed/tradingview.sqlite3
python scripts/tvbot-v2-data-quality --db feed/tradingview.sqlite3 --out reports/data-quality.md
pytest -q tests/test_feed_normalize.py tests/test_data_quality.py
```

**Gate:**

- Canonical feed has nonzero bars for at least one timeframe.
- Re-running export does not duplicate bars.
- Gaps/malformed rows are reported, not silently ignored.

---

## Phase 5 — Replay engine MVP

**Objective:** Replay a baseline strategy with conservative fills.

**Files:**

- `v2/src/tvbot_v2/models.py`
- `v2/src/tvbot_v2/replay/engine.py`
- `v2/src/tvbot_v2/replay/fills.py`
- `v2/src/tvbot_v2/replay/metrics.py`
- `v2/src/tvbot_v2/replay/runner.py`
- `v2/src/tvbot_v2/strategy/base.py`
- `v2/src/tvbot_v2/strategy/simple.py`
- `v2/scripts/tvbot-v2-replay`
- `v2/tests/test_replay_engine.py`
- `v2/tests/test_fill_model.py`
- `v2/tests/test_strategy_simple.py`

**Steps:**

1. Define core models:
   - `MarketBar`
   - `MarketState`
   - `StrategySignal`
   - `OrderIntent`
   - `Fill`
   - `PortfolioState`
2. Implement replay loop over ordered bars.
3. Ensure strategy only sees current/past bars.
4. Implement next-bar-open market fill.
5. Implement fees/slippage.
6. Implement simple baseline strategy.
7. Write immutable run output.

**Verification:**

```bash
python scripts/tvbot-v2-replay \
  --db feed/tradingview.sqlite3 \
  --strategy simple \
  --symbol MES \
  --timeframe 5m \
  --start 2026-01-01 \
  --end 2026-02-01
pytest -q tests/test_replay_engine.py tests/test_fill_model.py tests/test_strategy_simple.py
```

Expected run artifacts:

```text
runs/simple/<run-id>/config.toml
runs/simple/<run-id>/results.sqlite3
runs/simple/<run-id>/metrics.json
runs/simple/<run-id>/report.md
```

**Gate:**

- No lookahead tests pass.
- Metrics include fill assumptions.

---

## Phase 6 — Replay discipline: splits, sweeps, leaderboard

**Objective:** Port Kalshi/Polymarket replay discipline.

**Files:**

- `v2/src/tvbot_v2/replay/splits.py`
- `v2/src/tvbot_v2/replay/optimizer.py`
- `v2/src/tvbot_v2/reports/leaderboard.py`
- `v2/scripts/tvbot-v2-replay-matrix`

**Steps:**

1. Add chronological train/validation/test split helper.
2. Add bounded parameter sweep runner.
3. Write atomic checkpoints after each candidate.
4. Promote validation winners to test once.
5. Build leaderboard from `runs/*/metrics.json`.
6. Rank by deployability metrics:
   - net PnL after fees,
   - max drawdown,
   - worst day/session,
   - profit factor,
   - trade count,
   - exposure time,
   - validation-to-test degradation,
   - data-quality flags.

**Verification:**

```bash
python scripts/tvbot-v2-replay-matrix --db feed/tradingview.sqlite3 --strategies simple --timeframe 5m
python -m tvbot_v2.reports.leaderboard --runs runs --out reports/leaderboard.md
```

**Gate:**

- No strategy called deployable without validation/test evidence.

---

## Phase 7 — Strategy ports from v1 concepts

**Objective:** Move useful v1 strategy/scanner concepts into pure replayable modules.

**Files:**

- `v2/src/tvbot_v2/strategy/breakout_retest.py`
- `v2/src/tvbot_v2/strategy/regime.py`
- `v2/src/tvbot_v2/strategy/ai_label_baseline.py`
- corresponding tests

**Steps:**

1. Port `market_scanner_breakout_retest.py` concepts into pure strategy code.
2. Rebuild `market_regime.py` concepts cleanly rather than importing broken file.
3. Add `ai_label_baseline` to replay historical v1 AI decisions as labels/outcomes.
4. Add tests for each rule gate.
5. Run replay matrix.

**Gate:**

- Strategies do not call DB/API directly.
- Strategies record blocked reasons.

---

## Phase 8 — Brokerless simulation over latest feed

**Objective:** Simulate the best replay-tested strategy over new data.

**Files:**

- `v2/src/tvbot_v2/simulate/ledger.py`
- `v2/src/tvbot_v2/simulate/portfolio.py`
- `v2/src/tvbot_v2/simulate/brokerless_executor.py`
- `v2/scripts/tvbot-v2-simulate-once`
- `v2/tests/test_sim_ledger.py`

**Steps:**

1. Create persistent sim ledger.
2. Track last processed bar per strategy/symbol/timeframe.
3. Process only new bars.
4. Apply replay-compatible fill model.
5. Persist decisions/fills/positions.
6. Generate sim summary.

**Verification:**

```bash
python scripts/tvbot-v2-simulate-once --db feed/tradingview.sqlite3 --strategy simple --timeframe 5m
pytest -q tests/test_sim_ledger.py
```

**Gate:**

- No broker/order modules exist in sim execution path.

---

## Phase 9 — Reports and watchdogs

**Objective:** Make operations visible before any dashboard.

**Files:**

- `v2/src/tvbot_v2/reports/daily.py`
- `v2/src/tvbot_v2/reports/replay_report.py`
- `v2/src/tvbot_v2/reports/data_quality.py`
- `v2/src/tvbot_v2/cron/watchdog.py`
- `v2/scripts/tvbot-v2-daily-report`
- `v2/scripts/tvbot-v2-watchdog`

**Steps:**

1. Daily report includes:
   - feed freshness,
   - export status,
   - replay runs,
   - leaderboard changes,
   - sim ledger summary,
   - data-quality warnings,
   - watchdog alerts.
2. Watchdog checks:
   - feed stale,
   - export failed,
   - replay failed,
   - data-quality severe,
   - unexpected broker path present/enabled.
3. Watchdog silent on OK.

**Verification:**

```bash
python scripts/tvbot-v2-daily-report --out reports/daily/latest.md
python scripts/tvbot-v2-watchdog
```

**Gate:**

- Report is readable from Telegram/Discord markdown.
- Watchdog produces no output when healthy.

---

## Phase 10 — Hermes cron rollout

**Objective:** Replace APScheduler/n8n scheduling with safe one-shot jobs.

Suggested jobs:

1. Feed export:
   - schedule: every 5m or market-hours cadence.
   - command: `tvbot-v2-export-feed`.
2. Data quality:
   - after export.
   - command: `tvbot-v2-data-quality`.
3. Simulation:
   - after export, only once replay evidence exists.
   - command: `tvbot-v2-simulate-once`.
4. Daily report:
   - after market close.
5. Watchdog:
   - every 15m, silent-on-ok.

**Gate:**

- Cron jobs collect/report/simulate only.
- No broker submission cron exists.

---

## Phase 11 — Optional read-only dashboard

Only after report loop is trustworthy.

Requirements:

- Reads local feed DB and run artifacts only.
- No broker routes.
- No order routes.
- No external API calls at render time except local DB reads.
- Shows feed freshness, run leaderboard, latest sim state, data-quality warnings.

---

## Phase 12 — Future broker adapter, separate PRD only

If later requested, create a new PRD for paper/live broker integration.

Must include:

- broker target,
- paper-only first,
- hard config/CLI gates,
- manual approval,
- caps and kill switch,
- reconciliation,
- paper evidence review,
- separate live activation plan.

Not part of v2 MVP.

---

# Part 3 — Development Workflow

## 13. Codex/Hermes implementation workflow

Use Codex as a coding assistant, not a trading authority.

Recommended loop per phase:

1. Create/confirm failing tests.
2. Ask Codex to implement one small phase/task.
3. Run tests locally.
4. Review diff for secrets and forbidden imports.
5. Commit after passing tests.

Codex prompt must never include:

- local Supabase service-role key,
- DB password,
- broker credentials,
- webhook secrets,
- `.env` contents.

## 14. Forbidden imports/checks

V2 core should not import:

- `auth.py`
- `signalr_listener.py`
- ProjectX order functions from legacy `api.py`
- legacy `tradingview_projectx_bot.py` execution path

Add a test or grep check:

```bash
grep -R "from auth\|import auth\|signalr_listener\|place_market\|flatten_contract" v2/src v2/tests
```

Expected: no forbidden imports except explicit documentation strings/tests for the check itself.

## 15. Commit strategy

Commit after each phase:

- `docs: add tradingview v2 prd and migration plan`
- `feat(v2): add config and redaction safety gates`
- `feat(v2): add canonical feed schema`
- `feat(v2): inspect local supabase source`
- `feat(v2): export tradingview feed tape`
- `feat(v2): add replay engine baseline`
- `feat(v2): add replay splits and leaderboard`
- `feat(v2): add brokerless simulation ledger`
- `feat(v2): add reports and watchdogs`

## 16. MVP Definition of Done

MVP is complete when:

- TradingView Bot v2 is the active project shape.
- Kalshi/Polymarket are archived as process/reference systems.
- Local Supabase inspection works with redacted output.
- TradingView datafeed exports to canonical SQLite tape.
- Data-quality report shows row counts, coverage, gaps, malformed rows.
- At least one strategy replays without lookahead.
- Replay artifacts are immutable and include config, metrics, results DB, report.
- Replay metrics include fees/slippage assumptions.
- Brokerless simulation processes latest bars.
- Hermes cron can run export/report/watchdog jobs.
- No n8n runtime dependency.
- No Topstep/ProjectX/SignalR in v2 core.
- No paper/live broker order path exists in MVP.

## 17. First Implementation Slice

After approving this PRD/plan, implement only this slice first:

1. Create `v2/` skeleton.
2. Add `pyproject.toml`.
3. Add safe config/redaction modules.
4. Add `.env.example` and `config.example.toml`.
5. Add archive notes for Kalshi/Polymarket.
6. Add tests for config/redaction.
7. Run tests.

Commands:

```bash
cd /home/matt/workspace/tradingview-bot
mkdir -p v2/src/tvbot_v2/{supabase_local,feed,replay,strategy,simulate,reports,cron} \
  v2/scripts v2/tests/fixtures v2/feed v2/runs v2/logs v2/docs/archive-notes
cd v2
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install pytest pydantic python-dotenv requests 'psycopg[binary]'
```

Do not run the legacy bot. Do not connect to broker/order endpoints. Do not paste secrets into docs/prompts.
