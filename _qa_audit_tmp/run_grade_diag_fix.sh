#!/bin/bash
set -e
cd /opt/iqbal_ai_stg
CID=$(docker compose ps -q flask_app1)
docker cp /tmp/diag_fix/. "$CID":/tmp/
docker compose exec -T flask_app1 bash -lc 'cd /tmp; python test_grade_diag_fix.py'
