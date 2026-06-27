#!/bin/bash

# Watch for code changes and restart services

set -e

# Install inotify-tools if not present
if ! command -v inotifywait &> /dev/null; then
  echo "Installing inotify-tools..."
  apt-get update && apt-get install -y inotify-tools
fi

WORKSPACE="${WORKSPACE:-.}"
CONTAINER_NAME="netengine_dev"
WATCH_PATHS="${WATCH_PATHS:-netengine tests pyproject.toml}"

echo "Watching $WORKSPACE for changes..."
echo "Restart trigger: $WATCH_PATHS"

# Watch for file changes
inotifywait -m -r -e modify,create,delete \
  --include "\.py$|\.yaml$|\.yml$|\.toml$" \
  $WATCH_PATHS | while read -r path action file; do

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Detected $action: $path$file"

  # Restart the dev container
  echo "Restarting container $CONTAINER_NAME..."
  docker compose restart "$CONTAINER_NAME" || echo "Failed to restart, container may be rebuilding"

  echo "Waiting 2s before next check..."
  sleep 2
done
