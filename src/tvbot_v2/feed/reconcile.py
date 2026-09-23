"""Reconcile exported decision IDs with closed trade outcomes, without guessing fills."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def reconcile_exports(decisions_csv: str | Path, results_csv: str | Path) -> dict[str, Any]:
    decisions: dict[str, dict[str, str]] = {}
    duplicate_decision_ids = 0
    with open(decisions_csv, newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            key = (row.get("ai_decision_id") or "").strip()
            if not key:
                continue
            if key in decisions:
                duplicate_decision_ids += 1
                continue
            decisions[key] = row
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    def group_for(row: dict[str, str]) -> dict[str, Any]:
        key = (row.get("account") or "", row.get("timeframe") or "",
               row.get("prompt_version") or "")
        if key not in groups:
            groups[key] = {"account": key[0], "timeframe": key[1],
                           "prompt_version": key[2], "decisions": 0,
                           "linked_trade_rows": 0, "trade_rows_with_net": 0,
                           "trade_rows_without_net": 0, "linked_net_pnl": Decimal(0),
                           "same_account_net_pnl": Decimal(0),
                           "cross_account_net_pnl": Decimal(0),
                           "unknown_account_net_pnl": Decimal(0),
                           "account_mismatch": 0, "prompt_version_mismatch": 0}
        return groups[key]
    for row in decisions.values():
        group_for(row)["decisions"] += 1
    result_rows = 0
    unmatched = 0
    with open(results_csv, newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            result_rows += 1
            decision = decisions.get((row.get("ai_decision_id") or "").strip())
            if decision is None:
                unmatched += 1
                continue
            group = group_for(decision)
            group["linked_trade_rows"] += 1
            result_account = (row.get("account") or "").strip()
            if result_account == decision.get("account"):
                account_class = "same_account"
            elif result_account and not result_account.isdigit():
                account_class = "cross_account"
                group["account_mismatch"] += 1
            else:
                account_class = "unknown_account"
            result_version = (row.get("prompt_version") or "").strip()
            if result_version and result_version != decision.get("prompt_version"):
                group["prompt_version_mismatch"] += 1
            try:
                value = Decimal((row.get("net_pnl") or "").strip())
                if not value.is_finite():
                    raise InvalidOperation
            except InvalidOperation:
                group["trade_rows_without_net"] += 1
                continue
            group["trade_rows_with_net"] += 1
            group["linked_net_pnl"] += value
            group[f"{account_class}_net_pnl"] += value
    summaries = []
    for key in sorted(groups):
        row = groups[key].copy()
        for name in ("linked_net_pnl", "same_account_net_pnl", "cross_account_net_pnl",
                     "unknown_account_net_pnl"):
            row[name] = str(row[name].quantize(Decimal("0.01")))
        summaries.append(row)
    return {"decision_rows_with_id": len(decisions), "duplicate_decision_ids": duplicate_decision_ids,
            "result_rows": result_rows, "unmatched_result_rows": unmatched,
            "groups": summaries}
