import pytest

from app.models import Signal, TradeRequest
from app.services.risk import RiskManager


def test_risk_rejects_hold_order():
    with pytest.raises(ValueError, match="Only buy and sell"):
        RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.hold), 0, 0)


def test_risk_rejects_max_trade_count():
    with pytest.raises(ValueError, match="Max trades"):
        RiskManager().validate(TradeRequest(symbol="AAPL", qty=1, side=Signal.buy), 5, 0)
