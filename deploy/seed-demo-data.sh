#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/srv/veritaste
DATE="${1:-2026-04-15}"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

set -a
. /etc/veritaste.env
set +a

say "Seeding into $VERITASTE_DB for $DATE"
cd "$APP_DIR/server"

for loc in 30 9; do
  sudo -u veritaste --preserve-env=VERITASTE_DB \
    "$APP_DIR/.venv/bin/python" seed_waste.py --date "$DATE" --location "$loc" 2>&1 | tail -2
done

say "Result"
sudo -u veritaste sqlite3 "$VERITASTE_DB" \
  "select 'waste observations: ' || count(*) from waste_observation;
   select 'distinct recipes:   ' || count(distinct recipe_id) from waste_observation;" | sed 's/^/  /'

chmod 0640 "$VERITASTE_DB" 2>/dev/null || true
systemctl restart veritaste
sleep 2
curl -fsS --max-time 10 http://127.0.0.1:8000/monitor/health | sed 's/^/  /'
echo
