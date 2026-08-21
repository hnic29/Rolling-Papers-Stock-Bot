import base64

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def _basic_header(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_dashboard_is_open_when_no_credentials_are_configured(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")

    response = client.get("/")

    assert response.status_code == 200


def test_dashboard_requires_auth_once_configured(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret")

    response = client.get("/")

    assert response.status_code == 401
    # Must be JSON (not plain text) - the dashboard's own fetch() calls parse
    # every response as JSON and would throw on an unparseable 401 body,
    # which is exactly what broke settings save/test before this was fixed.
    assert response.json() == {"detail": "Authentication required"}
    assert response.headers["www-authenticate"].lower().startswith("basic")


def test_dashboard_rejects_wrong_credentials(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret")

    response = client.get("/", headers=_basic_header("admin", "wrong-password"))

    assert response.status_code == 401


def test_dashboard_accepts_correct_credentials(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret")

    response = client.get("/", headers=_basic_header("admin", "secret"))

    assert response.status_code == 200


def test_api_status_stays_public_for_healthchecks_even_when_auth_is_on(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret")

    response = client.get("/api/status")

    assert response.status_code == 200


def test_other_api_routes_are_protected_when_auth_is_on(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret")

    response = client.get("/api/positions")

    assert response.status_code == 401
