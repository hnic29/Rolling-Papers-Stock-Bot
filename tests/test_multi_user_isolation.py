"""End-to-end proof that two logged-in people get fully separate bots, bankrolls,
and trade history through the real HTTP API - the actual point of Stage 3's
per-user TradingBot/MarketScanner wiring, not just the unit-level isolation already
covered in test_trade_log.py/test_bankroll.py/test_bot_state.py/test_credentials.py."""

from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.brokers.alpaca_broker import AlpacaBroker
from app.main import app
from app.services import users as users_service


def _client_logged_in_as(username: str) -> TestClient:
    client = TestClient(app)
    users_service.create_user(username, "correct-horse-battery-123")
    response = client.post("/api/login", json={"username": username, "password": "correct-horse-battery-123"})
    assert response.status_code == 200
    return client


def test_two_users_auto_trading_toggles_are_independent():
    alice = _client_logged_in_as("alice")
    bob = _client_logged_in_as("bob")

    alice.post("/api/automation/start")

    assert alice.get("/api/status").json()["auto_trading_enabled"] is True
    assert bob.get("/api/status").json()["auto_trading_enabled"] is False


def test_two_users_bankrolls_and_trades_never_cross_over():
    alice = _client_logged_in_as("alice")
    bob = _client_logged_in_as("bob")

    with patch.object(AlpacaBroker, "for_user", classmethod(lambda cls, user_id: SimpleNamespace(account=lambda: SimpleNamespace(equity="10000.00")))):
        alice.post("/api/bankroll/withdraw", json={"amount": 1000})
        bob.post("/api/bankroll/withdraw", json={"amount": 2500})

    assert alice.get("/api/bankroll").json()["bankroll_balance"] == 1000.0
    assert bob.get("/api/bankroll").json()["bankroll_balance"] == 2500.0

    fake_order = SimpleNamespace(id="alice-order-1", symbol="AAPL", status="accepted")
    with patch.object(AlpacaBroker, "for_user", classmethod(lambda cls, user_id: SimpleNamespace(submit_market_order=lambda *a, **kw: fake_order))):
        trade_response = alice.post("/api/trade", json={"symbol": "AAPL", "qty": 1, "side": "buy"})
    assert trade_response.status_code == 200

    assert len(alice.get("/api/trades/history").json()["trades"]) == 1
    assert len(bob.get("/api/trades/history").json()["trades"]) == 0


def test_two_users_settings_are_never_shared():
    alice = _client_logged_in_as("alice")
    bob = _client_logged_in_as("bob")

    alice.post(
        "/api/settings",
        json={
            "alpaca_api_key": "alice-own-key",
            "alpaca_secret_key": "",
            "alpaca_paper": True,
            "fmp_api_key": "",
            "allow_live_trading": False,
        },
    )

    assert alice.get("/api/settings").json()["alpaca_api_key"].startswith("alic")
    assert bob.get("/api/settings").json()["alpaca_api_key"] == ""


def test_one_users_session_cannot_act_on_another_users_data():
    """Even with both accounts' ids known, there's no way to pass someone else's
    user_id explicitly - every route resolves it only from the caller's own signed
    session cookie."""
    alice = _client_logged_in_as("alice")
    bob = _client_logged_in_as("bob")

    alice.post("/api/automation/start")
    bob.post("/api/automation/trades-today?count=3")

    alice_status = alice.get("/api/status").json()
    bob_status = bob.get("/api/status").json()
    assert alice_status["auto_trading_enabled"] is True
    assert alice_status["trades_today"] == 0
    assert bob_status["auto_trading_enabled"] is False
    assert bob_status["trades_today"] == 3
