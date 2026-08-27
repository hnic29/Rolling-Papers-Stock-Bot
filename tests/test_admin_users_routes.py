from fastapi.testclient import TestClient

from app.main import app
from app.services import users as users_service

client = TestClient(app)


def _login(username: str, password: str = "correct-horse-battery-123") -> TestClient:
    session_client = TestClient(app)
    response = session_client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return session_client


def _bootstrap_admin(_isolated_trade_log_db) -> TestClient:
    admin_client = TestClient(app)
    response = admin_client.post("/api/bootstrap", json={"username": "admin", "password": "correct-horse-battery-123"})
    assert response.status_code == 200
    return admin_client


def test_admin_can_list_users(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)

    response = admin.get("/api/users")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["username"] == "admin"
    assert body[0]["is_admin"] is True
    assert "password_hash" not in body[0]


def test_non_admin_cannot_list_users(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)
    admin.post("/api/users", json={"username": "alice", "password": "alice-password-123"})
    alice = _login("alice", "alice-password-123")

    response = alice.get("/api/users")

    assert response.status_code == 403


def test_admin_can_create_a_new_user(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)

    response = admin.post("/api/users", json={"username": "alice", "password": "alice-password-123"})

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["is_admin"] is False

    # The new account can immediately log in with the password the admin set.
    alice = _login("alice", "alice-password-123")
    assert alice.get("/api/me").json()["username"] == "alice"


def test_admin_can_create_another_admin():
    users_service.create_user("owner", "owner-password-123", is_admin=True)
    owner = _login("owner", "owner-password-123")

    response = owner.post("/api/users", json={"username": "co-admin", "password": "co-admin-password-1", "is_admin": True})

    assert response.status_code == 200
    assert response.json()["is_admin"] is True


def test_non_admin_cannot_create_a_user(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)
    admin.post("/api/users", json={"username": "alice", "password": "alice-password-123"})
    alice = _login("alice", "alice-password-123")

    response = alice.post("/api/users", json={"username": "mallory", "password": "mallory-password-1"})

    assert response.status_code == 403
    assert users_service.get_user_by_username("mallory") is None


def test_create_user_rejects_a_duplicate_username(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)
    admin.post("/api/users", json={"username": "alice", "password": "alice-password-123"})

    response = admin.post("/api/users", json={"username": "alice", "password": "another-password-1"})

    assert response.status_code == 400


def test_create_user_rejects_a_short_password(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)

    response = admin.post("/api/users", json={"username": "alice", "password": "short"})

    assert response.status_code == 400
    assert users_service.get_user_by_username("alice") is None


def test_admin_can_reset_another_users_password(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)
    create_response = admin.post("/api/users", json={"username": "alice", "password": "alice-old-password-1"})
    alice_id = create_response.json()["id"]

    response = admin.post(f"/api/users/{alice_id}/reset-password", json={"new_password": "alice-new-password-1"})

    assert response.status_code == 200
    assert users_service.verify_password("alice", "alice-old-password-1") is None
    assert users_service.verify_password("alice", "alice-new-password-1") is not None


def test_non_admin_cannot_reset_someone_elses_password(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)
    create_response = admin.post("/api/users", json={"username": "alice", "password": "alice-password-123"})
    alice_id = create_response.json()["id"]
    admin.post("/api/users", json={"username": "bob", "password": "bob-password-123"})
    bob = _login("bob", "bob-password-123")

    response = bob.post(f"/api/users/{alice_id}/reset-password", json={"new_password": "hacked-password-1"})

    assert response.status_code == 403
    assert users_service.verify_password("alice", "alice-password-123") is not None


def test_reset_password_404s_for_an_unknown_user(_isolated_trade_log_db):
    admin = _bootstrap_admin(_isolated_trade_log_db)

    response = admin.post("/api/users/9999/reset-password", json={"new_password": "whatever-password-1"})

    assert response.status_code == 404
