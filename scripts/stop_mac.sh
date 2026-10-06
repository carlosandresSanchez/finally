#!/usr/bin/env bash
# Stop FinAlly (macOS/Linux). The data volume is kept.
set -euo pipefail
CONTAINER="finally"
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  docker rm -f "$CONTAINER" >/dev/null
  echo "FinAlly stopped. Data persists in the 'finally-data' volume."
else
  echo "FinAlly is not running."
fi
