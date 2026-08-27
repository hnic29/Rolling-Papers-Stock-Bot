from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.models import ScannerResponse, ScannerResult, Signal
from app.services.live_setup import build_pullback_setup
from app.services.scanner import MarketScanner


def _bar(hour, minute, close, volume=100_000):
    ts = datetime(2026, 8, 24, hour, minute, tzinfo=UTC).isoformat()
    return {"timestamp": ts, "open": close, "high": close, "low": close, "close": close, "volume": volume}


def test_build_pullback_setup_fetches_bars_through_the_scanners_own_broker(monkeypatch):
    """The scanner already knows which user it belongs to (MarketScanner._broker) -
    this must reuse that, not construct a fresh app-wide AlpacaBroker(), or a user's
    setup would be built from someone else's (or nobody's) Alpaca account."""
    scanner = MarketScanner(user_id=7)
    monkeypatch.setattr(
        scanner,
        "scan",
        lambda symbols: ScannerResponse(
            results=[
                ScannerResult(
                    symbol="AAPL", price=10.0, percent_change=15.0, total_volume=2_000_000,
                    relative_volume=6.0, score=4, signal=Signal.buy, reasons=[],
                )
            ]
        ),
    )

    fake_broker = MagicMock()
    fake_broker.historical_bars.return_value = [_bar(9, 30, 9.5), _bar(9, 31, 9.8), _bar(9, 32, 10.0)]
    seen_user_ids = []
    monkeypatch.setattr(scanner, "_broker", lambda: seen_user_ids.append(scanner.user_id) or fake_broker)

    build_pullback_setup("AAPL", scanner)

    assert seen_user_ids == [7]
    fake_broker.historical_bars.assert_called_once()
