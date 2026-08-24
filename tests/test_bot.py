from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models import Candle, PullbackSetup, ScannerResponse, ScannerResult, Signal, StockCandidate, StrategyDecision, TradeRequest
from app.services import bankroll, trade_log
from app.services.bot import TradingBot


def _fund_bankroll(monkeypatch, amount=100_000.0):
    """These tests exercise other behavior (market-closed checks, order
    submission, walk-away timing) and aren't about the bankroll feature
    itself - give them a bankroll large enough to never be the constraint,
    rather than every one of them needing its own withdrawal setup."""
    monkeypatch.setattr(bankroll, "available_to_trade", lambda: amount)


def _candidate(symbol, score=4):
    return ScannerResult(
        symbol=symbol, price=5.0, percent_change=15.0, total_volume=2_000_000,
        score=score, signal=Signal.buy, reasons=["qualifies"],
    )


def _setup(symbol, candles=None):
    candidate = StockCandidate(symbol=symbol, price=5.0, percent_change=15.0, relative_volume=6.0, total_volume=2_000_000)
    return PullbackSetup(
        candidate=candidate, candles=candles or [], ema9=4.9, macd=0.01, vwap=4.9,
        high_of_day=5.05, pullback_low=4.9, proposed_entry=5.0, proposed_stop=4.9,
    )


_RED_CANDLE = [Candle(open=5.2, high=5.3, low=5.0, close=5.0, volume=1000)]


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
    _fund_bankroll(monkeypatch)

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


def test_submit_trade_rejects_a_manual_buy_with_no_bankroll(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr(bankroll, "available_to_trade", lambda: 0.0)

    bot = TradingBot()
    bot.status.daily_pnl = 0.0

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        with pytest.raises(ValueError, match="No bankroll available"):
            bot.submit_trade(TradeRequest(symbol="AAPL", qty=1, side=Signal.buy))

    MockBroker.return_value.submit_market_order.assert_not_called()


def test_submit_trade_rejects_a_manual_buy_that_exceeds_the_bankroll(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr(bankroll, "available_to_trade", lambda: 100.0)

    bot = TradingBot()
    bot.status.daily_pnl = 0.0

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        # 10 shares @ $50 estimated = $500, well over the $100 available.
        with pytest.raises(ValueError, match="bankroll"):
            bot.submit_trade(TradeRequest(symbol="AAPL", qty=10, side=Signal.buy, estimated_price=50.0))

    MockBroker.return_value.submit_market_order.assert_not_called()


def test_submit_trade_allows_a_sell_regardless_of_bankroll():
    """You should always be able to close a position, even with an empty
    bankroll - the gate only applies to opening new exposure."""
    bot = TradingBot()
    bot.status.daily_pnl = 0.0

    fake_order = MagicMock()
    fake_order.id = "sell-order-1"
    fake_order.symbol = "AAPL"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker, patch("app.services.bot.bankroll") as MockBankroll:
        MockBankroll.available_to_trade.return_value = 0.0
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        result = bot.submit_trade(TradeRequest(symbol="AAPL", qty=1, side=Signal.sell))

    assert result["id"] == "sell-order-1"


def test_auto_cycle_is_a_no_op_when_auto_trading_is_disabled():
    bot = TradingBot()
    bot.status.auto_trading_enabled = False

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        bot.auto_cycle()

    MockBroker.return_value.client.get_clock.assert_not_called()
    MockBroker.return_value.positions_as_dicts.assert_not_called()


def test_auto_cycle_skips_when_market_is_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _fund_bankroll(monkeypatch)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=False)
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        bot.auto_cycle()

    assert "market is closed" in bot.status.last_message.lower()


def test_auto_cycle_skips_outside_the_trading_window(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: False)

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        bot.auto_cycle()

    assert "trading window" in bot.status.last_message.lower()
    MockBroker.return_value.submit_market_order.assert_not_called()


def _record_realized_trade(order_id: str, pnl: float, exit_at) -> None:
    trade_log.record_trade(order_id=order_id, symbol="ACHR", side="buy", qty=100, status="filled")
    trade_log.record_exit(
        order_id=order_id, exit_order_id=f"{order_id}-exit", exit_price=5.0, exit_qty=100,
        exit_at=exit_at.isoformat(), exit_reason="stop", realized_pnl=pnl,
    )


def test_session_state_walks_away_after_three_losses_in_a_row(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    bot = TradingBot()
    now = datetime.now(UTC)
    _record_realized_trade("t1", 50.0, now)
    _record_realized_trade("t2", -20.0, now)
    _record_realized_trade("t3", -15.0, now)
    _record_realized_trade("t4", -10.0, now)

    bot._update_session_state()

    assert bot.status.consecutive_losses == 3
    assert bot.status.walked_away_for_day is True
    assert "losing trades in a row" in bot.status.walk_away_reason


def test_session_state_walks_away_after_giving_back_half_the_peak_profit(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    bot = TradingBot()
    now = datetime.now(UTC)
    _record_realized_trade("t1", 100.0, now)  # peak = 100
    _record_realized_trade("t2", -60.0, now)  # running = 40, given back 60% of peak

    bot._update_session_state()

    assert bot.status.peak_daily_pnl == 100.0
    assert bot.status.walked_away_for_day is True
    assert "peak profit" in bot.status.walk_away_reason


def test_session_state_stays_active_for_a_normal_winning_day(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    bot = TradingBot()
    now = datetime.now(UTC)
    _record_realized_trade("t1", 50.0, now)
    _record_realized_trade("t2", -10.0, now)
    _record_realized_trade("t3", 30.0, now)

    bot._update_session_state()

    assert bot.status.consecutive_losses == 0
    assert bot.status.walked_away_for_day is False


def test_auto_cycle_walks_away_after_an_hour_with_no_trades(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot._auto_trading_started_at = datetime.now(UTC) - timedelta(minutes=61)
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        bot.auto_cycle()

    assert "momentum has cooled" in bot.status.last_message.lower()
    assert bot.status.walked_away_for_day is True
    MockBroker.return_value.submit_market_order.assert_not_called()


def test_auto_cycle_does_not_walk_away_within_the_first_hour(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _fund_bankroll(monkeypatch)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot._auto_trading_started_at = datetime.now(UTC) - timedelta(minutes=10)
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=False)
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        bot.auto_cycle()

    assert bot.status.walked_away_for_day is False
    assert "market is closed" in bot.status.last_message.lower()


def test_auto_cycle_stays_paused_once_walked_away_for_the_day(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.walked_away_for_day = True
    bot.status.walk_away_reason = "3 losing trades in a row"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        bot.auto_cycle()

    assert "3 losing trades in a row" in bot.status.last_message
    MockBroker.return_value.submit_market_order.assert_not_called()


def test_auto_cycle_places_a_bracket_trade_for_a_real_buy_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _fund_bankroll(monkeypatch)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)

    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[_candidate("ACHR")]))
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner: _setup(symbol))
    monkeypatch.setattr(bot.strategy, "evaluate", lambda setup: StrategyDecision(signal=Signal.buy, confidence=0.8, reasons=["ok"], risk_per_share=0.1, first_target=5.2))

    fake_order = MagicMock()
    fake_order.id = "auto-order-1"
    fake_order.symbol = "ACHR"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

        _, kwargs = MockBroker.return_value.submit_market_order.call_args
        assert kwargs["stop_loss_price"] == 4.9
        # No resting take-profit on an auto-entered buy - a winner isn't capped at the
        # first target, it's held until _manage_open_positions sees a real exit signal.
        assert kwargs["take_profit_price"] is None

    assert "ACHR" in bot.status.last_message
    assert bot.status.last_automation_run_at is not None
    trades = trade_log.list_trades()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "ACHR"


def test_auto_cycle_places_no_trade_when_bankroll_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr(bankroll, "available_to_trade", lambda: 0.0)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        bot.auto_cycle()

    assert "no bankroll available" in bot.status.last_message.lower()
    MockBroker.return_value.submit_market_order.assert_not_called()  # never gets to scanning for new entries


def test_auto_cycle_caps_position_size_to_available_bankroll(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    # $5/share entry - $15 available covers only 3 shares, well under what
    # the risk/capital settings alone would otherwise allow.
    monkeypatch.setattr(bankroll, "available_to_trade", lambda: 15.0)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)

    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[_candidate("ACHR")]))
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner: _setup(symbol))
    monkeypatch.setattr(bot.strategy, "evaluate", lambda setup: StrategyDecision(signal=Signal.buy, confidence=0.8, reasons=["ok"], risk_per_share=0.1, first_target=5.2))

    fake_order = MagicMock()
    fake_order.id = "auto-order-2"
    fake_order.symbol = "ACHR"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

        args, _ = MockBroker.return_value.submit_market_order.call_args
        assert args[1] == 3  # int(15.0 // 5.0), not whatever risk/capital sizing alone would pick


def test_auto_cycle_skips_symbols_already_held(monkeypatch, tmp_path):
    """A symbol you already hold is never re-entered as a new buy - held symbols only
    ever go through _manage_open_positions (to decide whether to exit), not the
    entry-scoring path."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)
    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[_candidate("ACHR")]))
    # No exit signal, so the held position should also stay open (no sell either).
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner: _setup(symbol))

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "ACHR", "qty": 10}]
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

    MockBroker.return_value.submit_market_order.assert_not_called()


def test_manage_open_positions_closes_on_a_red_candle_exit_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner: _setup(symbol, _RED_CANDLE))

    fake_order = MagicMock()
    fake_order.id = "exit-order-1"
    fake_order.symbol = "ACHR"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        exited = bot._manage_open_positions([{"symbol": "ACHR", "qty": 10}])

        args, _ = MockBroker.return_value.submit_market_order.call_args
        assert args[0] == "ACHR"
        assert args[1] == 10
        assert args[2] == "sell"

    assert exited == ["ACHR"]


def test_manage_open_positions_leaves_a_quiet_position_open(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    bot = TradingBot()
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner: _setup(symbol))

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        exited = bot._manage_open_positions([{"symbol": "ACHR", "qty": 10}])

    assert exited == []
    MockBroker.return_value.submit_market_order.assert_not_called()


def test_manage_open_positions_works_even_when_bankroll_is_empty(tmp_path, monkeypatch):
    """An empty available-to-trade balance (e.g. fully deployed into open positions)
    should never stop a position from being watched for a real exit - that's exactly
    the moment managing it matters most. manage_open_positions() doesn't even look at
    bankroll, unlike auto_cycle() (new entries), which correctly does."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr(bankroll, "available_to_trade", lambda: 0.0)

    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner: _setup(symbol, _RED_CANDLE))

    fake_order = MagicMock()
    fake_order.id = "exit-order-2"
    fake_order.symbol = "ACHR"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "ACHR", "qty": 10}]
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.manage_open_positions()

        args, _ = MockBroker.return_value.submit_market_order.call_args
        assert args == ("ACHR", 10, "sell")

    assert "Closed ACHR" in bot.status.last_message


def test_manage_open_positions_works_even_when_walked_away_for_the_day(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    bot.status.walked_away_for_day = True
    bot.status.walk_away_reason = "3 losing trades in a row"
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner: _setup(symbol, _RED_CANDLE))

    fake_order = MagicMock()
    fake_order.id = "exit-order-3"
    fake_order.symbol = "ACHR"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "ACHR", "qty": 10}]
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.manage_open_positions()

        args, _ = MockBroker.return_value.submit_market_order.call_args
        assert args == ("ACHR", 10, "sell")

    assert "Closed ACHR" in bot.status.last_message


def test_manage_open_positions_works_even_when_auto_trading_is_disabled(tmp_path, monkeypatch):
    """The actual gap this fixes: a position opened manually (with no broker-side
    stop-loss) used to go completely unwatched whenever auto-trading was toggled off,
    since exit-checking only ever ran inside auto_cycle(), which is a no-op when the
    toggle is off. manage_open_positions() must protect open risk regardless of that
    toggle - app.main's automation loop now calls it unconditionally every cycle."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot = TradingBot()
    bot.status.auto_trading_enabled = False
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner: _setup(symbol, _RED_CANDLE))

    fake_order = MagicMock()
    fake_order.id = "exit-order-4"
    fake_order.symbol = "SMTK"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "SMTK", "qty": 1}]
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.manage_open_positions()

        args, _ = MockBroker.return_value.submit_market_order.call_args
        assert args == ("SMTK", 1, "sell")

    assert "Closed SMTK" in bot.status.last_message


def test_auto_cycle_no_longer_manages_positions_itself(tmp_path, monkeypatch):
    """Position management moved to manage_open_positions() (called independently every
    automation cycle) - auto_cycle() should never touch an existing position anymore,
    even one that would obviously trip an exit signal."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _fund_bankroll(monkeypatch)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)
    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[]))
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner: _setup(symbol, _RED_CANDLE))

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "ACHR", "qty": 10}]
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

    MockBroker.return_value.submit_market_order.assert_not_called()
