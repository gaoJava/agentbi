#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/compose.offline.yml"
ENV_FILE="$SCRIPT_DIR/.env.runtime"
IMAGE_ARCHIVE="$SCRIPT_DIR/images/insightpilot-offline-images.tar"
CHECKSUM_FILE="$IMAGE_ARCHIVE.sha256"
OS_NAME=$(uname -s 2>/dev/null || printf 'Unknown')

case "$OS_NAME" in
  Darwin)
    PLATFORM_NAME="macOS"
    ;;
  Linux)
    PLATFORM_NAME="Linux"
    ;;
  MINGW*|MSYS*|CYGWIN*)
    echo "Windows shell detected; starting the PowerShell installer."
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$SCRIPT_DIR/install.ps1"
    ;;
  *)
    echo "Unsupported operating system: $OS_NAME" >&2
    exit 1
    ;;
esac

echo "InsightPilot offline installer - $PLATFORM_NAME"

case "$(uname -m 2>/dev/null || printf unknown)" in
  arm64|aarch64)
    echo "Apple Silicon/ARM detected; Docker will run the bundled amd64 images through emulation."
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install and start Docker Desktop first." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but the Docker engine is not running." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for service health checks." >&2
  exit 1
fi
if [ ! -f "$IMAGE_ARCHIVE" ]; then
  echo "Offline image archive was not found: $IMAGE_ARCHIVE" >&2
  exit 1
fi

if [ -f "$CHECKSUM_FILE" ]; then
  EXPECTED_HASH=$(awk '{print $1}' "$CHECKSUM_FILE")
  if command -v shasum >/dev/null 2>&1; then
    ACTUAL_HASH=$(shasum -a 256 "$IMAGE_ARCHIVE" | awk '{print $1}')
  elif command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_HASH=$(sha256sum "$IMAGE_ARCHIVE" | awk '{print $1}')
  else
    echo "Neither shasum nor sha256sum is available." >&2
    exit 1
  fi
  if [ "$EXPECTED_HASH" != "$ACTUAL_HASH" ]; then
    echo "Offline image archive SHA256 verification failed." >&2
    exit 1
  fi
  echo "Image archive checksum verified."
fi

echo "Loading offline Docker images..."
docker load --input "$IMAGE_ARCHIVE"

new_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "${1:-32}"
  else
    echo "OpenSSL is required to generate runtime secrets." >&2
    exit 1
  fi
}

if [ ! -f "$ENV_FILE" ]; then
  umask 077
  {
    echo "POSTGRES_PASSWORD=$(new_secret 32)"
    echo "SUPERSET_SECRET_KEY=$(new_secret 48)"
    echo "AGENTBI_API_KEY=$(new_secret 32)"
    echo "AGENTBI_SESSION_SECRET=$(new_secret 48)"
    echo "SUPERSONIC_TOKEN=pending"
    echo "BIND_ADDRESS=127.0.0.1"
    echo "SUPERSET_PORT=8088"
    echo "AGENTBI_PORT=8090"
    echo "SUPERSONIC_PORT=9080"
  } > "$ENV_FILE"
  echo "Created local runtime secrets."
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d db redis supersonic

echo "Waiting for SuperSonic..."
SUPERSONIC_RESPONSE=""
ATTEMPT=0
while [ "$ATTEMPT" -lt 80 ]; do
  SUPERSONIC_RESPONSE=$(curl -fsS --max-time 5 \
    -H 'Content-Type: application/json' \
    -d '{"name":"admin","password":"admin"}' \
    'http://127.0.0.1:9080/api/auth/user/login' 2>/dev/null || true)
  SUPERSONIC_TOKEN=$(printf '%s' "$SUPERSONIC_RESPONSE" | sed -n 's/.*"data"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  if [ -n "$SUPERSONIC_TOKEN" ]; then
    break
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep 3
done
if [ -z "${SUPERSONIC_TOKEN:-}" ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail 100 supersonic
  echo "SuperSonic did not become ready or the default admin login failed." >&2
  exit 1
fi

TEMP_ENV="$ENV_FILE.tmp.$$"
awk '!/^SUPERSONIC_TOKEN=/' "$ENV_FILE" > "$TEMP_ENV"
printf 'SUPERSONIC_TOKEN=Bearer %s\n' "$SUPERSONIC_TOKEN" >> "$TEMP_ENV"
chmod 600 "$TEMP_ENV"
mv "$TEMP_ENV" "$ENV_FILE"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d superset agentbi

echo "Waiting for InsightPilot..."
ATTEMPT=0
while [ "$ATTEMPT" -lt 60 ]; do
  if curl -fsS --max-time 5 'http://127.0.0.1:8090/health' >/dev/null 2>&1; then
    echo ""
    echo "InsightPilot is ready: http://127.0.0.1:8090/app"
    echo "Superset:             http://127.0.0.1:8088"
    echo "SuperSonic:           http://127.0.0.1:9080"
    echo "Demo accounts: admin/admin and user/user"
    echo "Stop command: docker compose --env-file \"$ENV_FILE\" -f \"$COMPOSE_FILE\" down"
    exit 0
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep 3
done

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail 100 agentbi superset
echo "InsightPilot did not become healthy in time." >&2
exit 1
