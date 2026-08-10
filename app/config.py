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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
