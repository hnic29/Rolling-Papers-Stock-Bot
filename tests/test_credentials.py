from app.services import credentials


def test_get_credentials_returns_sensible_defaults_for_a_new_user():
    creds = credentials.get_credentials(user_id=1)

    assert creds["alpaca_api_key"] == ""
    assert creds["alpaca_secret_key"] == ""
    assert creds["alpaca_paper"] is True
    assert creds["allow_live_trading"] is False
    assert creds["risk_per_trade_pct"] == 2.0


def test_save_and_get_round_trips_through_encryption():
    credentials.save_credentials(user_id=1, alpaca_api_key="PKTESTKEY123", alpaca_secret_key="secretvalue456")

    creds = credentials.get_credentials(user_id=1)

    assert creds["alpaca_api_key"] == "PKTESTKEY123"
    assert creds["alpaca_secret_key"] == "secretvalue456"


def test_secrets_are_actually_encrypted_at_rest_not_plaintext():
    credentials.save_credentials(user_id=1, alpaca_api_key="PKTESTKEY123", alpaca_secret_key="secretvalue456")

    conn = credentials._connect()
    row = conn.execute("SELECT alpaca_api_key_encrypted FROM user_credentials WHERE user_id = 1").fetchone()
    conn.close()

    assert row[0] is not None
    assert "PKTESTKEY123" not in row[0]


def test_save_credentials_is_a_partial_update():
    credentials.save_credentials(user_id=1, alpaca_api_key="PKTESTKEY123", alpaca_secret_key="secretvalue456")

    credentials.save_credentials(user_id=1, risk_per_trade_pct=3.5)

    creds = credentials.get_credentials(user_id=1)
    assert creds["risk_per_trade_pct"] == 3.5
    assert creds["alpaca_api_key"] == "PKTESTKEY123"  # untouched by the second save


def test_save_credentials_rejects_an_unknown_field():
    try:
        credentials.save_credentials(user_id=1, not_a_real_field="oops")
        assert False, "expected a ValueError"
    except ValueError as exc:
        assert "not_a_real_field" in str(exc)


def test_has_credentials_reflects_whether_alpaca_keys_are_set():
    assert credentials.has_credentials(user_id=1) is False

    credentials.save_credentials(user_id=1, alpaca_api_key="PKTESTKEY123", alpaca_secret_key="secretvalue456")

    assert credentials.has_credentials(user_id=1) is True


def test_credentials_for_different_users_are_fully_isolated():
    credentials.save_credentials(user_id=1, alpaca_api_key="alice-key", alpaca_secret_key="alice-secret")
    credentials.save_credentials(user_id=2, alpaca_api_key="bob-key", alpaca_secret_key="bob-secret")

    assert credentials.get_credentials(1)["alpaca_api_key"] == "alice-key"
    assert credentials.get_credentials(2)["alpaca_api_key"] == "bob-key"


def test_a_value_encrypted_under_a_rotated_key_decrypts_to_empty_rather_than_raising(monkeypatch):
    from app.config import settings

    credentials.save_credentials(user_id=1, alpaca_api_key="PKTESTKEY123", alpaca_secret_key="secretvalue456")

    # Simulate the master key having changed since this row was written.
    monkeypatch.setattr(settings, "credentials_encryption_key", "")
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "credentials_encryption_key", Fernet.generate_key().decode("utf-8"))

    creds = credentials.get_credentials(user_id=1)
    assert creds["alpaca_api_key"] == ""
