import pytest

from app.services import users


def test_create_user_hashes_the_password_not_stored_in_plaintext():
    user = users.create_user("alice", "correct-horse-battery")

    assert user["username"] == "alice"
    assert user["password_hash"] != "correct-horse-battery"
    assert user["is_admin"] == 0


def test_create_user_defaults_to_non_admin_and_bootstrap_sets_admin():
    non_admin = users.create_user("alice", "correct-horse-battery")
    admin = users.create_user("bob", "correct-horse-battery", is_admin=True)

    assert non_admin["is_admin"] == 0
    assert admin["is_admin"] == 1


def test_create_user_rejects_a_duplicate_username():
    users.create_user("alice", "correct-horse-battery")

    with pytest.raises(ValueError, match="already taken"):
        users.create_user("alice", "another-password")


def test_verify_password_accepts_the_correct_password():
    users.create_user("alice", "correct-horse-battery")

    user = users.verify_password("alice", "correct-horse-battery")

    assert user is not None
    assert user["username"] == "alice"


def test_verify_password_rejects_the_wrong_password():
    users.create_user("alice", "correct-horse-battery")

    assert users.verify_password("alice", "wrong-password") is None


def test_verify_password_rejects_an_unknown_username():
    assert users.verify_password("nobody", "whatever") is None


def test_count_users_reflects_created_accounts():
    assert users.count_users() == 0

    users.create_user("alice", "correct-horse-battery")

    assert users.count_users() == 1


def test_get_user_by_username_and_by_id_agree():
    created = users.create_user("alice", "correct-horse-battery")

    by_username = users.get_user_by_username("alice")
    by_id = users.get_user_by_id(created["id"])

    assert by_username == by_id


def test_get_user_by_id_returns_none_for_a_missing_user():
    assert users.get_user_by_id(9999) is None


def test_list_users_is_ordered_by_creation():
    users.create_user("alice", "correct-horse-battery")
    users.create_user("bob", "correct-horse-battery")

    listed = users.list_users()

    assert [u["username"] for u in listed] == ["alice", "bob"]
    assert "password_hash" not in listed[0]  # list view never leaks hashes


def test_set_password_changes_which_password_verifies():
    user = users.create_user("alice", "old-password-123")

    users.set_password(user["id"], "new-password-456")

    assert users.verify_password("alice", "old-password-123") is None
    assert users.verify_password("alice", "new-password-456") is not None


def test_migrate_legacy_dashboard_credentials_creates_an_admin_from_the_env_pair(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dashboard_username", "legacy-admin")
    monkeypatch.setattr(settings, "dashboard_password", "legacy-password-123")

    users.migrate_legacy_dashboard_credentials()

    migrated = users.verify_password("legacy-admin", "legacy-password-123")
    assert migrated is not None
    assert migrated["is_admin"] == 1


def test_migrate_legacy_dashboard_credentials_is_a_noop_without_legacy_credentials(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")

    users.migrate_legacy_dashboard_credentials()

    assert users.count_users() == 0


def test_migrate_legacy_dashboard_credentials_is_a_noop_once_a_user_already_exists(monkeypatch):
    from app.config import settings

    users.create_user("someone-else", "already-set-up-123")
    monkeypatch.setattr(settings, "dashboard_username", "legacy-admin")
    monkeypatch.setattr(settings, "dashboard_password", "legacy-password-123")

    users.migrate_legacy_dashboard_credentials()

    assert users.count_users() == 1
    assert users.get_user_by_username("legacy-admin") is None
