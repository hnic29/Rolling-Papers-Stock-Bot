"""Reconciles the local trade log against Alpaca's order state: entry fills, bracket-leg
exits (stop/target), and standalone closing sells (exit signals from position
management).

Lives here as a service — NOT inside the /api/trades/history/sync route — because the
automation loop must run it every cycle regardless of whether anyone has the dashboard
open. When this only existed in the route, a headless deployment never recorded a
single fill or exit: deployed_capital() read $0 forever (gutting the bankroll gate) and
the walk-away rules never saw a realized loss.
"""

from app.brokers.alpaca_broker import AlpacaBroker
from app.services import trade_log


def _enum_str(value) -> str:
    return str(value.value if hasattr(value, "value") else value)


def sync_orders(broker: AlpacaBroker) -> None:
    """One reconciliation pass. Each phase tolerates per-order failures — one
    unreachable order should never stall syncing the rest."""

    # Phase 1: entry fills — pending orders that may have filled/canceled since last look.
    for order_id in trade_log.pending_order_ids():
        try:
            order = broker.get_order(order_id)
        except Exception:
            continue
        trade_log.update_fill(
            order_id=order_id,
            status=_enum_str(order.status),
            filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price is not None else None,
            filled_qty=float(order.filled_qty) if order.filled_qty is not None else None,
            filled_at=order.filled_at.isoformat() if order.filled_at is not None else None,
        )

    # Phase 2: bracket-leg exits — a resting stop/target child order filled.
    for trade in trade_log.trades_awaiting_exit():
        try:
            order = broker.get_order(trade["order_id"])
        except Exception:
            continue
        filled_leg = next(
            (leg for leg in (order.legs or []) if _enum_str(leg.status) == "filled"),
            None,
        )
        if filled_leg is None or filled_leg.filled_avg_price is None:
            continue
        exit_reason = "target" if _enum_str(filled_leg.order_type) == "limit" else "stop"
        exit_price = float(filled_leg.filled_avg_price)
        exit_qty = float(filled_leg.filled_qty) if filled_leg.filled_qty is not None else trade["qty"]
        entry_price = trade["filled_avg_price"] or 0.0
        pnl = (exit_price - entry_price) * exit_qty if trade["side"] == "buy" else (entry_price - exit_price) * exit_qty
        trade_log.record_exit(
            order_id=trade["order_id"],
            exit_order_id=str(filled_leg.id),
            exit_price=exit_price,
            exit_qty=exit_qty,
            exit_at=filled_leg.filled_at.isoformat() if filled_leg.filled_at is not None else None,
            exit_reason=exit_reason,
            realized_pnl=round(pnl, 2),
        )

    # Phase 3: standalone closing sells — position management submitted a market sell
    # and linked it via record_pending_exit; complete the exit once that sell fills.
    for trade in trade_log.trades_with_pending_exit():
        try:
            exit_order = broker.get_order(trade["exit_order_id"])
        except Exception:
            continue
        if _enum_str(exit_order.status) != "filled" or exit_order.filled_avg_price is None:
            continue
        exit_price = float(exit_order.filled_avg_price)
        # This lot's own share count, NOT the sell's total fill - one closing sell can
        # cover several buy lots of the same symbol, and each lot's P&L is on its shares.
        exit_qty = float(trade["filled_qty"] if trade["filled_qty"] is not None else trade["qty"])
        entry_price = trade["filled_avg_price"] or 0.0
        pnl = (exit_price - entry_price) * exit_qty
        trade_log.record_exit(
            order_id=trade["order_id"],
            exit_order_id=trade["exit_order_id"],
            exit_price=exit_price,
            exit_qty=exit_qty,
            exit_at=exit_order.filled_at.isoformat() if exit_order.filled_at is not None else None,
            exit_reason=trade["exit_reason"] or "exit_signal",
            realized_pnl=round(pnl, 2),
        )
