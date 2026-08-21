import base64
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

# Left public even when auth is on: Railway (and similar hosts) hit this route
# unauthenticated to check the service is alive, and it carries nothing more
# sensitive than the bot's running state, which the dashboard shows anyway.
_UNPROTECTED_PATHS = {"/api/status"}


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Protects the whole app (dashboard + API) with HTTP Basic Auth whenever
    DASHBOARD_USERNAME/DASHBOARD_PASSWORD are configured. A no-op when
    they're blank, so local dev and existing deployments aren't locked out
    by a default credential."""

    async def dispatch(self, request: Request, call_next):
        if not settings.dashboard_username or request.url.path in _UNPROTECTED_PATHS:
            return await call_next(request)

        if self._is_authorized(request.headers.get("authorization")):
            return await call_next(request)

        return Response(
            status_code=401,
            content="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Rolling Papers Bot"'},
        )

    @staticmethod
    def _is_authorized(header: str | None) -> bool:
        if not header or not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except Exception:
            return False
        username, _, password = decoded.partition(":")
        # compare_digest on both (not `and`-short-circuited on a single combined
        # string) so a correct username doesn't leak via response-time differences
        # in the password check.
        username_ok = secrets.compare_digest(username, settings.dashboard_username)
        password_ok = secrets.compare_digest(password, settings.dashboard_password)
        return username_ok and password_ok
