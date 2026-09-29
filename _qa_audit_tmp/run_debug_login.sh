#!/bin/bash
set -e
cd /opt/iqbal_ai_stg
CID=$(docker compose ps -q flask_app1)
docker cp /tmp/diag_fix/debug_login.py "$CID":/tmp/debug_login.py
docker compose exec -T flask_app1 python /tmp/debug_login.py
