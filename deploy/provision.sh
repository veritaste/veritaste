#!/usr/bin/env bash
set -euo pipefail

APP_USER=veritaste
APP_DIR=/srv/veritaste
DOMAIN=veritaste.org

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "System identity"
. /etc/os-release && echo "$PRETTY_NAME"
echo "kernel $(uname -r)  arch $(uname -m)"

say "Swap"
if swapon --show | grep -q '/swapfile'; then
  echo "swapfile already active"
else
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "1G swapfile created and enabled"
fi
sysctl -q -w vm.swappiness=10
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
free -m | head -3

say "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  nginx python3 python3-venv python3-pip \
  certbot python3-certbot-nginx \
  ufw curl ca-certificates sqlite3 >/dev/null
echo "nginx    $(nginx -v 2>&1 | sed 's|.*/||')"
echo "python   $(python3 --version)"
echo "certbot  $(certbot --version 2>&1)"

say "Service account and directories"
if id "$APP_USER" >/dev/null 2>&1; then
  echo "user $APP_USER exists"
else
  adduser --system --group --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
  echo "created system user $APP_USER"
fi
mkdir -p "$APP_DIR"/{server,web}
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

chmod 0750 "$APP_DIR"
usermod -aG "$APP_USER" www-data
systemctl restart nginx 2>/dev/null || true
namei -m "$APP_DIR/web" | sed 's/^/  /'

say "Firewall"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null
ufw status numbered | sed 's/^/  /'

say "Done. Next: upload the application, then run setup-app.sh"
