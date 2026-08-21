#!/usr/bin/env bash
# Rolling Papers Bot - update script
#
# Run this ON THE CONTAINER itself (e.g. `pct enter <id>`, or SSH'd in), as
# root - pulls the latest code, refreshes dependencies, and restarts the
# service. Safe to re-run any time; does nothing destructive to the trade
# log or your .env-equivalent config file.

set -euo pipefail

SCRIPT_URL="https://raw.githubusercontent.com/hnic29/Rolling-Papers-Stock-Bot/main/deploy/update.sh"
APP_DIR="/opt/rolling-papers-bot"
SERVICE_USER="rpbot"
SERVICE="rolling-papers-bot"

# Self-elevate if not already root - re-runs the local file (preserving any
# edits) if invoked as one, or re-fetches the canonical script if invoked via
# `bash -c "$(curl ...)"` (no local file to re-exec in that case).
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || { echo "This needs root, and 'sudo' isn't installed - re-run as root manually." >&2; exit 1; }
  if [ -f "$0" ]; then
    exec sudo -E bash "$0" "$@"
  else
    exec sudo -E bash -c "$(curl -fsSL "$SCRIPT_URL")"
  fi
fi
[ -d "$APP_DIR/.git" ] || { echo "$APP_DIR isn't set up yet - run the Proxmox installer first." >&2; exit 1; }

echo "Refreshing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3-dev build-essential ca-certificates curl >/dev/null

echo "Pulling latest code..."
BEFORE="$(git -C "$APP_DIR" rev-parse --short HEAD)"
git -C "$APP_DIR" pull --ff-only
AFTER="$(git -C "$APP_DIR" rev-parse --short HEAD)"

echo "Updating dependencies..."
"$APP_DIR/.venv/bin/pip" install --no-cache-dir --quiet --upgrade pip setuptools wheel
"$APP_DIR/.venv/bin/pip" install --no-cache-dir --quiet -r "$APP_DIR/requirements.txt"

chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

echo "Restarting service..."
systemctl restart "$SERVICE"
sleep 1
systemctl --no-pager --lines=0 status "$SERVICE"

if [ "$BEFORE" = "$AFTER" ]; then
  echo "Already up to date ($AFTER) - service restarted anyway."
else
  echo "Updated $BEFORE -> $AFTER and restarted."
fi
