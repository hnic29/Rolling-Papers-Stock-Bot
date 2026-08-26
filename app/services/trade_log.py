import os
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")

# Deliberately relative to the working directory (not app.paths.resource_path's
# PyInstaller _MEIPASS), the same pattern app/services/env_file.py uses for .env —
# _MEIPASS is a read-only temp dir wiped on exit, which would silently lose every
# trade the moment the packaged app closed. TRADE_LOG_PATH lets a deployment point
# this at a mounted persistent volume, kept separate from data/ (which also holds
# the bundled symbol_metadata.csv/stock_universe.txt) so mounting a volume there
# doesn't shadow those read-only files.
DB_PATH = Path(os.environ.get("TRADE_LOG_PATH", "data/trade_log.db"))

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
    for column in ("stop_loss_price", "take_profit_price", "exit_price", "exit_qty", "realized_pnl", "entry_price_estimate"):
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
    entry_price_estimate: float | None = None,
) -> None:
    """entry_price_estimate lets bankroll.deployed_capital() count a buy the moment it's
    submitted rather than only once it fills - without it, everything between submission
    and fill-sync looks like free money and a second candidate in the same scan cycle
    would size itself against a bankroll the first one already committed."""
    conn = _connect()
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO trades
                (order_id, submitted_at, symbol, side, qty, status, stop_loss_price, take_profit_price, entry_price_estimate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                datetime.now(UTC).isoformat(),
                symbol.upper(),
                side,
                qty,
                status,
                stop_loss_price,
                take_profit_price,
                entry_price_estimate,
            ),
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


def record_pending_exit(order_id: str, exit_order_id: str, exit_reason: str) -> None:
    """Links a standalone closing sell to the original buy row the moment the sell is
    submitted - the sell fills asynchronously, so exit_price/realized_pnl stay NULL
    until trade_sync completes them (see trades_with_pending_exit). Without this link
    the buy row would look open forever: the sell isn't one of its bracket legs, so
    the legs-based sync path would never find it."""
    conn = _connect()
    with conn:
        conn.execute(
            "UPDATE trades SET exit_order_id = ?, exit_reason = ? WHERE order_id = ?",
            (exit_order_id, exit_reason, order_id),
        )
    conn.close()


def trades_with_pending_exit() -> list[dict]:
    """Buy rows whose closing sell was submitted (exit_order_id set) but hasn't been
    confirmed filled yet (realized_pnl still NULL) - trade_sync polls these."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trades WHERE side = 'buy' AND exit_order_id IS NOT NULL AND realized_pnl IS NULL"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def open_filled_buys(symbol: str | None = None) -> list[dict]:
    """Filled buys whose exit hasn't been CONFIRMED (no realized P&L yet) - the lots a
    closing sell should link back to, and the ones position management still owns.
    Deliberately keyed on realized_pnl rather than exit_order_id: a lot with a pending
    exit link (e.g. a breakeven stop resting after 2R protection) is still open - keying
    on the link would disarm the giveback layer the moment protection was placed, and a
    later close's sell would never get linked at all. Oldest first, FIFO."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM trades WHERE side = 'buy' AND status = 'filled' AND realized_pnl IS NULL"
    params: tuple = ()
    if symbol is not None:
        query += " AND symbol = ?"
        params = (symbol.upper(),)
    rows = conn.execute(query + " ORDER BY submitted_at ASC", params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


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


def todays_realized_trades(trading_day: date) -> list[dict]:
    """Exits filled today (market time), oldest first — used to evaluate the daily
    walk-away rules (peak-profit giveback, consecutive losses) against real fills
    rather than in-memory state that wouldn't survive a restart."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trades WHERE realized_pnl IS NOT NULL AND exit_at IS NOT NULL ORDER BY exit_at ASC"
    ).fetchall()
    conn.close()

    result = []
    for row in rows:
        exit_at = datetime.fromisoformat(row["exit_at"])
        if exit_at.tzinfo is None:
            exit_at = exit_at.replace(tzinfo=UTC)
        if exit_at.astimezone(MARKET_TZ).date() == trading_day:
            result.append(dict(row))
    return result


def todays_submitted_trades(trading_day: date) -> list[dict]:
    """Every order submitted today (market time), oldest first — used for the
    "no trade in over an hour" walk-away rule."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM trades ORDER BY submitted_at ASC").fetchall()
    conn.close()

    result = []
    for row in rows:
        submitted_at = datetime.fromisoformat(row["submitted_at"])
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.replace(tzinfo=UTC)
        if submitted_at.astimezone(MARKET_TZ).date() == trading_day:
            result.append(dict(row))
    return result


def pending_order_ids() -> list[str]:
    conn = _connect()
    placeholders = ",".join("?" * len(TERMINAL_STATUSES))
    rows = conn.execute(f"SELECT order_id FROM trades WHERE status NOT IN ({placeholders})", tuple(TERMINAL_STATUSES)).fetchall()
    conn.close()
    return [row[0] for row in rows]
