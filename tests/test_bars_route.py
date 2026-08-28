from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import RANGE_PRESETS, app, resolve_period

client = TestClient(app)


@pytest.fixture(autouse=True)
def _authenticated(_isolated_trade_log_db):
    from app.services import users as users_service

    users_service.create_user("test-admin", "test-password-123", is_admin=True)
    response = client.post("/api/login", json={"username": "test-admin", "password": "test-password-123"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "period, expected_value",
    [
        ("5Min", "5Min"),
        ("15Min", "15Min"),
        ("30Min", "30Min"),
        ("1Hour", "1Hour"),
        ("4Hour", "4Hour"),
    ],
)
def test_resolve_period_maps_the_new_intraday_intervals_to_the_right_timeframe(period, expected_value):
    timeframe, start = resolve_period(period, datetime(2026, 8, 28, tzinfo=UTC))

    assert timeframe.value == expected_value
    assert start < datetime(2026, 8, 28, tzinfo=UTC)


def test_new_intraday_presets_use_a_bounded_lookback_not_thousands_of_bars():
    # A sanity check on the lookback window, not the exact number - the point is
    # these stay in the "a few hundred bars" range, not multi-year requests.
    for key in ("5MIN", "15MIN", "30MIN", "1HOUR", "4HOUR"):
        _timeframe, lookback_days = RANGE_PRESETS[key]
        assert 0 < lookback_days <= 365


def test_bars_route_passes_the_resolved_intraday_timeframe_to_the_broker(monkeypatch):
    captured = {}

    class FakeBroker:
        def historical_bars(self, symbol, start, end, limit, sort, timeframe):
            captured["timeframe"] = timeframe
            captured["symbol"] = symbol
            return []

        for_user = classmethod(lambda cls, user_id: cls())

    monkeypatch.setattr("app.main.AlpacaBroker", FakeBroker)

    response = client.get("/api/bars/AAPL", params={"period": "15Min"})

    assert response.status_code == 200
    assert response.json()["bars"] == []
    assert captured["timeframe"].value == "15Min"
    assert captured["symbol"] == "AAPL"
