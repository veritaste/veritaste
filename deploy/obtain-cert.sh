#!/usr/bin/env bash
set -euo pipefail

DOMAIN=veritaste.org
EMAIL="${1:?usage: obtain-cert.sh you@example.com}"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "Confirming both names resolve to this droplet"
MY_IP=$(curl -fsS --max-time 10 https://api.ipify.org)
echo "  droplet public IP : $MY_IP"
ok=true
for host in "$DOMAIN" "www.$DOMAIN"; do
  got=$(getent ahostsv4 "$host" | awk '{print $1; exit}' || true)
  echo "  $host -> ${got:-<unresolved>}"
  [ "$got" = "$MY_IP" ] || ok=false
done
if [ "$ok" != true ]; then
  echo
  echo "  DNS does not yet point here for every name. Certificate issuance"
  echo "  would fail. Wait for propagation and re-run."
  exit 1
fi

say "Requesting certificate"
mkdir -p /var/www/certbot
certbot certonly \
  --webroot -w /var/www/certbot \
  -d "$DOMAIN" -d "www.$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos --no-eff-email \
  --non-interactive --keep-until-expiring

say "Switching nginx to the TLS configuration"
cp /srv/veritaste/deploy/nginx/veritaste.conf /etc/nginx/sites-available/veritaste
ln -sfn /etc/nginx/sites-available/veritaste /etc/nginx/sites-enabled/veritaste
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

say "Renewal"
systemctl list-timers certbot.timer --no-pager | sed -n '1,3p' | sed 's/^/  /'
certbot renew --dry-run 2>&1 | tail -3 | sed 's/^/  /'

say "Live check"
curl -fsS --max-time 15 "https://$DOMAIN/monitor/health" | sed 's/^/  apex: /'; echo
curl -fsS --max-time 15 -o /dev/null -w '  www : HTTP %{http_code} -> %{redirect_url}\n' "https://www.$DOMAIN/"
