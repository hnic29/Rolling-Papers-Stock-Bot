from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.brokers.alpaca_broker import AlpacaBroker
from app.services import backtest as backtest_module


def _daily_bar(day, close, volume):
    return SimpleNamespace(timestamp=datetime(day.year, day.month, day.day, 20, 0, tzinfo=timezone.utc), close=close, volume=volume)


def _minute_bar(day, hour, minute, open_, high, low, close, volume):
    ts = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).isoformat()
    return {"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _patch_broker(monkeypatch, daily_bars_response, minute_bars):
    monkeypatch.setattr(AlpacaBroker, "__init__", lambda self: None)
    monkeypatch.setattr(AlpacaBroker, "daily_bars", lambda self, symbols, start, end: daily_bars_response)
    monkeypatch.setattr(AlpacaBroker, "historical_bars", lambda self, symbol, start, end, limit=120, sort=None, timeframe=None: minute_bars)


def test_backtest_enters_on_qualifying_breakout_and_exits_at_target(monkeypatch):
    test_day = date(2026, 6, 1)
    prev_day = date(2026, 5, 29)

    daily_bars_response = SimpleNamespace(
        data={
            "AAPL": [
                _daily_bar(prev_day, close=9.0, volume=100_000),
                _daily_bar(test_day, close=10.5, volume=2_000_000),  # +16.7% change, 20x relative volume
            ]
        }
    )

    minute_bars = [
        _minute_bar(test_day, 13, 30, 9.0, 9.1, 8.9, 9.05, 50_000),  # impulse start
        _minute_bar(test_day, 13, 31, 9.05, 10.5, 9.0, 10.4, 80_000),  # impulse peak
        _minute_bar(test_day, 13, 32, 10.4, 10.45, 10.0, 10.05, 40_000),  # pullback
        _minute_bar(test_day, 13, 33, 10.05, 10.6, 10.05, 10.55, 60_000),  # breakout -> entry
        _minute_bar(test_day, 13, 34, 10.55, 10.65, 10.5, 10.6, 50_000),  # hits target (10.6)
    ]

    _patch_broker(monkeypatch, daily_bars_response, minute_bars)

    result = backtest_module.run_backtest("AAPL", prev_day, test_day, starting_capital=10000, position_value=1000)

    assert result["days_qualified"] == 1
    assert result["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["exit_reason"] == "target"
    assert trade["entry_price"] == 10.55
    assert trade["exit_price"] == 10.6
    assert trade["qty"] == 94  # floor(1000 / 10.55)
    assert trade["pnl"] == round((10.6 - 10.55) * 94, 2)
    assert result["ending_equity"] == round(10000 + trade["pnl"], 2)
    assert result["win_count"] == 1
    assert result["win_rate_pct"] == 100.0


def test_backtest_skips_days_that_dont_meet_the_score_gate(monkeypatch):
    test_day = date(2026, 6, 1)
    prev_day = date(2026, 5, 29)

    # Only a 2% move and modest relative volume — should never reach 4 of 4 available pillars.
    daily_bars_response = SimpleNamespace(
        data={
            "AAPL": [
                _daily_bar(prev_day, close=10.0, volume=1_000_000),
                _daily_bar(test_day, close=10.2, volume=1_100_000),
            ]
        }
    )
    minute_bars = [
        _minute_bar(test_day, 13, 30, 10.0, 10.1, 9.9, 10.05, 50_000),
        _minute_bar(test_day, 13, 31, 10.05, 10.2, 10.0, 10.15, 50_000),
        _minute_bar(test_day, 13, 32, 10.15, 10.25, 10.1, 10.2, 50_000),
    ]

    _patch_broker(monkeypatch, daily_bars_response, minute_bars)

    result = backtest_module.run_backtest("AAPL", prev_day, test_day, starting_capital=10000, position_value=1000)

    assert result["days_qualified"] == 0
    assert result["trade_count"] == 0
    assert result["ending_equity"] == 10000


def test_backtest_rejects_crypto_symbol():
    import pytest

    with pytest.raises(ValueError, match="stocks-only"):
        backtest_module.run_backtest("BTC/USD", date(2026, 1, 1), date(2026, 1, 2))


def test_backtest_rejects_range_over_cap():
    import pytest

    with pytest.raises(ValueError, match="limited to"):
        backtest_module.run_backtest("AAPL", date(2020, 1, 1), date(2023, 1, 1))
