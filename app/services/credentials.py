"""Per-user Alpaca/FMP credentials and risk settings - each person connects their
OWN Alpaca account rather than sharing the bot's. Secrets are encrypted at rest
with a server-side master key (Fernet, symmetric) that lives only in the
container's environment, generated and persisted on first use the same lazy way
session_auth.py handles its signing secret - never in the database, never in git.

Not yet wired into TradingBot/MarketScanner (that's the per-user bot registry,
still to come) - this module is the storage layer those will read from."""

import sqlite3

from cryptography.fernet import Fernet, InvalidToken

from app.services.trade_log import _connect as _trades_connect

_DEFAULTS = {
    "alpaca_api_key": "",
    "alpaca_secret_key": "",
    "alpaca_paper": True,
    "allow_live_trading": False,
    "fmp_api_key": "",
    "ntfy_topic": "",
    "risk_per_trade_pct": 2.0,
    "max_position_value_pct": 20.0,
    "max_daily_loss_pct": 6.0,
    "max_trades_per_day": 5,
    "premarket_trading_enabled": True,
}


def _connect() -> sqlite3.Connection:
    conn = _trades_connect()  # ensures the `trades` table (and its migrations) exist too
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_credentials (
            user_id INTEGER PRIMARY KEY,
            alpaca_api_key_encrypted TEXT,
            alpaca_secret_key_encrypted TEXT,
            alpaca_paper INTEGER NOT NULL DEFAULT 1,
            allow_live_trading INTEGER NOT NULL DEFAULT 0,
            fmp_api_key_encrypted TEXT,
            ntfy_topic TEXT NOT NULL DEFAULT '',
            risk_per_trade_pct REAL NOT NULL DEFAULT 2.0,
            max_position_value_pct REAL NOT NULL DEFAULT 20.0,
            max_daily_loss_pct REAL NOT NULL DEFAULT 6.0,
            max_trades_per_day INTEGER NOT NULL DEFAULT 5,
            premarket_trading_enabled INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )
    return conn


def _ensure_encryption_key() -> bytes:
    from app.config import reload_settings, settings
    from app.services.env_file import InvalidEnvValue, write_env

    if settings.credentials_encryption_key:
        return settings.credentials_encryption_key.encode("utf-8")
    key = Fernet.generate_key().decode("utf-8")
    try:
        write_env({"CREDENTIALS_ENCRYPTION_KEY": key})
    except InvalidEnvValue:
        pass  # extremely unlikely (a generated Fernet key never contains a newline)
    reload_settings()
    return (settings.credentials_encryption_key or key).encode("utf-8")


def _encrypt(value: str) -> str | None:
    if not value:
        return None
    return Fernet(_ensure_encryption_key()).encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str | None) -> str:
    """Empty string (not an exception) for anything that won't decrypt - a blank
    field, or a stale value encrypted under a since-rotated key - so a broker
    construction attempt fails on "missing credentials" rather than crashing here."""
    if not value:
        return ""
    try:
        return Fernet(_ensure_encryption_key()).decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def get_credentials(user_id: int) -> dict:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM user_credentials WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if row is None:
        return {"user_id": user_id, **_DEFAULTS}

    data = dict(row)
    return {
        "user_id": user_id,
        "alpaca_api_key": _decrypt(data["alpaca_api_key_encrypted"]),
        "alpaca_secret_key": _decrypt(data["alpaca_secret_key_encrypted"]),
        "alpaca_paper": bool(data["alpaca_paper"]),
        "allow_live_trading": bool(data["allow_live_trading"]),
        "fmp_api_key": _decrypt(data["fmp_api_key_encrypted"]),
        "ntfy_topic": data["ntfy_topic"],
        "risk_per_trade_pct": data["risk_per_trade_pct"],
        "max_position_value_pct": data["max_position_value_pct"],
        "max_daily_loss_pct": data["max_daily_loss_pct"],
        "max_trades_per_day": data["max_trades_per_day"],
        "premarket_trading_enabled": bool(data["premarket_trading_enabled"]),
    }


def save_credentials(user_id: int, **fields) -> dict:
    """Partial update - pass only the fields you want to change. Unknown keys are
    rejected up front rather than silently ignored, so a typo'd field name doesn't
    look like it saved."""
    unknown = set(fields) - set(_DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown credential field(s): {', '.join(sorted(unknown))}")

    current = get_credentials(user_id)
    current.update(fields)

    conn = _connect()
    with conn:
        conn.execute(
            """
            INSERT INTO user_credentials (
                user_id, alpaca_api_key_encrypted, alpaca_secret_key_encrypted, alpaca_paper,
                allow_live_trading, fmp_api_key_encrypted, ntfy_topic, risk_per_trade_pct,
                max_position_value_pct, max_daily_loss_pct, max_trades_per_day, premarket_trading_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                alpaca_api_key_encrypted = excluded.alpaca_api_key_encrypted,
                alpaca_secret_key_encrypted = excluded.alpaca_secret_key_encrypted,
                alpaca_paper = excluded.alpaca_paper,
                allow_live_trading = excluded.allow_live_trading,
                fmp_api_key_encrypted = excluded.fmp_api_key_encrypted,
                ntfy_topic = excluded.ntfy_topic,
                risk_per_trade_pct = excluded.risk_per_trade_pct,
                max_position_value_pct = excluded.max_position_value_pct,
                max_daily_loss_pct = excluded.max_daily_loss_pct,
                max_trades_per_day = excluded.max_trades_per_day,
                premarket_trading_enabled = excluded.premarket_trading_enabled
            """,
            (
                user_id,
                _encrypt(current["alpaca_api_key"]),
                _encrypt(current["alpaca_secret_key"]),
                int(current["alpaca_paper"]),
                int(current["allow_live_trading"]),
                _encrypt(current["fmp_api_key"]),
                current["ntfy_topic"],
                current["risk_per_trade_pct"],
                current["max_position_value_pct"],
                current["max_daily_loss_pct"],
                current["max_trades_per_day"],
                int(current["premarket_trading_enabled"]),
            ),
        )
    conn.close()
    return get_credentials(user_id)


def has_credentials(user_id: int) -> bool:
    creds = get_credentials(user_id)
    return bool(creds["alpaca_api_key"] and creds["alpaca_secret_key"])
