#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

REMOTE_NAME="nubeglenp"
REMOTE_PATH="gnn-theia-dvc-store"
LOCAL_STORE=".dvc_remote"
DVC_REMOTE_NAME="localstore"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

ensure_tool() {
  local tool="$1"
  if ! command_exists "$tool"; then
    echo "[ERROR] Required tool not found in PATH: $tool" >&2
    echo "Activate the venv or install requirements before running this script." >&2
    exit 1
  fi
}

ensure_rclone_remote() {
  if ! rclone lsd "${REMOTE_NAME}:" >/dev/null 2>&1; then
    echo "[WARN] rclone remote '${REMOTE_NAME}:' is not configured or not reachable."
    echo "Opening 'rclone config'. Create or reconnect remote '${REMOTE_NAME}'."
    if [[ -t 0 && -t 1 ]]; then
      rclone config
    else
      echo "[ERROR] Non-interactive shell: run 'rclone config' and create '${REMOTE_NAME}' first." >&2
      exit 1
    fi
  fi

  if ! rclone lsd "${REMOTE_NAME}:" >/dev/null 2>&1; then
    echo "[ERROR] rclone remote '${REMOTE_NAME}:' is still not reachable." >&2
    exit 1
  fi
}

ensure_tool dvc
ensure_tool rclone

mkdir -p "$LOCAL_STORE"

DVC_NO_ANALYTICS=1 dvc config cache.type hardlink
DVC_NO_ANALYTICS=1 dvc config cache.dir "$LOCAL_STORE"

if dvc remote list | awk '{print $1}' | grep -qx "$DVC_REMOTE_NAME"; then
  dvc remote modify "$DVC_REMOTE_NAME" url "$LOCAL_STORE"
else
  dvc remote add -d "$DVC_REMOTE_NAME" "$LOCAL_STORE"
fi
dvc remote default "$DVC_REMOTE_NAME"

echo "[INFO] Pushing DVC-tracked data into local store: $LOCAL_STORE"
dvc push

ensure_rclone_remote

echo "[INFO] Syncing $LOCAL_STORE to ${REMOTE_NAME}:${REMOTE_PATH}"
rclone sync "$LOCAL_STORE" "${REMOTE_NAME}:${REMOTE_PATH}" --progress --transfers 8 --checkers 16

echo "[OK] DVC store pushed to ${REMOTE_NAME}:${REMOTE_PATH}"
