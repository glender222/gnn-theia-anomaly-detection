#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PASO 06 - Baseline oficial: GraphSAGE + Isolation Forest

Entrena un encoder GraphSAGE auto-supervisado con reconstruccion de aristas,
extrae embeddings de nodos y ajusta Isolation Forest sobre embeddings de train.
"""

import argparse
import csv
import json
from pathlib import Path

import joblib
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.ensemble import IsolationForest
from torch_geometric.nn import SAGEConv
from torch_geometric.utils import negative_sampling


class GraphSAGEEncoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, embedding_dim: int, dropout: float):
        super().__init__()
        self.conv1 = SAGEConv(input_dim, hidden_dim)
        self.conv2 = SAGEConv(hidden_dim, embedding_dim)
        self.dropout = dropout

    def forward(self, x, edge_index):
        x = self.conv1(x, edge_index)
        x = F.relu(x)
        x = F.dropout(x, p=self.dropout, training=self.training)
        return self.conv2(x, edge_index)


def load_graph(path: Path, device: torch.device):
    try:
        graph = torch.load(path, map_location=device, weights_only=False)
    except TypeError:
        graph = torch.load(path, map_location=device)

    graph["x"] = graph["x"].to(device)
    graph["edge_index"] = graph["edge_index"].to(device)
    return graph


def load_split(pyg_dir: Path):
    split_path = pyg_dir / "split_windows.json"
    if split_path.exists():
        with split_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    window_ids = sorted(
        int(path.stem.split("_")[-1])
        for path in pyg_dir.glob("window_*.pt")
    )
    n = len(window_ids)
    train_end = max(1, int(n * 0.70))
    val_end = max(train_end + 1, int(n * 0.85))
    return {
        "train": window_ids[:train_end],
        "val": window_ids[train_end:val_end],
        "test": window_ids[val_end:],
    }


def graph_path_for_window(pyg_dir: Path, window_id: int) -> Path:
    return pyg_dir / f"window_{window_id:06d}.pt"


def edge_logits(z, edge_index):
    src, dst = edge_index
    return (z[src] * z[dst]).sum(dim=1)


def reconstruction_loss(z, edge_index, num_nodes):
    if edge_index.numel() == 0:
        return z.sum() * 0.0

    pos_logits = edge_logits(z, edge_index)
    neg_edge_index = negative_sampling(
        edge_index=edge_index,
        num_nodes=num_nodes,
        num_neg_samples=edge_index.size(1),
        method="sparse",
    )

    if neg_edge_index.numel() == 0:
        return F.binary_cross_entropy_with_logits(
            pos_logits,
            torch.ones_like(pos_logits),
        )

    neg_logits = edge_logits(z, neg_edge_index)
    logits = torch.cat([pos_logits, neg_logits], dim=0)
    labels = torch.cat([
        torch.ones_like(pos_logits),
        torch.zeros_like(neg_logits),
    ], dim=0)
    return F.binary_cross_entropy_with_logits(logits, labels)


def train_encoder(model, graphs, epochs: int, lr: float):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    history = []

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        used_graphs = 0

        for graph in graphs:
            x = graph["x"]
            edge_index = graph["edge_index"]

            if x.size(0) == 0 or edge_index.numel() == 0:
                continue

            optimizer.zero_grad()
            z = model(x, edge_index)
            loss = reconstruction_loss(z, edge_index, x.size(0))
            loss.backward()
            optimizer.step()

            total_loss += float(loss.detach().cpu())
            used_graphs += 1

        avg_loss = total_loss / used_graphs if used_graphs else 0.0
        history.append({"epoch": epoch, "loss": avg_loss, "graphs": used_graphs})
        print(f"[TRAIN] epoch={epoch:03d} loss={avg_loss:.6f} graphs={used_graphs}")

    return history


def collect_embeddings(model, pyg_dir: Path, window_ids, device: torch.device):
    model.eval()
    embeddings = []
    rows = []

    with torch.no_grad():
        for window_id in window_ids:
            path = graph_path_for_window(pyg_dir, int(window_id))
            if not path.exists():
                print(f"[WARN] Missing window file: {path}")
                continue

            graph = load_graph(path, device)
            x = graph["x"]
            edge_index = graph["edge_index"]

            if x.size(0) == 0:
                continue

            z = model(x, edge_index).detach().cpu().numpy()
            embeddings.append(z)

            node_ids = graph.get("node_ids", [""] * z.shape[0])
            node_types = graph.get("node_types", [""] * z.shape[0])

            for local_id in range(z.shape[0]):
                rows.append({
                    "window_id": int(window_id),
                    "local_id": local_id,
                    "node_id": str(node_ids[local_id]) if local_id < len(node_ids) else "",
                    "node_type": str(node_types[local_id]) if local_id < len(node_types) else "",
                })

    if not embeddings:
        return np.empty((0, 0), dtype=np.float32), rows

    return np.vstack(embeddings).astype(np.float32), rows


def write_scores(path: Path, rows, embeddings, scores):
    fieldnames = [
        "window_id",
        "local_id",
        "node_id",
        "node_type",
        "anomaly_score",
    ] + [f"embedding_{i}" for i in range(embeddings.shape[1])]

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row, embedding, score in zip(rows, embeddings, scores):
            out = dict(row)
            out["anomaly_score"] = float(score)
            for idx, value in enumerate(embedding):
                out[f"embedding_{idx}"] = float(value)
            writer.writerow(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyg", default="data/processed/theia/pyg")
    parser.add_argument("--out", default="results/first_training_test")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--hidden", type=int, default=32)
    parser.add_argument("--embedding-dim", type=int, default=16)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--contamination", default="auto")
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    pyg_dir = Path(args.pyg)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not pyg_dir.exists():
        raise FileNotFoundError(f"No existe el dataset PyG: {pyg_dir}")

    device = torch.device(args.device)
    split = load_split(pyg_dir)
    train_ids = [int(x) for x in split.get("train", [])]
    all_ids = sorted({int(x) for values in split.values() for x in values})

    if not train_ids:
        raise RuntimeError("No hay ventanas de train en split_windows.json")
    if not all_ids:
        raise RuntimeError("No hay ventanas PyG disponibles")

    print(f"[INFO] PyG dir: {pyg_dir}")
    print(f"[INFO] Train windows: {len(train_ids)}")
    print(f"[INFO] All windows: {len(all_ids)}")
    print(f"[INFO] Device: {device}")

    train_graphs = [
        load_graph(graph_path_for_window(pyg_dir, window_id), device)
        for window_id in train_ids
        if graph_path_for_window(pyg_dir, window_id).exists()
    ]

    if not train_graphs:
        raise RuntimeError("No se pudieron cargar grafos de train")

    input_dim = int(train_graphs[0]["x"].size(1))
    model = GraphSAGEEncoder(
        input_dim=input_dim,
        hidden_dim=args.hidden,
        embedding_dim=args.embedding_dim,
        dropout=args.dropout,
    ).to(device)

    history = train_encoder(model, train_graphs, epochs=args.epochs, lr=args.lr)

    train_embeddings, train_rows = collect_embeddings(model, pyg_dir, train_ids, device)
    all_embeddings, all_rows = collect_embeddings(model, pyg_dir, all_ids, device)

    if train_embeddings.size == 0 or all_embeddings.size == 0:
        raise RuntimeError("No se generaron embeddings")

    iforest = IsolationForest(
        contamination=args.contamination,
        random_state=args.random_state,
        n_jobs=-1,
    )
    iforest.fit(train_embeddings)

    anomaly_scores = -iforest.decision_function(all_embeddings)
    predictions = iforest.predict(all_embeddings)

    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "input_dim": input_dim,
            "hidden": args.hidden,
            "embedding_dim": args.embedding_dim,
            "dropout": args.dropout,
        },
        out_dir / "graphsage_encoder.pt",
    )
    joblib.dump(iforest, out_dir / "isolation_forest.joblib")
    np.save(out_dir / "node_embeddings.npy", all_embeddings)
    write_scores(out_dir / "node_anomaly_scores.csv", all_rows, all_embeddings, anomaly_scores)

    summary = {
        "pyg_dir": str(pyg_dir),
        "epochs": args.epochs,
        "hidden": args.hidden,
        "embedding_dim": args.embedding_dim,
        "input_dim": input_dim,
        "train_windows": len(train_ids),
        "all_windows": len(all_ids),
        "train_nodes": int(train_embeddings.shape[0]),
        "all_nodes": int(all_embeddings.shape[0]),
        "anomaly_score_min": float(np.min(anomaly_scores)),
        "anomaly_score_max": float(np.max(anomaly_scores)),
        "predicted_outliers": int(np.sum(predictions == -1)),
        "history": history,
    }

    with (out_dir / "training_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n[FINALIZADO]")
    print(f"Encoder: {out_dir / 'graphsage_encoder.pt'}")
    print(f"Isolation Forest: {out_dir / 'isolation_forest.joblib'}")
    print(f"Scores: {out_dir / 'node_anomaly_scores.csv'}")
    print(f"Summary: {out_dir / 'training_summary.json'}")


if __name__ == "__main__":
    main()
