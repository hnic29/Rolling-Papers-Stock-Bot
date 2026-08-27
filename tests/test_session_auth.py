from fastapi.testclient import TestClient

from app.main import app
from app.services import users


def _client() -> TestClient:
    # A fresh TestClient per test - its cookie jar must never leak a session from
    # one test into the next.
    return TestClient(app)


def test_needs_bootstrap_is_true_before_any_user_exists():
    response = _client().get("/api/auth/status")

    assert response.status_code == 200
    assert response.json() == {"needs_bootstrap": True}


def test_needs_bootstrap_is_false_once_a_user_exists():
    users.create_user("alice", "correct-horse-battery")

    response = _client().get("/api/auth/status")

    assert response.json() == {"needs_bootstrap": False}


def test_protected_route_rejects_a_request_with_no_session():
    response = _client().get("/api/positions")

    assert response.status_code == 401


def test_status_and_index_stay_public_with_no_session():
    client = _client()

    assert client.get("/api/status").status_code == 200
    assert client.get("/").status_code == 200


def test_bootstrap_creates_the_first_admin_and_logs_them_in():
    client = _client()

    response = client.post("/api/bootstrap", json={"username": "alice", "password": "correct-horse-battery"})

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["is_admin"] is True

    # The session cookie from bootstrap should already be authenticated.
    me_response = client.get("/api/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "alice"


def test_bootstrap_refuses_once_a_user_already_exists():
    users.create_user("alice", "correct-horse-battery")

    response = _client().post("/api/bootstrap", json={"username": "bob", "password": "another-password-123"})

    assert response.status_code == 403


def test_bootstrap_rejects_a_short_password():
    response = _client().post("/api/bootstrap", json={"username": "alice", "password": "short"})

    assert response.status_code == 400
    assert users.count_users() == 0


def test_login_succeeds_with_correct_credentials_and_sets_a_working_session():
    users.create_user("alice", "correct-horse-battery")
    client = _client()

    response = client.post("/api/login", json={"username": "alice", "password": "correct-horse-battery"})

    assert response.status_code == 200
    assert client.get("/api/me").json()["username"] == "alice"


def test_login_rejects_wrong_password():
    users.create_user("alice", "correct-horse-battery")

    response = _client().post("/api/login", json={"username": "alice", "password": "wrong-password"})

    assert response.status_code == 401


def test_login_rejects_unknown_username():
    response = _client().post("/api/login", json={"username": "nobody", "password": "whatever-123"})

    assert response.status_code == 401


def test_logout_clears_the_session_so_protected_routes_401_again():
    users.create_user("alice", "correct-horse-battery")
    client = _client()
    client.post("/api/login", json={"username": "alice", "password": "correct-horse-battery"})
    assert client.get("/api/me").status_code == 200

    client.post("/api/logout")

    assert client.get("/api/me").status_code == 401


def test_tampered_session_cookie_is_rejected():
    users.create_user("alice", "correct-horse-battery")
    client = _client()
    client.post("/api/login", json={"username": "alice", "password": "correct-horse-battery"})

    client.cookies.set("session", client.cookies.get("session") + "tampered")

    assert client.get("/api/me").status_code == 401


def test_change_my_password_requires_the_correct_current_password():
    users.create_user("alice", "old-password-123")
    client = _client()
    client.post("/api/login", json={"username": "alice", "password": "old-password-123"})

    response = client.post(
        "/api/me/password",
        json={"current_password": "wrong-password", "new_password": "new-password-456"},
    )

    assert response.status_code == 401


def test_change_my_password_succeeds_and_new_password_works_next_login():
    users.create_user("alice", "old-password-123")
    client = _client()
    client.post("/api/login", json={"username": "alice", "password": "old-password-123"})

    response = client.post(
        "/api/me/password",
        json={"current_password": "old-password-123", "new_password": "new-password-456"},
    )
    assert response.status_code == 200

    fresh_client = _client()
    login_response = fresh_client.post("/api/login", json={"username": "alice", "password": "new-password-456"})
    assert login_response.status_code == 200


def test_one_users_session_cannot_see_another_users_identity():
    users.create_user("alice", "alice-password-123")
    users.create_user("bob", "bob-password-123")
    alice_client = _client()
    bob_client = _client()

    alice_client.post("/api/login", json={"username": "alice", "password": "alice-password-123"})
    bob_client.post("/api/login", json={"username": "bob", "password": "bob-password-123"})

    assert alice_client.get("/api/me").json()["username"] == "alice"
    assert bob_client.get("/api/me").json()["username"] == "bob"
