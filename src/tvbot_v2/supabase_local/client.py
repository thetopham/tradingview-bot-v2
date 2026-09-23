"""Read-only PostgREST access to the user's local Supabase feed."""

from __future__ import annotations

import os
from typing import Any

import requests


ALLOWED_TABLES = ("tv_datafeed_5m", "tv_datafeed_15m", "tv_datafeed_30m",
                  "ai_trading_log", "trade_results")


class LocalSupabaseClient:
    def __init__(self, base_url: str | None = None, key: str | None = None,
                 session: requests.Session | None = None):
        self.base_url = (base_url or os.getenv("TVBOT_V2_SUPABASE_URL") or "http://192.168.0.35:8000").rstrip("/")
        self.key = key or os.getenv("TVBOT_V2_SUPABASE_KEY")
        if not self.key:
            raise ValueError("TVBOT_V2_SUPABASE_KEY is required for local feed access")
        self.session = session or requests.Session()

    def get_rows(self, table: str, *, offset: int = 0, limit: int = 1000,
                 order: str | None = None) -> tuple[list[dict[str, Any]], int | None]:
        if table not in ALLOWED_TABLES:
            raise ValueError("table is not allowlisted")
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid pagination")
        params = {"select": "*", "limit": limit, "offset": offset}
        if order:
            if order not in {"id.asc", "timestamp.asc", "created_at.asc", "time.asc"}:
                raise ValueError("unsupported ordering")
            params["order"] = order
        response = self.session.get(
            f"{self.base_url}/rest/v1/{table}", params=params,
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}",
                     "Prefer": "count=exact"}, timeout=20)
        if response.status_code != 200:
            raise RuntimeError(f"local Supabase {table} returned HTTP {response.status_code}")
        rows = response.json()
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("unexpected local Supabase response")
        content_range = response.headers.get("Content-Range", "")
        count_text = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
        count = int(count_text) if count_text.isdigit() else None
        return rows, count

    def inspect(self, tables: tuple[str, ...] = ALLOWED_TABLES) -> list[dict[str, Any]]:
        inventory = []
        for table in tables:
            try:
                rows, count = self.get_rows(table, limit=1)
                inventory.append({"table": table, "count": count,
                                  "columns": sorted(rows[0]) if rows else [], "status": "ok"})
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                inventory.append({"table": table, "status": "error",
                                  "error": type(exc).__name__})
        return inventory
