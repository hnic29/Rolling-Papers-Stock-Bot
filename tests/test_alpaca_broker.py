from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from alpaca.data.enums import DataFeed

from app.brokers.alpaca_broker import AlpacaBroker, BrokerUnavailable
from app.config import settings
from app.services import credentials


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


def test_top_gainers_maps_the_screener_response_to_plain_dicts():
    from types import SimpleNamespace

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.screener_client = MagicMock()
    broker.screener_client.get_market_movers.return_value = SimpleNamespace(
        gainers=[SimpleNamespace(symbol="XPON", percent_change=80.49, price=6.2)],
        losers=[],
    )

    result = broker.top_gainers(top=5)

    assert result == [{"symbol": "XPON", "percent_change": 80.49, "price": 6.2}]


def test_constructor_falls_back_to_global_settings_when_no_credentials_passed(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_api_key", "settings-key")
    monkeypatch.setattr(settings, "alpaca_secret_key", "settings-secret")
    monkeypatch.setattr(settings, "alpaca_paper", True)

    broker = AlpacaBroker()

    assert broker.client._api_key == "settings-key"
    assert broker.client._secret_key == "settings-secret"


def test_constructor_prefers_explicit_credentials_over_global_settings(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_api_key", "settings-key")
    monkeypatch.setattr(settings, "alpaca_secret_key", "settings-secret")

    broker = AlpacaBroker(api_key="explicit-key", secret_key="explicit-secret", paper=True)

    assert broker.client._api_key == "explicit-key"
    assert broker.client._secret_key == "explicit-secret"


def test_constructor_still_blocks_live_trading_without_explicit_confirmation():
    with pytest.raises(BrokerUnavailable, match="Live trading is blocked"):
        AlpacaBroker(api_key="k", secret_key="s", paper=False, allow_live_trading=False)


def test_for_user_builds_a_broker_from_that_users_own_saved_credentials():
    credentials.save_credentials(user_id=1, alpaca_api_key="alice-key", alpaca_secret_key="alice-secret")
    credentials.save_credentials(user_id=2, alpaca_api_key="bob-key", alpaca_secret_key="bob-secret")

    alice_broker = AlpacaBroker.for_user(1)
    bob_broker = AlpacaBroker.for_user(2)

    assert alice_broker.client._api_key == "alice-key"
    assert bob_broker.client._api_key == "bob-key"


def test_for_user_raises_broker_unavailable_when_that_user_has_no_credentials_saved():
    with pytest.raises(BrokerUnavailable):
        AlpacaBroker.for_user(999)
