#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PASO 05 - Convertir grafos por ventana a tensores PyTorch

Entrada:
    data/processed/theia/v4/windows/window_xxxxxx/
        nodes.csv
        edges.csv

Salida:
    data/processed/theia/pyg/
        window_000000.pt
        window_000001.pt
        ...
        pyg_dataset_summary.csv
        split_windows.json

Cada archivo .pt contiene:
    x          -> features de nodos
    edge_index -> conexiones source-target
    edge_attr  -> features de aristas
    metadata   -> ids y nombres
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import torch


NUM_NODE_TYPES = 7
NUM_EDGE_TYPES = 17


def safe_float(value, default=0.0):
    if pd.isna(value):
        return default

    try:
        text = str(value).strip()
        if text == "":
            return default
        return float(text)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        return int(float(safe_float(value, default)))
    except Exception:
        return default


def one_hot(index, size):
    vector = [0.0] * size

    if 0 <= index < size:
        vector[index] = 1.0

    return vector


def build_node_features(nodes_df):
    features = []

    for _, row in nodes_df.iterrows():
        node_type_id = safe_int(row.get("node_type_id", 6), 6)
        node_type_oh = one_hot(node_type_id, NUM_NODE_TYPES)

        in_degree = math.log1p(safe_float(row.get("in_degree", 0)))
        out_degree = math.log1p(safe_float(row.get("out_degree", 0)))
        total_degree = math.log1p(safe_float(row.get("total_degree", 0)))

        weighted_in_degree = math.log1p(safe_float(row.get("weighted_in_degree", 0)))
        weighted_out_degree = math.log1p(safe_float(row.get("weighted_out_degree", 0)))
        weighted_total_degree = math.log1p(safe_float(row.get("weighted_total_degree", 0)))

        uid_raw = row.get("uid", "")
        has_uid = 0.0 if pd.isna(uid_raw) or str(uid_raw).strip() == "" else 1.0
        uid_value = safe_float(uid_raw, -1.0)
        is_root = 1.0 if uid_value == 0 else 0.0
        is_user = 1.0 if uid_value > 0 else 0.0

        name_raw = row.get("name", "")
        has_name = 0.0 if pd.isna(name_raw) or str(name_raw).strip() == "" else 1.0

        pid_raw = row.get("pid", "")
        has_pid = 0.0 if pd.isna(pid_raw) or str(pid_raw).strip() == "" else 1.0

        row_features = (
            node_type_oh
            + [
                in_degree,
                out_degree,
                total_degree,
                weighted_in_degree,
                weighted_out_degree,
                weighted_total_degree,
                has_uid,
                is_root,
                is_user,
                has_name,
                has_pid,
            ]
        )

        features.append(row_features)

    feature_names = (
        [f"node_type_{i}" for i in range(NUM_NODE_TYPES)]
        + [
            "log_in_degree",
            "log_out_degree",
            "log_total_degree",
            "log_weighted_in_degree",
            "log_weighted_out_degree",
            "log_weighted_total_degree",
            "has_uid",
            "is_root",
            "is_user",
            "has_name",
            "has_pid",
        ]
    )

    return torch.tensor(features, dtype=torch.float32), feature_names


def build_edge_tensors(edges_df):
    source = edges_df["source"].astype(int).to_numpy()
    target = edges_df["target"].astype(int).to_numpy()

    edge_index = torch.tensor(
        np.vstack([source, target]),
        dtype=torch.long
    )

    edge_features = []

    for _, row in edges_df.iterrows():
        edge_type_id = safe_int(row.get("edge_type_id", -1), -1)
        edge_type_oh = one_hot(edge_type_id, NUM_EDGE_TYPES)

        weight_log1p = safe_float(row.get("weight_log1p", 0.0), 0.0)

        first_ts = safe_float(row.get("first_timestamp", 0.0), 0.0)
        last_ts = safe_float(row.get("last_timestamp", 0.0), 0.0)

        duration_seconds = max(0.0, (last_ts - first_ts) / 1_000_000_000)
        duration_log1p = math.log1p(duration_seconds)

        edge_features.append(edge_type_oh + [weight_log1p, duration_log1p])

    edge_feature_names = (
        [f"edge_type_{i}" for i in range(NUM_EDGE_TYPES)]
        + ["weight_log1p", "duration_log1p"]
    )

    edge_attr = torch.tensor(edge_features, dtype=torch.float32)

    return edge_index, edge_attr, edge_feature_names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default="data/processed/theia/v4/windows")
    parser.add_argument("--out", default="data/processed/theia/pyg")

    args = parser.parse_args()

    windows_dir = Path(args.windows)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    window_dirs = sorted([p for p in windows_dir.glob("window_*") if p.is_dir()])

    if not window_dirs:
        raise FileNotFoundError(f"No encontré ventanas en: {windows_dir}")

    summary_rows = []

    for window_dir in window_dirs:
        window_name = window_dir.name
        window_id = int(window_name.split("_")[-1])

        nodes_path = window_dir / "nodes.csv"
        edges_path = window_dir / "edges.csv"

        if not nodes_path.exists() or not edges_path.exists():
            print(f"[WARN] Saltando {window_name}: faltan CSV")
            continue

        nodes_df = pd.read_csv(nodes_path)
        edges_df = pd.read_csv(edges_path)

        x, node_feature_names = build_node_features(nodes_df)
        edge_index, edge_attr, edge_feature_names = build_edge_tensors(edges_df)

        y = torch.zeros(x.size(0), dtype=torch.long)

        graph_dict = {
            "window_id": window_id,
            "x": x,
            "edge_index": edge_index,
            "edge_attr": edge_attr,
            "y": y,
            "node_ids": nodes_df["node_id"].astype(str).tolist(),
            "node_types": nodes_df["node_type"].astype(str).tolist(),
            "node_feature_names": node_feature_names,
            "edge_feature_names": edge_feature_names,
            "num_nodes": int(x.size(0)),
            "num_edges": int(edge_index.size(1)),
        }

        out_path = out_dir / f"{window_name}.pt"
        torch.save(graph_dict, out_path)

        summary_rows.append({
            "window_id": window_id,
            "pt_file": str(out_path),
            "num_nodes": int(x.size(0)),
            "num_edges": int(edge_index.size(1)),
            "node_feature_dim": int(x.size(1)),
            "edge_feature_dim": int(edge_attr.size(1)),
        })

        print(
            f"[OK] {window_name}: "
            f"x={tuple(x.shape)}, "
            f"edge_index={tuple(edge_index.shape)}, "
            f"edge_attr={tuple(edge_attr.shape)}"
        )

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(out_dir / "pyg_dataset_summary.csv", index=False)

    window_ids = sorted(summary_df["window_id"].tolist())
    n = len(window_ids)

    train_end = max(1, int(n * 0.70))
    val_end = max(train_end + 1, int(n * 0.85))

    split = {
        "train": window_ids[:train_end],
        "val": window_ids[train_end:val_end],
        "test": window_ids[val_end:],
    }

    with (out_dir / "split_windows.json").open("w", encoding="utf-8") as f:
        json.dump(split, f, indent=2)

    print("\n[FINALIZADO]")
    print(f"Ventanas convertidas: {len(summary_rows)}")
    print(f"Salida: {out_dir}")
    print(f"Resumen: {out_dir / 'pyg_dataset_summary.csv'}")
    print(f"Split: {out_dir / 'split_windows.json'}")
    print(split)


if __name__ == "__main__":
    main()


    