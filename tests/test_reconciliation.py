import csv

from tvbot_v2.feed.reconcile import reconcile_exports


def write_csv(path, fields, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_reconcile_uses_decision_provenance_and_never_invents_missing_net(tmp_path):
    decisions = tmp_path / "decisions.csv"
    results = tmp_path / "results.csv"
    write_csv(decisions, ("ai_decision_id", "account", "timeframe", "prompt_version"),
              [{"ai_decision_id": "1", "account": "practice", "timeframe": "5m", "prompt_version": "v1"},
               {"ai_decision_id": "2", "account": "epsilon", "timeframe": "30m", "prompt_version": "v2"}])
    write_csv(results, ("id", "ai_decision_id", "account", "prompt_version", "net_pnl"),
              [{"id": "10", "ai_decision_id": "1", "account": "practice", "prompt_version": "v1", "net_pnl": "2.50"},
               {"id": "11", "ai_decision_id": "1", "account": "practice", "prompt_version": "v1", "net_pnl": ""},
               {"id": "13", "ai_decision_id": "1", "account": "delta", "prompt_version": "v1", "net_pnl": "10"},
               {"id": "12", "ai_decision_id": "99", "account": "practice", "prompt_version": "", "net_pnl": "5"}])
    report = reconcile_exports(decisions, results)
    assert report["unmatched_result_rows"] == 1
    practice = next(group for group in report["groups"] if group["account"] == "practice")
    assert practice["linked_trade_rows"] == 3
    assert practice["trade_rows_with_net"] == 2
    assert practice["trade_rows_without_net"] == 1
    assert practice["same_account_net_pnl"] == "2.50"
    assert practice["cross_account_net_pnl"] == "10.00"
    assert practice["account_mismatch"] == 1
