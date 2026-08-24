"""Persists TradingBot's daily/toggle state across restarts. Reuses trade_log's DB
(already has a writable path wired up in every deployment - systemd ReadWritePaths,
local dev default) rather than introducing a new file/path to manage."""

import sqlite3

from app.services.trade_log import _connect as _trades_connect


def _connect() -> sqlite3.Connection:
    conn = _trades_connect()  # ensures the `trades` table (and its migrations) exist too
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            auto_trading_enabled INTEGER NOT NULL DEFAULT 0,
            trading_day TEXT NOT NULL,
            trades_today INTEGER NOT NULL DEFAULT 0,
            peak_daily_pnl REAL NOT NULL DEFAULT 0,
            consecutive_losses INTEGER NOT NULL DEFAULT 0,
            walked_away_for_day INTEGER NOT NULL DEFAULT 0,
            walk_away_reason TEXT,
            auto_trading_started_at TEXT
        )
        """
    )
    # Lightweight migration for a bot_state table created before `running` existed -
    # CREATE TABLE IF NOT EXISTS is a no-op against an already-existing table.
    try:
        conn.execute("ALTER TABLE bot_state ADD COLUMN running INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    return conn


def load() -> dict | None:
    """The persisted state, or None if nothing's been saved yet (a brand new
    deployment, or a database that predates this)."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bot_state WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else None


def save(
    *,
    auto_trading_enabled: bool,
    running: bool,
    trading_day: str,
    trades_today: int,
    peak_daily_pnl: float,
    consecutive_losses: int,
    walked_away_for_day: bool,
    walk_away_reason: str | None,
    auto_trading_started_at: str | None,
) -> None:
    conn = _connect()
    with conn:
        conn.execute(
            """
            INSERT INTO bot_state (
                id, auto_trading_enabled, running, trading_day, trades_today, peak_daily_pnl,
                consecutive_losses, walked_away_for_day, walk_away_reason, auto_trading_started_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                auto_trading_enabled = excluded.auto_trading_enabled,
                running = excluded.running,
                trading_day = excluded.trading_day,
                trades_today = excluded.trades_today,
                peak_daily_pnl = excluded.peak_daily_pnl,
                consecutive_losses = excluded.consecutive_losses,
                walked_away_for_day = excluded.walked_away_for_day,
                walk_away_reason = excluded.walk_away_reason,
                auto_trading_started_at = excluded.auto_trading_started_at
            """,
            (
                int(auto_trading_enabled),
                int(running),
                trading_day,
                trades_today,
                peak_daily_pnl,
                consecutive_losses,
                int(walked_away_for_day),
                walk_away_reason,
                auto_trading_started_at,
            ),
        )
    conn.close()
