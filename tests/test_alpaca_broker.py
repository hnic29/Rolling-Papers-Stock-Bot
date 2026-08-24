from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from alpaca.data.enums import DataFeed

from app.brokers.alpaca_broker import AlpacaBroker


def _broker_with_mocked_data_client():
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.data_client = MagicMock()
    broker.data_client.get_stock_bars.return_value = "irrelevant"
    return broker


def test_daily_bars_defaults_to_the_consolidated_sip_feed_not_iex():
    """Regression guard: verified live that IEX alone captures only ~2-3% of real
    volume for a liquid name (631K vs 19.8M shares on the same session) - both
    volume-based scoring pillars (total volume, relative volume) were silently reading
    a small fraction of the real market. Every current caller relies on this default."""
    broker = _broker_with_mocked_data_client()
    end = datetime.now(UTC)
    start = end - timedelta(days=5)

    broker.daily_bars(["AAPL"], start=start, end=end)

    request = broker.data_client.get_stock_bars.call_args.args[0]
    assert request.feed == DataFeed.SIP


def test_daily_bars_feed_can_still_be_overridden():
    broker = _broker_with_mocked_data_client()
    end = datetime.now(UTC)
    start = end - timedelta(days=5)

    broker.daily_bars(["AAPL"], start=start, end=end, feed=DataFeed.IEX)

    request = broker.data_client.get_stock_bars.call_args.args[0]
    assert request.feed == DataFeed.IEX
