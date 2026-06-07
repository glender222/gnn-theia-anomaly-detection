#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PASO 04 - Construcción de grafos por ventana temporal

Entrada:
    data/processed/theia/v2/nodes_global.csv
    data/processed/theia/v3/edges_compacted_w10s.csv

Salida:
    data/processed/theia/v4/windows/
        window_000000/
            nodes.csv
            edges.csv
        window_000001/
            nodes.csv
            edges.csv
        ...
    data/processed/theia/v4/graph_windows_summary.csv

Objetivo:
    Convertir las aristas compactadas por ventana en grafos separados.
    Cada ventana temporal será un grafo independiente para GNN.
"""

import csv
import argparse
import math
from pathlib import Path
from collections import defaultdict, Counter


NODE_TYPE_IDS = {
    "SUBJECT": 0,
    "FILE": 1,
    "NETFLOW": 2,
    "IPC": 3,
    "MEMORY": 4,
    "PRINCIPAL": 5,
    "UNKNOWN": 6,
}

EDGE_TYPE_IDS = {
    "EVENT_WRITE": 0,
    "EVENT_READ": 1,
    "EVENT_MMAP": 2,
    "EVENT_OPEN": 3,
    "EVENT_RECVMSG": 4,
    "EVENT_MPROTECT": 5,
    "EVENT_SENDMSG": 6,
    "EVENT_RECVFROM": 7,
    "EVENT_SENDTO": 8,
    "EVENT_CLONE": 9,
    "EVENT_EXECUTE": 10,
    "EVENT_OTHER": 11,
    "EVENT_CONNECT": 12,
    "EVENT_UNLINK": 13,
    "EVENT_SHM": 14,
    "EVENT_CORRELATION": 15,
    "EVENT_MODIFY_FILE_ATTRIBUTES": 16,
}


def load_nodes(nodes_path: Path):
    nodes = {}

    with nodes_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            node_id = row["node_id"]
            nodes[node_id] = row

    return nodes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", default="data/processed/theia/v2/nodes_global.csv")
    parser.add_argument("--edges", default="data/processed/theia/v3/edges_compacted_w10s.csv")
    parser.add_argument("--out", default="data/processed/theia/v4/windows")
    parser.add_argument("--summary", default="data/processed/theia/v4/graph_windows_summary.csv")

    args = parser.parse_args()

    nodes_path = Path(args.nodes)
    edges_path = Path(args.edges)
    out_dir = Path(args.out)
    summary_path = Path(args.summary)

    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    print("[INFO] Cargando nodos globales...")
    global_nodes = load_nodes(nodes_path)
    print(f"[INFO] Nodos globales: {len(global_nodes)}")

    print("[INFO] Cargando aristas compactadas por ventana...")

    windows = defaultdict(list)

    with edges_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            window_id = int(row["window_id"])
            windows[window_id].append(row)

    print(f"[INFO] Ventanas encontradas: {len(windows)}")

    summary_rows = []

    for window_id in sorted(windows.keys()):
        edges = windows[window_id]
        window_dir = out_dir / f"window_{window_id:06d}"
        window_dir.mkdir(parents=True, exist_ok=True)

        active_node_ids = set()

        for edge in edges:
            active_node_ids.add(edge["source_id"])
            active_node_ids.add(edge["target_id"])

        active_node_ids = sorted(active_node_ids)
        node_id_to_local = {node_id: idx for idx, node_id in enumerate(active_node_ids)}

        in_degree = Counter()
        out_degree = Counter()
        weighted_in_degree = Counter()
        weighted_out_degree = Counter()
        edge_type_counter = Counter()

        for edge in edges:
            src = edge["source_id"]
            tgt = edge["target_id"]
            edge_type = edge["edge_type"]
            weight = int(edge["weight_count"])

            out_degree[src] += 1
            in_degree[tgt] += 1
            weighted_out_degree[src] += weight
            weighted_in_degree[tgt] += weight
            edge_type_counter[edge_type] += 1

        # nodes.csv por ventana
        nodes_out = window_dir / "nodes.csv"

        node_fieldnames = [
            "local_id",
            "node_id",
            "node_type",
            "node_type_id",
            "name",
            "pid",
            "ppid",
            "uid",
            "host",
            "timestamp_first_seen",
            "in_degree",
            "out_degree",
            "total_degree",
            "weighted_in_degree",
            "weighted_out_degree",
            "weighted_total_degree",
            "label",
        ]

        with nodes_out.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=node_fieldnames)
            writer.writeheader()

            for node_id in active_node_ids:
                original = global_nodes.get(node_id, {})
                node_type = original.get("node_type", "UNKNOWN")
                node_type_id = NODE_TYPE_IDS.get(node_type, NODE_TYPE_IDS["UNKNOWN"])

                in_d = in_degree[node_id]
                out_d = out_degree[node_id]
                win_d = weighted_in_degree[node_id]
                wout_d = weighted_out_degree[node_id]

                writer.writerow({
                    "local_id": node_id_to_local[node_id],
                    "node_id": node_id,
                    "node_type": node_type,
                    "node_type_id": node_type_id,
                    "name": original.get("name", ""),
                    "pid": original.get("pid", ""),
                    "ppid": original.get("ppid", ""),
                    "uid": original.get("uid", ""),
                    "host": original.get("host", ""),
                    "timestamp_first_seen": original.get("timestamp_first_seen", ""),
                    "in_degree": in_d,
                    "out_degree": out_d,
                    "total_degree": in_d + out_d,
                    "weighted_in_degree": win_d,
                    "weighted_out_degree": wout_d,
                    "weighted_total_degree": win_d + wout_d,
                    "label": original.get("label", 0),
                })

        # edges.csv por ventana
        edges_out = window_dir / "edges.csv"

        edge_fieldnames = [
            "source",
            "target",
            "source_id",
            "target_id",
            "edge_type",
            "edge_type_id",
            "weight_count",
            "weight_log1p",
            "first_timestamp",
            "last_timestamp",
            "label",
        ]

        with edges_out.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=edge_fieldnames)
            writer.writeheader()

            for edge in edges:
                src_id = edge["source_id"]
                tgt_id = edge["target_id"]
                edge_type = edge["edge_type"]
                weight = int(edge["weight_count"])

                writer.writerow({
                    "source": node_id_to_local[src_id],
                    "target": node_id_to_local[tgt_id],
                    "source_id": src_id,
                    "target_id": tgt_id,
                    "edge_type": edge_type,
                    "edge_type_id": EDGE_TYPE_IDS.get(edge_type, -1),
                    "weight_count": weight,
                    "weight_log1p": math.log1p(weight),
                    "first_timestamp": edge["first_timestamp"],
                    "last_timestamp": edge["last_timestamp"],
                    "label": 0,
                })

        top_edge_type = edge_type_counter.most_common(1)[0][0] if edge_type_counter else ""
        top_edge_count = edge_type_counter.most_common(1)[0][1] if edge_type_counter else 0

        summary_rows.append({
            "window_id": window_id,
            "nodes": len(active_node_ids),
            "edges": len(edges),
            "top_edge_type": top_edge_type,
            "top_edge_type_count": top_edge_count,
            "window_dir": str(window_dir),
        })

        print(
            f"[OK] window_{window_id:06d}: "
            f"{len(active_node_ids)} nodos, {len(edges)} aristas"
        )

    with summary_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "window_id",
            "nodes",
            "edges",
            "top_edge_type",
            "top_edge_type_count",
            "window_dir",
        ]

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    print("\n[FINALIZADO]")
    print(f"Ventanas generadas: {len(summary_rows)}")
    print(f"Resumen: {summary_path}")
    print(f"Salida: {out_dir}")


if __name__ == "__main__":
    main()