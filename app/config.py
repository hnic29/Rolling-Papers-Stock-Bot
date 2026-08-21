import os

from pydantic_settings import BaseSettings, SettingsConfigDict

# Deliberately the SAME env var (and same default) as app.services.env_file's
# ENV_PATH - both must point at the same file, or a save from the dashboard's
# Settings page (which writes via env_file.write_env) would never be visible
# to Settings() re-reading a different one. A deployment sets this via
# systemd Environment= rather than putting it inside the file itself, which
# would be a chicken-and-egg problem.
_ENV_FILE_PATH = os.environ.get("CONFIG_ENV_PATH", ".env")


class Settings(BaseSettings):
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    bot_symbol: str = "AAPL"
    bot_qty: int = 1
    max_daily_loss: float = 100.0
    max_trades_per_day: int = 5
    max_position_value: float = 1000.0
    allow_live_trading: bool = False
    fmp_api_key: str = ""
    automation_interval_seconds: int = 120
    automation_scan_limit: int = 10
    automation_max_symbols: int = 30
    risk_per_trade: float = 200.0
    min_reward_risk_ratio: float = 2.0
    trading_window_start: str = "07:00"
    trading_window_end: str = "10:00"
    max_daily_giveback_pct: float = 50.0
    max_consecutive_losses: int = 3
    max_minutes_without_trade: int = 60
    dashboard_username: str = ""
    dashboard_password: str = ""

    # extra="ignore": pydantic-settings' default is to reject any dotenv-file
    # key with no matching field. TRADE_LOG_PATH (read directly via os.environ
    # in app.services.trade_log, deliberately not a Settings field) lives in
    # the same file as everything else here — without this, its mere presence
    # crashes the app at import time with a ValidationError. Real env vars
    # were never validated this strictly (pydantic-settings only pulls the
    # ones matching a declared field), so this restores that same leniency
    # for the dotenv-file source and guards against the same class of bug
    # for any future deploy-only var that ends up in this file.
    model_config = SettingsConfigDict(env_file=_ENV_FILE_PATH, env_file_encoding="utf-8", extra="ignore")


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
