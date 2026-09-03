#!/usr/bin/env bash
set -euo pipefail

if [ -f /offline-seed/superset.dump ]; then
  pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --clean --if-exists --no-owner --no-acl /offline-seed/superset.dump
fi

if [ -f /offline-seed/examples.dump ]; then
  if ! psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
      --command "SELECT 1 FROM pg_database WHERE datname = 'examples'" | grep -q '^1$'; then
    createdb --username "$POSTGRES_USER" --encoding UTF8 examples
  fi
  pg_restore --username "$POSTGRES_USER" --dbname examples --clean --if-exists --no-owner --no-acl /offline-seed/examples.dump
fi

