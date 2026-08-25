from datetime import date, datetime, timezone

import pytest

from app.brokers.alpaca_broker import AlpacaBroker
from app.config import settings
from app.services import backtest as backtest_module
from app.services.scanner import MarketScanner

TEST_DAY = date(2026, 6, 1)
PREV_DAY = date(2026, 5, 29)


def _daily_bar(day, close, volume):
    return type("Bar", (), {"timestamp": datetime(day.year, day.month, day.day, 20, 0, tzinfo=timezone.utc), "close": close, "volume": volume})()


def _minute_bar(day, hour, minute, open_, high, low, close, volume):
    ts = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).isoformat()
    return {"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


# A qualifying day for ACHR: +16.7%, 20x relative volume off a $9 close.
_ACHR_DAILY = type("Response", (), {"data": {"ACHR": [_daily_bar(PREV_DAY, close=9.0, volume=100_000), _daily_bar(TEST_DAY, close=10.5, volume=2_000_000)]}})()
_ACHR_BREAKOUT_BARS = [
    _minute_bar(TEST_DAY, 13, 30, 9.0, 9.1, 8.9, 9.05, 50_000),  # impulse start
    _minute_bar(TEST_DAY, 13, 31, 9.05, 10.5, 9.0, 10.4, 80_000),  # impulse peak
    _minute_bar(TEST_DAY, 13, 32, 10.4, 10.45, 10.0, 10.05, 40_000),  # pullback
    _minute_bar(TEST_DAY, 13, 33, 10.05, 10.6, 10.05, 10.55, 60_000),  # breakout -> entry
    _minute_bar(TEST_DAY, 13, 34, 10.55, 11.2, 10.5, 11.1, 70_000),  # runs - no fixed target caps it
    _minute_bar(TEST_DAY, 13, 35, 11.1, 11.15, 10.9, 10.85, 40_000),  # red candle -> exit_signal
]

_ACHR_METADATA = {"ACHR": {"float_shares": 15_000_000, "sector": "tech"}}


@pytest.fixture(autouse=True)
def _stub_broker(monkeypatch):
    monkeypatch.setattr(AlpacaBroker, "__init__", lambda self: None)


def _single_symbol_universe(monkeypatch, symbol="ACHR", metadata=None):
    monkeypatch.setattr(MarketScanner, "load_universe", lambda self: [symbol])
    monkeypatch.setattr(MarketScanner, "load_metadata", lambda self: metadata or _ACHR_METADATA)


def test_daily_backtest_qualifies_and_enters_on_a_real_breakout(monkeypatch):
    _single_symbol_universe(monkeypatch)
    monkeypatch.setattr(AlpacaBroker, "daily_bars", lambda self, symbols, start, end: _ACHR_DAILY)
    monkeypatch.setattr(AlpacaBroker, "historical_bars", lambda self, symbol, start, end, limit=390, sort=None, timeframe=None: _ACHR_BREAKOUT_BARS)

    result = backtest_module.run_daily_backtest(TEST_DAY, starting_capital=10000, position_value=1000)

    assert result["symbols_scanned"] == 1
    assert result["symbols_qualified"] == 1
    assert result["symbols_traded"] == 1
    assert result["trade_count"] == 1

    trade = result["trades"][0]
    assert trade["symbol"] == "ACHR"
    assert trade["entry_price"] == 10.55
    assert trade["qty"] == 94  # floor(1000 / 10.55) - the position cap binds here
    # No fixed take-profit: it ran well past 10.6 (the old target) before the red
    # candle at 13:35 closed it - the whole point of the live-behavior fix.
    assert trade["exit_reason"] == "exit_signal"
    assert trade["exit_price"] == 10.85
    assert trade["pnl"] == round((10.85 - 10.55) * 94, 2)
    assert result["ending_equity"] == round(10000 + trade["pnl"], 2)


def test_daily_backtest_skips_symbols_that_dont_meet_the_score_gate(monkeypatch):
    quiet_daily = type("Response", (), {"data": {"ACHR": [_daily_bar(PREV_DAY, close=10.0, volume=1_000_000), _daily_bar(TEST_DAY, close=10.2, volume=1_100_000)]}})()
    quiet_minutes = [
        _minute_bar(TEST_DAY, 13, 30, 10.0, 10.1, 9.9, 10.05, 50_000),
        _minute_bar(TEST_DAY, 13, 31, 10.05, 10.2, 10.0, 10.15, 50_000),
        _minute_bar(TEST_DAY, 13, 32, 10.15, 10.25, 10.1, 10.2, 50_000),
    ]
    _single_symbol_universe(monkeypatch)
    monkeypatch.setattr(AlpacaBroker, "daily_bars", lambda self, symbols, start, end: quiet_daily)
    monkeypatch.setattr(AlpacaBroker, "historical_bars", lambda self, symbol, start, end, limit=390, sort=None, timeframe=None: quiet_minutes)

    result = backtest_module.run_daily_backtest(TEST_DAY, starting_capital=10000)

    assert result["symbols_scanned"] == 1
    assert result["symbols_qualified"] == 0
    assert result["trade_count"] == 0
    assert result["ending_equity"] == 10000
    assert result["candidates"][0]["qualified"] is False


def test_daily_backtest_rejects_today_or_a_future_date():
    with pytest.raises(ValueError, match="past"):
        backtest_module.run_daily_backtest(datetime.now(backtest_module.MARKET_TZ).date())


def test_daily_backtest_defaults_to_the_live_universe_when_no_symbols_given(monkeypatch):
    calls = []
    monkeypatch.setattr(MarketScanner, "load_universe", lambda self: calls.append("called") or ["ACHR"])
    monkeypatch.setattr(MarketScanner, "load_metadata", lambda self: _ACHR_METADATA)
    monkeypatch.setattr(AlpacaBroker, "daily_bars", lambda self, symbols, start, end: _ACHR_DAILY)
    monkeypatch.setattr(AlpacaBroker, "historical_bars", lambda self, symbol, start, end, limit=390, sort=None, timeframe=None: _ACHR_BREAKOUT_BARS)

    backtest_module.run_daily_backtest(TEST_DAY, symbols=None)

    assert calls == ["called"]


def test_daily_backtest_uses_the_supplied_symbols_instead_of_the_universe_when_given(monkeypatch):
    universe_calls = []
    monkeypatch.setattr(MarketScanner, "load_universe", lambda self: universe_calls.append("called") or ["SOMETHING_ELSE"])
    monkeypatch.setattr(MarketScanner, "load_metadata", lambda self: _ACHR_METADATA)
    monkeypatch.setattr(AlpacaBroker, "daily_bars", lambda self, symbols, start, end: _ACHR_DAILY)
    monkeypatch.setattr(AlpacaBroker, "historical_bars", lambda self, symbol, start, end, limit=390, sort=None, timeframe=None: _ACHR_BREAKOUT_BARS)

    result = backtest_module.run_daily_backtest(TEST_DAY, symbols=["achr"])  # lowercase - must normalize

    assert universe_calls == []  # never touched the live universe
    assert result["symbols_scanned"] == 1
    assert result["candidates"][0]["symbol"] == "ACHR"


def test_daily_backtest_shares_one_capital_pool_across_two_qualifying_symbols(monkeypatch):
    """The bug this fixes: a second candidate on the same day should size itself
    against what the first one left available, not an independent pot of money."""
    achr_daily = type("Response", (), {"data": {"ACHR": _ACHR_DAILY.data["ACHR"]}})()
    bynd_daily = type("Response", (), {"data": {"BYND": [_daily_bar(PREV_DAY, close=9.0, volume=100_000), _daily_bar(TEST_DAY, close=10.5, volume=2_000_000)]}})()
    # Same minutes as ACHR's own setup bars, so BYND's entry gets evaluated at the exact
    # same timestamp ACHR's does - "ACHR" sorts before "BYND", so ACHR's cash deduction
    # has already happened by the time BYND's sizing is computed, with no dependence on
    # when (or whether) ACHR's position later exits.
    bynd_bars = [
        _minute_bar(TEST_DAY, 13, 30, 9.0, 9.1, 8.9, 9.05, 50_000),
        _minute_bar(TEST_DAY, 13, 31, 9.05, 10.5, 9.0, 10.4, 80_000),
        _minute_bar(TEST_DAY, 13, 32, 10.4, 10.45, 10.0, 10.05, 40_000),
        _minute_bar(TEST_DAY, 13, 33, 10.05, 10.6, 10.05, 10.55, 60_000),
    ]

    monkeypatch.setattr(MarketScanner, "load_universe", lambda self: ["ACHR", "BYND"])
    monkeypatch.setattr(MarketScanner, "load_metadata", lambda self: {"ACHR": _ACHR_METADATA["ACHR"], "BYND": {"float_shares": 15_000_000, "sector": None}})

    def fake_daily_bars(self, symbols, start, end):
        return achr_daily if symbols == ["ACHR"] else bynd_daily

    def fake_historical_bars(self, symbol, start, end, limit=390, sort=None, timeframe=None):
        return _ACHR_BREAKOUT_BARS if symbol == "ACHR" else bynd_bars

    monkeypatch.setattr(AlpacaBroker, "daily_bars", fake_daily_bars)
    monkeypatch.setattr(AlpacaBroker, "historical_bars", fake_historical_bars)
    # Risk-based sizing intentionally leaves most capital unspent by design (that's the
    # point of risking only 2% per trade) - it's the position-value cap that two
    # symbols can actually compete over, so raise the risk cap out of the way to
    # isolate that specifically.
    monkeypatch.setattr(settings, "risk_per_trade_pct", 100.0)

    # $995 starting capital, $1000 position-value cap: ACHR's entry (94 shares @
    # $10.55 = $991.70, the largest qty $1000 can buy) leaves only $3.30 - not enough
    # for BYND to buy even a single additional share.
    result = backtest_module.run_daily_backtest(TEST_DAY, starting_capital=995, position_value=1000)

    achr_trades = [t for t in result["trades"] if t["symbol"] == "ACHR"]
    bynd_trades = [t for t in result["trades"] if t["symbol"] == "BYND"]
    assert len(achr_trades) == 1
    assert not bynd_trades, "BYND should never have gotten enough shared capital to open a position"
