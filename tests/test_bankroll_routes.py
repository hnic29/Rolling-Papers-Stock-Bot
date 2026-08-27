from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.brokers.alpaca_broker import BrokerUnavailable
from app.main import app
from app.services import trade_log
from app.services import users as users_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def _authenticated(_isolated_trade_log_db):
    """See test_routes.py's identical fixture for why this is needed per test."""
    users_service.create_user("test-admin", "test-password-123", is_admin=True)
    response = client.post("/api/login", json={"username": "test-admin", "password": "test-password-123"})
    assert response.status_code == 200


class _Broker:
    def __init__(self, equity="50000.00"):
        self._equity = equity

    def account(self):
        return SimpleNamespace(equity=self._equity)


class _UnavailableBroker:
    def __init__(self):
        raise BrokerUnavailable("Missing Alpaca API credentials. Add paper keys to .env.")


def test_get_bankroll_starts_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.main.AlpacaBroker", lambda: _Broker())

    response = client.get("/api/bankroll")

    assert response.status_code == 200
    body = response.json()
    assert body["bankroll_balance"] == 0.0
    assert body["available_to_trade"] == 0.0
    assert body["savings_balance"] == 50000.0
    assert body["transactions"] == []


def test_withdraw_increases_the_bankroll_and_reduces_savings(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.main.AlpacaBroker", lambda: _Broker(equity="50000.00"))

    response = client.post("/api/bankroll/withdraw", json={"amount": 2000, "note": "starting bankroll"})

    assert response.status_code == 200
    body = response.json()
    assert body["bankroll_balance"] == 2000.0
    assert body["available_to_trade"] == 2000.0
    assert body["savings_balance"] == 48000.0
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["note"] == "starting bankroll"


def test_withdraw_rejects_more_than_whats_in_the_account(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.main.AlpacaBroker", lambda: _Broker(equity="1000.00"))

    response = client.post("/api/bankroll/withdraw", json={"amount": 2000})

    assert response.status_code == 400
    assert "available to withdraw" in response.json()["detail"]


def test_withdraw_requires_a_configured_broker(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.main.AlpacaBroker", _UnavailableBroker)

    response = client.post("/api/bankroll/withdraw", json={"amount": 2000})

    assert response.status_code == 400
    assert "credentials" in response.json()["detail"].lower()


def test_return_to_savings_decreases_the_bankroll(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.main.AlpacaBroker", lambda: _Broker(equity="50000.00"))
    client.post("/api/bankroll/withdraw", json={"amount": 2000})

    response = client.post("/api/bankroll/return", json={"amount": 500})

    assert response.status_code == 200
    body = response.json()
    assert body["bankroll_balance"] == 1500.0
    assert body["savings_balance"] == 48500.0


def test_return_to_savings_rejects_more_than_available(monkeypatch, tmp_path):
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.main.AlpacaBroker", lambda: _Broker(equity="50000.00"))
    client.post("/api/bankroll/withdraw", json={"amount": 2000})

    response = client.post("/api/bankroll/return", json={"amount": 2001})

    assert response.status_code == 400
    assert "available to return" in response.json()["detail"]


def test_get_bankroll_still_works_when_broker_is_unavailable(monkeypatch, tmp_path):
    """Savings balance just can't be computed - shouldn't break the rest of
    the bankroll panel (which is otherwise fully self-contained)."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")
    monkeypatch.setattr("app.main.AlpacaBroker", _UnavailableBroker)

    response = client.get("/api/bankroll")

    assert response.status_code == 200
    body = response.json()
    assert body["savings_balance"] is None
    assert body["savings_unavailable_reason"]
