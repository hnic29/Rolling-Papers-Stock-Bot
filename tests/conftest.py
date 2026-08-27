import pytest

from app.config import settings
from app.services import trade_log


@pytest.fixture(autouse=True)
def _isolated_trade_log_db(tmp_path, monkeypatch):
    """Every test runs against a fresh throwaway SQLite file instead of the real
    data/trade_log.db. Without this, route-level tests that don't explicitly reach
    for tmp_path (most of test_routes.py/test_bankroll_routes.py, and now anything
    touching the new users table) would read and write the actual production
    database just by running the test suite - harmless for most tables, but the
    users table makes that a real risk (a stray bootstrap-admin account with a
    test password landing in production). A test that needs a specific path or a
    shared db across calls can still monkeypatch trade_log.DB_PATH itself
    afterward; that assignment simply overrides this one."""
    monkeypatch.setattr(trade_log, "DB_PATH", tmp_path / "trade_log.db")


@pytest.fixture(autouse=True)
def _fixed_session_secret(monkeypatch):
    """app.services.session_auth lazily generates and persists a signing secret to
    the real .env file the first time a session cookie is issued. Pinning a fixed
    value here makes that a no-op during tests, so the suite never touches the
    real .env or leaks a generated secret across runs."""
    monkeypatch.setattr(settings, "session_secret", "test-session-secret-not-for-production-use")
