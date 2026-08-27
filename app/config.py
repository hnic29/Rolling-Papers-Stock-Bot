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
    max_trades_per_day: int = 5
    allow_live_trading: bool = False
    # Percentages of the current bankroll (app.services.bankroll.current_bankroll()),
    # not fixed dollar amounts - a static "$200 risk per trade" doesn't mean anything
    # without knowing the account size it was picked against, and stops making sense the
    # moment the bankroll changes (a withdrawal, a return-to-savings, or just P&L). 2%
    # risk per trade / 20% max position value / 6% max daily loss are standard small-
    # account guidelines, and 6% ~= three losses at 2% each - consistent with the
    # existing "3 consecutive losses" walk-away rule instead of an arbitrary number that
    # could be blown through by a single trade (the old $100 daily cap was smaller than
    # the old $200 per-trade risk, so one loss alone could exceed the "daily" limit).
    risk_per_trade_pct: float = 2.0
    max_position_value_pct: float = 20.0
    max_daily_loss_pct: float = 6.0
    fmp_api_key: str = ""
    # 60s, halved from 120: Ross's window is measured in minutes, and per-cycle API
    # cost (~28 sweep + ~28 snapshot requests) stays well inside Alpaca's 200/min
    # free-tier limit even at this pace.
    automation_interval_seconds: int = 60
    automation_scan_limit: int = 10
    # The curated universe list (data/stock_universe.txt) is ~110 symbols; this used to
    # default to 30, silently scanning only the same alphabetical first slice of it every
    # cycle and never looking at the rest. 150 gives headroom above the current list size.
    automation_max_symbols: int = 150
    min_reward_risk_ratio: float = 2.0
    # Auto-trading also gates on the broker's actual market-open flag, so 07:00-09:30 was
    # always a no-op anyway - widened to the full regular session (9:30-16:00 ET) rather
    # than just the first half hour after open, so a real signal can act on whenever it
    # actually shows up instead of only in a 30-minute window each day.
    trading_window_start: str = "09:30"
    trading_window_end: str = "16:00"
    # Ross Cameron's real trading window opens well before the bell (his 2026-08-25
    # session's entire P&L - RCON +$17k, GRML -$8k, DAIC, AMIX - happened 7:00-9:30 ET,
    # fully resolved by the open). Extended-hours orders are LIMIT-only (Alpaca
    # disallows market orders and bracket/stop legs outside 9:30-16:00), so premarket
    # entries carry real execution risk a regular-hours market order doesn't - opt-out,
    # not opt-in, is deliberate given that tradeoff.
    premarket_trading_enabled: bool = True
    premarket_window_start: str = "07:00"
    max_daily_giveback_pct: float = 50.0
    max_consecutive_losses: int = 3
    max_minutes_without_trade: int = 60
    dashboard_username: str = ""
    dashboard_password: str = ""
    # Signs session-login cookies (app.services.session_auth). Blank on a fresh
    # install - session_auth generates one and persists it back to the env file the
    # first time it's needed, the same lazy-provisioning pattern as everything else
    # in this file. Must stay stable across restarts, or every logged-in session
    # would be invalidated on every deploy.
    session_secret: str = ""
    # Encrypts each person's own Alpaca/FMP keys at rest (app.services.credentials,
    # Fernet symmetric encryption) once multi-user credential storage is in use.
    # Same lazy generate-and-persist pattern as session_secret above - never checked
    # into git, never stored in the database itself.
    credentials_encryption_key: str = ""
    # Push notifications (app.services.notify). Empty topic = disabled. The server
    # default is the free public instance; point it at a self-hosted ntfy to go fully
    # private - the rest of the code doesn't change.
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"

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
    """Re-reads the env file into the EXISTING `settings` object in place,
    rather than constructing a new one and rebinding the module-level name.
    Several other modules (alpaca_broker, fmp, bot, risk, ...) did
    `from app.config import settings`, which binds their own reference to
    the object's identity at import time - rebinding app.config's own name
    would never reach them, so a saved API key would never actually take
    effect anywhere except in this module itself. Mutating the shared
    object's fields means every existing reference sees the update."""
    fresh = Settings()
    for field_name in Settings.model_fields:
        setattr(settings, field_name, getattr(fresh, field_name))
    return settings
