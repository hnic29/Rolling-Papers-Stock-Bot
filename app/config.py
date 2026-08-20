from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
