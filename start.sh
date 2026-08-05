#!/bin/sh
set -e

echo "=== Initializing database ==="
python scripts/init_db.py

echo "=== Running Alembic migrations ==="
alembic upgrade head

echo "=== Starting application ==="
# --proxy-headers : derrière le proxy Railway, fait lire la vraie IP client
# (X-Forwarded-For) au lieu de l'IP du proxy → rate-limit par client, pas global.
# forwarded-allow-ips=* est sûr ici : l'app n'est joignable QUE via le proxy Railway.
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*"
