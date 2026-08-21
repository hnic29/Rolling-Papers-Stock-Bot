import os
from pathlib import Path


# CONFIG_ENV_PATH lets a deployment point this at a persistent location
# outside the app checkout (see deploy/rolling-papers-bot.service) so a
# `git pull` during an update never touches it, and so it's the same file
# app.config's Settings reads on startup - see the comment there for why
# that consistency matters.
ENV_PATH = Path(os.environ.get("CONFIG_ENV_PATH", ".env"))


def read_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    values: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


class InvalidEnvValue(ValueError):
    """A key or value can't be written to the env file safely."""


def write_env(updates: dict[str, str]) -> None:
    # Each line becomes its own KEY=value entry on the next read, and every
    # value here (API keys, etc.) ultimately comes from a request body -
    # without this check, a value containing a newline (e.g.
    # "abc\nALLOW_LIVE_TRADING=true") would inject an entirely separate,
    # attacker-chosen config line - including ones like ALLOW_LIVE_TRADING or
    # DASHBOARD_USERNAME/PASSWORD that this endpoint was never meant to let
    # a caller set. Reject rather than silently strip, so a caller finds out
    # immediately rather than having their key silently truncated/mangled.
    for key, value in updates.items():
        if "\n" in key or "\r" in key or "\n" in value or "\r" in value:
            raise InvalidEnvValue(f"value for {key!r} can't contain a newline")

    values = read_env()
    values.update(updates)
    lines = [f"{key}={value}" for key, value in values.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
