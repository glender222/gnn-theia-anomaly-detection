#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PASO 03 - Compactación temporal de aristas THEIA

Entrada:
    data/processed/theia/v2/
        nodes_global.csv
        edges_batch_*.csv

Salida:
    data/processed/theia/v3/
        edges_compacted_w10s.csv
        compact_stats.txt
        window_stats.csv

Objetivo:
    Compactar aristas repetidas por:
        window_id + source_id + target_id + edge_type

    En lugar de tener millones de EVENT_WRITE repetidos,
    se guarda una sola arista con weight_count.
"""

import csv
import argparse
from pathlib import Path
from collections import defaultdict, Counter


def find_min_max_timestamp(edge_files):
    min_ts = None
    max_ts = None
    total_rows = 0
    skipped = 0

    for edge_file in edge_files:
        print(f"[PASS 1] Leyendo timestamps: {edge_file.name}")

        with edge_file.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                total_rows += 1
                ts_raw = str(row.get("timestamp", ""))

                if not ts_raw.isdigit():
                    skipped += 1
                    continue

                ts = int(ts_raw)

                if min_ts is None or ts < min_ts:
                    min_ts = ts

                if max_ts is None or ts > max_ts:
                    max_ts = ts

    return min_ts, max_ts, total_rows, skipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed", default="data/processed/theia/v2")
    parser.add_argument("--out", default="data/processed/theia/v3")
    parser.add_argument("--window-seconds", type=int, default=10)

    args = parser.parse_args()

    processed_dir = Path(args.processed)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    edge_files = sorted(processed_dir.glob("edges_batch_*.csv"))

    if not edge_files:
        raise FileNotFoundError(f"No encontré edges_batch_*.csv en {processed_dir}")

    window_ns = args.window_seconds * 1_000_000_000

    print("[INFO] Buscando rango temporal...")
    min_ts, max_ts, total_rows, skipped_ts = find_min_max_timestamp(edge_files)

    if min_ts is None or max_ts is None:
        raise RuntimeError("No se encontraron timestamps válidos.")

    print(f"[INFO] Timestamp mínimo: {min_ts}")
    print(f"[INFO] Timestamp máximo: {max_ts}")
    print(f"[INFO] Duración segundos: {(max_ts - min_ts) / 1_000_000_000:.2f}")
    print(f"[INFO] Ventana temporal: {args.window_seconds} segundos")

    compact = {}
    raw_window_counts = Counter()
    edge_type_counts_raw = Counter()

    total_edges = 0
    skipped_edges = 0

    print("[INFO] Compactando aristas...")

    for edge_file in edge_files:
        print(f"[PASS 2] Procesando: {edge_file.name}")

        with edge_file.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                total_edges += 1

                src = str(row["source_id"])
                tgt = str(row["target_id"])
                edge_type = str(row["edge_type"])
                ts_raw = str(row.get("timestamp", ""))

                if not ts_raw.isdigit():
                    skipped_edges += 1
                    continue

                ts = int(ts_raw)
                window_id = (ts - min_ts) // window_ns

                key = (window_id, src, tgt, edge_type)

                if key not in compact:
                    compact[key] = {
                        "window_id": window_id,
                        "window_start_ns": min_ts + window_id * window_ns,
                        "window_end_ns": min_ts + (window_id + 1) * window_ns - 1,
                        "source_id": src,
                        "target_id": tgt,
                        "edge_type": edge_type,
                        "weight_count": 0,
                        "first_timestamp": ts,
                        "last_timestamp": ts,
                    }

                item = compact[key]
                item["weight_count"] += 1

                if ts < item["first_timestamp"]:
                    item["first_timestamp"] = ts

                if ts > item["last_timestamp"]:
                    item["last_timestamp"] = ts

                raw_window_counts[window_id] += 1
                edge_type_counts_raw[edge_type] += 1

    compacted_path = out_dir / f"edges_compacted_w{args.window_seconds}s.csv"

    fieldnames = [
        "window_id",
        "window_start_ns",
        "window_end_ns",
        "source_id",
        "target_id",
        "edge_type",
        "weight_count",
        "first_timestamp",
        "last_timestamp",
    ]

    print(f"[INFO] Escribiendo: {compacted_path}")

    with compacted_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for key in sorted(compact.keys(), key=lambda x: (x[0], x[3], x[1], x[2])):
            writer.writerow(compact[key])

    compacted_window_counts = Counter()

    for key in compact.keys():
        window_id = key[0]
        compacted_window_counts[window_id] += 1

    window_stats_path = out_dir / "window_stats.csv"

    with window_stats_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "window_id",
            "window_start_ns",
            "window_end_ns",
            "raw_edges_count",
            "compacted_edges_count",
        ])

        for window_id in sorted(raw_window_counts.keys()):
            writer.writerow([
                window_id,
                min_ts + window_id * window_ns,
                min_ts + (window_id + 1) * window_ns - 1,
                raw_window_counts[window_id],
                compacted_window_counts[window_id],
            ])

    stats_path = out_dir / "compact_stats.txt"

    with stats_path.open("w", encoding="utf-8") as f:
        f.write("=== THEIA EDGE COMPACTION STATS ===\n\n")
        f.write(f"Raw edges total: {total_edges}\n")
        f.write(f"Skipped edges: {skipped_edges}\n")
        f.write(f"Compacted edges total: {len(compact)}\n")
        f.write(f"Reduction ratio: {total_edges / len(compact):.2f}x\n")
        f.write(f"Window seconds: {args.window_seconds}\n")
        f.write(f"Number of windows: {len(raw_window_counts)}\n")
        f.write(f"Timestamp min: {min_ts}\n")
        f.write(f"Timestamp max: {max_ts}\n")
        f.write(f"Duration seconds: {(max_ts - min_ts) / 1_000_000_000:.2f}\n")

        f.write("\n=== Raw edge types ===\n")
        for edge_type, count in edge_type_counts_raw.most_common():
            pct = count / total_edges * 100 if total_edges else 0
            f.write(f"{edge_type}: {count} ({pct:.4f}%)\n")

        f.write("\n=== Top compacted edges by weight ===\n")
        top_edges = sorted(compact.values(), key=lambda x: x["weight_count"], reverse=True)[:50]

        for item in top_edges:
            f.write(
                f"w={item['window_id']} | {item['edge_type']} | "
                f"{item['source_id']} -> {item['target_id']} | "
                f"weight={item['weight_count']}\n"
            )

    print("\n[FINALIZADO]")
    print(f"Aristas originales: {total_edges}")
    print(f"Aristas compactadas: {len(compact)}")
    print(f"Reducción: {total_edges / len(compact):.2f}x")
    print(f"Salida: {compacted_path}")
    print(f"Stats: {stats_path}")
    print(f"Window stats: {window_stats_path}")


if __name__ == "__main__":
    main()