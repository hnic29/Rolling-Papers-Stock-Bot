from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.scanner import MarketScanner, _session_progress_fraction


def _et(year, month, day, hour, minute):
    from zoneinfo import ZoneInfo
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def test_session_progress_is_zero_ish_right_at_the_open():
    fraction = _session_progress_fraction(_et(2026, 8, 19, 9, 30))  # Wednesday
    assert 0 < fraction <= 0.06


def test_session_progress_is_half_at_midday():
    fraction = _session_progress_fraction(_et(2026, 8, 19, 12, 45))  # 195 of 390 minutes in
    assert abs(fraction - 0.5) < 0.01


def test_session_progress_is_one_after_the_close():
    assert _session_progress_fraction(_et(2026, 8, 19, 16, 30)) == 1.0


def test_session_progress_is_one_before_the_open():
    assert _session_progress_fraction(_et(2026, 8, 19, 6, 0)) == 1.0


def test_session_progress_is_one_on_a_weekend():
    assert _session_progress_fraction(_et(2026, 8, 22, 12, 0)) == 1.0  # Saturday


def _bar(close, volume):
    return SimpleNamespace(open=close, high=close, low=close, close=close, volume=volume)


def _mock_broker(bars_by_symbol):
    broker = MagicMock()
    broker.daily_bars.return_value = SimpleNamespace(data=bars_by_symbol)
    broker.latest_quote.side_effect = Exception("no quote in test")
    broker.latest_news.side_effect = Exception("no news in test")
    return broker


def test_relative_volume_is_prorated_by_time_of_day(monkeypatch):
    """20 prior days averaging 1,000,000 shares, today's partial bar at 500,000 so far.
    Halfway through the session, that's on pace with the full-day average (relative volume
    ~1.0) - the old, un-prorated math would have read 0.5, half of what it actually is."""
    monkeypatch.setattr("app.services.scanner._session_progress_fraction", lambda now: 0.5)
    bars = {"ACHR": [_bar(10.0, 1_000_000) for _ in range(20)] + [_bar(10.5, 500_000)]}

    scanner = MarketScanner()
    with patch("app.services.scanner.AlpacaBroker", return_value=_mock_broker(bars)):
        response = scanner.scan(["ACHR"])

    assert len(response.results) == 1
    assert abs(response.results[0].relative_volume - 1.0) < 0.01


def test_fmp_float_lookup_is_skipped_for_a_symbol_failing_the_cheap_pillars(monkeypatch):
    monkeypatch.setattr("app.services.scanner._session_progress_fraction", lambda now: 1.0)
    # Barely moves, ordinary volume - fails relative volume, total volume, and percent change.
    bars = {"COLD": [_bar(10.0, 500_000) for _ in range(20)] + [_bar(10.05, 500_000)]}

    scanner = MarketScanner()
    scanner.fmp.shares_float = MagicMock(return_value={"float_shares": 1_000_000})
    with patch("app.services.scanner.AlpacaBroker", return_value=_mock_broker(bars)):
        scanner.scan(["COLD"])

    scanner.fmp.shares_float.assert_not_called()


def test_fmp_float_lookup_runs_for_a_symbol_clearing_the_cheap_pillars(monkeypatch):
    monkeypatch.setattr("app.services.scanner._session_progress_fraction", lambda now: 1.0)
    # 15% move, 6x relative volume, well over 1M shares, price in range - clears everything
    # except float, which is exactly the case float should be spent checking.
    bars = {"HOTT": [_bar(10.0, 1_000_000) for _ in range(20)] + [_bar(11.5, 6_000_000)]}

    scanner = MarketScanner()
    scanner.fmp.shares_float = MagicMock(return_value={"float_shares": 5_000_000})
    with patch("app.services.scanner.AlpacaBroker", return_value=_mock_broker(bars)):
        response = scanner.scan(["HOTT"])

    scanner.fmp.shares_float.assert_called_once_with("HOTT")
    assert response.results[0].float_shares == 5_000_000
    assert response.results[0].score == 5
