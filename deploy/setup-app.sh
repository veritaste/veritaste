#!/usr/bin/env bash
set -euo pipefail

APP_USER=veritaste
APP_DIR=/srv/veritaste

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "Python environment"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/server/requirements.txt"
"$APP_DIR/.venv/bin/pip" list --format=freeze | grep -Ei '^(flask|apiflask|marshmallow|gunicorn|httpx)=' | sed 's/^/  /'

say "Secrets"
ENV_FILE=/etc/veritaste.env
if [ ! -f "$ENV_FILE" ]; then
  umask 077
  {
    echo "VERITASTE_SECRET=$(head -c 32 /dev/urandom | base64)"
    echo "VERITASTE_DB=$APP_DIR/veritaste.db"
    echo "VERITASTE_WEB=$APP_DIR/web"
  } > "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  echo "created $ENV_FILE"
else
  echo "$ENV_FILE already present, left alone"
fi

say "systemd unit"
cat > /etc/systemd/system/veritaste.service <<'UNIT'
[Unit]
Description=Veritaste API (ENSC S-106)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=veritaste
Group=veritaste
WorkingDirectory=/srv/veritaste/server
EnvironmentFile=/etc/veritaste.env
ExecStart=/srv/veritaste/.venv/bin/gunicorn \
    --workers 2 \
    --timeout 30 \
    --graceful-timeout 30 \
    --bind 127.0.0.1:8000 \
    --access-logfile - \
    --error-logfile - \
    wsgi:app
Restart=always
RestartSec=3

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/veritaste
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now veritaste >/dev/null
sleep 3
systemctl is-active veritaste | sed 's/^/  service: /'

say "Local health check"
curl -fsS --max-time 10 http://127.0.0.1:8000/monitor/health | sed 's/^/  /' || {
  echo "  FAILED — recent logs:"; journalctl -u veritaste -n 30 --no-pager | sed 's/^/    /'; exit 1; }
echo
