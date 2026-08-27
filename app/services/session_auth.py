"""Replaces basic_auth.py's single shared Basic Auth credential with per-person,
signed session cookies - Basic Auth has no way to represent "which of several
people is this," which multi-user support needs. A no-op only in the sense that an
unauthenticated request always gets a 401 now (there's no "auth is off" mode
anymore); the dashboard itself handles a 401 by showing a login/setup screen
instead of the rest of the app."""

import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import reload_settings, settings
from app.services import users
from app.services.env_file import InvalidEnvValue, write_env

SESSION_COOKIE_NAME = "session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Left reachable without a session: static assets and the index page so the
# login/setup screen itself can load, plus the handful of API routes the
# login flow needs before a session cookie exists. Everything else under
# /api requires a valid session.
_PUBLIC_PATHS = {"/", "/api/status", "/api/auth/status", "/api/login", "/api/bootstrap", "/api/logout"}
_PUBLIC_PREFIXES = ("/static/",)


def _ensure_session_secret() -> str:
    """Lazily generates and persists the signing secret on first use - same
    pattern as every other credential in this app (see config.py), so a fresh
    install doesn't need a manual setup step, but the secret still survives a
    restart instead of invalidating every session on every deploy."""
    if settings.session_secret:
        return settings.session_secret
    secret = secrets.token_urlsafe(32)
    try:
        write_env({"SESSION_SECRET": secret})
    except InvalidEnvValue:
        pass  # extremely unlikely (token_urlsafe never contains a newline) - fall through
    reload_settings()
    return settings.session_secret or secret


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_ensure_session_secret(), salt="rolling-papers-bot-session")


def create_session_token(user_id: int) -> str:
    return _serializer().dumps({"user_id": user_id})


def verify_session_token(token: str) -> int | None:
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("user_id")


class SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE_NAME)
        user_id = verify_session_token(token) if token else None
        user = users.get_user_by_id(user_id) if user_id is not None else None
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "Login required"})

        request.state.user_id = user["id"]
        request.state.username = user["username"]
        request.state.is_admin = bool(user["is_admin"])
        return await call_next(request)
