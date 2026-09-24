# Simulated broker runbook

The broker is in `src/tvbot_v2/simulate/` inside tradingview-bot-v2. It keeps each demo account in an independent SQLite ledger. There is no account-count limit in the software; storage, feed quality, and model cost set practical limits. The five starter profiles alpha, beta, gamma, delta, and epsilon are provisional experiments, not restored Topstep accounts. Add more accounts by registering another JSON portfolio. The account name is the stable key. A changed profile requires a new name or a future explicit migration.

## Start and inspect

Run from the repository root after installing the package (`pip install -e .`):

```powershell
python -m tvbot_v2.simulate --db data/sim_broker.sqlite init
python -m tvbot_v2.simulate --db data/sim_broker.sqlite status
python -m tvbot_v2.simulate --db data/sim_broker.sqlite history epsilon --limit 20
```

`init` is safe to repeat. It adds missing profiles and rejects conflicting settings on an existing account. For additional accounts, pass `init --portfolio profiles.json`; that JSON is an array of profiles:

`profiles/epsilon-30m-1m-execution.json` adds an independent `epsilon_1m` account. Its ProDex decision cadence remains 30 minutes; `execution_timeframe: "1m"` uses the one-minute feed for fills, brackets, and risk checks. The original `epsilon` account remains a 30-minute execution baseline. Run `init --portfolio profiles/epsilon-30m-1m-execution.json` before restarting the legacy bridge so the new account appears in its compatibility map.

```json
[
  {
    "name": "prodex_30m_risk_b",
    "timeframe": "30m",
    "strategy_id": "prodex_prompt_v2",
    "brackets": {
      "1": {"contracts": 1, "stop_ticks": 24, "target_ticks": 48},
      "2": {"contracts": 2, "stop_ticks": 12, "target_ticks": 24},
      "3": {"contracts": 3, "stop_ticks": 8, "target_ticks": 16}
    }
  }
]
```

`size` in a model decision selects a configured bracket. It is never interpreted as unrestricted leverage. One MES tick is 0.25 index points; the starter bracket choices have a gross $30 stop and $60 target before slippage and fees. The profile can choose different brackets within the 50-micro Combine cap.

To test a recorded strategy on historical bars in the persistent broker, use the imported canonical feed and its recovered decision tape. Run each strategy in its own named account; use a new database or reset for a different trial. `--limit` is available for a short smoke test.

```powershell
python -m tvbot_v2.simulate --db data/sim_broker.sqlite replay epsilon --feed-db feed/tradingview.sqlite3 --signals runs/recovered-epsilon-30m.jsonl --limit 100
```

The replay command also accepts `--csv bars.csv` instead of `--feed-db`. A missing signal becomes HOLD. The recovered tape's old bracket code is mapped to `size`; it does not reconstruct the original broker fill or the model input snapshot.

## Advance one closed bar

Pass one envelope per account per **closed** bar. `bar.timestamp` is the bar **open** instant with a UTC offset. Do not submit the still-forming candle. Supply `decision` only after the model has observed that closed bar. A missing or failed model response is represented by no `decision`, which records HOLD.

```json
{
  "account": "epsilon",
  "bar": {
    "timestamp": "2026-09-23T14:00:00Z",
    "open": 6000.0,
    "high": 6001.0,
    "low": 5999.0,
    "close": 6000.5,
    "volume": 1200
  },
  "decision": {
    "account": "epsilon",
    "timeframe": "30m",
    "bar_ts": "2026-09-23T14:00:00Z",
    "signal": "BUY",
    "size": 1,
    "source": "prodex",
    "prompt_version": "simple-30m-epsilon-1-13-2026-optimize",
    "reason": "Trend resumed above VWAP",
    "invalidation": "A close below VWAP would invalidate the setup",
    "next_check": "Check whether the position holds above VWAP."
  }
}
```

```powershell
python -m tvbot_v2.simulate --db data/sim_broker.sqlite step --input closed-bar-and-decision.json
python -m tvbot_v2.simulate --db data/sim_broker.sqlite pause epsilon
python -m tvbot_v2.simulate --db data/sim_broker.sqlite resume epsilon
python -m tvbot_v2.simulate --db data/sim_broker.sqlite settle epsilon
python -m tvbot_v2.simulate --db data/sim_broker.sqlite reset epsilon --reason "new experiment"
```

The BUY above queues an intent. The earliest fill is the next contiguous 30-minute bar open. Duplicate submission of the same bar and decision returns the stored snapshot; a changed duplicate or older bar is rejected. A data gap cancels a pending entry and flattens an open position at the last known close, with a risk event. Manual pause blocks new entries but does not disable stop, target, session, or explicit FLAT exits. Reset requires a flat account and retains past generations, trades, and events.

For profiles with a faster execution feed, submit the closed strategy decision separately with `SimBroker.submit_decision`. It records when the decision became available and queues it for the next execution bar **open after that time**. Advance the account on each closed one-minute candle with `process_bar` and no decision. A missing minute cancels pending entries and flattens any open position at the last known close. This avoids filling a 1-minute candle whose open preceded the ProDex response.

For the original Flask scheduler/overseer path, use `profiles/broker-demo-1m.json` in a **new** broker database. It registers alpha through epsilon as independent demo accounts with their original decision cadences and one-minute execution. Additional accounts can be registered with another portfolio JSON; there is no fixed account count. `SimBroker.submit_order` accepts a BUY, SELL, or FLAT after the overseer returns, using the receipt time to select the next one-minute open. A caller-provided `client_order_id` makes retries idempotent. The one-minute feed alone advances fills, brackets, equity, and Combine risk. Existing ledgers remain untouched when a new database is used.

`status` returns each account's balance, marked equity, end-of-day balance peak, current-day P&L, maximum-loss floor and remaining room, win rate, consecutive losses, open position, pending intent, and last `next_check`. The legacy bridge sends these fields as account context to a configured ProDex overseer.

## Rules and boundaries

The default model approximates the current **50K Trading Combine**: $50,000 start, $2,000 trailing maximum loss, $3,000 nominal target, 55% best-day consistency, 50 MES micros maximum, and a 3:10 PM Central flat cutoff. The maximum-loss floor trails end-of-day balance, locks at $50,000, and is also checked against adverse intrabar marks. Profit target/pass status is calculated at the next trading-day rollover or by `settle` after that day's 3:10 PM Central close; an account near target during the current day remains active until settlement. Fees default to $1.22 per MES round turn and fills assume one adverse tick of slippage. If a candle touches stop and target, the stop wins. These are research assumptions and can differ from actual fills.

The broker accepts caller-provided closed bars and recorded decisions. The separate legacy bridge on the Pi receives n8n's post-insert feed rows and calls the numeric ProDex overseer; the broker itself has no network order endpoint. The expired chart-image service is outside the numeric simulator. XFA funded-account rules need a separate implementation; a passed Combine does not automatically create an XFA.

Rule references: [Trading Combine parameters](https://help.topstep.com/en/articles/8284197-trading-combine-parameters), [maximum loss](https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit), [consistency](https://help.topstep.com/en/articles/8284208-consistency-at-topstep), [trading hours](https://help.topstep.com/en/articles/8284206-when-and-what-products-can-i-trade).
