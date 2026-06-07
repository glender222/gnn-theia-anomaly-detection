#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PASO 02 - Control de calidad del procesamiento THEIA v2

Entrada:
    data/processed/theia/v2/
        nodes_global.csv
        edges_batch_*.csv

Salida:
    results/theia_qc/
        qc_summary.txt
        node_type_counts.csv
        edge_type_counts.csv
        batch_stats.csv
        top_repeated_edges.csv
        top_out_degree_nodes.csv
        top_in_degree_nodes.csv
"""

import csv
import argparse
from pathlib import Path
from collections import Counter, defaultdict


def load_nodes(nodes_path: Path):
    node_ids = set()
    node_type_counter = Counter()

    with nodes_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            node_id = str(row["node_id"])
            node_type = str(row["node_type"])

            node_ids.add(node_id)
            node_type_counter[node_type] += 1

    return node_ids, node_type_counter


def write_counter_csv(path: Path, header, counter: Counter):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)

        for key, count in counter.most_common():
            if isinstance(key, tuple):
                writer.writerow(list(key) + [count])
            else:
                writer.writerow([key, count])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed", default="data/processed/theia/v2")
    parser.add_argument("--out", default="results/theia_qc")
    parser.add_argument("--top", type=int, default=50)

    args = parser.parse_args()

    processed_dir = Path(args.processed)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = processed_dir / "nodes_global.csv"
    edge_files = sorted(processed_dir.glob("edges_batch_*.csv"))

    if not nodes_path.exists():
        raise FileNotFoundError(f"No existe: {nodes_path}")

    if not edge_files:
        raise FileNotFoundError(f"No encontré edges_batch_*.csv en: {processed_dir}")

    print("[INFO] Cargando nodos globales...")
    node_ids, node_type_counter = load_nodes(nodes_path)

    edge_type_counter = Counter()
    repeated_edges_counter = Counter()
    out_degree = Counter()
    in_degree = Counter()

    batch_rows = []

    total_edges = 0
    missing_refs = 0
    unique_edge_nodes = set()

    min_ts = None
    max_ts = None

    print(f"[INFO] Nodos globales: {len(node_ids)}")
    print(f"[INFO] Archivos de aristas: {len(edge_files)}")

    for edge_file in edge_files:
        print(f"[INFO] Revisando {edge_file.name}")

        batch_edges = 0
        batch_missing = 0
        batch_unique_nodes = set()
        batch_edge_types = Counter()

        with edge_file.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                src = str(row["source_id"])
                tgt = str(row["target_id"])
                edge_type = str(row["edge_type"])
                timestamp = str(row.get("timestamp", ""))

                batch_edges += 1
                total_edges += 1

                edge_type_counter[edge_type] += 1
                batch_edge_types[edge_type] += 1

                repeated_edges_counter[(src, tgt, edge_type)] += 1

                out_degree[src] += 1
                in_degree[tgt] += 1

                unique_edge_nodes.add(src)
                unique_edge_nodes.add(tgt)
                batch_unique_nodes.add(src)
                batch_unique_nodes.add(tgt)

                if src not in node_ids:
                    missing_refs += 1
                    batch_missing += 1

                if tgt not in node_ids:
                    missing_refs += 1
                    batch_missing += 1

                if timestamp.isdigit():
                    ts = int(timestamp)

                    if min_ts is None or ts < min_ts:
                        min_ts = ts

                    if max_ts is None or ts > max_ts:
                        max_ts = ts

        most_common_type = batch_edge_types.most_common(1)
        top_type = most_common_type[0][0] if most_common_type else ""
        top_type_count = most_common_type[0][1] if most_common_type else 0

        batch_rows.append({
            "batch_file": edge_file.name,
            "edges": batch_edges,
            "unique_nodes_in_edges": len(batch_unique_nodes),
            "missing_refs": batch_missing,
            "top_edge_type": top_type,
            "top_edge_type_count": top_type_count,
        })

    # Archivos CSV
    write_counter_csv(
        out_dir / "node_type_counts.csv",
        ["node_type", "count"],
        node_type_counter
    )

    write_counter_csv(
        out_dir / "edge_type_counts.csv",
        ["edge_type", "count"],
        edge_type_counter
    )

    write_counter_csv(
        out_dir / "top_repeated_edges.csv",
        ["source_id", "target_id", "edge_type", "count"],
        Counter(dict(repeated_edges_counter.most_common(args.top)))
    )

    write_counter_csv(
        out_dir / "top_out_degree_nodes.csv",
        ["node_id", "out_degree"],
        Counter(dict(out_degree.most_common(args.top)))
    )

    write_counter_csv(
        out_dir / "top_in_degree_nodes.csv",
        ["node_id", "in_degree"],
        Counter(dict(in_degree.most_common(args.top)))
    )

    with (out_dir / "batch_stats.csv").open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "batch_file",
            "edges",
            "unique_nodes_in_edges",
            "missing_refs",
            "top_edge_type",
            "top_edge_type_count",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(batch_rows)

    # Reporte TXT
    summary_path = out_dir / "qc_summary.txt"

    with summary_path.open("w", encoding="utf-8") as f:
        f.write("=== THEIA V2 QUALITY CHECK ===\n\n")
        f.write(f"Nodos globales: {len(node_ids)}\n")
        f.write(f"Aristas totales: {total_edges}\n")
        f.write(f"Nodos únicos usados por aristas: {len(unique_edge_nodes)}\n")
        f.write(f"Referencias faltantes source/target: {missing_refs}\n")
        f.write(f"Timestamp mínimo: {min_ts}\n")
        f.write(f"Timestamp máximo: {max_ts}\n")

        if min_ts is not None and max_ts is not None:
            duration_seconds = (max_ts - min_ts) / 1_000_000_000
            f.write(f"Duración aproximada en segundos: {duration_seconds:.2f}\n")
            f.write(f"Duración aproximada en minutos: {duration_seconds / 60:.2f}\n")

        f.write("\n=== Tipos de nodos ===\n")
        for key, value in node_type_counter.most_common():
            f.write(f"{key}: {value}\n")

        f.write("\n=== Tipos de aristas ===\n")
        for key, value in edge_type_counter.most_common():
            pct = (value / total_edges) * 100 if total_edges else 0
            f.write(f"{key}: {value} ({pct:.4f}%)\n")

        f.write("\n=== Top aristas repetidas ===\n")
        for (src, tgt, edge_type), count in repeated_edges_counter.most_common(args.top):
            f.write(f"{edge_type} | {src} -> {tgt} | {count}\n")

        f.write("\n=== Top nodos por salida ===\n")
        for node_id, count in out_degree.most_common(args.top):
            f.write(f"{node_id}: {count}\n")

        f.write("\n=== Top nodos por entrada ===\n")
        for node_id, count in in_degree.most_common(args.top):
            f.write(f"{node_id}: {count}\n")

    print("\n[FINALIZADO]")
    print(f"Reporte: {summary_path}")
    print(f"CSV generados en: {out_dir}")


if __name__ == "__main__":
    main()