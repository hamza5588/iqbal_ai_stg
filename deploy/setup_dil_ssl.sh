#!/bin/bash
set -euo pipefail
APP=/root/iqbal_ai_stg
DOMAIN=dil.iqbalai.com
EMAIL=admin@iqbalai.com

mkdir -p "$APP/certbot/www/.well-known/acme-challenge"
echo ok-acme > "$APP/certbot/www/.well-known/acme-challenge/ping"
chmod +x "$APP/renew_dil_ssl.sh"

cd "$APP"
docker compose up -d nginx
sleep 4
docker compose exec -T nginx nginx -t
echo "ACME ping: $(curl -sS -o /tmp/acme.ping -w '%{http_code}' http://127.0.0.1/.well-known/acme-challenge/ping || true)"
cat /tmp/acme.ping || true
echo

export DEBIAN_FRONTEND=noninteractive
if ! command -v certbot >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y certbot
fi

certbot certonly \
  --webroot \
  -w "$APP/certbot/www" \
  -d "$DOMAIN" \
  --non-interactive \
  --agree-tos \
  --email "$EMAIL" \
  --keep-until-expiring

test -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
test -f "/etc/letsencrypt/live/$DOMAIN/privkey.pem"

cp "$APP/nginx.dil.iqbalai.com.conf" "$APP/nginx.conf"
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload

# Daily renew at 03:17 UTC
CRON_LINE="17 3 * * * $APP/renew_dil_ssl.sh >> /var/log/dil-ssl-renew.log 2>&1"
(crontab -l 2>/dev/null | grep -v renew_dil_ssl.sh || true; echo "$CRON_LINE") | crontab -

echo SSL_SETUP_DONE
curl -sS -o /dev/null -w "https_domain:%{http_code} issuer_check_next\n" "https://$DOMAIN/health" || true
echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -subject -issuer -dates | head -20
