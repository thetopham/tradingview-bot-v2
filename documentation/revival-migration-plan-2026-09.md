# TradingView Bot v2 revival and migration plan

**Updated:** 2026-09-23
**Status:** Offline reconstruction working; live simulated decision loop still to build.

This plan supersedes the earlier no-n8n runtime assumption in the May 2026 migration documents. The user now wants to try the ProDex model in self-hosted n8n first, while v2 owns the feed, five simulated account ledgers, rule checks, and evaluation. TypeSafe Jev is a later classifier sidegrade and split test. There are no current Topstep accounts to connect. Do not start the legacy ProjectX order path.

## What is recoverable now

- Legacy source: [tradingview-bot](https://github.com/thetopham/tradingview-bot). V2 source and the earlier migration specification: [tradingview-bot-v2](https://github.com/thetopham/tradingview-bot-v2).
- Supplied 30-minute workflow: `simple-30m-epsilon-1-13-2026-optimize`. It is a MES overseer that returns `BUY`, `SELL`, `HOLD`, or `FLAT`, a bracket-choice code `1..3`, reason, invalidation, and a short next check. Its `size` was coupled to approximately $30 stop / $60 target, not free leverage.
- Supplied practice workflow file: `simple-5m-practice-1-6-2026-optimize.json`. It runs the same overseer shape every **5 minutes** and queries `tv_datafeed_5m`. Its active LLM connection is an OpenAI Chat Model; the supplied 30-minute epsilon workflow's active connection is ProDex. The practice prompt embedded in that file says `simple-5m-practice-1-13-2026-optimize`; the filename and prompt version differ. The 5-minute practice log has 16,239 rows with that embedded version and 552 with `simple-5m-practice-1-6-2026-continuity`. Preserve the recorded version per decision.
- CSVs supplied: `ai_trading_log_rows`, `trade_results_rows`, `ai_trade_feed_rows`, and `tv_datafeed_30m_rows`. `ai_trade_feed_rows` is a joined log/result convenience view; retain the original decision and result tables as separate evidence.
- The old prompt's full 5-minute derived snapshot, chart image, bracket context, and position/account context are **not** present in the exported 30-minute decision rows. Existing decisions can be replayed as a tape; the original model decision cannot be independently regenerated from these CSVs.
- The historical alpha, beta, gamma, and delta logs end earlier than epsilon; their ends do not prove when accounts were canceled. The user's remembered break-even month and the partial exports describe different windows and should not be merged into a single performance claim.

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
  -> five independent Combine account ledgers
  -> conservative fill/rule engine
  -> immutable runs, comparisons, and reports
  -> later Jev classifier variant on the same snapshots
```

The decision adapter may use the Pi's existing n8n installation (`n8n.thetopham.com`) and the exported overseer workflow as a specification. V2 must pass explicit bar timestamp, account state, current position, bracket choices, and prompt/model version; reject stale, malformed, or duplicate responses. A response must never call a ProjectX or Topstep order endpoint. Record model responses so replay can use the same decision tape without calling a model again. For comparison, use the same market windows, fees, fills, and rules for ProDex-only, deterministic reference, and ProDex plus Jev-gate variants. Avoid charging five model calls merely because there are five ledgers if the decision inputs are identical.

The later Jev adapter is already a bounded **candidate approval gate**. It is not a substitute for the ProDex overseer or for deterministic account rules. Jev receives only the closed-bar candidate state and returns a typed `approve/reject` choice with probability and confidence; missing or uncertain responses block new entries. Its live API has not been called. See [TypeSafe's Choice API](https://docs.typesafe.ai/introduction/quickstart).

## Work sequence and exit criteria

1. **Recovery and audit — partly done.** Import CSVs idempotently; keep raw rows and normalize bars; match decisions to bar closes with a strict delay bound; reconcile decision IDs to trade results. Obtain `tv_datafeed_5m` and any archived chart/position snapshots before interpreting the practice workflow. Exit when each included decision has an input provenance record and all exclusions have counts.
2. **Combine simulator — first pass done.** Extend the current five-ledger replay with session calendars, explicit missing-data handling, fee/slippage sensitivity, intrabar MLL edge cases, day consistency, and account pass/fail tests. Validate against hand-built rule scenarios and any surviving known Topstep trade sequences. Exit when ledgers are deterministic and rule boundaries are covered.
3. **ProDex decision-only loop.** Clone the supplied 30-minute workflow into a decision-only simulation workflow in the Pi n8n instance; remove old execution and broker-state dependencies. Feed v2-generated snapshots and simulated position context. Persist every request and response with idempotency keys. Start with a forward shadow run before any performance interpretation. Keep the five account ledgers separate.
4. **Comparison and optimization.** Use chronological train/validation/test windows, identical market data and execution assumptions, costs, drawdown, pass rate, best-day concentration, and data-exclusion rate. Do not optimize bracket settings on the final holdout window. Report each account variant and the common decision source separately.
5. **Jev sidegrade.** Record Jev answers beside ProDex decisions on the same forward snapshots, with a capped call budget. Compare ProDex-only to Jev-gated decisions and probability thresholds; keep deterministic MLL/session controls outside both models.
6. **Only after sustained simulated evidence:** decide whether to open a new Combine and design a separate, explicitly reviewed broker adapter. Current work has no route to place orders.

## Commands for the local offline baseline

```powershell
python -m tvbot_v2.data_cli import-feed --csv <tv_datafeed_30m_rows.csv> --table tv_datafeed_30m --db feed/tradingview.sqlite3
python -m tvbot_v2.data_cli recover-signals --csv <ai_trading_log_rows.csv> --db feed/tradingview.sqlite3 --account epsilon --timeframe 30m --prompt-version simple-30m-epsilon-1-13-2026-optimize --output runs/recovered-epsilon-30m.jsonl
python -m tvbot_v2.cli --db feed/tradingview.sqlite3 --timeframe 30m --signals runs/recovered-epsilon-30m.jsonl --output runs/epsilon-five-variant
```

Feed DB, recovered tape, and run artifacts remain ignored by Git. No keys or raw account data belong in the repository.
