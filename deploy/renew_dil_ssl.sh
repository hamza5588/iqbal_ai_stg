#!/bin/bash
# Renew Let's Encrypt cert for dil.iqbalai.com and reload Docker nginx.
set -euo pipefail
APP=/root/iqbal_ai_stg
certbot renew --webroot -w "$APP/certbot/www" --quiet
cd "$APP"
docker compose exec -T nginx nginx -s reload
