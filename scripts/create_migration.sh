#!/bin/bash

# Script pour créer une nouvelle migration Alembic

if [ -z "$1" ]; then
    echo "Usage: ./create_migration.sh 'description de la migration'"
    exit 1
fi

echo "📝 Creating new migration: $1"

cd "$(dirname "$0")/.." || exit

alembic revision --autogenerate -m "$1"

echo "✅ Migration created successfully"
