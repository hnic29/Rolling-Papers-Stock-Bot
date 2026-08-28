from fastapi.testclient import TestClient

from app.main import app
from app.services import credentials
import app.services.finnhub_live as finnhub_live

client = TestClient(app)


def _bootstrap_admin(_isolated_trade_log_db) -> TestClient:
    admin_client = TestClient(app)
    response = admin_client.post("/api/bootstrap", json={"username": "admin", "password": "correct-horse-battery-123"})
    assert response.status_code == 200
    return admin_client


def test_live_stream_rejects_a_connection_with_no_session(_isolated_trade_log_db):
    with client.websocket_connect("/ws/live/AAPL") as ws:
        message = ws.receive_json()

    assert message["error"] == "Login required."


def test_live_stream_asks_for_a_finnhub_key_when_none_is_saved(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)

    with admin.websocket_connect("/ws/live/AAPL") as ws:
        message = ws.receive_json()

    assert "Finnhub API key" in message["error"]


def test_live_stream_reports_a_clean_error_when_the_upstream_connection_fails(_isolated_trade_log_db, monkeypatch):
    admin = _bootstrap_admin(_isolated_trade_log_db)
    credentials.save_credentials(user_id=1, finnhub_api_key="fake-test-key")

    def _fake_connect(*args, **kwargs):
        raise RuntimeError("simulated upstream failure - no real network call in tests")

    monkeypatch.setattr(finnhub_live.websockets, "connect", _fake_connect)

    with admin.websocket_connect("/ws/live/AAPL") as ws:
        message = ws.receive_json()

    assert "Live data stream failed" in message["error"]
