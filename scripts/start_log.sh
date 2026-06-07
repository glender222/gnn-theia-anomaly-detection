#!/usr/bin/env bash

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_FILE="$LOG_DIR/session_${TIMESTAMP}.log"

echo "=============================================="
echo "Sesión registrada iniciada"
echo "Repositorio: $REPO_DIR"
echo "Log: $LOG_FILE"
echo "Fecha: $(date)"
echo "=============================================="
echo
echo "Todo lo que ejecutes ahora quedará guardado."
echo "Para terminar la sesión escribe: exit"
echo

cd "$REPO_DIR"

script -q -f -a "$LOG_FILE"
