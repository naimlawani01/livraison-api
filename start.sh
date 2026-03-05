#!/bin/sh
set -e

echo "=== Running Alembic migrations ==="
alembic upgrade head

echo "=== Starting application ==="
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
