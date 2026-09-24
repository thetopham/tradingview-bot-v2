# TradingView Bot v2 revival and migration plan

> **Historical recovery snapshot.** This plan records the September 23 reconstruction and its then-pending tasks. The Pi now runs a Flask bridge, ten one-minute-execution demo accounts, ProDex numeric and chart-image workflows, an authenticated dashboard, and a read-only strategy farm. For current operation, use the [user guide](user-guide.md), [broker runbook](simulated-broker-runbook.md), and [strategy farm](strategy-farm.md). Treat dates, account counts, workflow statuses, and "still to build" statements below as historical.

**Updated:** 2026-09-23
**Status at this snapshot:** Offline reconstructions and local persistent simulated broker working; live feed and ProDex loop were still to build. See the current guide above for their later implementation.

This plan supersedes the earlier no-n8n runtime assumption in the May 2026 migration documents. The user now wants to try the ProDex model in self-hosted n8n first, while v2 owns the feed, any number of independent simulated account ledgers, rule checks, and evaluation. Alpha through epsilon are starter profiles, not an account limit. TypeSafe Jev is a later classifier sidegrade and split test. There are no current Topstep accounts to connect. Do not start the legacy ProjectX order path.

## What is recoverable now

- Legacy source: [tradingview-bot](https://github.com/thetopham/tradingview-bot). V2 source and the earlier migration specification: [tradingview-bot-v2](https://github.com/thetopham/tradingview-bot-v2).
- Supplied 30-minute workflow: `simple-30m-epsilon-1-13-2026-optimize`. It is a MES overseer that returns `BUY`, `SELL`, `HOLD`, or `FLAT`, a bracket-choice code `1..3`, reason, invalidation, and a short next check. Its `size` was coupled to approximately $30 stop / $60 target, not free leverage. Its node is named `5m derived snapshot` and its prompt calls the numeric snapshot “5m,” **but its connected Supabase node actually queries `tv_datafeed_30m`**. Preserve that naming mismatch as historical evidence; do not assume the decisions used 5-minute candles.
- Supplied practice workflow file: `simple-5m-practice-1-6-2026-optimize.json`. It runs the same overseer shape every **5 minutes** and queries `tv_datafeed_5m`. Its current LLM connection is an OpenAI Chat Model; the current 30-minute epsilon workflow's connection is ProDex, which the user installed on September 23. **The January-April epsilon decisions were not ProDex decisions.** The practice prompt embedded in that file says `simple-5m-practice-1-13-2026-optimize`; the filename and prompt version differ. The 5-minute practice log has 16,239 rows with that embedded version and 552 with `simple-5m-practice-1-6-2026-continuity`. Preserve the recorded version per decision. The two workflow feeds share a similar schema, but the supplied 30-minute CSV's `timeframe=30` and roughly 30-minute spacing establish it as a separate tape.
- CSVs supplied: `ai_trading_log_rows`, `trade_results_rows`, `ai_trade_feed_rows`, `tv_datafeed_30m_rows`, and `tv_datafeed_5m_rows`. `ai_trade_feed_rows` is a joined log/result convenience view; retain the original decision and result tables as separate evidence.
- The old prompt's full derived snapshot, chart image, bracket context, and position/account context are **not** present in the exported decision rows. Existing decisions can be replayed as tapes; the original model decisions cannot be independently regenerated from these CSVs.
- The historical alpha, beta, gamma, and delta logs end earlier than epsilon; their ends do not prove when accounts were canceled. The user's remembered break-even month and the partial exports describe different windows and should not be merged into a single performance claim.

### Pi n8n review (read only)

The Pi has 77 workflow definitions. The current alpha, beta, gamma, and delta overseer workflows are inactive. Epsilon, practice, and `sim001` are marked active, but the Pi's present execution database has **zero saved executions** for those overseers. Its active `datafeed` workflow has 3,857 saved executions from September 21-23, 2026. The Pi database therefore does not currently provide January-April overseer execution payloads. Current epsilon routes ProDex into `Basic LLM Chain`; practice routes an OpenAI Chat Model. The old overseers also read and write `ai_trading_log` and `charts`, so the new simulation workflow should be a separate, decision-only path. No Pi workflow was changed during this review.

The CSV logs do retain decision text, signal, prompt version, and many linked outcomes. They do **not** establish that every original input was saved: `position_context` and `market_state` are empty in the exported epsilon and practice `ai_trading_log` rows, and the joined `decision_json` does not restore those fields for those streams. Some early alpha and beta rows do contain context. The `charts` table and any older n8n storage are still worth checking before declaring chart or webhook inputs unrecoverable.

## Current offline recovery result

The v2 import retained all 6,388 supplied 30-minute raw feed rows for audit. It normalized 6,150 to known MES bar opens. It excluded 238 late receipt timestamps rather than guessing which bar they represented. From 2,748 epsilon decisions with prompt version `simple-30m-epsilon-1-13-2026-optimize`, 2,550 unique decisions aligned within 120 seconds after a known bar close; 188 did not align and 10 were duplicates of an aligned bar. The aligned tape runs from January 14 through April 7, 2026 and includes 2,140 HOLD, 229 BUY, 166 SELL, and 15 FLAT signals.

An **illustrative** replay applied that one epsilon tape to five independent 50K Combine-style account variants. It used next-bar-open execution, one adverse tick of slippage, MES fees, conservative stop-first handling when a bar could hit both brackets, and a pre-3:10 PM CT flat policy. The variants are research settings; they do not reproduce the old account sizing or fills.

| Variant | MES contracts | Stop / target (ticks) | Result | Simulated net P&L |
|---|---:|---:|---|---:|
| Conservative | 1 | 32 / 64 | Active | -$1,060.23 |
| Balanced | 2 | 32 / 64 | Failed | -$1,754.50 |
| Higher size | 4 | 32 / 64 | Failed | -$1,597.08 |
| Tight bracket | 2 | 20 / 40 | Failed | -$2,019.84 |
| Wide bracket | 2 | 48 / 96 | Failed | -$1,462.40 |

The five-account total is **-$7,894.05** under these assumptions. This is a diagnostic baseline, not a verified profit estimate or a reproduction of the original Topstep executions. The feed has missing sessions, OHLC bars do not give the intrabar path, and the old AI position context is incomplete. No real orders were placed.

The subsequently supplied `tv_datafeed_5m_rows.csv` contains 38,555 raw MES rows. Of these, 38,523 have plausible bar-close receipt times and 32 are late or malformed. Two July time slots have conflicting OHLCV values; both are now excluded from the canonical tape, leaving **38,519 unique usable bars**. From 16,239 practice decisions carrying the embedded `simple-5m-practice-1-13-2026-optimize` version, 15,318 unique decisions aligned; 881 did not align and 40 duplicated an aligned bar. The aligned tape runs January 14 through April 7, 2026, with 13,879 HOLD, 583 SELL, 573 BUY, and 283 FLAT signals.

Applying the **same illustrative risk settings** to that separate five-minute practice tape yields:

| Variant | Result | Simulated net P&L | Trades |
|---|---|---:|---:|
| Conservative | Failed | -$2,003.52 | 466 |
| Balanced | Failed | -$2,014.68 | 172 |
| Higher size | Failed | -$2,019.36 | 47 |
| Tight bracket | Failed | -$2,014.78 | 212 |
| Wide bracket | Active | -$1,619.98 | 167 |

The five-account total is **-$9,672.32** in this practice proxy. This is a different decision stream and cadence from epsilon. These two historical runs are not a controlled model comparison; ProDex was installed only on September 23. Neither run establishes how the canceled beta accounts would have performed.

### January account coverage

The logs include the five original account names as well as practice. Against the supplied five-minute bars, all 670 explicitly labeled alpha 5-minute decisions align; beta has 773 of 776 aligned; gamma has 694 of 695 aligned. Delta has 260 explicitly labeled 15-minute decisions and needs its 15-minute feed before an equivalent replay. Epsilon uses the 30-minute feed above. Earlier rows with blank timeframe or prompt version need separate provenance work; do not assign them a cadence from account name alone. The first five-account replay used one decision tape with five illustrative risk settings; it did **not** reproduce the historical alpha/beta/gamma/delta/epsilon strategy split.

Joining `trade_results_rows` to `ai_trading_log_rows` by decision ID gives a separate view of **recorded historical outcomes**, which should not be confused with the five-variant replay. The optimized epsilon stream has 277 linked result rows, all labeled epsilon, totaling **-$596.06 net** where a net value exists. The optimized practice stream has 1,021 linked result rows. Of those, 995 are labeled practice and total **-$3,096.52 net**; 26 are labeled delta and total **+$1,287.11 net**. The joined total of -$1,809.41 mixes account labels, so it must not be reported as practice-only P&L. Across all exports, 561 result rows have no matching decision ID in the supplied decision log. The `ai_trade_feed` join is useful for inspection, but its combined rows do not replace this provenance check.

## Account rules to model

Model the **current 50K Trading Combine** first. Its nominal starting balance is $50,000, profit target $3,000, and maximum loss limit $2,000. The MLL starts at $48,000, rises with end-of-day balance, never falls, and locks at $50,000. It is monitored against unrealized P&L during the day. The best profit day must be no more than 55% of total profit; otherwise the target increases. The account can hold at most 50 micros. All positions must be flat by 3:10 PM CT; trading resumes at 5 PM CT. These are current public rules, not the canceled beta account terms. See [Combine parameters](https://help.topstep.com/en/articles/8284197-trading-combine-parameters), [maximum loss limit](https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit), [consistency](https://help.topstep.com/en/articles/8284208-consistency-at-topstep), and [trading hours](https://help.topstep.com/en/articles/8284206-when-and-what-products-can-i-trade).

An Express Funded Account is a **different state machine**: its 50K label is buying power and its balance starts at $0, with an MLL initially at -$2,000. Add XFA simulation only after the Combine model and decision loop are validated. Do not automatically roll a passed Combine into an XFA without modeling its distinct payout path and limits. See [Topstep's XFA parameters](https://help.topstep.com/en/articles/8284215-express-funded-account-parameters).

## Target architecture

```text
TradingView alert / local Supabase feed
  -> immutable, timestamped MES bars + quality checks
  -> v2 snapshot builder (closed bars only, no future data)
  -> decision adapter: n8n ProDex first
  -> validated BUY / SELL / HOLD / FLAT decision record
  -> independent Combine account ledgers (any number of profiles)
  -> conservative fill/rule engine
  -> immutable runs, comparisons, and reports
  -> later Jev classifier variant on the same snapshots
```

The decision adapter may use the Pi's existing n8n installation (`n8n.thetopham.com`) and the exported overseer workflow as a specification. V2 must pass explicit bar timestamp, account state, current position, bracket choices, and prompt/model version; reject stale, malformed, or duplicate responses. A response must never call a ProjectX or Topstep order endpoint. Record model responses so replay can use the same decision tape without calling a model again. For comparison, use the same market windows, fees, fills, and rules for ProDex-only, deterministic reference, and ProDex plus Jev-gate variants. Share model decisions only when the complete input, including account state, is identical; otherwise record separate calls and costs.

### What “back online” means

1. The fresh `tvdatafeed` input continues to produce one validated, closed MES bar per expected interval; duplicate or late bars are visible as alerts.
2. A dedicated v2 simulation runner creates persistent, independently resettable 50K Combine ledgers from versioned profiles, with no fixed account-count limit. Use alpha, beta, gamma, delta, and epsilon as starter profiles. Historical alpha/beta/gamma were 5-minute, delta 15-minute, and epsilon 30-minute; preserve those as the first comparison candidates, then add new settings under new profile names.
3. At each eligible bar close, the runner provides ProDex with the bar snapshot and that account's simulated balance, position, MLL, current bracket choices, and previous check. It validates the four-signal response, records the prompt/model version, and advances the simulated ledger once. `size=1..3` remains the old bracket choice and must be translated through an explicit per-variant risk table.
4. The runner writes a durable event for every bar, decision, simulated fill, fee, rule breach, and account pass/fail. Replaying after a restart must produce the same state and must not duplicate a fill. A manual pause stops new simulated entries while preserving the ledger.
5. A read-only dashboard or daily report shows each account's balance, end-of-day peak, MLL remaining, daily P&L, best-day share, open position, trades, exclusions, and pass/fail status. A missed feed/model response produces an alert and no new entry.

The offline prototype models five generic risk settings. The persistent broker now has five named starter profiles and can register any number of additional accounts. Those settings are **not** reconstructed original settings. A passed Combine remains separate from any future XFA ledger. See the [simulated broker runbook](simulated-broker-runbook.md).

The later Jev adapter is already a bounded **candidate approval gate**. It is not a substitute for the ProDex overseer or for deterministic account rules. Jev receives only the closed-bar candidate state and returns a typed `approve/reject` choice with probability and confidence; missing or uncertain responses block new entries. Its live API has not been called. See [TypeSafe's Choice API](https://docs.typesafe.ai/introduction/quickstart).

## Work sequence and exit criteria

1. **Recovery and audit — partly done.** Both 30-minute and 5-minute feed CSVs are imported idempotently, raw rows retained, ambiguous candles excluded, decisions matched to known bar closes, and result IDs reconciled without mixing account labels. Recover the 15-minute feed for delta; inspect the Supabase `charts` table and any older n8n backup for missing snapshots. Exit when each included decision has a documented input source and all exclusions have counts.
2. **Persistent simulated broker — local core implemented.** An event-backed SQLite runner supports arbitrary independent ledgers, restart recovery, duplicate-bar protection, manual pause, and account reset/pass/fail transitions. It accepts a caller-provided closed bar and decision; it has not been deployed to the Pi or wired to live feed/ProDex. Add an XFA ruleset later as a separate state machine. Exit when restart, duplicate alert, missing bar, stop/target collision, MLL touch, session close, and consistency-target scenarios pass tests and an observed forward run.
3. **ProDex decision-only loop.** Create a dedicated simulation workflow in the Pi n8n instance, using the current epsilon ProDex connection and overseer prompt as a starting specification. Do not reuse the old `ai_trading_log` or `charts` write nodes as the simulation ledger, and do not call the legacy broker path. Feed v2-generated snapshots and each simulated account's position context. Persist every request and response with an idempotency key. Start with a forward shadow run before any performance interpretation. Keep every account ledger and variant setting separate.
4. **Forward shadow operation and comparison.** Run the simulated profiles from fresh bars without any broker route. Compare each variant on the same calendar windows using net results after fees, drawdown, pass rate, best-day concentration, and data-exclusion rate. Check daily that Supabase bars, n8n decisions, and v2 ledger events reconcile. Use chronological train/validation/test windows; do not optimize settings on the final holdout window. Exit when the loop survives restarts and missed responses, account status is explainable from ledger events, and forward results are long enough to judge beyond a few isolated trades.
5. **Jev sidegrade.** Record Jev answers beside ProDex decisions on the same forward snapshots, with a capped call budget. Compare ProDex-only to Jev-gated decisions and probability thresholds; keep deterministic MLL/session controls outside both models.
6. **Only after sustained simulated evidence:** decide whether to open a new Combine and design a separate, explicitly reviewed broker adapter. Current work has no route to place orders.

## Commands for the local offline baseline

```powershell
python -m tvbot_v2.data_cli import-feed --csv <tv_datafeed_30m_rows.csv> --table tv_datafeed_30m --db feed/tradingview.sqlite3
python -m tvbot_v2.data_cli recover-signals --csv <ai_trading_log_rows.csv> --db feed/tradingview.sqlite3 --account epsilon --timeframe 30m --prompt-version simple-30m-epsilon-1-13-2026-optimize --output runs/recovered-epsilon-30m.jsonl
python -m tvbot_v2.cli --db feed/tradingview.sqlite3 --timeframe 30m --signals runs/recovered-epsilon-30m.jsonl --output runs/epsilon-five-variant
python -m tvbot_v2.data_cli import-feed --csv <tv_datafeed_5m_rows.csv> --table tv_datafeed_5m --db feed/tradingview.sqlite3
python -m tvbot_v2.data_cli recover-signals --csv <ai_trading_log_rows.csv> --db feed/tradingview.sqlite3 --account practice --timeframe 5m --prompt-version simple-5m-practice-1-13-2026-optimize --output runs/recovered-practice-5m.jsonl
python -m tvbot_v2.cli --db feed/tradingview.sqlite3 --timeframe 5m --signals runs/recovered-practice-5m.jsonl --output runs/practice-five-variant
python -m tvbot_v2.data_cli reconcile-results --decisions <ai_trading_log_rows.csv> --results <trade_results_rows.csv> --output runs/reconciled-trade-results.json
```

Feed DB, recovered tape, and run artifacts remain ignored by Git. No keys or raw account data belong in the repository.
