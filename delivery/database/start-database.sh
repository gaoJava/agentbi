#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
docker compose -f compose.database.yml up -d
echo "PostgreSQL is starting. First initialization restores superset and examples automatically."
echo "Connection: 127.0.0.1:5432; user: superset; databases: superset, examples"

