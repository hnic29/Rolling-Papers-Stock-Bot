from datetime import timedelta
from unittest.mock import MagicMock, patch

from app.models import Signal, TradeRequest
from app.services import trade_log
from app.services.bot import TradingBot


def test_trades_today_resets_on_new_trading_day():
    bot = TradingBot()
    bot.status.trades_today = 3
    bot._trading_day = bot._trading_day - timedelta(days=1)

    bot.refresh_status()

    assert bot.status.trades_today == 0


def test_trades_today_persists_within_same_trading_day():
    bot = TradingBot()
    bot.status.trades_today = 3

    bot.refresh_status()

    assert bot.status.trades_today == 3


def test_successful_trade_is_recorded_in_history(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot = TradingBot()
    bot.status.daily_pnl = 0.0  # clear of the daily-loss guard for this test

    fake_order = MagicMock()
    fake_order.id = "order-123"
    fake_order.symbol = "AAPL"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        result = bot.submit_trade(TradeRequest(symbol="AAPL", qty=1, side=Signal.buy))

    assert result["id"] == "order-123"
    trades = trade_log.list_trades()
    assert len(trades) == 1
    assert trades[0]["order_id"] == "order-123"
    assert trades[0]["symbol"] == "AAPL"
    assert trades[0]["side"] == "buy"
    assert trades[0]["status"] == "accepted"
