import sqlite3
from datetime import UTC, datetime

from app.services.trade_log import _connect as _trades_connect


def _connect() -> sqlite3.Connection:
    conn = _trades_connect()  # ensures the `trades` table (and its migrations) exist too
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bankroll_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT
        )
        """
    )
    # Every transaction before multi-user support belonged to the one account that
    # existed then - see trade_log._connect's identical migration for user_id=1.
    try:
        conn.execute("ALTER TABLE bankroll_transactions ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # column already exists
    return conn


def _net_withdrawn(user_id: int = 1) -> float:
    """Total cash moved from savings into the bankroll, ignoring trading P&L —
    what you'd see if you just added up every withdraw/return-to-savings
    transaction, the same way a bank statement would."""
    conn = _connect()
    rows = conn.execute("SELECT kind, amount FROM bankroll_transactions WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    net = 0.0
    for kind, amount in rows:
        net += amount if kind == "withdrawal" else -amount
    return net


def _earliest_transaction_at(user_id: int = 1) -> str | None:
    conn = _connect()
    row = conn.execute("SELECT MIN(created_at) FROM bankroll_transactions WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def realized_pnl(user_id: int = 1) -> float:
    """Realized P&L from trades closed on/after the bankroll's first
    transaction. Trades from before the bankroll existed don't count against
    it — this is meant to track only what happened with the money you
    actually withdrew."""
    since_iso = _earliest_transaction_at(user_id)
    if since_iso is None:
        return 0.0
    since = datetime.fromisoformat(since_iso)

    conn = _connect()
    rows = conn.execute(
        "SELECT realized_pnl, exit_at FROM trades WHERE realized_pnl IS NOT NULL AND exit_at IS NOT NULL AND user_id = ?",
        (user_id,),
    ).fetchall()
    conn.close()

    total = 0.0
    for pnl, exit_at in rows:
        exit_dt = datetime.fromisoformat(exit_at)
        if exit_dt.tzinfo is None:
            exit_dt = exit_dt.replace(tzinfo=UTC)
        if exit_dt >= since:
            total += pnl
    return total


def current_bankroll(user_id: int = 1) -> float:
    """The bot's own self-contained "account balance" — net withdrawn (minus
    anything returned to savings) plus whatever it's made or lost trading
    since. Completely independent of the real Alpaca account equity, which
    is the whole point: this is what the bot is allowed to think it has."""
    return round(_net_withdrawn(user_id) + realized_pnl(user_id), 2)


def deployed_capital(user_id: int = 1) -> float:
    """Cost basis of the bot's currently-committed money — filled positions not yet
    exited, plus buys that are submitted but haven't filled yet (counted at the
    estimated entry price recorded at submission). Pending buys matter: without them,
    everything between submission and fill-sync looks like free money, so a second
    candidate in the same scan cycle would size itself against a bankroll the first
    one already spent. Buys only — a sell row is an exit returning money, and counting
    it as deployment (as this once did) double-charged every closed position forever."""
    conn = _connect()
    # (exit_order_id IS NULL OR realized_pnl IS NULL): a buy stays deployed until its
    # exit is CONFIRMED filled (realized_pnl set by trade_sync), not merely submitted -
    # the cash isn't back until the closing sell actually executes.
    rows = conn.execute(
        """
        SELECT status, qty, filled_qty, filled_avg_price, entry_price_estimate FROM trades
        WHERE side = 'buy'
          AND (exit_order_id IS NULL OR realized_pnl IS NULL)
          AND status NOT IN ('canceled', 'rejected', 'expired')
          AND user_id = ?
        """,
        (user_id,),
    ).fetchall()
    conn.close()

    total = 0.0
    for status, qty, filled_qty, filled_avg_price, entry_price_estimate in rows:
        if status == "filled" and filled_qty is not None and filled_avg_price is not None:
            total += filled_qty * filled_avg_price
        elif status != "filled" and entry_price_estimate is not None:
            total += (qty or 0) * entry_price_estimate
    return round(total, 2)


def available_to_trade(user_id: int = 1) -> float:
    """What's left to open a NEW position with — the bankroll minus whatever
    is already committed to open positions. Can go to zero (or, if a stop
    slips, briefly negative after a loss) — matches how a real small account
    behaves, not a hard floor enforced mid-trade."""
    return round(current_bankroll(user_id) - deployed_capital(user_id), 2)


def record_withdrawal(amount: float, account_equity: float, note: str | None = None, user_id: int = 1) -> None:
    """"Withdraws" money from the real (paper) account into the bot's
    bankroll. Bounded by what's actually left in the account outside the
    bankroll — you can't withdraw money you don't have, same as a real bank."""
    if amount <= 0:
        raise ValueError("Withdrawal amount must be positive.")
    savings_available = round(account_equity - current_bankroll(user_id), 2)
    if amount > savings_available:
        raise ValueError(f"Only ${savings_available:,.2f} is available to withdraw from your account.")

    conn = _connect()
    with conn:
        conn.execute(
            "INSERT INTO bankroll_transactions (created_at, kind, amount, note, user_id) VALUES (?, 'withdrawal', ?, ?, ?)",
            (datetime.now(UTC).isoformat(), amount, note, user_id),
        )
    conn.close()


def record_return_to_savings(amount: float, note: str | None = None, user_id: int = 1) -> None:
    """Moves money back out of the bankroll, e.g. to lock in profits or
    scale back down. Bounded by what isn't currently tied up in an open
    position — same as not being able to withdraw cash that's invested."""
    if amount <= 0:
        raise ValueError("Return amount must be positive.")
    available = available_to_trade(user_id)
    if amount > available:
        raise ValueError(f"Only ${available:,.2f} of the bankroll is available to return — the rest is in open positions.")

    conn = _connect()
    with conn:
        conn.execute(
            "INSERT INTO bankroll_transactions (created_at, kind, amount, note, user_id) VALUES (?, 'return_to_savings', ?, ?, ?)",
            (datetime.now(UTC).isoformat(), amount, note, user_id),
        )
    conn.close()


def transactions(limit: int = 50, user_id: int = 1) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM bankroll_transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
