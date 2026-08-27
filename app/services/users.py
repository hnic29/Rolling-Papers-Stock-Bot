"""Account storage for dashboard logins. One row per person - the admin who runs the
bot plus anyone they've provisioned an account for. Reuses trade_log's DB (same
pattern as bankroll.py/bot_state.py) rather than a separate file to manage."""

import sqlite3
from datetime import UTC, datetime

import bcrypt

from app.services.trade_log import _connect as _trades_connect


def _connect() -> sqlite3.Connection:
    conn = _trades_connect()  # ensures the `trades` table (and its migrations) exist too
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed/legacy hash should fail closed, not raise past the caller.
        return False


def count_users() -> int:
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    conn.close()
    return row[0]


def create_user(username: str, password: str, is_admin: bool = False) -> dict:
    conn = _connect()
    try:
        with conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                (username, hash_password(password), int(is_admin), datetime.now(UTC).isoformat()),
            )
    except sqlite3.IntegrityError as exc:
        conn.close()
        raise ValueError(f"Username '{username}' is already taken.") from exc
    user_id = cursor.lastrowid
    conn.close()
    return get_user_by_id(user_id)


def get_user_by_id(user_id: int) -> dict | None:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def verify_password(username: str, password: str) -> dict | None:
    """Returns the user row on a correct username/password, else None. Always runs
    the bcrypt check even for an unknown username (against a dummy hash) so a
    nonexistent-username response doesn't return measurably faster than a
    wrong-password one."""
    user = get_user_by_username(username)
    reference_hash = user["password_hash"] if user else _UNKNOWN_USER_DUMMY_HASH
    if not _check_password(password, reference_hash):
        return None
    return user


# A real bcrypt hash of an unguessable placeholder, spent solely so verify_password
# has something to check a password against when the username doesn't exist.
_UNKNOWN_USER_DUMMY_HASH = hash_password(f"no-such-user-{id(object())}")


def list_users() -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_password(user_id: int, new_password: str) -> None:
    conn = _connect()
    with conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user_id))
    conn.close()


def migrate_legacy_dashboard_credentials() -> None:
    """One-time upgrade path: a deployment that already had DASHBOARD_USERNAME/
    DASHBOARD_PASSWORD (the old single shared Basic Auth login) set in its .env gets
    an equivalent admin account created automatically, so the person already running
    the bot isn't locked out or forced through first-run setup again. A no-op once
    any user row exists, and a no-op for a fresh install that never had Basic Auth
    configured (those go through the normal /api/bootstrap first-run screen)."""
    from app.config import settings  # deferred: avoids a circular import at module load

    if count_users() > 0:
        return
    if not settings.dashboard_username or not settings.dashboard_password:
        return
    create_user(settings.dashboard_username, settings.dashboard_password, is_admin=True)
