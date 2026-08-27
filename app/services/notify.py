"""Push notifications via ntfy (https://ntfy.sh or a self-hosted instance).

Fire-and-forget by design: a notification is never worth failing a trade, an exit, or
an automation cycle over, so send() swallows every error. Disabled entirely until a
topic is configured (each person's own Settings page - ntfy_topic is a per-user
value, app.services.credentials, precisely so one person's phone doesn't get
another's trade alerts). The server URL stays deployment-wide (NTFY_SERVER) -
switching to a self-hosted ntfy means changing that one setting, nothing else.
"""

import requests

from app.config import settings

_TIMEOUT_SECONDS = 5


def enabled(topic: str) -> bool:
    return bool(topic)


def send(topic: str, title: str, message: str, priority: str = "default", tags: str = "") -> bool:
    """Returns True only if the push was accepted — callers may ignore it."""
    if not enabled(topic):
        return False
    url = f"{settings.ntfy_server.rstrip('/')}/{topic}"
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    try:
        response = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=_TIMEOUT_SECONDS)
        return response.ok
    except Exception:
        return False
