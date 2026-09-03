#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
docker compose -f compose.database.yml down
echo "Database stopped. The Docker volume and all imported data were preserved."

