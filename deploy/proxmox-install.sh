#!/usr/bin/env bash
# Rolling Papers Bot - Proxmox VE LXC installer
#
# Run this ON THE PROXMOX HOST as root. It creates a Debian 12 LXC container,
# installs the app inside it as a systemd service, and prints the URL + next
# steps when done. Safe to review before running - nothing here reaches out
# anywhere except your Proxmox storage/template mirror and GitHub for the app
# repo itself.
#
# Usage:
#   bash proxmox-install.sh
# or edit the variables below first if you want non-default sizing/network.

set -euo pipefail

# ---------------------------------------------------------------------------
# Editable settings
# ---------------------------------------------------------------------------
CT_HOSTNAME="rolling-papers-bot"
CT_CORES=2
CT_RAM_MB=1024
CT_SWAP_MB=512
CT_DISK_GB=8
CT_BRIDGE="vmbr0"
CT_STORAGE="local-lvm"       # where the container's rootfs is created
TEMPLATE_STORAGE="local"     # where LXC templates are cached
REPO_URL="https://github.com/hnic29/Rolling-Papers-Stock-Bot.git"
CT_ID="${CT_ID:-}"           # leave blank to auto-pick the next free ID

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
COLOR_RESET='\033[0m'; COLOR_GREEN='\033[1;32m'; COLOR_YELLOW='\033[1;33m'; COLOR_RED='\033[1;31m'; COLOR_BLUE='\033[1;34m'
msg_info()  { echo -e " ${COLOR_BLUE}i${COLOR_RESET} $1"; }
msg_ok()    { echo -e " ${COLOR_GREEN}\xE2\x9C\x93${COLOR_RESET} $1"; }
msg_error() { echo -e " ${COLOR_RED}\xE2\x9C\x97${COLOR_RESET} $1" >&2; }
die() { msg_error "$1"; exit 1; }

echo -e "${COLOR_GREEN}"
echo " Rolling Papers Bot - Proxmox LXC installer"
echo -e "${COLOR_RESET}"

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "Run this as root on the Proxmox host."
command -v pct >/dev/null 2>&1 || die "'pct' not found - this doesn't look like a Proxmox VE host."

# ---------------------------------------------------------------------------
# Resolve container ID
# ---------------------------------------------------------------------------
if [ -z "$CT_ID" ]; then
  CT_ID="$(pvesh get /cluster/nextid)"
fi
pct status "$CT_ID" >/dev/null 2>&1 && die "Container ID $CT_ID already exists - set CT_ID to something else."
msg_ok "Using container ID $CT_ID"

# ---------------------------------------------------------------------------
# Resolve and download a Debian 12 template if needed
# ---------------------------------------------------------------------------
msg_info "Checking for a Debian 12 LXC template..."
pveam update >/dev/null 2>&1 || true
TEMPLATE="$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep '^debian-12-standard' | sort -V | tail -1)"
[ -n "$TEMPLATE" ] || die "Could not find a debian-12-standard template via 'pveam available'. Run 'pveam update' manually and re-run this script."

if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
  msg_info "Downloading $TEMPLATE (this can take a minute)..."
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi
msg_ok "Template ready: $TEMPLATE"

# ---------------------------------------------------------------------------
# Create + start the container
# ---------------------------------------------------------------------------
msg_info "Creating container $CT_ID ($CT_HOSTNAME)..."
pct create "$CT_ID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "$CT_HOSTNAME" \
  --cores "$CT_CORES" \
  --memory "$CT_RAM_MB" \
  --swap "$CT_SWAP_MB" \
  --rootfs "${CT_STORAGE}:${CT_DISK_GB}" \
  --net0 "name=eth0,bridge=${CT_BRIDGE},ip=dhcp" \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1 \
  >/dev/null
msg_ok "Container created"

pct start "$CT_ID"
msg_info "Waiting for network..."
for _ in $(seq 1 30); do
  IP="$(pct exec "$CT_ID" -- hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "${IP:-}" ] && break
  sleep 2
done
[ -n "${IP:-}" ] || msg_error "Container started but no IP detected yet - check 'pct exec $CT_ID -- ip a' once it's up."
msg_ok "Container is up${IP:+ at $IP}"

# ---------------------------------------------------------------------------
# Install the app inside the container
# ---------------------------------------------------------------------------
msg_info "Installing the app inside the container (Python venv + systemd service)..."
pct exec "$CT_ID" -- bash -s -- "$REPO_URL" <<'CTEOF'
set -euo pipefail
REPO_URL="$1"
APP_DIR="/opt/rolling-papers-bot"
STATE_DIR="/var/lib/rolling-papers-bot"
CONF_DIR="/etc/rolling-papers-bot"
ENV_FILE="$CONF_DIR/rolling-papers-bot.env"
SERVICE_USER="rpbot"

apt-get update -qq
apt-get install -y -qq python3 python3-venv git >/dev/null

id "$SERVICE_USER" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --quiet
else
  git clone --quiet "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir --quiet -r requirements.txt

mkdir -p "$STATE_DIR" "$CONF_DIR"

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'ENVEOF'
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_PAPER=true
FMP_API_KEY=
ALLOW_LIVE_TRADING=false
TRADE_LOG_PATH=/var/lib/rolling-papers-bot/trade_log.db
ENVEOF
fi
chmod 600 "$ENV_FILE"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR" "$STATE_DIR" "$CONF_DIR"

cp "$APP_DIR/deploy/rolling-papers-bot.service" /etc/systemd/system/rolling-papers-bot.service
systemctl daemon-reload
systemctl enable --now rolling-papers-bot
CTEOF
msg_ok "App installed and service started"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
msg_ok "Setup complete."
echo -e " ${COLOR_YELLOW}Next steps:${COLOR_RESET}"
echo "   1. Add your real API keys:"
echo "        pct exec $CT_ID -- nano /etc/rolling-papers-bot/rolling-papers-bot.env"
echo "   2. Restart the service to pick them up:"
echo "        pct exec $CT_ID -- systemctl restart rolling-papers-bot"
echo "   3. Open the dashboard:"
echo -e "        ${COLOR_GREEN}http://${IP:-<container-ip>}:8000${COLOR_RESET}"
echo "   4. Logs:"
echo "        pct exec $CT_ID -- journalctl -u rolling-papers-bot -f"
