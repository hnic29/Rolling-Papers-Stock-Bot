from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.services import bankroll, trade_log, trade_sync


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")


def _order(status="filled", filled_avg_price=None, filled_qty=None, legs=None, order_id="o-1"):
    order = MagicMock()
    order.id = order_id
    order.status = status
    order.filled_avg_price = filled_avg_price
    order.filled_qty = filled_qty
    order.filled_at = datetime(2026, 8, 24, 15, 0, tzinfo=UTC)
    order.legs = legs or []
    return order


def test_sync_records_an_entry_fill():
    trade_log.record_trade(order_id="buy-1", symbol="ACHR", side="buy", qty=10, status="accepted", entry_price_estimate=5.0)

    broker = MagicMock()
    broker.get_order.return_value = _order(status="filled", filled_avg_price=5.05, filled_qty=10)

    trade_sync.sync_orders(broker)

    trade = trade_log.list_trades()[0]
    assert trade["status"] == "filled"
    assert trade["filled_avg_price"] == 5.05
    assert trade["filled_qty"] == 10


def test_sync_completes_a_pending_exit_and_realizes_pnl():
    """The standalone-sell exit path: position management submits a market sell and
    links it via record_pending_exit; once that sell fills, sync computes the realized
    P&L on the buy row - which is what the walk-away rules and bankroll read."""
    trade_log.record_trade(order_id="buy-1", symbol="ACHR", side="buy", qty=10, status="filled")
    trade_log.update_fill(order_id="buy-1", status="filled", filled_avg_price=5.0, filled_qty=10, filled_at=datetime.now(UTC).isoformat())
    trade_log.record_pending_exit("buy-1", "sell-1", "exit_signal")

    broker = MagicMock()
    broker.get_order.return_value = _order(status="filled", filled_avg_price=5.5, filled_qty=10, order_id="sell-1")

    trade_sync.sync_orders(broker)

    trade = next(t for t in trade_log.list_trades() if t["order_id"] == "buy-1")
    assert trade["exit_price"] == 5.5
    assert trade["realized_pnl"] == 5.0  # (5.5 - 5.0) * 10
    assert trade["exit_reason"] == "exit_signal"
    assert bankroll.deployed_capital() == 0.0  # confirmed exit frees the bankroll


def test_sync_leaves_a_pending_exit_alone_until_the_sell_fills():
    trade_log.record_trade(order_id="buy-1", symbol="ACHR", side="buy", qty=10, status="filled")
    trade_log.update_fill(order_id="buy-1", status="filled", filled_avg_price=5.0, filled_qty=10, filled_at=datetime.now(UTC).isoformat())
    trade_log.record_pending_exit("buy-1", "sell-1", "exit_signal")

    broker = MagicMock()
    broker.get_order.return_value = _order(status="accepted", filled_avg_price=None, filled_qty=None, order_id="sell-1")

    trade_sync.sync_orders(broker)

    trade = next(t for t in trade_log.list_trades() if t["order_id"] == "buy-1")
    assert trade["realized_pnl"] is None
    assert bankroll.deployed_capital() == 50.0  # still deployed until the exit confirms


def test_sync_notifies_with_realized_pnl_when_an_exit_confirms(monkeypatch):
    trade_log.record_trade(order_id="buy-1", symbol="ACHR", side="buy", qty=10, status="filled")
    trade_log.update_fill(order_id="buy-1", status="filled", filled_avg_price=5.0, filled_qty=10, filled_at=datetime.now(UTC).isoformat())
    trade_log.record_pending_exit("buy-1", "sell-1", "exit_signal")

    sent = []
    monkeypatch.setattr("app.services.trade_sync.notify.send", lambda topic, title, message, **kw: sent.append((title, message)))

    broker = MagicMock()
    broker.get_order.return_value = _order(status="filled", filled_avg_price=5.5, filled_qty=10, order_id="sell-1")

    trade_sync.sync_orders(broker)

    assert len(sent) == 1
    title, message = sent[0]
    assert "+$5.00" in title and "ACHR" in title
    assert "exit signal" in message


def test_sync_survives_a_broker_error_on_one_order():
    trade_log.record_trade(order_id="buy-1", symbol="ACHR", side="buy", qty=10, status="accepted")

    broker = MagicMock()
    broker.get_order.side_effect = Exception("alpaca hiccup")

    trade_sync.sync_orders(broker)  # must not raise

    assert trade_log.list_trades()[0]["status"] == "accepted"  # unchanged, retried next cycle
