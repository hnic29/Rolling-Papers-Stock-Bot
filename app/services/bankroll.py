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
    return conn


def _net_withdrawn() -> float:
    """Total cash moved from savings into the bankroll, ignoring trading P&L —
    what you'd see if you just added up every withdraw/return-to-savings
    transaction, the same way a bank statement would."""
    conn = _connect()
    rows = conn.execute("SELECT kind, amount FROM bankroll_transactions").fetchall()
    conn.close()
    net = 0.0
    for kind, amount in rows:
        net += amount if kind == "withdrawal" else -amount
    return net


def _earliest_transaction_at() -> str | None:
    conn = _connect()
    row = conn.execute("SELECT MIN(created_at) FROM bankroll_transactions").fetchone()
    conn.close()
    return row[0] if row else None


def realized_pnl() -> float:
    """Realized P&L from trades closed on/after the bankroll's first
    transaction. Trades from before the bankroll existed don't count against
    it — this is meant to track only what happened with the money you
    actually withdrew."""
    since_iso = _earliest_transaction_at()
    if since_iso is None:
        return 0.0
    since = datetime.fromisoformat(since_iso)

    conn = _connect()
    rows = conn.execute("SELECT realized_pnl, exit_at FROM trades WHERE realized_pnl IS NOT NULL AND exit_at IS NOT NULL").fetchall()
    conn.close()

    total = 0.0
    for pnl, exit_at in rows:
        exit_dt = datetime.fromisoformat(exit_at)
        if exit_dt.tzinfo is None:
            exit_dt = exit_dt.replace(tzinfo=UTC)
        if exit_dt >= since:
            total += pnl
    return total


def current_bankroll() -> float:
    """The bot's own self-contained "account balance" — net withdrawn (minus
    anything returned to savings) plus whatever it's made or lost trading
    since. Completely independent of the real Alpaca account equity, which
    is the whole point: this is what the bot is allowed to think it has."""
    return round(_net_withdrawn() + realized_pnl(), 2)


def deployed_capital() -> float:
    """Cost basis of the bot's currently-open positions (filled, not yet
    exited) — money that's tied up and can't be withdrawn back to savings
    or spent on a new trade until the position closes."""
    conn = _connect()
    rows = conn.execute(
        """
        SELECT filled_qty, filled_avg_price FROM trades
        WHERE status = 'filled' AND exit_order_id IS NULL
          AND filled_qty IS NOT NULL AND filled_avg_price IS NOT NULL
        """
    ).fetchall()
    conn.close()
    return round(sum((qty or 0) * (price or 0) for qty, price in rows), 2)


def available_to_trade() -> float:
    """What's left to open a NEW position with — the bankroll minus whatever
    is already committed to open positions. Can go to zero (or, if a stop
    slips, briefly negative after a loss) — matches how a real small account
    behaves, not a hard floor enforced mid-trade."""
    return round(current_bankroll() - deployed_capital(), 2)


def record_withdrawal(amount: float, account_equity: float, note: str | None = None) -> None:
    """"Withdraws" money from the real (paper) account into the bot's
    bankroll. Bounded by what's actually left in the account outside the
    bankroll — you can't withdraw money you don't have, same as a real bank."""
    if amount <= 0:
        raise ValueError("Withdrawal amount must be positive.")
    savings_available = round(account_equity - current_bankroll(), 2)
    if amount > savings_available:
        raise ValueError(f"Only ${savings_available:,.2f} is available to withdraw from your account.")

    conn = _connect()
    with conn:
        conn.execute(
            "INSERT INTO bankroll_transactions (created_at, kind, amount, note) VALUES (?, 'withdrawal', ?, ?)",
            (datetime.now(UTC).isoformat(), amount, note),
        )
    conn.close()


def record_return_to_savings(amount: float, note: str | None = None) -> None:
    """Moves money back out of the bankroll, e.g. to lock in profits or
    scale back down. Bounded by what isn't currently tied up in an open
    position — same as not being able to withdraw cash that's invested."""
    if amount <= 0:
        raise ValueError("Return amount must be positive.")
    available = available_to_trade()
    if amount > available:
        raise ValueError(f"Only ${available:,.2f} of the bankroll is available to return — the rest is in open positions.")

    conn = _connect()
    with conn:
        conn.execute(
            "INSERT INTO bankroll_transactions (created_at, kind, amount, note) VALUES (?, 'return_to_savings', ?, ?)",
            (datetime.now(UTC).isoformat(), amount, note),
        )
    conn.close()


def transactions(limit: int = 50) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM bankroll_transactions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]
