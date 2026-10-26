#!/bin/sh
set -e

# Default to /data if DATA_DIR is unset
DATA_DIR_PATH="${DATA_DIR:-/data}"

# If running as root (e.g. on Render or local Docker without user flag),
# ensure DATA_DIR exists, transfer ownership to user (UID 1000), and drop privileges.
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR_PATH"
    chown -R user:user "$DATA_DIR_PATH" 2>/dev/null || true
    exec gosu user "$@"
else
    # Running as non-root (e.g. Hugging Face Spaces which passes -u 1000)
    mkdir -p "$DATA_DIR_PATH" 2>/dev/null || true
    exec "$@"
fi
