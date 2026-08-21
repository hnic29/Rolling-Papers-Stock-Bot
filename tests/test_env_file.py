import pytest

from app.services.env_file import InvalidEnvValue, read_env, write_env
from app.services import env_file


def test_write_env_rejects_newline_in_value(monkeypatch, tmp_path):
    fake_env = tmp_path / ".env"
    fake_env.write_text("EXISTING=untouched\n", encoding="utf-8")
    monkeypatch.setattr(env_file, "ENV_PATH", fake_env)

    with pytest.raises(InvalidEnvValue):
        write_env({"ALPACA_API_KEY": "abc\nALLOW_LIVE_TRADING=true"})

    # Rejected before any write happened - the file is untouched, not
    # partially written with the malicious content.
    assert fake_env.read_text(encoding="utf-8") == "EXISTING=untouched\n"


def test_write_env_rejects_carriage_return_in_value(monkeypatch, tmp_path):
    fake_env = tmp_path / ".env"
    fake_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_file, "ENV_PATH", fake_env)

    with pytest.raises(InvalidEnvValue):
        write_env({"ALPACA_API_KEY": "abc\rDASHBOARD_USERNAME=attacker"})


def test_write_env_rejects_newline_in_key(monkeypatch, tmp_path):
    fake_env = tmp_path / ".env"
    fake_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_file, "ENV_PATH", fake_env)

    with pytest.raises(InvalidEnvValue):
        write_env({"ALPACA_API_KEY\nALLOW_LIVE_TRADING": "true"})


def test_write_env_accepts_ordinary_values(monkeypatch, tmp_path):
    fake_env = tmp_path / ".env"
    fake_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_file, "ENV_PATH", fake_env)

    write_env({"ALPACA_API_KEY": "a-normal-looking-key-123"})

    assert read_env()["ALPACA_API_KEY"] == "a-normal-looking-key-123"
