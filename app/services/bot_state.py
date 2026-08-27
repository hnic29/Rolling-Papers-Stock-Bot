"""Persists TradingBot's daily/toggle state across restarts, per user. Reuses
trade_log's DB (already has a writable path wired up in every deployment - systemd
ReadWritePaths, local dev default) rather than introducing a new file/path to manage."""

import sqlite3

from app.services.trade_log import _connect as _trades_connect


def _connect() -> sqlite3.Connection:
    conn = _trades_connect()  # ensures the `trades` table (and its migrations) exist too
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            user_id INTEGER PRIMARY KEY,
            auto_trading_enabled INTEGER NOT NULL DEFAULT 0,
            running INTEGER NOT NULL DEFAULT 0,
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
    _migrate_single_row_schema(conn)
    return conn


def _migrate_single_row_schema(conn: sqlite3.Connection) -> None:
    """One-time upgrade from the pre-multi-user schema, which hard-constrained
    bot_state to exactly one row (`id INTEGER PRIMARY KEY CHECK (id = 1)`) - detected
    by the presence of an `id` column (dropped in the new schema) rather than the
    absence of `user_id` (CREATE TABLE IF NOT EXISTS above already adds that to a
    brand new table, so checking for it can't tell an old table from a new one)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(bot_state)").fetchall()}
    if "id" not in columns:
        return  # already migrated, or a fresh install that never had the old schema

    with conn:
        conn.execute("ALTER TABLE bot_state RENAME TO bot_state_old")
        conn.execute(
            """
            CREATE TABLE bot_state (
                user_id INTEGER PRIMARY KEY,
                auto_trading_enabled INTEGER NOT NULL DEFAULT 0,
                running INTEGER NOT NULL DEFAULT 0,
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
        conn.execute(
            """
            INSERT INTO bot_state (
                user_id, auto_trading_enabled, running, trading_day, trades_today, peak_daily_pnl,
                consecutive_losses, walked_away_for_day, walk_away_reason, auto_trading_started_at
            )
            SELECT 1, auto_trading_enabled, running, trading_day, trades_today, peak_daily_pnl,
                   consecutive_losses, walked_away_for_day, walk_away_reason, auto_trading_started_at
            FROM bot_state_old WHERE id = 1
            """
        )
        conn.execute("DROP TABLE bot_state_old")


def load(user_id: int = 1) -> dict | None:
    """The persisted state for this user, or None if nothing's been saved yet (a
    brand new deployment, or a user who's never started the bot)."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bot_state WHERE user_id = ?", (user_id,)).fetchone()
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
    user_id: int = 1,
) -> None:
    conn = _connect()
    with conn:
        conn.execute(
            """
            INSERT INTO bot_state (
                user_id, auto_trading_enabled, running, trading_day, trades_today, peak_daily_pnl,
                consecutive_losses, walked_away_for_day, walk_away_reason, auto_trading_started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
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
                user_id,
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
