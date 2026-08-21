from datetime import UTC, datetime

import pytest

from app.services import bankroll, trade_log


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")


def _open_trade(order_id, symbol, qty, filled_avg_price):
    trade_log.record_trade(order_id=order_id, symbol=symbol, side="buy", qty=qty, status="filled")
    trade_log.update_fill(order_id=order_id, status="filled", filled_avg_price=filled_avg_price, filled_qty=qty, filled_at=datetime.now(UTC).isoformat())


def _closed_trade(order_id, symbol, qty, filled_avg_price, realized_pnl, exit_at=None):
    _open_trade(order_id, symbol, qty, filled_avg_price)
    trade_log.record_exit(
        order_id=order_id, exit_order_id=f"{order_id}-exit", exit_price=filled_avg_price, exit_qty=qty,
        exit_at=(exit_at or datetime.now(UTC)).isoformat(), exit_reason="target", realized_pnl=realized_pnl,
    )


def test_bankroll_starts_at_zero_with_no_transactions():
    assert bankroll.current_bankroll() == 0.0
    assert bankroll.available_to_trade() == 0.0


def test_withdrawal_increases_the_bankroll():
    bankroll.record_withdrawal(2000.0, account_equity=50_000.0)

    assert bankroll.current_bankroll() == 2000.0
    assert bankroll.available_to_trade() == 2000.0


def test_withdrawal_cannot_exceed_whats_actually_in_the_account():
    with pytest.raises(ValueError, match="available to withdraw"):
        bankroll.record_withdrawal(2000.0, account_equity=1000.0)

    assert bankroll.current_bankroll() == 0.0  # rejected withdrawal wrote nothing


def test_a_second_withdrawal_is_bounded_by_remaining_savings():
    bankroll.record_withdrawal(2000.0, account_equity=5000.0)

    # Only $3,000 of the original $5,000 account is left outside the bankroll.
    with pytest.raises(ValueError, match="available to withdraw"):
        bankroll.record_withdrawal(3001.0, account_equity=5000.0)

    bankroll.record_withdrawal(3000.0, account_equity=5000.0)  # exactly what's left - should succeed
    assert bankroll.current_bankroll() == 5000.0


def test_withdrawal_amount_must_be_positive():
    with pytest.raises(ValueError):
        bankroll.record_withdrawal(0, account_equity=10_000.0)
    with pytest.raises(ValueError):
        bankroll.record_withdrawal(-100, account_equity=10_000.0)


def test_realized_trading_pnl_flows_into_the_bankroll():
    bankroll.record_withdrawal(2000.0, account_equity=50_000.0)
    _closed_trade("t1", "ACHR", 100, 5.0, realized_pnl=150.0)

    assert bankroll.realized_pnl() == 150.0
    assert bankroll.current_bankroll() == 2150.0


def test_trades_closed_before_the_bankroll_existed_do_not_count():
    _closed_trade("old-trade", "AAPL", 10, 200.0, realized_pnl=500.0, exit_at=datetime(2020, 1, 1, tzinfo=UTC))
    bankroll.record_withdrawal(1000.0, account_equity=10_000.0)

    assert bankroll.realized_pnl() == 0.0
    assert bankroll.current_bankroll() == 1000.0


def test_deployed_capital_reflects_open_positions_only():
    bankroll.record_withdrawal(2000.0, account_equity=10_000.0)
    _open_trade("open-1", "ACHR", 100, 5.0)  # $500 open, not yet exited
    _closed_trade("closed-1", "ACHR", 50, 5.0, realized_pnl=25.0)  # already closed - not "deployed"

    assert bankroll.deployed_capital() == 500.0
    assert bankroll.current_bankroll() == 2025.0
    assert bankroll.available_to_trade() == 1525.0  # 2025 - 500 deployed


def test_return_to_savings_decreases_the_bankroll():
    bankroll.record_withdrawal(2000.0, account_equity=10_000.0)
    bankroll.record_return_to_savings(500.0)

    assert bankroll.current_bankroll() == 1500.0


def test_return_to_savings_cannot_exceed_whats_not_tied_up_in_open_positions():
    bankroll.record_withdrawal(2000.0, account_equity=10_000.0)
    _open_trade("open-1", "ACHR", 100, 15.0)  # $1,500 deployed, $500 still available

    with pytest.raises(ValueError, match="available to return"):
        bankroll.record_return_to_savings(501.0)

    bankroll.record_return_to_savings(500.0)  # exactly what's available - should succeed
    assert bankroll.current_bankroll() == 1500.0


def test_return_to_savings_amount_must_be_positive():
    bankroll.record_withdrawal(1000.0, account_equity=10_000.0)
    with pytest.raises(ValueError):
        bankroll.record_return_to_savings(0)


def test_transactions_are_listed_most_recent_first():
    bankroll.record_withdrawal(1000.0, account_equity=10_000.0, note="first")
    bankroll.record_withdrawal(500.0, account_equity=10_000.0, note="second")

    txns = bankroll.transactions()
    assert [t["note"] for t in txns] == ["second", "first"]
    assert all(t["kind"] == "withdrawal" for t in txns)
