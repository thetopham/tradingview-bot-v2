import pytest
from tvbot_v2.config import AppConfig, ModeConfig, load_config


def test_default_config_is_safe():
    cfg = AppConfig()
    cfg.validate_safety()


@pytest.mark.parametrize("field", ["broker_orders_enabled", "live_trading_enabled"])
def test_order_and_live_flags_rejected(field):
    kwargs = {field: True}
    cfg = AppConfig(mode=ModeConfig(**kwargs))
    with pytest.raises(ValueError):
        cfg.validate_safety()


def test_example_config_loads():
    cfg = load_config("config.example.toml")
    assert cfg.mode.dry_run is True
    assert cfg.mode.simulation_only is True
    assert cfg.mode.broker_orders_enabled is False
