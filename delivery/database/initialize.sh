#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PGHOST=${PGHOST:-127.0.0.1}
PGPORT=${PGPORT:-5432}
PGUSER=${PGUSER:-superset}
PGDATABASE=${PGDATABASE:-examples}
export PGHOST PGPORT PGUSER PGDATABASE

command -v psql >/dev/null 2>&1 || {
  echo "psql was not found. Install the PostgreSQL 17 client or use delivery/offline/install.sh." >&2
  exit 1
}

psql -v ON_ERROR_STOP=1 -f "$SCRIPT_DIR/postgresql/001_schema.sql"
psql -v ON_ERROR_STOP=1 -v "data_file=$SCRIPT_DIR/postgresql/video_game_sales.csv" -f "$SCRIPT_DIR/postgresql/002_load_data.sql"
psql -v ON_ERROR_STOP=1 -f "$SCRIPT_DIR/postgresql/verify.sql"

