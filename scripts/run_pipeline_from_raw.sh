#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

LOG_DIR="$REPO_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run_pipeline_from_raw_$(date '+%Y%m%d_%H%M%S').log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "=============================================="
echo "THEIA pipeline from raw"
echo "Repo: $REPO_DIR"
echo "Log: $LOG_FILE"
echo "Started: $(date)"
echo "=============================================="

require_path() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "[ERROR] Required path not found: $path" >&2
    exit 1
  fi
}

require_path "data/raw/theia"

echo "[INFO] Cleaning generated outputs before rebuilding from raw"
rm -rf \
  data/processed/theia/v2 \
  data/processed/theia/v3 \
  data/processed/theia/v4 \
  data/processed/theia/pyg \
  results/theia_qc

python3 src/01_process_theia_global.py --raw data/raw/theia --out data/processed/theia/v2 --batch-edges 500000
python3 src/02_quality_check_theia.py --processed data/processed/theia/v2 --out results/theia_qc --top 50
python3 src/03_compact_theia_edges.py --processed data/processed/theia/v2 --out data/processed/theia/v3 --window-seconds 10
python3 src/04_build_graph_windows.py --nodes data/processed/theia/v2/nodes_global.csv --edges data/processed/theia/v3/edges_compacted_w10s.csv --out data/processed/theia/v4/windows --summary data/processed/theia/v4/graph_windows_summary.csv
python3 src/05_convert_windows_to_pyg.py --windows data/processed/theia/v4/windows --out data/processed/theia/pyg

require_path "data/raw/theia"
require_path "data/processed/theia/v2/nodes_global.csv"
require_path "data/processed/theia/v3/edges_compacted_w10s.csv"
require_path "data/processed/theia/v4/graph_windows_summary.csv"
require_path "data/processed/theia/pyg/pyg_dataset_summary.csv"
require_path "data/processed/theia/pyg/split_windows.json"

echo "=============================================="
echo "[OK] Pipeline completed and outputs validated"
echo "Finished: $(date)"
echo "=============================================="
