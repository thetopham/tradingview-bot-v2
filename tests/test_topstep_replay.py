from datetime import datetime, timedelta, timezone

import pytest

from tvbot_v2.replay.topstep import (
    Account, AccountVariant, Bar, CombineRules, SignalTape, _finalize_day, simulate, trade_day,
)
from tvbot_v2.strategy.jev import BaselineGate, FixtureGate, parse_jev_answer
from tvbot_v2.cli import main


def bars(prices, start="2026-09-22T14:00:00+00:00"):
    ts = datetime.fromisoformat(start)
    return [Bar(ts + timedelta(minutes=5 * i), *ohlc) for i, ohlc in enumerate(prices)]


def variants(quantity=1, stop=4, target=4):
    return tuple(AccountVariant(f"account_{i}", quantity, stop, target) for i in range(5))


def tape(bar, signal):
    return SignalTape([{"bar_ts": bar.ts.isoformat(), "signal": signal}])


def test_five_accounts_replay_next_bar_and_fee():
    feed = bars([(5000, 5000, 5000, 5000), (5000, 5000.5, 5000, 5000.5)])
    result = simulate(feed, BaselineGate(), variants=variants(), signal_tape=tape(feed[0], "BUY"))
    assert len(result["accounts"]) == 5
    assert all(account["trade_count"] == 1 for account in result["accounts"])
    assert result["accounts"][0]["trades"][0]["entry_price"] == 5000.25
    assert result["accounts"][0]["trades"][0]["reason"] == "end_of_data"
    assert result["accounts"][0]["net_pnl"] == pytest.approx(-1.22)
    assert result["combined_net_pnl"] == pytest.approx(-6.10)


def test_ambiguous_bar_charges_stop_first():
    feed = bars([(5000, 5000, 5000, 5000), (5000, 5002, 4998, 5001)])
    result = simulate(feed, BaselineGate(), variants=variants(stop=4, target=4),
                      signal_tape=tape(feed[0], "BUY"))
    trade = result["accounts"][0]["trades"][0]
    assert trade["reason"] == "stop_first_if_ambiguous"
    assert trade["gross_pnl"] < 0


def test_recorded_flat_closes_on_next_bar_open():
    feed = bars([(5000, 5000, 5000, 5000), (5000, 5000, 5000, 5000),
                 (5001, 5001, 5001, 5001), (5002, 5002, 5002, 5002)])
    decisions = SignalTape([{"bar_ts": feed[0].ts.isoformat(), "signal": "BUY"},
                            {"bar_ts": feed[2].ts.isoformat(), "signal": "FLAT"}])
    result = simulate(feed, BaselineGate(), variants=variants(stop=100, target=100),
                      signal_tape=decisions)
    trade = result["accounts"][0]["trades"][0]
    assert trade["reason"] == "signal_flat"
    assert trade["exit_ts"] == feed[3].ts.isoformat()


def test_trade_day_rolls_at_five_pm_chicago():
    assert trade_day(datetime.fromisoformat("2026-09-22T16:59:00-05:00")).isoformat() == "2026-09-22"
    assert trade_day(datetime.fromisoformat("2026-09-22T17:00:00-05:00")).isoformat() == "2026-09-23"


def test_combine_mll_trails_on_day_close_and_consistency_can_raise_target():
    rules = CombineRules()
    account = Account(variants()[0], 50_000, 48_000, 50_000)
    account.balance = 51_800
    _finalize_day(account, datetime(2026, 9, 22).date(), rules)
    assert account.mll == 49_800
    account.balance = 53_000
    _finalize_day(account, datetime(2026, 9, 23).date(), rules)
    assert account.mll == 50_000
    assert account.status == "active"
    assert account.effective_target(rules) == pytest.approx(1_800 / 0.55)
    account.balance = 53_300
    _finalize_day(account, datetime(2026, 9, 24).date(), rules)
    assert account.status == "passed"


def test_unrealized_mll_breach_fails_account():
    feed = bars([(5000, 5000, 5000, 5000), (5000, 5000, 4990, 4995)])
    result = simulate(feed, BaselineGate(), variants=variants(quantity=50, stop=1000, target=1000),
                      signal_tape=tape(feed[0], "BUY"))
    account = result["accounts"][0]
    assert account["status"] == "failed"
    assert account["trades"][0]["reason"] == "maximum_loss_intrabar"


def test_jev_schema_and_uncertainty_fail_closed():
    answer = {"answers": {"entry_quality": {"choice": "approve", "probabilities":
              {"approve": 0.8, "reject": 0.2}, "confidence": 0.7}}}
    assert parse_jev_answer(answer).approved
    answer["answers"]["entry_quality"]["confidence"] = 0.2
    assert not parse_jev_answer(answer).approved
    assert not parse_jev_answer({"answers": {}}).approved
    assert not FixtureGate({}).evaluate({"candidate_id": "missing"}).approved


def test_missing_or_duplicate_bars_rejected():
    with pytest.raises(ValueError):
        simulate([], BaselineGate())
    feed = bars([(5000, 5000, 5000, 5000)])
    with pytest.raises(ValueError):
        simulate(feed * 2, BaselineGate())


def test_missing_next_bar_drops_signal_instead_of_late_entry():
    feed = [Bar(datetime.fromisoformat("2026-09-22T14:00:00+00:00"), 5000, 5000, 5000, 5000),
            Bar(datetime.fromisoformat("2026-09-22T14:10:00+00:00"), 5000, 5000, 5000, 5000)]
    result = simulate(feed, BaselineGate(), variants=variants(), signal_tape=tape(feed[0], "BUY"))
    assert all(account["trade_count"] == 0 for account in result["accounts"])


def test_missing_intraday_bar_closes_existing_position_and_flags_gap():
    start = datetime.fromisoformat("2026-09-22T14:00:00+00:00")
    feed = [Bar(start + timedelta(minutes=offset), 5000, 5000, 5000, 5000)
            for offset in (0, 5, 15)]
    result = simulate(feed, BaselineGate(), variants=variants(stop=100, target=100),
                      signal_tape=tape(feed[0], "BUY"))
    assert result["accounts"][0]["trades"][0]["reason"] == "data_gap_flat"
    assert any(event["reason"] == "missing_intraday_bars"
               for event in result["accounts"][0]["risk_events"])


def test_30_minute_bars_flat_before_1510_chicago_cutoff():
    feed = bars([(5000, 5000, 5000, 5000), (5000, 5000, 5000, 5000),
                 (5000, 5000, 5000, 5000)], start="2026-09-22T19:00:00+00:00")
    feed = [Bar(feed[0].ts + timedelta(minutes=30 * i), 5000, 5000, 5000, 5000)
            for i in range(3)]
    result = simulate(feed, BaselineGate(), variants=variants(stop=100, target=100),
                      signal_tape=tape(feed[0], "BUY"), bar_minutes=30)
    assert result["accounts"][0]["trades"][0]["reason"] == "session_flat"
    assert result["accounts"][0]["trades"][0]["exit_ts"] == feed[1].ts.isoformat()


def test_cli_writes_immutable_reviewable_run(tmp_path):
    csv_file = tmp_path / "bars.csv"
    csv_file.write_text("timestamp,open,high,low,close\n"
                        "2026-09-22T14:00:00+00:00,5000,5000,5000,5000\n"
                        "2026-09-22T14:05:00+00:00,5000,5000.5,5000,5000.5\n")
    signals = tmp_path / "signals.jsonl"
    signals.write_text('{"bar_ts":"2026-09-22T14:00:00+00:00","signal":"BUY"}\n')
    output = tmp_path / "runs"
    assert main(["--csv", str(csv_file), "--signals", str(signals), "--output", str(output)]) == 0
    (run,) = list(output.iterdir())
    assert (run / "report.md").exists()
    assert (run / "results.sqlite3").exists()
    assert (run / "decisions.jsonl").exists()
    assert "No order was placed" in (run / "report.md").read_text()
