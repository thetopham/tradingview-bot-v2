"""Account isolation, persistence, and fill timing for the simulated broker."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from tvbot_v2.replay.topstep import Bar
from tvbot_v2.simulate.brokerless_executor import SimBroker
from tvbot_v2.simulate.ledger import SimLedger
from tvbot_v2.simulate.portfolio import Bracket, SimVariant
from tvbot_v2.simulate.__main__ import main


START = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)


def bar(n: int, *, open: float = 6000, high: float = 6001,
        low: float = 5999, close: float = 6000) -> Bar:
    return Bar(START + timedelta(minutes=5 * n), open, high, low, close)


def variant(name: str) -> SimVariant:
    return SimVariant(name, "5m", f"strategy_{name}", {1: Bracket(1, 24, 48)})


def test_unbounded_registration_and_independent_restart(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    variants = tuple(variant(f"account_{n}") for n in range(40))
    ledger.register(variants)
    assert len(ledger.status()) == 40
    broker = SimBroker(ledger)
    first = broker.process_bar("account_0", bar(0), {"signal": "BUY", "size": 1})
    assert first["position"] is None
    assert first["pending"]["signal"] == "BUY"
    restarted = SimBroker(SimLedger(tmp_path / "broker.sqlite"))
    opened = restarted.process_bar("account_0", bar(1))
    assert opened["position"]["direction"] == 1
    assert opened["position"]["entry_price"] == 6000.25
    assert opened["balance"] == 49999.39
    assert restarted.ledger.status("account_1")[0]["balance"] == 50000
    assert restarted.process_bar("account_0", bar(1)) == opened
    with pytest.raises(ValueError, match="conflicting"):
        restarted.process_bar("account_0", bar(1), {"signal": "SELL"})
    restarted.ledger.register((variant("account_40"),))
    assert len(restarted.ledger.status()) == 41
    with pytest.raises(ValueError, match="different settings"):
        restarted.ledger.register((SimVariant("account_40", "5m", "changed",
                                            {1: Bracket(1, 24, 48)}),))


def test_stop_fill_audit_and_reset(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    ledger.register((variant("a"),))
    broker = SimBroker(ledger)
    broker.process_bar("a", bar(0), {"signal": "BUY", "size": 1})
    stopped = broker.process_bar("a", bar(1, low=5993, close=5994))
    assert stopped["position"] is None
    assert stopped["trade_count"] == 1
    assert stopped["balance"] < 50000
    assert stopped["win_rate"] == 0
    assert stopped["consecutive_losses"] == 1
    assert stopped["current_day_pnl"] < 0
    with ledger.connection() as conn:
        assert conn.execute("SELECT reason FROM sim_trade").fetchone()[0] == "stop"
        assert conn.execute("SELECT count(*) FROM sim_event WHERE event_type='entry_fill'").fetchone()[0] == 1
    reset = ledger.reset("a", "new experiment")
    assert reset["generation"] == 2
    assert reset["balance"] == 50000
    assert reset["trade_count"] == 0
    with ledger.connection() as conn:
        assert conn.execute("SELECT count(*) FROM sim_trade").fetchone()[0] == 1


def test_gap_cancels_pending_and_flattens(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    ledger.register((variant("a"),))
    broker = SimBroker(ledger)
    broker.process_bar("a", bar(0), {"signal": "BUY"})
    missed = broker.process_bar("a", bar(2))
    assert missed["position"] is None
    assert missed["pending"] is None
    broker.process_bar("a", bar(3), {"signal": "BUY"})
    broker.process_bar("a", bar(4))
    flattened = broker.process_bar("a", bar(6))
    assert flattened["position"] is None
    with ledger.connection() as conn:
        assert conn.execute("SELECT reason FROM sim_trade").fetchone()[0] == "data_gap_flat"


def test_pause_blocks_entry_but_stop_still_runs(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    ledger.register((variant("a"),))
    broker = SimBroker(ledger)
    broker.process_bar("a", bar(0), {"signal": "BUY"})
    ledger.set_pause("a", True)
    assert broker.process_bar("a", bar(1))["position"] is None
    ledger.set_pause("a", False)
    broker.process_bar("a", bar(2), {"signal": "BUY"})
    opened = broker.process_bar("a", bar(3))
    assert opened["position"] is not None
    ledger.set_pause("a", True)
    assert broker.process_bar("a", bar(4, low=5993, close=5994))["position"] is None


def test_bad_decision_rolls_back_bar(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    ledger.register((variant("a"),))
    broker = SimBroker(ledger)
    with pytest.raises(ValueError, match="size"):
        broker.process_bar("a", bar(0), {"signal": "BUY", "size": 4})
    assert ledger.status("a")[0]["last_bar_ts"] is None
    assert broker.process_bar("a", bar(0), {"signal": "HOLD"})["last_bar_ts"]


def test_30m_decision_fills_only_on_next_available_1m_open(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    setting = SimVariant("epsilon", "30m", "prodex", {1: Bracket(1, 24, 48)}, "1m")
    ledger.register((setting,))
    broker = SimBroker(ledger)

    def minute(n, **prices):
        return Bar(START + timedelta(minutes=n), prices.get("open", 6000),
                   prices.get("high", 6001), prices.get("low", 5999),
                   prices.get("close", 6000))

    broker.process_bar("epsilon", minute(29))
    available = START + timedelta(minutes=30, seconds=20)
    queued = broker.submit_decision("epsilon", START.isoformat(),
                                    {"signal": "BUY", "size": 1, "timeframe": "30m"},
                                    available_at=available)
    assert queued["pending"]["earliest_fill_ts"] == (START + timedelta(minutes=31)).isoformat()
    assert broker.submit_decision("epsilon", START.isoformat(),
                                  {"signal": "BUY", "size": 1, "timeframe": "30m"},
                                  available_at=available) == queued
    with pytest.raises(ValueError, match="conflicting"):
        broker.submit_decision("epsilon", START.isoformat(), {"signal": "SELL"},
                               available_at=available)
    before = broker.process_bar("epsilon", minute(30))
    assert before["position"] is None
    assert before["pending"]["signal"] == "BUY"
    filled = broker.process_bar("epsilon", minute(31))
    assert filled["position"]["entry_ts"] == (START + timedelta(minutes=31)).isoformat()
    assert filled["position"]["entry_price"] == 6000.25
    stopped = broker.process_bar("epsilon", minute(32, low=5993, close=5994))
    assert stopped["position"] is None
    assert stopped["trade_count"] == 1
    with ledger.connection() as conn:
        assert conn.execute("SELECT count(*) FROM sim_decision").fetchone()[0] == 1
        assert conn.execute("SELECT reason FROM sim_trade").fetchone()[0] == "stop"


def test_one_minute_gap_cancels_queued_decision(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    ledger.register((SimVariant("epsilon", "30m", "prodex",
                                {1: Bracket(1, 24, 48)}, "1m"),))
    broker = SimBroker(ledger)
    broker.process_bar("epsilon", Bar(START + timedelta(minutes=29), 6000, 6001, 5999, 6000))
    broker.submit_decision("epsilon", START.isoformat(), {"signal": "BUY"},
                           available_at=START + timedelta(minutes=30, seconds=20))
    gap = broker.process_bar("epsilon", Bar(START + timedelta(minutes=31), 6000, 6001, 5999, 6000))
    assert gap["pending"] is None
    assert gap["position"] is None


def test_ambiguous_bar_and_intrabar_mll(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    ledger.register((SimVariant("collision", "5m", "test", {1: Bracket(1, 4, 4)}),
                     SimVariant("mll", "5m", "test", {1: Bracket(50, 1000, 1000)})))
    broker = SimBroker(ledger)
    for account in ("collision", "mll"):
        broker.process_bar(account, bar(0), {"signal": "BUY"})
    broker.process_bar("collision", bar(1, high=6002, low=5998))
    failed = broker.process_bar("mll", bar(1, high=6000, low=5990, close=5995))
    assert failed["status"] == "failed"
    with ledger.connection() as conn:
        reasons = {row["account"]: row["reason"] for row in conn.execute(
            "SELECT account,reason FROM sim_trade")}
    assert reasons == {"collision": "stop_first_if_ambiguous", "mll": "maximum_loss_intrabar"}


def test_session_flat_and_day_rollover(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    ledger.register((SimVariant("a", "30m", "test", {1: Bracket(1, 100, 100)}),))
    broker = SimBroker(ledger)
    afternoon = datetime(2026, 9, 23, 19, 0, tzinfo=timezone.utc)
    broker.process_bar("a", Bar(afternoon, 6000, 6000, 6000, 6000), {"signal": "BUY"})
    flat = broker.process_bar("a", Bar(afternoon + timedelta(minutes=30),
                                      6000, 6000, 6000, 6000))
    assert flat["position"] is None
    next_day = broker.process_bar("a", Bar(datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc),
                                          6000, 6000, 6000, 6000))
    assert "2026-09-23" in next_day["day_pnl"]
    with ledger.connection() as conn:
        assert conn.execute("SELECT reason FROM sim_trade").fetchone()[0] == "session_flat"


def test_explicit_settlement_is_idempotent_across_rollover(tmp_path):
    ledger = SimLedger(tmp_path / "broker.sqlite")
    ledger.register((variant("a"),))
    broker = SimBroker(ledger)
    broker.process_bar("a", bar(0))
    with pytest.raises(ValueError, match="session close"):
        broker.settle_day("a", datetime(2026, 9, 23, 19, 0, tzinfo=timezone.utc))
    settled = broker.settle_day("a", datetime(2026, 9, 23, 21, 0, tzinfo=timezone.utc))
    assert settled["day_pnl"] == {"2026-09-23": 0.0}
    assert broker.settle_day("a", datetime(2026, 9, 23, 21, 0, tzinfo=timezone.utc)) == settled
    next_bar = Bar(datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc),
                   6000, 6000, 6000, 6000)
    assert broker.process_bar("a", next_bar)["day_pnl"] == settled["day_pnl"]
    with ledger.connection() as conn:
        assert conn.execute("SELECT count(*) FROM sim_event WHERE event_type='day_finalized'").fetchone()[0] == 1


def test_cli_registers_extra_profile_and_steps(tmp_path, capsys):
    db = tmp_path / "broker.sqlite"
    profile = tmp_path / "profiles.json"
    profile.write_text(json.dumps([json.loads(variant("strategy_100").to_json())]), encoding="utf-8")
    assert main(["--db", str(db), "init", "--portfolio", str(profile)]) == 0
    capsys.readouterr()
    envelope = tmp_path / "bar.json"
    envelope.write_text(json.dumps({"account": "strategy_100", "bar": {
        "timestamp": START.isoformat(), "open": 6000, "high": 6001,
        "low": 5999, "close": 6000}, "decision": {"signal": "BUY", "size": 1}}),
        encoding="utf-8")
    assert main(["--db", str(db), "step", "--input", str(envelope)]) == 0
    stepped = json.loads(capsys.readouterr().out)
    assert stepped["pending"]["signal"] == "BUY"
    assert main(["--db", str(db), "status", "strategy_100"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["account"] == "strategy_100"
