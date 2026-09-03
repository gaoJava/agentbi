#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

for command in python3 node java docker; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "Required command not found: $command" >&2
    exit 1
  }
done

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python delivery/database/init_database.py

if [ -f integrations/superset-extension/frontend/package-lock.json ]; then
  (cd integrations/superset-extension/frontend && npm ci)
fi

echo "AgentBI installed. Run: .venv/bin/python -m pytest -q"
echo "The full Windows competition demo is started with scripts/start-demo.ps1."

