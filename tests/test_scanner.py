from datetime import UTC, datetime, timedelta
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


def test_relative_volume_average_uses_only_the_last_20_trading_days(monkeypatch):
    """Regression: this used to average every bar the 80-calendar-day fetch happened to
    return (~56 trading days), silently diverging from the documented and backtest-
    matching 20-day convention. 40 prior days at very different volumes - if the whole
    fetch were still being averaged, the result would be far from what a clean 20-day
    window gives."""
    monkeypatch.setattr("app.services.scanner._session_progress_fraction", lambda now: 1.0)
    # 20 older days at 3,000,000 (would drag the average way up if included) followed
    # by 20 recent days at 1,000,000 (the window this SHOULD use), then today.
    bars = {
        "ACHR": [_bar(10.0, 3_000_000) for _ in range(20)]
        + [_bar(10.0, 1_000_000) for _ in range(20)]
        + [_bar(10.5, 5_000_000)]
    }

    scanner = MarketScanner()
    with patch("app.services.scanner.AlpacaBroker", return_value=_mock_broker(bars)):
        response = scanner.scan(["ACHR"])

    # 5,000,000 / 1,000,000 = 5.0 if only the last 20 days count; including the older,
    # higher-volume days would pull relative_volume down well under that.
    assert abs(response.results[0].relative_volume - 5.0) < 0.01


def test_scan_queries_past_the_sip_recency_restriction(monkeypatch):
    """Alpaca's free tier rejects a SIP query newer than ~15 minutes (verified live).
    scan() must query a window ending far enough in the past to actually get data back,
    not literal now()."""
    from app.services import scanner as scanner_module

    captured = {}

    def capturing_daily_bars(symbols, start, end):
        captured["end"] = end
        return SimpleNamespace(data={})

    broker = MagicMock()
    broker.daily_bars.side_effect = capturing_daily_bars
    broker.latest_news.side_effect = Exception("no news in test")

    scanner = MarketScanner()
    with patch("app.services.scanner.AlpacaBroker", return_value=broker):
        scanner.scan(["ACHR"])
    after_call = datetime.now(UTC)

    # Measured from AFTER the call: scan()'s own now() is <= after_call, so its
    # (now - buffer) end must sit at least the full buffer before after_call.
    # Measuring from before the call is a race - scan's now() lands microseconds
    # later, leaving the observed lag a hair UNDER the buffer.
    lag = after_call - captured["end"]
    assert lag >= timedelta(minutes=scanner_module.SIP_RECENCY_BUFFER_MINUTES)


def test_todays_gainers_filters_to_tradeable_common_shares():
    """The raw top-gainers list is full of +300% warrants at $0.01 - only common shares
    in the strategy's price range that already cleared the minimum move are worth a
    scan slot. Real symbols from a live screener pull."""
    broker = MagicMock()
    broker.top_gainers.return_value = [
        {"symbol": "XPON", "percent_change": 80.5, "price": 6.2},  # the one that matters
        {"symbol": "DAICW", "percent_change": 380.0, "price": 0.0096},  # warrant, penny
        {"symbol": "TLSIW", "percent_change": 103.6, "price": 3.14},  # warrant suffix, price in range
        {"symbol": "GIPR", "percent_change": 150.7, "price": 0.75},  # under $2
        {"symbol": "SDOT", "percent_change": 75.7, "price": 23.16},  # over $20
        {"symbol": "PMI", "percent_change": 9.0, "price": 5.13},  # under the 10% minimum move
        {"symbol": "TONT.WS", "percent_change": 35.5, "price": 4.8},  # not a clean symbol
    ]

    scanner = MarketScanner()
    with patch("app.services.scanner.AlpacaBroker", return_value=broker):
        picked = scanner.todays_gainers()

    assert picked == {"XPON"}


def test_todays_gainers_is_empty_when_the_screener_fails():
    """A screener hiccup must never break the scan - the cycle just falls back to the
    static universe alone, same as before the movers feed existed."""
    broker = MagicMock()
    broker.top_gainers.side_effect = Exception("screener down")

    scanner = MarketScanner()
    with patch("app.services.scanner.AlpacaBroker", return_value=broker):
        assert scanner.todays_gainers() == set()


def test_scan_universe_merges_sweep_gainers_and_the_static_list(monkeypatch):
    """The static watchlist alone can't catch a day-of runner (XPON, JUNS - both real
    missed trades) - every scan must also cover the whole-market sweep and today's
    live top gainers."""
    scanner = MarketScanner()
    monkeypatch.setattr(scanner, "load_universe", lambda: ["ACHR", "BYND"])
    monkeypatch.setattr(scanner, "todays_gainers", lambda: {"PMI", "BYND"})
    monkeypatch.setattr(scanner, "full_market_sweep", lambda: ({"XPON"}, 8_253))

    scanned = {}

    def fake_scan(symbols):
        scanned["symbols"] = symbols
        from app.models import ScannerResponse
        return ScannerResponse(results=[], scanned_count=len(symbols))

    monkeypatch.setattr(scanner, "scan", fake_scan)

    response = scanner.scan_universe()

    assert scanned["symbols"] == ["ACHR", "BYND", "PMI", "XPON"]  # merged, deduped, sorted
    assert response.scanned_count == 4
    assert response.swept_count == 8_253


def _sweep_bar(close, volume):
    return SimpleNamespace(close=close, volume=volume)


def test_full_market_sweep_finds_a_qualifying_mover_across_the_whole_market(monkeypatch):
    """The Ross-style scanner: a symbol on NO list anywhere must still be found the
    moment its real numbers fit the profile. RUNR clears all four bar-math pillars;
    the others each fail exactly one (or are a derivative listing)."""
    monkeypatch.setattr("app.services.scanner._session_progress_fraction", lambda now: 1.0)

    quiet_history = [_sweep_bar(5.0, 400_000) for _ in range(20)]
    bars_by_symbol = {
        "RUNR": quiet_history + [_sweep_bar(6.0, 5_000_000)],  # +20%, 12.5x relvol, 5M shares
        "FLAT": quiet_history + [_sweep_bar(5.1, 5_000_000)],  # only +2% - fails the move
        "THIN": quiet_history + [_sweep_bar(6.0, 900_000)],  # fails total volume
        "RICH": [_sweep_bar(50.0, 400_000)] * 20 + [_sweep_bar(60.0, 5_000_000)],  # out of price range
        "SLOWW": [_sweep_bar(5.0, 4_000_000)] * 20 + [_sweep_bar(6.0, 5_000_000)],  # only 1.25x relvol
        "ABCDW": quiet_history + [_sweep_bar(6.0, 5_000_000)],  # warrant suffix
    }

    scanner = MarketScanner()
    monkeypatch.setattr(scanner, "_tradable_symbols", lambda: sorted(bars_by_symbol))

    broker = MagicMock()
    broker.daily_bars.side_effect = lambda symbols, start, end: SimpleNamespace(
        data={s: bars_by_symbol[s] for s in symbols}
    )
    with patch("app.services.scanner.AlpacaBroker", return_value=broker):
        hits, swept = scanner.full_market_sweep()

    assert hits == {"RUNR"}
    assert swept == 6


def test_full_market_sweep_survives_a_failed_chunk(monkeypatch):
    monkeypatch.setattr("app.services.scanner._session_progress_fraction", lambda now: 1.0)
    scanner = MarketScanner()
    monkeypatch.setattr(scanner, "_tradable_symbols", lambda: ["AAAA", "BBBB"])

    broker = MagicMock()
    broker.daily_bars.side_effect = Exception("one chunk timed out")
    with patch("app.services.scanner.AlpacaBroker", return_value=broker):
        hits, swept = scanner.full_market_sweep()

    assert hits == set()
    assert swept == 0


def test_tradable_symbols_are_cached_for_the_day(monkeypatch):
    from app.services import scanner as scanner_module

    monkeypatch.setattr(scanner_module, "_tradable_symbols_cache", {"date": None, "symbols": []})
    broker = MagicMock()
    broker.all_tradable_symbols.return_value = ["AAAA", "BBBB"]

    scanner = MarketScanner()
    with patch("app.services.scanner.AlpacaBroker", return_value=broker):
        assert scanner._tradable_symbols() == ["AAAA", "BBBB"]
        assert scanner._tradable_symbols() == ["AAAA", "BBBB"]  # served from the day cache

    broker.all_tradable_symbols.assert_called_once()


def test_tradable_symbols_fall_back_to_the_stale_cache_on_failure(monkeypatch):
    from datetime import date

    from app.services import scanner as scanner_module

    monkeypatch.setattr(
        scanner_module,
        "_tradable_symbols_cache",
        {"date": date(2020, 1, 1), "symbols": ["STALE"]},  # yesterday's list
    )
    broker = MagicMock()
    broker.all_tradable_symbols.side_effect = Exception("asset endpoint down")

    scanner = MarketScanner()
    with patch("app.services.scanner.AlpacaBroker", return_value=broker):
        assert scanner._tradable_symbols() == ["STALE"]  # stale beats nothing mid-day


def test_live_float_lookup_is_skipped_for_a_symbol_failing_the_cheap_pillars(monkeypatch):
    monkeypatch.setattr("app.services.scanner._session_progress_fraction", lambda now: 1.0)
    # Barely moves, ordinary volume - fails relative volume, total volume, and percent change.
    bars = {"COLD": [_bar(10.0, 500_000) for _ in range(20)] + [_bar(10.05, 500_000)]}
    lookup = MagicMock(return_value=1_000_000)
    monkeypatch.setattr("app.services.scanner.float_lookup.float_shares", lookup)

    scanner = MarketScanner()
    with patch("app.services.scanner.AlpacaBroker", return_value=_mock_broker(bars)):
        scanner.scan(["COLD"])

    lookup.assert_not_called()


def test_live_float_lookup_runs_for_a_symbol_clearing_the_cheap_pillars(monkeypatch):
    monkeypatch.setattr("app.services.scanner._session_progress_fraction", lambda now: 1.0)
    # 15% move, 6x relative volume, well over 1M shares, price in range - clears everything
    # except float, which is exactly the case float should be spent checking.
    bars = {"HOTT": [_bar(10.0, 1_000_000) for _ in range(20)] + [_bar(11.5, 6_000_000)]}
    lookup = MagicMock(return_value=5_000_000)
    monkeypatch.setattr("app.services.scanner.float_lookup.float_shares", lookup)

    scanner = MarketScanner()
    with patch("app.services.scanner.AlpacaBroker", return_value=_mock_broker(bars)):
        response = scanner.scan(["HOTT"])

    lookup.assert_called_once_with("HOTT")
    assert response.results[0].float_shares == 5_000_000
    assert response.results[0].score == 5


def test_universe_age_days_reads_the_build_date_from_the_header(tmp_path):
    universe_path = tmp_path / "stock_universe.txt"
    universe_path.write_text(
        "# Built by scripts/build_universe.py on 2026-08-01 - live-screened for price...\nAAPL\n",
        encoding="utf-8",
    )
    scanner = MarketScanner()
    scanner.universe_path = universe_path

    built = datetime(2026, 8, 1, tzinfo=UTC).date()
    expected_age = (datetime.now(UTC).date() - built).days

    assert scanner.universe_age_days() == expected_age


def test_universe_age_days_is_none_without_a_recognizable_header(tmp_path):
    universe_path = tmp_path / "stock_universe.txt"
    universe_path.write_text("AAPL\nTSLA\n", encoding="utf-8")
    scanner = MarketScanner()
    scanner.universe_path = universe_path

    assert scanner.universe_age_days() is None


def test_universe_age_days_is_none_when_the_file_is_missing(tmp_path):
    scanner = MarketScanner()
    scanner.universe_path = tmp_path / "does_not_exist.txt"

    assert scanner.universe_age_days() is None
