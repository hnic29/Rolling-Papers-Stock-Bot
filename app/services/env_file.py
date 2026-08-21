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


def write_env(updates: dict[str, str]) -> None:
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
