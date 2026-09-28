#!/bin/sh
set -e

echo "=== Initializing database ==="
python scripts/init_db.py

echo "=== Running Alembic migrations ==="
alembic upgrade head

echo "=== Starting application ==="
# --proxy-headers : lit X-Forwarded-Proto (https) derrière le proxy Railway, pour
#   que les redirections générées restent en https.
# ⚠️ Avec --forwarded-allow-ips="*", uvicorn remplace request.client.host par la
#   1re IP de X-Forwarded-For — ÉCRITE PAR LE CLIENT, donc falsifiable. Ne JAMAIS
#   utiliser request.client.host pour la sécurité : passer par
#   app/core/client_ip.ip_client() (IP ajoutée par le proxy Railway).
# --no-server-header : ne pas annoncer « server: uvicorn » (empreinte techno).
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*" --no-server-header
