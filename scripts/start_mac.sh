#!/usr/bin/env bash
# Start FinAlly in Docker (macOS/Linux). Usage: ./scripts/start_mac.sh [--build] [--no-open]
set -euo pipefail

IMAGE="finally"
CONTAINER="finally"
VOLUME="finally-data"
PORT="${PORT:-8000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BUILD=false
OPEN=true
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=true ;;
    --no-open) OPEN=false ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

command -v docker >/dev/null || { echo "Docker is required: https://docs.docker.com/get-docker/" >&2; exit 1; }

if $BUILD || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building $IMAGE image..."
  docker build -t "$IMAGE" "$ROOT"
fi

# Idempotent: replace any existing container (data lives in the volume).
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

ENV_ARGS=()
if [[ -f "$ROOT/.env" ]]; then
  ENV_ARGS=(--env-file "$ROOT/.env")
else
  echo "Warning: no .env found; AI chat needs OPENROUTER_API_KEY (see .env.example)." >&2
fi

docker run -d --name "$CONTAINER" \
  -p "$PORT:8000" \
  -v "$VOLUME:/app/db" \
  ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} \
  "$IMAGE" >/dev/null

URL="http://localhost:$PORT"
echo -n "Waiting for FinAlly to start"
for _ in $(seq 1 60); do
  if curl -sf "$URL/api/health" >/dev/null 2>&1; then break; fi
  echo -n "."; sleep 1
done
echo
echo "FinAlly is running at $URL"

if $OPEN; then
  if command -v open >/dev/null; then open "$URL"
  elif command -v xdg-open >/dev/null; then xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi
