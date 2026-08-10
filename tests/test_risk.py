import pytest

from app.models import Signal, TradeRequest
from app.services.risk import RiskManager


def test_risk_rejects_hold_order():
    with pytest.raises(ValueError, match="Only buy and sell"):
        RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.hold), 0, 0)


def test_risk_rejects_max_trade_count():
    with pytest.raises(ValueError, match="Max trades"):
        RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.buy), 5, 0)


def test_risk_rejects_max_daily_loss():
    with pytest.raises(ValueError, match="Max daily loss"):
        RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.buy), 0, -150)


def test_risk_allows_trade_within_daily_loss_limit():
    RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.buy), 0, -50)


def test_risk_allows_valid_bracket_order():
    trade = TradeRequest(symbol="AAPL", qty=1, side=Signal.buy, stop_loss_price=95, take_profit_price=110)
    RiskManager().validate(trade, 0, 0)


def test_risk_rejects_bracket_order_for_crypto():
    trade = TradeRequest(symbol="BTC/USD", qty=1, side=Signal.buy, stop_loss_price=60000, take_profit_price=70000)
    with pytest.raises(ValueError, match="crypto"):
        RiskManager().validate(trade, 0, 0)


def test_risk_rejects_bracket_order_for_fractional_qty():
    trade = TradeRequest(symbol="AAPL", qty=1.5, side=Signal.buy, stop_loss_price=95, take_profit_price=110)
    with pytest.raises(ValueError, match="whole-share"):
        RiskManager().validate(trade, 0, 0)


def test_risk_rejects_inverted_stop_and_target_on_buy():
    trade = TradeRequest(symbol="AAPL", qty=1, side=Signal.buy, stop_loss_price=110, take_profit_price=95)
    with pytest.raises(ValueError, match="Stop-loss must be below take-profit"):
        RiskManager().validate(trade, 0, 0)


def test_risk_rejects_inverted_stop_and_target_on_sell():
    trade = TradeRequest(symbol="AAPL", qty=1, side=Signal.sell, stop_loss_price=95, take_profit_price=110)
    with pytest.raises(ValueError, match="Stop-loss must be above take-profit"):
        RiskManager().validate(trade, 0, 0)
