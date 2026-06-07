#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PASO 01 CORREGIDO - Procesamiento THEIA con nodos globales

Corrige:
1. No reinicia los nodos por batch.
2. Guarda un nodes_global.csv único.
3. Guarda edges por batch.
4. Elimina aristas duplicadas por evento.
5. Ignora UUID nulos 00000000-0000-0000-0000-000000000000.
"""

import argparse
import csv
import gzip
import json
import sys
from pathlib import Path
from collections import defaultdict

try:
    from bson import decode_file_iter
except Exception:
    decode_file_iter = None


INVALID_UUIDS = {
    "",
    "None",
    "nan",
    "00000000-0000-0000-0000-000000000000",
}

SUPPORTED_SUFFIXES = (
    ".json",
    ".jsonl",
    ".json.gz",
    ".jsonl.gz",
    ".bson",
    ".bson.gz",
)

NODE_FIELDNAMES = [
    "node_id",
    "node_type",
    "raw_type",
    "name",
    "pid",
    "ppid",
    "uid",
    "host",
    "timestamp_first_seen",
    "label",
]

EDGE_FIELDNAMES = [
    "edge_id",
    "source_id",
    "target_id",
    "edge_type",
    "event_uuid",
    "timestamp",
    "direction_rule",
    "raw_event_type",
    "label",
]


def is_supported_file(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in SUPPORTED_SUFFIXES)


def unwrap_avro_union(value):
    if isinstance(value, dict) and len(value) == 1:
        key, inner = next(iter(value.items()))
        if (
            key.startswith("com.bbn.tc.schema")
            or key in {"string", "int", "long", "float", "double", "boolean", "bytes", "null"}
        ):
            return inner
    return value


def to_str(value) -> str:
    value = unwrap_avro_union(value)

    if value is None:
        return ""

    if isinstance(value, bytes):
        return value.hex()

    if isinstance(value, (str, int, float, bool)):
        return str(value)

    if isinstance(value, dict):
        for key in [
            "uuid",
            "com.bbn.tc.schema.avro.cdm18.UUID",
            "string",
            "int",
            "long",
            "bytes",
        ]:
            if key in value:
                return to_str(value[key])

        for item in value.values():
            result = to_str(item)
            if result:
                return result

    if isinstance(value, list):
        for item in value:
            result = to_str(item)
            if result:
                return result

    return ""


def valid_uuid(value: str) -> bool:
    return value not in INVALID_UUIDS


def find_key(obj, target_keys):
    if obj is None:
        return None

    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in target_keys:
                return value

        for value in obj.values():
            found = find_key(value, target_keys)
            if found is not None:
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = find_key(value, target_keys)
            if found is not None:
                return found

    return None


def get_record_type_and_body(record):
    if not isinstance(record, dict):
        return "", None

    datum = record.get("datum", record)

    if isinstance(datum, dict):
        for key, value in datum.items():
            if key.startswith("com.bbn.tc.schema"):
                raw_type = key.split(".")[-1]
                return raw_type, unwrap_avro_union(value)

    raw_type = to_str(record.get("type", ""))
    return raw_type, record


def get_uuid(obj) -> str:
    return to_str(find_key(obj, {"uuid"}))


def get_properties(obj) -> dict:
    props = find_key(obj, {"properties"})

    if not isinstance(props, dict):
        return {}

    props = unwrap_avro_union(props)

    if "map" in props and isinstance(props["map"], dict):
        return {str(k): to_str(v) for k, v in props["map"].items()}

    return {str(k): to_str(v) for k, v in props.items()}


def map_node_type(raw_type: str) -> str:
    raw = raw_type.upper()

    if "SUBJECT" in raw:
        return "SUBJECT"
    if "FILE" in raw:
        return "FILE"
    if "NETFLOW" in raw or "SOCKET" in raw:
        return "NETFLOW"
    if "IPC" in raw or "SRCSINK" in raw or "PIPE" in raw:
        return "IPC"
    if "MEMORY" in raw:
        return "MEMORY"
    if "PRINCIPAL" in raw:
        return "PRINCIPAL"

    return "UNKNOWN"


def extract_node(raw_type: str, obj: dict):
    node_type = map_node_type(raw_type)

    if node_type == "UNKNOWN":
        return None

    node_id = get_uuid(obj)

    if not valid_uuid(node_id):
        return None

    props = get_properties(obj)

    name_candidates = [
        find_key(obj, {"cmdLine"}),
        find_key(obj, {"commandLine"}),
        find_key(obj, {"path"}),
        find_key(obj, {"name"}),
        props.get("cmdLine"),
        props.get("commandLine"),
        props.get("path"),
        props.get("name"),
        props.get("filename"),
        props.get("localAddress"),
        props.get("remoteAddress"),
    ]

    name = ""
    for candidate in name_candidates:
        candidate_str = to_str(candidate)
        if candidate_str:
            name = candidate_str
            break

    pid = (
        props.get("pid")
        or props.get("PID")
        or to_str(find_key(obj, {"pid"}))
        or to_str(find_key(obj, {"cid"}))
    )

    ppid = (
        props.get("ppid")
        or props.get("PPID")
        or to_str(find_key(obj, {"ppid"}))
        or to_str(find_key(obj, {"parentPid"}))
    )

    uid = (
        props.get("uid")
        or props.get("UID")
        or to_str(find_key(obj, {"uid"}))
        or to_str(find_key(obj, {"userId"}))
    )

    host = (
        props.get("host")
        or props.get("hostname")
        or to_str(find_key(obj, {"hostId"}))
        or to_str(find_key(obj, {"hostname"}))
    )

    timestamp = (
        to_str(find_key(obj, {"startTimestampNanos"}))
        or to_str(find_key(obj, {"timestampNanos"}))
        or to_str(find_key(obj, {"timestamp"}))
    )

    return {
        "node_id": node_id,
        "node_type": node_type,
        "raw_type": raw_type,
        "name": name,
        "pid": pid,
        "ppid": ppid,
        "uid": uid,
        "host": host,
        "timestamp_first_seen": timestamp,
        "label": 0,
    }


def upsert_node(nodes: dict, node_row: dict):
    node_id = node_row["node_id"]

    if node_id not in nodes:
        nodes[node_id] = node_row
        return

    old = nodes[node_id]

    for key, value in node_row.items():
        if value and not old.get(key):
            old[key] = value

    if old.get("node_type") == "UNKNOWN" and node_row.get("node_type") != "UNKNOWN":
        old["node_type"] = node_row["node_type"]

    if old.get("raw_type") == "UNKNOWN" and node_row.get("raw_type") != "UNKNOWN":
        old["raw_type"] = node_row["raw_type"]


def ref_to_id(value) -> str:
    if value is None:
        return ""

    value = unwrap_avro_union(value)

    if isinstance(value, (str, int)):
        return str(value)

    if isinstance(value, dict):
        for key in [
            "uuid",
            "com.bbn.tc.schema.avro.cdm18.UUID",
            "string",
            "bytes",
        ]:
            if key in value:
                return to_str(value[key])

        return to_str(value)

    return to_str(value)


def orient_edge(event_type: str, subject_id: str, object_id: str):
    event_upper = event_type.upper()

    if "READ" in event_upper or "RECV" in event_upper:
        return object_id, subject_id, "object_to_subject"

    return subject_id, object_id, "subject_to_object"


def extract_edges(raw_type: str, obj: dict):
    if raw_type.upper() != "EVENT":
        return []

    event_uuid = get_uuid(obj)

    if not valid_uuid(event_uuid):
        return []

    event_type = to_str(find_key(obj, {"type"}))

    if not event_type:
        return []

    timestamp = (
        to_str(find_key(obj, {"timestampNanos"}))
        or to_str(find_key(obj, {"timestampMicros"}))
        or to_str(find_key(obj, {"timestamp"}))
    )

    subject_id = ref_to_id(obj.get("subject") or find_key(obj, {"subject"}))

    if not valid_uuid(subject_id):
        return []

    target_candidates = [
        obj.get("predicateObject"),
        obj.get("predicateObject2"),
        obj.get("object"),
    ]

    target_ids = []

    for target in target_candidates:
        target_id = ref_to_id(target)

        if not valid_uuid(target_id):
            continue

        if target_id == subject_id:
            continue

        if target_id not in target_ids:
            target_ids.append(target_id)

    edges = []

    for index, target_id in enumerate(target_ids):
        source_id, final_target_id, direction_rule = orient_edge(
            event_type=event_type,
            subject_id=subject_id,
            object_id=target_id,
        )

        edge_id = f"{event_uuid}_{source_id}_{final_target_id}_{index}"

        edges.append(
            {
                "edge_id": edge_id,
                "source_id": source_id,
                "target_id": final_target_id,
                "edge_type": event_type,
                "event_uuid": event_uuid,
                "timestamp": timestamp,
                "direction_rule": direction_rule,
                "raw_event_type": raw_type,
                "label": 0,
            }
        )

    return edges


def add_placeholder_node(nodes: dict, node_id: str):
    if not valid_uuid(node_id):
        return

    if node_id not in nodes:
        nodes[node_id] = {
            "node_id": node_id,
            "node_type": "UNKNOWN",
            "raw_type": "UNKNOWN",
            "name": "",
            "pid": "",
            "ppid": "",
            "uid": "",
            "host": "",
            "timestamp_first_seen": "",
            "label": 0,
        }


def iter_json_records(path: Path):
    opener = gzip.open if path.name.lower().endswith(".gz") else open

    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def iter_bson_records(path: Path):
    if decode_file_iter is None:
        raise RuntimeError(
            "No se pudo importar bson.decode_file_iter. "
            "Instala pymongo con: pip install pymongo"
        )

    opener = gzip.open if path.name.lower().endswith(".gz") else open

    with opener(path, "rb") as f:
        for record in decode_file_iter(f):
            yield record


def iter_records(path: Path):
    name = path.name.lower()

    if name.endswith(".bson") or name.endswith(".bson.gz"):
        yield from iter_bson_records(path)
    else:
        yield from iter_json_records(path)


def flush_edges_batch(out_dir: Path, batch_index: int, edges: list):
    out_dir.mkdir(parents=True, exist_ok=True)

    edges_path = out_dir / f"edges_batch_{batch_index:06d}.csv"

    with edges_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EDGE_FIELDNAMES)
        writer.writeheader()
        writer.writerows(edges)

    return {
        "batch": batch_index,
        "edges_file": str(edges_path),
        "edges_count": len(edges),
    }


def write_nodes_global(out_dir: Path, nodes: dict):
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = out_dir / "nodes_global.csv"

    with nodes_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NODE_FIELDNAMES)
        writer.writeheader()
        writer.writerows(nodes.values())

    return nodes_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="data/raw/theia")
    parser.add_argument("--out", default="data/processed/theia/v2")
    parser.add_argument("--batch-edges", type=int, default=500_000)

    args = parser.parse_args()

    raw_dir = Path(args.raw)
    out_dir = Path(args.out)

    if not raw_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta: {raw_dir}")

    files = sorted([p for p in raw_dir.rglob("*") if p.is_file() and is_supported_file(p)])

    if not files:
        print(f"[ERROR] No encontré archivos JSON/JSONL/BSON en: {raw_dir}")
        sys.exit(1)

    print(f"[INFO] Archivos encontrados: {len(files)}")
    print(f"[INFO] Salida: {out_dir}")
    print(f"[INFO] Batch edges: {args.batch_edges}")

    global_nodes = {}
    edges_batch = []
    stats = defaultdict(int)
    manifest_rows = []

    batch_index = 1
    total_records = 0
    total_edges = 0

    for file_index, file_path in enumerate(files, start=1):
        print(f"[INFO] Procesando archivo {file_index}/{len(files)}: {file_path}")

        for record in iter_records(file_path):
            total_records += 1

            raw_type, body = get_record_type_and_body(record)

            if not raw_type or body is None:
                stats["UNKNOWN_RECORD"] += 1
                continue

            stats[raw_type] += 1

            node = extract_node(raw_type, body)
            if node:
                upsert_node(global_nodes, node)

            new_edges = extract_edges(raw_type, body)

            for edge in new_edges:
                add_placeholder_node(global_nodes, edge["source_id"])
                add_placeholder_node(global_nodes, edge["target_id"])
                edges_batch.append(edge)

            total_edges += len(new_edges)

            if len(edges_batch) >= args.batch_edges:
                row = flush_edges_batch(out_dir, batch_index, edges_batch)
                manifest_rows.append(row)
                print(f"[OK] Edges batch {batch_index:06d}: {row['edges_count']} aristas")

                batch_index += 1
                edges_batch = []

    if edges_batch:
        row = flush_edges_batch(out_dir, batch_index, edges_batch)
        manifest_rows.append(row)
        print(f"[OK] Edges batch {batch_index:06d}: {row['edges_count']} aristas")

    nodes_path = write_nodes_global(out_dir, global_nodes)

    manifest_path = out_dir / "processing_manifest.csv"

    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["batch", "edges_file", "edges_count"])
        writer.writeheader()
        writer.writerows(manifest_rows)

    stats_path = out_dir / "processing_stats.csv"

    with stats_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["record_type", "count"])
        for key, value in sorted(stats.items()):
            writer.writerow([key, value])

    print("\n[FINALIZADO]")
    print(f"Registros leídos: {total_records}")
    print(f"Aristas extraídas: {total_edges}")
    print(f"Nodos globales: {len(global_nodes)}")
    print(f"Nodes global: {nodes_path}")
    print(f"Manifest: {manifest_path}")
    print(f"Stats: {stats_path}")


if __name__ == "__main__":
    main()