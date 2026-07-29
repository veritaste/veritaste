#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/srv/veritaste

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "Ownership and stale bytecode"
chown -R veritaste:veritaste "$APP_DIR/web" "$APP_DIR/server"
find "$APP_DIR" -name '*.pyc' -delete
find "$APP_DIR" -name __pycache__ -type d -empty -delete
echo "  done"

say "Pruning orphaned cache rows"
. /etc/veritaste.env
BEFORE=$(sudo -u veritaste sqlite3 "$VERITASTE_DB" 'select count(*) from upstream_cache;')
sudo -u veritaste sqlite3 "$VERITASTE_DB" \
  "delete from upstream_cache where key glob 'menus:*:*:*';"
AFTER=$(sudo -u veritaste sqlite3 "$VERITASTE_DB" 'select count(*) from upstream_cache;')
echo "  cache rows: $BEFORE -> $AFTER"

say "Restarting"
systemctl restart veritaste
sleep 4
systemctl is-active veritaste | sed 's/^/  service: /'
curl -fsS --max-time 10 http://127.0.0.1:8000/monitor/health | sed 's/^/  /'
echo
