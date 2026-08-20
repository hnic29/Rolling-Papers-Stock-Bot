#!/usr/bin/env bash
# Rolling Papers Bot - update script
#
# Run this ON THE CONTAINER itself (e.g. `pct enter <id>`, or SSH'd in), as
# root - pulls the latest code, refreshes dependencies, and restarts the
# service. Safe to re-run any time; does nothing destructive to the trade
# log or your .env-equivalent config file.

set -euo pipefail

APP_DIR="/opt/rolling-papers-bot"
SERVICE_USER="rpbot"
SERVICE="rolling-papers-bot"

[ "$(id -u)" -eq 0 ] || { echo "Run this as root." >&2; exit 1; }
[ -d "$APP_DIR/.git" ] || { echo "$APP_DIR isn't set up yet - run the Proxmox installer first." >&2; exit 1; }

echo "Pulling latest code..."
BEFORE="$(git -C "$APP_DIR" rev-parse --short HEAD)"
git -C "$APP_DIR" pull --ff-only
AFTER="$(git -C "$APP_DIR" rev-parse --short HEAD)"

echo "Updating dependencies..."
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
