import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# Deliberately relative to the working directory (not app.paths.resource_path's
# PyInstaller _MEIPASS), the same pattern app/services/env_file.py uses for .env —
# _MEIPASS is a read-only temp dir wiped on exit, which would silently lose every
# trade the moment the packaged app closed.
DB_PATH = Path("data/trade_log.db")

TERMINAL_STATUSES = {"filled", "canceled", "rejected", "expired"}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            order_id TEXT PRIMARY KEY,
            submitted_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            status TEXT NOT NULL,
            filled_avg_price REAL,
            filled_qty REAL,
            filled_at TEXT
        )
        """
    )
    # Lightweight migration for databases created before stop/target or exit tracking
    # existed — CREATE TABLE IF NOT EXISTS is a no-op against an already-existing table.
    for column in ("stop_loss_price", "take_profit_price", "exit_price", "exit_qty", "realized_pnl"):
        try:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {column} REAL")
        except sqlite3.OperationalError:
            pass  # column already exists
    for column in ("exit_order_id", "exit_at", "exit_reason"):
        try:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
    return conn


def record_trade(
    order_id: str,
    symbol: str,
    side: str,
    qty: float,
    status: str,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
) -> None:
    conn = _connect()
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO trades
                (order_id, submitted_at, symbol, side, qty, status, stop_loss_price, take_profit_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, datetime.now(UTC).isoformat(), symbol.upper(), side, qty, status, stop_loss_price, take_profit_price),
        )
    conn.close()


def update_fill(order_id: str, status: str, filled_avg_price: float | None, filled_qty: float | None, filled_at: str | None) -> None:
    conn = _connect()
    with conn:
        conn.execute(
            "UPDATE trades SET status = ?, filled_avg_price = ?, filled_qty = ?, filled_at = ? WHERE order_id = ?",
            (status, filled_avg_price, filled_qty, filled_at, order_id),
        )
    conn.close()


def record_exit(
    order_id: str,
    exit_order_id: str,
    exit_price: float,
    exit_qty: float,
    exit_at: str | None,
    exit_reason: str,
    realized_pnl: float,
) -> None:
    conn = _connect()
    with conn:
        conn.execute(
            """
            UPDATE trades
            SET exit_order_id = ?, exit_price = ?, exit_qty = ?, exit_at = ?, exit_reason = ?, realized_pnl = ?
            WHERE order_id = ?
            """,
            (exit_order_id, exit_price, exit_qty, exit_at, exit_reason, realized_pnl, order_id),
        )
    conn.close()


def trades_awaiting_exit() -> list[dict]:
    """Filled bracket trades (a stop loss or take profit was attached) whose exit leg
    hasn't filled yet, so the sync route knows to keep checking their child orders."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM trades
        WHERE status = 'filled'
          AND exit_order_id IS NULL
          AND (stop_loss_price IS NOT NULL OR take_profit_price IS NOT NULL)
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_trades(limit: int = 100) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM trades ORDER BY submitted_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def pending_order_ids() -> list[str]:
    conn = _connect()
    placeholders = ",".join("?" * len(TERMINAL_STATUSES))
    rows = conn.execute(f"SELECT order_id FROM trades WHERE status NOT IN ({placeholders})", tuple(TERMINAL_STATUSES)).fetchall()
    conn.close()
    return [row[0] for row in rows]
