from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.main import app
from app.services import env_file

client = TestClient(app)


def _isolate_env_file(monkeypatch, tmp_path, initial: str = "") -> None:
    """Points BOTH env_file.py's ENV_PATH (used by read_env/write_env) and
    Settings' own dotenv path (used when reload_settings() re-reads) at the
    same isolated file - they must always agree, same as in production
    (see config.py's comment on _ENV_FILE_PATH), or a write here wouldn't
    actually be visible on the settings object afterward."""
    fake_env = tmp_path / ".env"
    fake_env.write_text(initial, encoding="utf-8")
    monkeypatch.setattr(env_file, "ENV_PATH", fake_env)
    monkeypatch.setitem(Settings.model_config, "env_file", str(fake_env))


def test_first_time_setup_does_not_require_a_current_password(monkeypatch, tmp_path):
    _isolate_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")

    response = client.post(
        "/api/settings/dashboard-auth",
        json={"current_password": "", "new_username": "admin", "new_password": "a-strong-password"},
    )

    assert response.status_code == 200
    assert response.json()["dashboard_username"] == "admin"
    assert env_file.read_env()["DASHBOARD_PASSWORD"] == "a-strong-password"


def test_changing_an_existing_password_requires_the_correct_current_one(monkeypatch, tmp_path):
    _isolate_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "old-password")

    response = client.post(
        "/api/settings/dashboard-auth",
        json={"current_password": "wrong-password", "new_username": "admin", "new_password": "new-password-123"},
        auth=("admin", "old-password"),  # valid basic-auth creds - reaches the handler, which then rejects on current_password
    )

    assert response.status_code == 401
    # Nothing should have been written on a rejected change.
    assert env_file.read_env() == {}


def test_changing_an_existing_password_with_the_correct_current_one_succeeds(monkeypatch, tmp_path):
    _isolate_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "old-password")

    response = client.post(
        "/api/settings/dashboard-auth",
        json={"current_password": "old-password", "new_username": "admin", "new_password": "new-password-123"},
        auth=("admin", "old-password"),
    )

    assert response.status_code == 200
    assert env_file.read_env()["DASHBOARD_PASSWORD"] == "new-password-123"
    assert settings.dashboard_password == "new-password-123"  # takes effect immediately, no restart


def test_rejects_a_new_password_shorter_than_eight_characters(monkeypatch, tmp_path):
    _isolate_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")

    response = client.post(
        "/api/settings/dashboard-auth",
        json={"current_password": "", "new_username": "admin", "new_password": "short"},
    )

    assert response.status_code == 400
    assert env_file.read_env() == {}


def test_rejects_a_blank_username(monkeypatch, tmp_path):
    _isolate_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")

    response = client.post(
        "/api/settings/dashboard-auth",
        json={"current_password": "", "new_username": "   ", "new_password": "a-strong-password"},
    )

    assert response.status_code == 400
    assert env_file.read_env() == {}


def test_rejects_a_newline_smuggled_username(monkeypatch, tmp_path):
    """Same config-injection class as /api/settings - write_env()'s own
    protection should already cover this endpoint too."""
    _isolate_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")

    response = client.post(
        "/api/settings/dashboard-auth",
        json={"current_password": "", "new_username": "admin\nALLOW_LIVE_TRADING=true", "new_password": "a-strong-password"},
    )

    assert response.status_code == 400
    assert env_file.read_env() == {}


def test_dashboard_auth_endpoint_itself_requires_basic_auth_once_configured(monkeypatch, tmp_path):
    _isolate_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "current-password")

    # No Authorization header at all - BasicAuthMiddleware should reject
    # this before the route body ever runs, regardless of what's posted.
    response = client.post(
        "/api/settings/dashboard-auth",
        json={"current_password": "current-password", "new_username": "attacker", "new_password": "attacker-password"},
    )

    assert response.status_code == 401
    assert settings.dashboard_username == "admin"  # unchanged
