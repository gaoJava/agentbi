#!/usr/bin/env bash
set -euo pipefail

if [ -f /offline-seed/superset.dump ]; then
  pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --clean --if-exists --no-owner --no-acl /offline-seed/superset.dump
  # Superset encrypts connection secrets with SECRET_KEY. The offline installer
  # generates a fresh key per machine, so encrypted values from the build host
  # must not be carried into the restored metadata. The init service writes the
  # examples connection again with the destination environment's key.
  psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 \
    --command "UPDATE dbs SET password = NULL, encrypted_extra = NULL, server_cert = NULL;"
fi

if [ -f /offline-seed/examples.dump ]; then
  if ! psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
      --command "SELECT 1 FROM pg_database WHERE datname = 'examples'" | grep -q '^1$'; then
    createdb --username "$POSTGRES_USER" --encoding UTF8 examples
  fi
  pg_restore --username "$POSTGRES_USER" --dbname examples --clean --if-exists --no-owner --no-acl /offline-seed/examples.dump
fi
