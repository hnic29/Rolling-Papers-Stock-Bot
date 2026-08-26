from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models import Candle, PullbackSetup, ScannerResponse, ScannerResult, Signal, StockCandidate, StrategyDecision, TradeRequest
from app.services import bankroll, bot_state, trade_log
from app.services.bot import PREMARKET_LIMIT_BUFFER_PCT, STALE_UNIVERSE_DAYS, TradingBot, current_trading_day


@pytest.fixture(autouse=True)
def _no_real_gap_lane(monkeypatch):
    """auto_cycle's real-time gap lane hits live snapshot/asset endpoints - tests must
    never do that. Individual tests override this on their own bot.scanner instance."""
    monkeypatch.setattr("app.services.scanner.MarketScanner.realtime_gap_candidates", lambda self: [])


def _fund_bankroll(monkeypatch, amount=100_000.0):
    """These tests exercise other behavior (market-closed checks, order
    submission, walk-away timing) and aren't about the bankroll feature
    itself - give them a bankroll large enough to never be the constraint,
    rather than every one of them needing its own withdrawal setup. Patches
    both functions since risk_per_trade_pct/max_position_value_pct sizing
    reads current_bankroll(), while the hard availability gate reads
    available_to_trade() - a real bankroll would have both agree."""
    monkeypatch.setattr(bankroll, "available_to_trade", lambda: amount)
    monkeypatch.setattr(bankroll, "current_bankroll", lambda: amount)


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


def test_submit_trade_allows_a_sell_regardless_of_bankroll(tmp_path, monkeypatch):
    """You should always be able to close a position, even with an empty
    bankroll - the gate only applies to opening new exposure."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
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


def test_a_manual_sell_links_the_exit_back_to_the_buy_row(tmp_path, monkeypatch):
    """The gap that left a manually-closed position charging the bankroll forever:
    exit linkage only existed in the automated exit-signal path, so a sell placed
    through the dashboard/API never marked the buy row as closing - deployed_capital
    kept counting it and its realized P&L never got recorded. Every sell through
    submit_trade now links, with reason 'manual_close' unless the caller says
    otherwise."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    trade_log.record_trade(order_id="manual-buy-1", symbol="SMTK", side="buy", qty=1, status="filled")
    trade_log.update_fill(order_id="manual-buy-1", status="filled", filled_avg_price=4.79, filled_qty=1, filled_at=datetime.now(UTC).isoformat())

    bot = TradingBot()
    bot.status.daily_pnl = 0.0

    fake_order = MagicMock()
    fake_order.id = "manual-sell-1"
    fake_order.symbol = "SMTK"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.submit_trade(TradeRequest(symbol="SMTK", qty=1, side=Signal.sell))

    buy = next(t for t in trade_log.list_trades() if t["order_id"] == "manual-buy-1")
    assert buy["exit_order_id"] == "manual-sell-1"
    assert buy["exit_reason"] == "manual_close"
    assert buy["realized_pnl"] is None  # completed by trade_sync once the sell fills


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
    monkeypatch.setattr(bot, "_in_premarket_window", lambda now: False)  # neither regular hours nor premarket

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
    # Pin the session open well before the baseline so the clamp is a no-op here -
    # this test is about the idle rule itself, not the clamp.
    monkeypatch.setattr("app.services.bot._todays_session_open_utc", lambda: datetime.now(UTC) - timedelta(hours=8))

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
    monkeypatch.setattr("app.services.bot._todays_session_open_utc", lambda: datetime.now(UTC) - timedelta(hours=8))
    _fund_bankroll(monkeypatch)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot._auto_trading_started_at = datetime.now(UTC) - timedelta(minutes=10)
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)
    monkeypatch.setattr(bot, "_in_premarket_window", lambda now: False)  # neither regular hours nor premarket

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=False)
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        bot.auto_cycle()

    assert bot.status.walked_away_for_day is False
    assert "market is closed" in bot.status.last_message.lower()


def test_auto_cycle_gap_lane_trades_a_live_gapper_the_lagged_scan_cannot_see(tmp_path, monkeypatch):
    """The Ross lane end-to-end: the lagged scan returns NOTHING (e.g. the first 16
    minutes after the open, before a consolidated today-bar exists), but a real-time
    gap candidate still reaches entry evaluation carrying its LIVE numbers - including
    total_volume=0 scoring an honest 4-of-5, which qualifies."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _fund_bankroll(monkeypatch)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)
    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[]))

    live_gapper = StockCandidate(
        symbol="GAPR", price=6.2, percent_change=82.0, relative_volume=25.0,
        total_volume=0, float_shares=900_000, is_leading_gainer=True,
    )
    monkeypatch.setattr(bot.scanner, "realtime_gap_candidates", lambda: [live_gapper])

    captured = {}

    def fake_setup(symbol, scanner, candidate=None):
        captured["candidate"] = candidate
        return _setup(symbol)

    monkeypatch.setattr("app.services.bot.build_pullback_setup", fake_setup)
    monkeypatch.setattr(bot.strategy, "evaluate", lambda setup: StrategyDecision(signal=Signal.buy, confidence=0.8, reasons=["ok"], risk_per_share=0.1, first_target=6.5))

    fake_order = MagicMock()
    fake_order.id = "gap-order-1"
    fake_order.symbol = "GAPR"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

        args, _ = MockBroker.return_value.submit_market_order.call_args
        assert args[0] == "GAPR"

    # The LIVE candidate was injected into setup-building - not re-derived from
    # lagged data that would have described yesterday.
    assert captured["candidate"] is live_gapper


def test_idle_walk_away_clock_starts_at_the_open_not_before(monkeypatch, tmp_path):
    """The bug that silenced the bot at the bell on 2026-08-25: the idle baseline was
    the midnight state-rollover timestamp, so the FIRST 9:30 cycle read 425+ idle
    minutes and walked away without a single scan. Idle time must only count within
    the session: baseline hours before the open + 25 minutes into the session must
    NOT trip a 60-minute rule."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.services.bot._todays_session_open_utc", lambda: datetime.now(UTC) - timedelta(minutes=25))

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot._auto_trading_started_at = datetime.now(UTC) - timedelta(hours=8)  # e.g. midnight rollover

    bot._update_session_state()

    assert bot.status.walked_away_for_day is False


def test_idle_walk_away_still_trips_an_hour_into_the_session(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.services.bot._todays_session_open_utc", lambda: datetime.now(UTC) - timedelta(minutes=90))

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot._auto_trading_started_at = datetime.now(UTC) - timedelta(hours=8)

    bot._update_session_state()

    assert bot.status.walked_away_for_day is True
    assert "momentum has cooled" in bot.status.walk_away_reason


def test_resume_day_clears_a_walk_away_and_survives_a_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot = TradingBot()
    bot.status.walked_away_for_day = True
    bot.status.walk_away_reason = "no trade taken in over 60 minutes — momentum has cooled"
    bot._persist_state()

    bot.resume_day()

    assert bot.status.walked_away_for_day is False
    assert bot.status.walk_away_reason is None
    assert TradingBot().status.walked_away_for_day is False  # persisted, not just in-memory


def test_correct_trades_today_persists_and_floors_at_zero(monkeypatch, tmp_path):
    """The 2026-08-26 duplicate-order bug ate all 5 daily slots on one bug-triggered,
    fully-cancelled-and-unfilled entry attempt - this is the bookkeeping correction,
    not a way to bypass the cap for real trading days."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    bot = TradingBot()
    bot.status.trades_today = 5
    bot._persist_state()

    bot.correct_trades_today(0)

    assert bot.status.trades_today == 0
    assert TradingBot().status.trades_today == 0  # persisted, not just in-memory

    bot.correct_trades_today(-3)
    assert bot.status.trades_today == 0  # floors at zero, never negative


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
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))
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
    # $5/share entry - $15 *available* covers only 3 shares, well under what the
    # risk/capital percentages alone would otherwise allow off a large overall bankroll
    # (most of it just isn't available right now - e.g. tied up in other positions).
    monkeypatch.setattr(bankroll, "available_to_trade", lambda: 15.0)
    monkeypatch.setattr(bankroll, "current_bankroll", lambda: 100_000.0)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)

    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[_candidate("ACHR")]))
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))
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


def test_premarket_sizing_respects_the_position_cap_against_the_buffered_limit_price(tmp_path, monkeypatch):
    """Regression: qty used to be sized against the unbuffered proposed_entry, then the
    order was placed at proposed_entry PLUS the premarket buffer - a boundary case
    (qty=4000 @ $5.00 = exactly the $20,000 cap, but @ the buffered $5.03 = $20,120,
    over it) that risk.validate correctly rejected, silently dropping the trade to
    "skipped" with no order ever submitted. Sizing must use the buffered price."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr(bankroll, "available_to_trade", lambda: 100_000.0)
    monkeypatch.setattr(bankroll, "current_bankroll", lambda: 100_000.0)  # 20% cap = $20,000

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_in_premarket_window", lambda now: True)
    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[]))
    monkeypatch.setattr(bot.scanner, "realtime_gap_candidates", lambda: [
        StockCandidate(symbol="RCON", price=5.0, percent_change=90.0, relative_volume=30.0, total_volume=0, float_shares=700_000, is_leading_gainer=True)
    ])
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))  # proposed_entry 5.0
    monkeypatch.setattr(bot.strategy, "evaluate", lambda setup: StrategyDecision(signal=Signal.buy, confidence=0.8, reasons=["ok"], risk_per_share=0.1, first_target=5.5))

    fake_order = MagicMock()
    fake_order.id = "premarket-sizing-1"
    fake_order.symbol = "RCON"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=False)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.submit_extended_hours_limit_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        status = bot.auto_cycle()

    assert "skipped" not in status.last_message.lower()
    args, _ = MockBroker.return_value.submit_extended_hours_limit_order.call_args
    _, qty, _, limit_price = args
    assert round(qty * limit_price, 2) <= 20_000.0  # inside the 20% position-value cap


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
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "ACHR", "qty": 10}]
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

    MockBroker.return_value.submit_market_order.assert_not_called()


def test_auto_cycle_does_not_stack_a_duplicate_order_on_an_unfilled_pending_buy(monkeypatch, tmp_path):
    """Live incident, 2026-08-26 premarket: BRNX's limit buy sat unfilled ("new"),
    never appeared in positions_as_dicts() (unfilled orders aren't positions), and the
    same BRNX candidate re-qualified every cycle - 5 duplicate buy orders stacked up
    before this check existed, one every ~90 seconds. A pending (non-terminal) buy
    order must block re-entry exactly like an already-held position does."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    trade_log.record_trade(order_id="brnx-pending-1", symbol="BRNX", side="buy", qty=72, status="new", stop_loss_price=5.4)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)
    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[_candidate("BRNX")]))
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []  # unfilled - not a position yet
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

    MockBroker.return_value.submit_market_order.assert_not_called()
    MockBroker.return_value.submit_extended_hours_limit_order.assert_not_called()


def test_auto_cycle_flags_a_stale_universe_in_its_status_message(monkeypatch, tmp_path):
    """Nothing re-runs scripts/build_universe.py on its own - without a visible warning,
    stale float/price/volume data would just keep getting used forever unnoticed."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _fund_bankroll(monkeypatch)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)
    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[]))
    monkeypatch.setattr(bot.scanner, "universe_age_days", lambda: STALE_UNIVERSE_DAYS + 10)

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

    assert "universe data is" in bot.status.last_message
    assert "build_universe.py" in bot.status.last_message


def test_auto_cycle_does_not_flag_a_fresh_universe(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _fund_bankroll(monkeypatch)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_within_trading_window", lambda now: True)
    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[]))
    monkeypatch.setattr(bot.scanner, "universe_age_days", lambda: 1)

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

    assert "universe data is" not in bot.status.last_message


def test_manage_open_positions_closes_on_a_red_candle_exit_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol, _RED_CANDLE))

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


def test_manage_open_positions_cancels_resting_orders_before_selling(tmp_path, monkeypatch):
    """An auto-entered position has a stop-loss RESTING at the broker, which holds the
    shares - Alpaca rejects a second sell for held shares outright, so without a cancel
    first the exit-signal close never actually works for any auto-entered position."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol, _RED_CANDLE))

    fake_order = MagicMock()
    fake_order.id = "exit-order-5"
    fake_order.symbol = "ACHR"
    fake_order.status = "accepted"

    resting_stop = MagicMock()
    resting_stop.id = "resting-stop-1"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.open_orders.return_value = [resting_stop]
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot._manage_open_positions([{"symbol": "ACHR", "qty": 10}])

    MockBroker.return_value.open_orders.assert_called_once_with("ACHR")
    MockBroker.return_value.cancel_order.assert_called_once_with("resting-stop-1")
    MockBroker.return_value.submit_market_order.assert_called_once()


def test_manage_open_positions_links_the_exit_back_to_the_buy_row(tmp_path, monkeypatch):
    """The closing sell isn't a bracket leg of the original buy order, so without an
    explicit link the buy row would look open forever - no realized P&L for the
    walk-away rules, and deployed_capital charging the bankroll for a closed position."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    trade_log.record_trade(order_id="orig-buy-1", symbol="ACHR", side="buy", qty=10, status="filled")
    trade_log.update_fill(order_id="orig-buy-1", status="filled", filled_avg_price=5.0, filled_qty=10, filled_at=datetime.now(UTC).isoformat())

    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol, _RED_CANDLE))

    fake_order = MagicMock()
    fake_order.id = "closing-sell-1"
    fake_order.symbol = "ACHR"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot._manage_open_positions([{"symbol": "ACHR", "qty": 10}])

    buy = next(t for t in trade_log.list_trades() if t["order_id"] == "orig-buy-1")
    assert buy["exit_order_id"] == "closing-sell-1"
    assert buy["exit_reason"] == "exit_signal"
    assert buy["realized_pnl"] is None  # completed by trade_sync once the sell fills


def _open_lot(symbol="RUNR", entry=10.0, stop=9.5, qty=40):
    """A filled auto-entry lot on the books: entry $10, stop $9.50 -> R = $0.50."""
    trade_log.record_trade(order_id=f"{symbol}-buy", symbol=symbol, side="buy", qty=qty, status="filled", stop_loss_price=stop)
    trade_log.update_fill(order_id=f"{symbol}-buy", status="filled", filled_avg_price=entry, filled_qty=qty, filled_at=datetime.now(UTC).isoformat())


def test_a_position_reaching_2r_gets_its_stop_moved_to_breakeven(tmp_path, monkeypatch):
    """Ross's documented rule: once a trade has proven itself, "move the stop up
    toward break-even" - a proven winner can no longer turn into a loss."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _open_lot()  # entry 10.00, R 0.50 -> 2R prints at 11.00
    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))  # no exit signal

    stop_order = MagicMock()
    stop_order.id = "breakeven-stop-1"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.submit_stop_order.return_value = stop_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        exited = bot._manage_open_positions([{"symbol": "RUNR", "qty": 40, "current_price": 11.05}])
        # Same cycle again - protection must be idempotent, not stack duplicate stops.
        bot._manage_open_positions([{"symbol": "RUNR", "qty": 40, "current_price": 11.10}])

    assert exited == []  # still holding - protected, not closed
    MockBroker.return_value.submit_stop_order.assert_called_once_with("RUNR", 40, 10.0)
    buy = next(t for t in trade_log.list_trades() if t["order_id"] == "RUNR-buy")
    assert buy["exit_order_id"] == "breakeven-stop-1"  # linked so trade_sync realizes it if it fills
    assert buy["exit_reason"] == "stop"


def test_a_2r_winner_falling_back_to_1r_is_closed_as_giveback(tmp_path, monkeypatch):
    """What EXOD exposed: +$28 unrealized decayed to +$8 waiting for a reversal candle
    alone. Once 2R has printed, falling back below +1R banks the remaining gain."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _open_lot()  # entry 10.00, R 0.50: 2R = 11.00, +1R floor = 10.50
    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))

    fake_order = MagicMock()
    fake_order.id = "giveback-sell-1"
    fake_order.symbol = "RUNR"
    fake_order.status = "accepted"
    stop_order = MagicMock()
    stop_order.id = "breakeven-stop-1"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.submit_stop_order.return_value = stop_order
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot._manage_open_positions([{"symbol": "RUNR", "qty": 40, "current_price": 11.20}])  # peak 2.4R
        exited = bot._manage_open_positions([{"symbol": "RUNR", "qty": 40, "current_price": 10.45}])  # back under +1R

    assert exited == ["RUNR"]
    args, _ = MockBroker.return_value.submit_market_order.call_args
    assert args == ("RUNR", 40, "sell")
    buy = next(t for t in trade_log.list_trades() if t["order_id"] == "RUNR-buy")
    assert buy["exit_reason"] == "giveback"


def test_a_position_below_2r_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _open_lot()  # 2R = 11.00
    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        exited = bot._manage_open_positions([{"symbol": "RUNR", "qty": 40, "current_price": 10.60}])  # only +1.2R

    assert exited == []
    MockBroker.return_value.submit_stop_order.assert_not_called()
    MockBroker.return_value.submit_market_order.assert_not_called()


def test_a_stopless_manual_position_gets_no_2r_management(tmp_path, monkeypatch):
    """No recorded stop means no defined R - the 2R/giveback layers can't apply, and
    the position stays under exit-indicator watch only."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    trade_log.record_trade(order_id="m-buy", symbol="MANU", side="buy", qty=10, status="filled")  # no stop
    trade_log.update_fill(order_id="m-buy", status="filled", filled_avg_price=5.0, filled_qty=10, filled_at=datetime.now(UTC).isoformat())
    bot = TradingBot()
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")
        exited = bot._manage_open_positions([{"symbol": "MANU", "qty": 10, "current_price": 50.0}])  # up 10x, still untouched

    assert exited == []
    MockBroker.return_value.submit_stop_order.assert_not_called()


def test_manage_open_positions_leaves_a_quiet_position_open(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    bot = TradingBot()
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))

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
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol, _RED_CANDLE))

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
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol, _RED_CANDLE))

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
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol, _RED_CANDLE))

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
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol, _RED_CANDLE))

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "ACHR", "qty": 10}]
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

    MockBroker.return_value.submit_market_order.assert_not_called()


def test_a_new_trading_bot_restores_auto_trading_enabled_across_a_restart(tmp_path, monkeypatch):
    """The actual bug this fixes: every deploy restarts the process, and
    auto_trading_enabled used to live only in memory - silently reverting to off with no
    signal to anyone, discovered live when it kept happening after routine updates."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    first = TradingBot()
    first.start_auto_trading()

    second = TradingBot()  # simulates the process restarting

    assert second.status.auto_trading_enabled is True


def test_a_new_trading_bot_restores_todays_risk_counters(tmp_path, monkeypatch):
    """A crash mid-session shouldn't reset the walk-away safety counters - otherwise a
    crash-loop could quietly let the bot keep re-entering past what the daily discipline
    rules intended."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    first = TradingBot()
    first.status.walked_away_for_day = True
    first.status.walk_away_reason = "3 losing trades in a row"
    first.status.consecutive_losses = 3
    first._persist_state()

    second = TradingBot()

    assert second.status.walked_away_for_day is True
    assert second.status.walk_away_reason == "3 losing trades in a row"
    assert second.status.consecutive_losses == 3


def test_stale_persisted_state_from_a_previous_day_resets_normally(tmp_path, monkeypatch):
    """Restoring stale state on startup is harmless - refresh_status()'s existing
    day-rollover check resets it the same way it already handles a rollover that happens
    mid-session, so a restart after days offline doesn't replay a stale walk-away."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    stale_day = (current_trading_day() - timedelta(days=3)).isoformat()
    bot_state.save(
        auto_trading_enabled=True, running=True, trading_day=stale_day, trades_today=4,
        peak_daily_pnl=99.0, consecutive_losses=3, walked_away_for_day=True,
        walk_away_reason="3 losing trades in a row", auto_trading_started_at=None,
    )

    bot = TradingBot()
    bot.refresh_status()  # triggers the day-rollover check

    assert bot.status.auto_trading_enabled is True  # the toggle itself isn't daily state
    assert bot.status.trades_today == 0
    assert bot.status.walked_away_for_day is False
    assert bot.status.walk_away_reason is None


def test_stopping_auto_trading_persists_across_a_restart_too(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    first = TradingBot()
    first.start_auto_trading()
    first.stop_auto_trading()

    second = TradingBot()

    assert second.status.auto_trading_enabled is False


def test_a_submitted_buy_sends_a_notification(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _fund_bankroll(monkeypatch)
    sent = []
    monkeypatch.setattr("app.services.bot.notify.send", lambda title, message, **kw: sent.append((title, message)))

    bot = TradingBot()
    bot.status.daily_pnl = 0.0

    fake_order = MagicMock()
    fake_order.id = "order-n1"
    fake_order.symbol = "ACHR"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.submit_trade(TradeRequest(symbol="ACHR", qty=10, side=Signal.buy, estimated_price=5.0, stop_loss_price=4.9))

    assert len(sent) == 1
    title, message = sent[0]
    assert "ACHR" in title
    assert "$5.00" in message and "stop $4.90" in message


def test_a_sell_does_not_send_a_submission_notification(tmp_path, monkeypatch):
    """Sells notify when the exit CONFIRMS with real P&L (trade_sync) - notifying the
    submission too would double-ping every exit."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    sent = []
    monkeypatch.setattr("app.services.bot.notify.send", lambda *a, **kw: sent.append(a))

    bot = TradingBot()
    bot.status.daily_pnl = 0.0

    fake_order = MagicMock()
    fake_order.id = "order-n2"
    fake_order.symbol = "ACHR"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.submit_market_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.submit_trade(TradeRequest(symbol="ACHR", qty=10, side=Signal.sell))

    assert sent == []


def test_tripping_a_walk_away_sends_a_high_priority_notification(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    sent = []
    monkeypatch.setattr(
        "app.services.bot.notify.send",
        lambda title, message, priority="default", **kw: sent.append((title, priority)),
    )

    bot = TradingBot()
    now = datetime.now(UTC)
    _record_realized_trade("n1", -20.0, now)
    _record_realized_trade("n2", -15.0, now)
    _record_realized_trade("n3", -10.0, now)

    bot._update_session_state()
    assert bot.status.walked_away_for_day is True
    assert len(sent) == 1
    assert sent[0] == ("Walked away for the day", "high")

    bot._update_session_state()  # already tripped - must not re-notify every cycle
    assert len(sent) == 1


def test_market_open_close_notifications_prime_silently_on_first_check(tmp_path, monkeypatch):
    """A routine restart mid-session must never fire a false 'market opened' the
    instant the process comes back up - the first check after startup only primes
    the known-state, it never notifies."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    sent = []
    monkeypatch.setattr("app.services.bot.notify.send", lambda *a, **kw: sent.append(a))

    bot = TradingBot()
    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        bot.check_market_open_close_notifications()

    assert sent == []
    assert bot._last_known_market_open is True


def test_market_open_close_notifications_fire_on_each_transition(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    sent = []
    monkeypatch.setattr(
        "app.services.bot.notify.send",
        lambda title, message, **kw: sent.append((title, message)),
    )

    bot = TradingBot()
    bot._last_known_market_open = False  # already primed, as if the bot has been running a while

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        bot.check_market_open_close_notifications()  # closed -> open

    assert len(sent) == 1
    assert sent[0][0] == "Market is open"

    _record_realized_trade("mc1", 12.5, datetime.now(UTC))
    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=False)
        bot.check_market_open_close_notifications()  # open -> closed

    assert len(sent) == 2
    assert sent[1][0] == "Market is closed"
    assert "1 trade(s) closed" in sent[1][1]
    assert "+$12.50" in sent[1][1]


def test_market_open_close_notifications_stay_silent_with_no_transition(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    sent = []
    monkeypatch.setattr("app.services.bot.notify.send", lambda *a, **kw: sent.append(a))

    bot = TradingBot()
    bot._last_known_market_open = True

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        bot.check_market_open_close_notifications()
        bot.check_market_open_close_notifications()

    assert sent == []  # still open both times - nothing changed, nothing to say


def test_refresh_status_reflects_a_runtime_paper_mode_change(tmp_path, monkeypatch):
    """Settings saves take effect without a restart, but status.paper was only set at
    construction - so after switching to live mode the dashboard's Mode field kept
    saying "Paper" while real-money orders could already be going out."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    bot = TradingBot()
    assert bot.status.paper is True

    monkeypatch.setattr("app.services.bot.settings.alpaca_paper", False)
    bot.refresh_status()

    assert bot.status.paper is False


def test_running_flag_also_survives_a_restart(tmp_path, monkeypatch):
    """Cosmetic, not safety-critical (nothing gates on this flag), but the Start/Stop
    button shouldn't lie about whether the bot is actually running after a restart."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")

    first = TradingBot()
    first.start()

    second = TradingBot()

    assert second.status.running is True


# --- Premarket trading -------------------------------------------------------
# Built 2026-08-26 after discovering Ross Cameron's entire 2026-08-25 P&L (RCON
# +$17k, GRML -$8k, DAIC, AMIX) happened 7:00-9:30 ET, fully resolved before a
# regular-hours-only bot would ever look. Rollback bookmark: commit 574b781.

def test_in_premarket_window_true_between_7am_and_930am_on_a_weekday(monkeypatch):
    monkeypatch.setattr("app.config.settings.premarket_window_start", "07:00")
    bot = TradingBot()
    tuesday_730am_et = datetime(2026, 8, 25, 11, 30, tzinfo=UTC)  # 7:30 ET (EDT, UTC-4)
    assert bot._in_premarket_window(tuesday_730am_et) is True


def test_in_premarket_window_false_before_the_window_starts(monkeypatch):
    monkeypatch.setattr("app.config.settings.premarket_window_start", "07:00")
    bot = TradingBot()
    tuesday_6am_et = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)  # 6:00 ET
    assert bot._in_premarket_window(tuesday_6am_et) is False


def test_in_premarket_window_false_once_regular_hours_opens(monkeypatch):
    monkeypatch.setattr("app.config.settings.premarket_window_start", "07:00")
    bot = TradingBot()
    tuesday_930am_et = datetime(2026, 8, 25, 13, 30, tzinfo=UTC)  # 9:30 ET exactly
    assert bot._in_premarket_window(tuesday_930am_et) is False


def test_in_premarket_window_false_on_a_weekend(monkeypatch):
    monkeypatch.setattr("app.config.settings.premarket_window_start", "07:00")
    bot = TradingBot()
    saturday_730am_et = datetime(2026, 8, 29, 11, 30, tzinfo=UTC)  # Saturday
    assert bot._in_premarket_window(saturday_730am_et) is False


def test_auto_cycle_places_an_extended_hours_limit_order_premarket(tmp_path, monkeypatch):
    """The core premarket path: market CLOSED (clock.is_open=False) but inside the
    premarket window - a genuine gap-lane buy signal must submit an extended-hours
    LIMIT order (with a buffer above proposed_entry), never a market order, since
    Alpaca rejects market orders entirely outside 9:30-16:00."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _fund_bankroll(monkeypatch)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    bot.status.daily_pnl = 0.0
    monkeypatch.setattr(bot, "_in_premarket_window", lambda now: True)
    monkeypatch.setattr(bot.scanner, "scan_universe", lambda **kw: ScannerResponse(results=[]))

    live_gapper = StockCandidate(
        symbol="RCON", price=6.0, percent_change=90.0, relative_volume=30.0,
        total_volume=0, float_shares=700_000, is_leading_gainer=True,
    )
    monkeypatch.setattr(bot.scanner, "realtime_gap_candidates", lambda: [live_gapper])
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))
    monkeypatch.setattr(bot.strategy, "evaluate", lambda setup: StrategyDecision(signal=Signal.buy, confidence=0.8, reasons=["ok"], risk_per_share=0.1, first_target=5.5))

    fake_order = MagicMock()
    fake_order.id = "premarket-order-1"
    fake_order.symbol = "RCON"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=False)  # market closed
        MockBroker.return_value.positions_as_dicts.return_value = []
        MockBroker.return_value.submit_extended_hours_limit_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

        MockBroker.return_value.submit_market_order.assert_not_called()
        args, _ = MockBroker.return_value.submit_extended_hours_limit_order.call_args
        symbol, qty, side, limit_price = args
        assert symbol == "RCON"
        assert side == "buy"
        expected_limit = round(5.0 * (1 + PREMARKET_LIMIT_BUFFER_PCT / 100), 2)  # _setup's proposed_entry is 5.0
        assert limit_price == expected_limit

    buy = next(t for t in trade_log.list_trades() if t["order_id"] == "premarket-order-1")
    assert buy["stop_loss_price"] == 4.9  # _setup's proposed_stop - recorded for OUR OWN R tracking


def test_auto_cycle_stays_idle_premarket_when_the_feature_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.config.settings.premarket_trading_enabled", False)

    bot = TradingBot()
    bot.status.auto_trading_enabled = True
    monkeypatch.setattr(bot, "_in_premarket_window", lambda now: True)

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=False)
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.auto_cycle()

    assert "market is closed" in bot.status.last_message.lower()
    MockBroker.return_value.submit_extended_hours_limit_order.assert_not_called()


def test_manage_open_positions_runs_premarket_not_just_regular_hours(tmp_path, monkeypatch):
    """RCON's real pattern: a premarket position can fully round-trip before 9:30.
    Exit-indicator monitoring is its ONLY protection during that window - it must not
    sit unwatched just because the market technically hasn't opened yet."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _open_lot(symbol="RCON")
    bot = TradingBot()
    monkeypatch.setattr(bot, "_in_premarket_window", lambda now: True)
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol, _RED_CANDLE))  # triggers exit_indicators

    fake_order = MagicMock()
    fake_order.id = "premarket-exit-1"
    fake_order.symbol = "RCON"
    fake_order.status = "accepted"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=False)  # premarket
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "RCON", "qty": 40, "current_price": 9.0}]
        MockBroker.return_value.open_orders.return_value = []
        MockBroker.return_value.submit_extended_hours_limit_order.return_value = fake_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        status = bot.manage_open_positions()

    assert "RCON" in status.last_message
    MockBroker.return_value.submit_market_order.assert_not_called()
    args, _ = MockBroker.return_value.submit_extended_hours_limit_order.call_args
    _, _, side, limit_price = args
    assert side == "sell"
    assert limit_price == round(9.0 * (1 - PREMARKET_LIMIT_BUFFER_PCT / 100), 2)  # below current price - urgency, not best price


def test_manage_open_positions_ignores_premarket_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.config.settings.premarket_trading_enabled", False)
    bot = TradingBot()
    monkeypatch.setattr(bot, "_in_premarket_window", lambda now: True)

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=False)
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        status = bot.manage_open_positions()

    MockBroker.return_value.positions_as_dicts.assert_not_called()


def test_regular_hours_open_arms_a_stop_for_a_premarket_filled_position(tmp_path, monkeypatch):
    """The protection-gap closer: a premarket fill recorded a stop_loss_price but the
    broker holds NOTHING (extended hours disallows stop orders entirely) - the first
    regular-hours cycle must place the real resting stop."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _open_lot(symbol="RCON", entry=6.0, stop=5.7, qty=40)
    bot = TradingBot()
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))  # no exit signal

    stop_order = MagicMock()
    stop_order.id = "arm-stop-1"

    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)  # regular hours now
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "RCON", "qty": 40, "current_price": 6.2}]
        MockBroker.return_value.open_orders.return_value = []  # nothing resting - premarket fill had no bracket
        MockBroker.return_value.submit_stop_order.return_value = stop_order
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.manage_open_positions()

    MockBroker.return_value.submit_stop_order.assert_called_once_with("RCON", 40, 5.7)
    buy = next(t for t in trade_log.list_trades() if t["order_id"] == "RCON-buy")
    assert buy["exit_order_id"] == "arm-stop-1"
    assert buy["exit_reason"] == "stop"


def test_regular_hours_open_does_not_rearm_a_stop_that_already_exists(tmp_path, monkeypatch):
    """A regular-hours bracket entry already has its stop resting - the arming check
    must not fire a second, duplicate stop order on top of it."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    _open_lot(symbol="ACHR", entry=6.0, stop=5.7, qty=40)
    bot = TradingBot()
    monkeypatch.setattr("app.services.bot.build_pullback_setup", lambda symbol, scanner, candidate=None: _setup(symbol))

    existing_stop = MagicMock()
    with patch("app.services.bot.AlpacaBroker") as MockBroker:
        MockBroker.return_value.client.get_clock.return_value = MagicMock(is_open=True)
        MockBroker.return_value.positions_as_dicts.return_value = [{"symbol": "ACHR", "qty": 40, "current_price": 6.2}]
        MockBroker.return_value.open_orders.return_value = [existing_stop]  # bracket stop already resting
        MockBroker.return_value.daily_pnl.side_effect = Exception("no live account in test")

        bot.manage_open_positions()

    MockBroker.return_value.submit_stop_order.assert_not_called()
