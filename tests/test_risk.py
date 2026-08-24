import pytest

from app.models import Signal, TradeRequest
from app.services import bankroll, trade_log
from app.services.risk import RiskManager


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    # RiskManager now reads bankroll.current_bankroll(), which is backed by the same
    # trade_log SQLite DB bankroll.py uses - without this, these tests would silently
    # read/write whatever's sitting in the real local dev database.
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")


def _fund(monkeypatch, amount):
    monkeypatch.setattr(bankroll, "current_bankroll", lambda: amount)


def test_risk_rejects_hold_order():
    with pytest.raises(ValueError, match="Only buy and sell"):
        RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.hold), 0, 0)


def test_risk_rejects_max_trade_count():
    with pytest.raises(ValueError, match="Max trades"):
        RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.buy), 5, 0)


def test_risk_never_blocks_a_sell_at_the_daily_trade_cap(monkeypatch):
    """The trade cap exists to stop opening NEW risk - blocking sells meant that once
    it tripped, an exit signal could no longer close a position."""
    _fund(monkeypatch, 2000.0)
    RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.sell), 5, 0)


def test_risk_never_blocks_a_sell_past_the_daily_loss_limit(monkeypatch):
    """Same principle for the loss limit - a bot that's down its daily max is exactly
    the bot that most needs to be able to close its losing positions."""
    _fund(monkeypatch, 2000.0)
    RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.sell), 0, -500.0)


def test_risk_rejects_max_daily_loss(monkeypatch):
    _fund(monkeypatch, 2000.0)  # 6% cap = $120
    with pytest.raises(ValueError, match="Max daily loss"):
        RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.buy), 0, -150)


def test_risk_allows_trade_within_daily_loss_limit(monkeypatch):
    _fund(monkeypatch, 2000.0)  # 6% cap = $120
    RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.buy), 0, -50)


def test_risk_skips_the_daily_loss_check_at_a_zero_bankroll(monkeypatch):
    """A $0 bankroll means a $0 cap - without this guard, any daily P&L <= $0.00 would
    incorrectly trip "max daily loss" right at the boundary, even with no actual loss."""
    _fund(monkeypatch, 0.0)
    RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.sell), 0, 0.0)


def test_risk_rejects_a_position_over_the_percentage_cap(monkeypatch):
    _fund(monkeypatch, 1000.0)  # 20% cap = $200
    trade = TradeRequest(symbol="AAPL", qty=10, side=Signal.buy, estimated_price=25.0)  # $250
    with pytest.raises(ValueError, match="max position value"):
        RiskManager().validate(trade, 0, 0)


def test_risk_allows_a_position_within_the_percentage_cap(monkeypatch):
    _fund(monkeypatch, 1000.0)  # 20% cap = $200
    trade = TradeRequest(symbol="AAPL", qty=5, side=Signal.buy, estimated_price=25.0)  # $125
    RiskManager().validate(trade, 0, 0)


def test_risk_allows_valid_bracket_order(monkeypatch):
    _fund(monkeypatch, 100_000.0)
    trade = TradeRequest(symbol="AAPL", qty=1, side=Signal.buy, stop_loss_price=95, take_profit_price=110)
    RiskManager().validate(trade, 0, 0)


def test_risk_rejects_bracket_order_for_fractional_qty(monkeypatch):
    _fund(monkeypatch, 100_000.0)
    trade = TradeRequest(symbol="AAPL", qty=1.5, side=Signal.buy, stop_loss_price=95, take_profit_price=110)
    with pytest.raises(ValueError, match="whole-share"):
        RiskManager().validate(trade, 0, 0)


def test_risk_rejects_inverted_stop_and_target_on_buy(monkeypatch):
    _fund(monkeypatch, 100_000.0)
    trade = TradeRequest(symbol="AAPL", qty=1, side=Signal.buy, stop_loss_price=110, take_profit_price=95)
    with pytest.raises(ValueError, match="Stop-loss must be below take-profit"):
        RiskManager().validate(trade, 0, 0)


def test_risk_rejects_inverted_stop_and_target_on_sell(monkeypatch):
    _fund(monkeypatch, 100_000.0)
    trade = TradeRequest(symbol="AAPL", qty=1, side=Signal.sell, stop_loss_price=95, take_profit_price=110)
    with pytest.raises(ValueError, match="Stop-loss must be above take-profit"):
        RiskManager().validate(trade, 0, 0)
