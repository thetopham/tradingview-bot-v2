from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tomllib


@dataclass(frozen=True)
class ModeConfig:
    dry_run: bool = True
    simulation_only: bool = True
    broker_orders_enabled: bool = False
    live_trading_enabled: bool = False


@dataclass(frozen=True)
class DataConfig:
    supabase_url: str = "http://192.168.0.35:8000"
    feed_db: str = "feed/tradingview.sqlite3"


@dataclass(frozen=True)
class ReplayConfig:
    default_slippage_bps: float = 1.0
    default_fee_per_trade: float = 0.0
    fill_model: str = "next_bar_open"


@dataclass(frozen=True)
class AppConfig:
    mode: ModeConfig = ModeConfig()
    data: DataConfig = DataConfig()
    replay: ReplayConfig = ReplayConfig()

    def validate_safety(self) -> None:
        if self.mode.broker_orders_enabled:
            raise ValueError("broker_orders_enabled must remain false for v2 MVP")
        if self.mode.live_trading_enabled:
            raise ValueError("live_trading_enabled must remain false for v2 MVP")
        if not self.mode.dry_run or not self.mode.simulation_only:
            raise ValueError("v2 MVP must run dry_run=true and simulation_only=true")


def load_config(path: str | Path = "config.example.toml") -> AppConfig:
    data = {}
    p = Path(path)
    if p.exists():
        data = tomllib.loads(p.read_text())
    mode = ModeConfig(**data.get("mode", {}))
    d = data.get("data", {})
    supabase_url = os.getenv("TVBOT_V2_SUPABASE_URL") or d.get("supabase_url", DataConfig.supabase_url)
    feed_db = os.getenv("TVBOT_V2_FEED_DB") or d.get("feed_db", DataConfig.feed_db)
    replay = ReplayConfig(**data.get("replay", {}))
    cfg = AppConfig(mode=mode, data=DataConfig(supabase_url=supabase_url, feed_db=feed_db), replay=replay)
    cfg.validate_safety()
    return cfg
