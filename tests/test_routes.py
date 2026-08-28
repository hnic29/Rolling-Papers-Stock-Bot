from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.brokers.alpaca_broker import AlpacaBroker, BrokerUnavailable
from app.main import app
from app.services import credentials, trade_log
from app.services import users as users_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def _authenticated(_isolated_trade_log_db):
    """Every route below is now behind session auth. `_isolated_trade_log_db` (from
    conftest.py) gives each test a fresh, empty users table, so log a throwaway
    admin in fresh every test too - declared as a dependency (not just relying on
    fixture ordering) so it always runs after the DB is pointed at tmp_path."""
    users_service.create_user("test-admin", "test-password-123", is_admin=True)
    response = client.post("/api/login", json={"username": "test-admin", "password": "test-password-123"})
    assert response.status_code == 200


def test_index_serves_the_dashboard_html():
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_status_returns_bot_status_shape():
    response = client.get("/api/status")

    assert response.status_code == 200
    body = response.json()
    assert "running" in body
    assert "symbol" in body
    assert "trades_today" in body
    assert "daily_pnl" in body


def test_automation_start_and_stop_toggle_status():
    start_response = client.post("/api/automation/start")
    assert start_response.status_code == 200
    assert start_response.json()["auto_trading_enabled"] is True

    stop_response = client.post("/api/automation/stop")
    assert stop_response.status_code == 200
    assert stop_response.json()["auto_trading_enabled"] is False


def test_account_returns_400_when_broker_is_unavailable(monkeypatch):
    class UnavailableBroker:
        def __init__(self):
            raise BrokerUnavailable("no Alpaca credentials configured")

        for_user = classmethod(lambda cls, user_id: cls())

    monkeypatch.setattr("app.main.AlpacaBroker", UnavailableBroker)

    response = client.get("/api/account")

    assert response.status_code == 400


def test_account_returns_502_not_a_raw_traceback_on_unexpected_broker_failure(monkeypatch):
    class BoomBroker:
        def account(self):
            raise RuntimeError("connection reset")

        for_user = classmethod(lambda cls, user_id: cls())

    monkeypatch.setattr("app.main.AlpacaBroker", BoomBroker)

    response = client.get("/api/account")

    assert response.status_code == 502
    assert "Could not fetch Alpaca account" in response.json()["detail"]


def test_settings_test_reports_unconfigured_keys_as_not_configured(monkeypatch):
    class UnavailableBroker:
        def __init__(self):
            raise BrokerUnavailable("no Alpaca credentials configured")

        for_user = classmethod(lambda cls, user_id: cls())

    class UnconfiguredFmp:
        configured = False

    monkeypatch.setattr("app.main.AlpacaBroker", UnavailableBroker)
    monkeypatch.setattr("app.main.FmpClient", UnconfiguredFmp)

    response = client.get("/api/settings/test")

    assert response.status_code == 200
    body = response.json()
    assert body["alpaca"]["configured"] is False
    assert body["alpaca"]["ok"] is False
    assert body["fmp"]["configured"] is False


def test_settings_test_reports_a_working_alpaca_key(monkeypatch):
    class WorkingBroker:
        def account(self):
            return SimpleNamespace(status="ACTIVE")

        for_user = classmethod(lambda cls, user_id: cls())

    class UnconfiguredFmp:
        configured = False

    monkeypatch.setattr("app.main.AlpacaBroker", WorkingBroker)
    monkeypatch.setattr("app.main.FmpClient", UnconfiguredFmp)

    response = client.get("/api/settings/test")

    body = response.json()
    assert body["alpaca"]["configured"] is True
    assert body["alpaca"]["ok"] is True
    assert "ACTIVE" in body["alpaca"]["detail"]


def test_settings_test_reports_a_rejected_alpaca_key_without_a_raw_traceback(monkeypatch):
    class RejectingBroker:
        def account(self):
            raise RuntimeError("401 unauthorized")

        for_user = classmethod(lambda cls, user_id: cls())

    class UnconfiguredFmp:
        configured = False

    monkeypatch.setattr("app.main.AlpacaBroker", RejectingBroker)
    monkeypatch.setattr("app.main.FmpClient", UnconfiguredFmp)

    response = client.get("/api/settings/test")

    assert response.status_code == 200
    body = response.json()
    assert body["alpaca"]["configured"] is True
    assert body["alpaca"]["ok"] is False
    assert "rejected" in body["alpaca"]["detail"]


def test_settings_test_reports_a_working_fmp_key(monkeypatch):
    class UnavailableBroker:
        def __init__(self):
            raise BrokerUnavailable("no Alpaca credentials configured")

        for_user = classmethod(lambda cls, user_id: cls())

    class WorkingFmp:
        configured = True

        def shares_float(self, symbol, use_cache=True):
            return {"symbol": symbol, "floatShares": 1000000}

    monkeypatch.setattr("app.main.AlpacaBroker", UnavailableBroker)
    monkeypatch.setattr("app.main.FmpClient", WorkingFmp)

    response = client.get("/api/settings/test")

    body = response.json()
    assert body["fmp"]["configured"] is True
    assert body["fmp"]["ok"] is True


def test_positions_returns_502_not_a_raw_traceback_on_unexpected_broker_failure(monkeypatch):
    class BoomBroker:
        def positions_as_dicts(self):
            raise RuntimeError("connection reset")

        for_user = classmethod(lambda cls, user_id: cls())

    monkeypatch.setattr("app.main.AlpacaBroker", BoomBroker)

    response = client.get("/api/positions")

    assert response.status_code == 502
    assert "Could not fetch Alpaca positions" in response.json()["detail"]


def test_trade_rejects_invalid_side_enum_value():
    response = client.post("/api/trade", json={"symbol": "AAPL", "qty": 1, "side": "banana"})

    assert response.status_code == 422


def test_trade_rejects_a_position_over_the_configured_cap_without_touching_the_broker():
    response = client.post(
        "/api/trade",
        json={"symbol": "AAPL", "qty": 1000, "side": "buy", "estimated_price": 100.0},
    )

    assert response.status_code == 400


def test_backtest_runs_end_to_end_over_http(monkeypatch):
    test_day = date(2026, 6, 1)
    prev_day = date(2026, 5, 29)

    def daily_bar(day, close, volume):
        return SimpleNamespace(timestamp=datetime(day.year, day.month, day.day, 20, 0, tzinfo=timezone.utc), close=close, volume=volume)

    def minute_bar(hour, minute, open_, high, low, close, volume):
        ts = datetime(test_day.year, test_day.month, test_day.day, hour, minute, tzinfo=timezone.utc).isoformat()
        return {"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume}

    daily_bars_response = SimpleNamespace(
        data={
            "AAPL": [
                daily_bar(prev_day, close=9.0, volume=100_000),
                daily_bar(test_day, close=10.5, volume=2_000_000),
            ]
        }
    )
    minute_bars = [
        minute_bar(13, 30, 9.0, 9.1, 8.9, 9.05, 50_000),
        minute_bar(13, 31, 9.05, 10.5, 9.0, 10.4, 80_000),
        minute_bar(13, 32, 10.4, 10.45, 10.0, 10.05, 40_000),
        minute_bar(13, 33, 10.05, 10.6, 10.05, 10.55, 60_000),
        minute_bar(13, 34, 10.55, 10.65, 10.5, 10.4, 50_000),  # red candle -> exit_signal
    ]

    monkeypatch.setattr(AlpacaBroker, "__init__", lambda self: None)
    monkeypatch.setattr(AlpacaBroker, "daily_bars", lambda self, symbols, start, end: daily_bars_response)
    monkeypatch.setattr(
        AlpacaBroker,
        "historical_bars",
        lambda self, symbol, start, end, limit=390, sort=None, timeframe=None: minute_bars,
    )

    response = client.post(
        "/api/backtest",
        json={
            "day": test_day.isoformat(),
            "symbols": ["AAPL"],
            "starting_capital": 10000,
            "position_value": 1000,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trade_count"] == 1
    assert body["trades"][0]["exit_reason"] == "exit_signal"
    assert body["symbols_scanned"] == 1
    assert body["symbols_qualified"] == 1


def test_sync_records_the_exit_leg_of_a_filled_bracket_trade(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    trade_log.record_trade(
        order_id="bracket1", symbol="ACHR", side="buy", qty=1, status="accepted",
        stop_loss_price=6.45, take_profit_price=6.90,
    )
    trade_log.update_fill(order_id="bracket1", status="filled", filled_avg_price=6.56, filled_qty=1, filled_at="2026-08-10T14:41:08Z")

    target_leg = SimpleNamespace(
        id="leg-target", status="filled", order_type="limit",
        filled_avg_price=6.90, filled_qty=1,
        filled_at=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
    )
    stop_leg = SimpleNamespace(id="leg-stop", status="canceled", order_type="stop", filled_avg_price=None, filled_qty=None, filled_at=None)
    parent_order = SimpleNamespace(legs=[target_leg, stop_leg])

    class FakeBroker:
        def get_order(self, order_id):
            return parent_order

        for_user = classmethod(lambda cls, user_id: cls())

    monkeypatch.setattr("app.main.AlpacaBroker", FakeBroker)

    response = client.post("/api/trades/history/sync")

    assert response.status_code == 200
    trade = response.json()["trades"][0]
    assert trade["exit_price"] == 6.90
    assert trade["exit_reason"] == "target"
    assert trade["realized_pnl"] == 0.34


def test_settings_get_masks_secrets_and_never_returns_them_raw():
    credentials.save_credentials(user_id=1, alpaca_api_key="PKTESTSECRETVALUE1234", alpaca_paper=True)

    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert "PKTESTSECRETVALUE1234" not in body["alpaca_api_key"]
    assert body["alpaca_api_key"].startswith("PKTE")


def test_settings_post_saves_only_to_this_users_own_credentials():
    response = client.post(
        "/api/settings",
        json={
            "alpaca_api_key": "PKNEWTESTKEY000000",
            "alpaca_secret_key": "",
            "alpaca_paper": True,
            "fmp_api_key": "",
            "allow_live_trading": False,
        },
    )

    assert response.status_code == 200
    assert credentials.get_credentials(user_id=1)["alpaca_api_key"] == "PKNEWTESTKEY000000"
    assert credentials.get_credentials(user_id=2)["alpaca_api_key"] == ""  # another user's row is untouched


def test_settings_post_strips_a_pasted_label_off_the_key():
    response = client.post(
        "/api/settings",
        json={
            "alpaca_api_key": "",
            "alpaca_secret_key": "",
            "alpaca_paper": True,
            "fmp_api_key": "apikey: TestKeyNotARealCredential123",
            "allow_live_trading": False,
        },
    )

    assert response.status_code == 200
    saved = credentials.get_credentials(user_id=1)
    assert saved["fmp_api_key"] == "TestKeyNotARealCredential123"


def _live_settings_payload(confirm=None):
    payload = {
        "alpaca_api_key": "",
        "alpaca_secret_key": "",
        "alpaca_paper": False,
        "fmp_api_key": "",
        "allow_live_trading": True,
    }
    if confirm is not None:
        payload["confirm_live_trading"] = confirm
    return payload


def test_settings_post_refuses_to_arm_live_trading_without_explicit_confirmation():
    """Two mis-clicked checkboxes (or a bare API call) must never be enough to put real
    money in play - arming live trading requires an explicit confirmation flag."""
    response = client.post("/api/settings", json=_live_settings_payload())

    assert response.status_code == 400
    assert "LIVE" in response.json()["detail"]
    assert credentials.get_credentials(user_id=1)["allow_live_trading"] is False  # nothing saved


def test_settings_post_arms_live_trading_with_explicit_confirmation():
    response = client.post("/api/settings", json=_live_settings_payload(confirm=True))

    assert response.status_code == 200
    saved = credentials.get_credentials(user_id=1)
    assert saved["alpaca_paper"] is False
    assert saved["allow_live_trading"] is True


def test_settings_post_needs_no_confirmation_while_staying_paper():
    """The allow flag alone (paper still on) doesn't arm anything - the broker gate
    needs BOTH - so it shouldn't demand the scary confirmation either."""
    payload = _live_settings_payload()
    payload["alpaca_paper"] = True

    response = client.post("/api/settings", json=payload)

    assert response.status_code == 200


def test_scanner_status_is_idle_by_default():
    response = client.get("/api/scanner/status")

    assert response.status_code == 200
    body = response.json()
    assert body["scanning"] is False
    assert body["phase"] == "idle"
    assert body["results"] == []


def test_scanner_status_reflects_a_failed_scan_without_getting_stuck_scanning():
    """No Alpaca credentials saved in this test's isolated DB, so the scan fails
    immediately - the status endpoint should show that failure, not leave
    `scanning: true` stuck forever (which is what a poller would see if the
    in-progress state was never cleared on an error path)."""
    scan_response = client.post("/api/scanner", json={"symbols": ["AAPL"]})
    assert scan_response.status_code == 400  # BrokerUnavailable - no credentials configured

    status_response = client.get("/api/scanner/status")

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["scanning"] is False
    assert body["detail"]  # carries the failure reason
