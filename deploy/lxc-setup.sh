#!/usr/bin/env bash
# Run this INSIDE a Debian/Ubuntu Proxmox LXC container (e.g. `pct enter <id>`,
# or `pct exec <id> -- bash /path/to/this/script`), as root.
#
# Re-running it is safe: it pulls the latest code if the app dir already
# exists, and won't clobber your .env or existing trade log.
set -euo pipefail

REPO_URL="https://github.com/hnic29/Rolling-Papers-Stock-Bot.git"
APP_DIR="/opt/rolling-papers-bot"
STATE_DIR="/var/lib/rolling-papers-bot"
CONF_DIR="/etc/rolling-papers-bot"
ENV_FILE="$CONF_DIR/rolling-papers-bot.env"
SERVICE_USER="rpbot"

apt-get update
apt-get install -y python3 python3-venv git

if ! id "$SERVICE_USER" &>/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull
else
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir -r requirements.txt

mkdir -p "$STATE_DIR" "$CONF_DIR"

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'ENVEOF'
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_PAPER=true
FMP_API_KEY=
ALLOW_LIVE_TRADING=false
TRADE_LOG_PATH=/var/lib/rolling-papers-bot/trade_log.db
# Optional: set both to put the dashboard behind HTTP Basic Auth. Left blank,
# it's open to anyone who can reach the URL.
DASHBOARD_USERNAME=
DASHBOARD_PASSWORD=
ENVEOF
  echo ">>> Wrote $ENV_FILE — fill in your API keys before starting the service."
fi
chmod 600 "$ENV_FILE"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR" "$STATE_DIR" "$CONF_DIR"

cp "$APP_DIR/deploy/rolling-papers-bot.service" /etc/systemd/system/rolling-papers-bot.service
systemctl daemon-reload
systemctl enable rolling-papers-bot

echo ">>> Setup complete."
echo ">>> Edit $ENV_FILE with real API keys, then run: systemctl start rolling-papers-bot"
echo ">>> Check status with: systemctl status rolling-papers-bot"
echo ">>> Tail logs with: journalctl -u rolling-papers-bot -f"
