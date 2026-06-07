#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PASO 01 - Procesamiento inicial de THEIA / DARPA TC

Objetivo:
- Leer archivos crudos JSON / JSONL / BSON / GZ.
- Extraer entidades como nodos: procesos, archivos, sockets, memoria/IPC.
- Extraer eventos como aristas: READ, WRITE, EXECUTE, CONNECT, FORK, etc.
- Guardar resultados por lotes para no reventar RAM.

Entrada esperada:
    data/raw/theia/

Salida:
    data/processed/theia/batches/
        batch_000001_nodes.csv
        batch_000001_edges.csv
        batch_000002_nodes.csv
        batch_000002_edges.csv
        processing_manifest.csv

Este script NO hace:
- selección de features
- entrenamiento
- GraphSAGE
- Isolation Forest
- validación final
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
    """
    DARPA TC suele venir con estructuras tipo Avro/CDM.
    Algunas claves tienen forma:
    {"com.bbn.tc.schema.avro.cdm18.Subject": {...}}
    {"string": "..."}
    {"long": 123}
    """
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
        priority_keys = [
            "uuid",
            "string",
            "int",
            "long",
            "bytes",
            "com.bbn.tc.schema.avro.cdm18.UUID",
        ]

        for key in priority_keys:
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


def find_key(obj, target_keys):
    """
    Búsqueda recursiva segura de una clave.
    target_keys debe ser set, por ejemplo: {"uuid", "pid"}.
    """
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
    """
    Extrae el tipo CDM del registro:
    Subject, Event, FileObject, NetFlowObject, SrcSinkObject, etc.
    """
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
    raw_type_upper = raw_type.upper()

    if "SUBJECT" in raw_type_upper:
        return "SUBJECT"

    if "FILE" in raw_type_upper:
        return "FILE"

    if "NETFLOW" in raw_type_upper or "SOCKET" in raw_type_upper:
        return "NETFLOW"

    if "SRCSINK" in raw_type_upper or "PIPE" in raw_type_upper or "MEMORY" in raw_type_upper:
        return "MEMORY"

    if "PRINCIPAL" in raw_type_upper:
        return "PRINCIPAL"

    return "UNKNOWN"


def extract_node(raw_type: str, obj: dict):
    node_type = map_node_type(raw_type)

    if node_type == "UNKNOWN":
        return None

    node_id = get_uuid(obj)
    if not node_id:
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


def ref_to_id(value) -> str:
    """
    Convierte referencias CDM a IDs de nodo.
    Ejemplo:
    {"com.bbn.tc.schema.avro.cdm18.UUID": "..."}
    """
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
    """
    Orientación causal básica.

    Para READ/RECV:
        objeto -> proceso
        porque la información fluye hacia el proceso.

    Para WRITE/SEND/CONNECT/EXECUTE:
        proceso -> objeto
    """
    event_upper = event_type.upper()

    if "READ" in event_upper or "RECV" in event_upper:
        return object_id, subject_id, "object_to_subject"

    return subject_id, object_id, "subject_to_object"


def extract_edges(raw_type: str, obj: dict):
    if raw_type.upper() != "EVENT":
        return []

    event_uuid = get_uuid(obj)
    event_type = to_str(find_key(obj, {"type"}))
    timestamp = (
        to_str(find_key(obj, {"timestampNanos"}))
        or to_str(find_key(obj, {"timestampMicros"}))
        or to_str(find_key(obj, {"timestamp"}))
    )

    subject_id = ref_to_id(obj.get("subject") or find_key(obj, {"subject"}))

    possible_targets = [
        obj.get("predicateObject"),
        obj.get("predicateObject2"),
        obj.get("object"),
        find_key(obj, {"predicateObject"}),
        find_key(obj, {"predicateObject2"}),
    ]

    edges = []

    for target in possible_targets:
        target_id = ref_to_id(target)

        if not subject_id or not target_id:
            continue

        if subject_id == target_id:
            continue

        source_id, final_target_id, direction_rule = orient_edge(
            event_type=event_type,
            subject_id=subject_id,
            object_id=target_id,
        )

        edge_id = f"{event_uuid}_{source_id}_{final_target_id}_{len(edges)}"

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


def iter_json_records(path: Path):
    opener = gzip.open if path.name.lower().endswith(".gz") else open

    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                if line_number == 1:
                    print(
                        f"[WARN] {path} no parece JSONL. "
                        "Este script espera JSON por línea.",
                        file=sys.stderr,
                    )
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


def add_placeholder_node(nodes: dict, node_id: str):
    if not node_id:
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


def flush_batch(out_dir: Path, batch_index: int, nodes: dict, edges: list):
    if not nodes and not edges:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = out_dir / f"batch_{batch_index:06d}_nodes.csv"
    edges_path = out_dir / f"batch_{batch_index:06d}_edges.csv"

    with nodes_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NODE_FIELDNAMES)
        writer.writeheader()
        writer.writerows(nodes.values())

    with edges_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EDGE_FIELDNAMES)
        writer.writeheader()
        writer.writerows(edges)

    return {
        "batch": batch_index,
        "nodes_file": str(nodes_path),
        "edges_file": str(edges_path),
        "nodes_count": len(nodes),
        "edges_count": len(edges),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="data/raw/theia", help="Carpeta con data cruda THEIA")
    parser.add_argument("--out", default="data/processed/theia/batches", help="Carpeta de salida")
    parser.add_argument("--batch-events", type=int, default=500_000, help="Cantidad de aristas por batch")
    args = parser.parse_args()

    raw_dir = Path(args.raw)
    out_dir = Path(args.out)

    if not raw_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta: {raw_dir}")

    files = sorted([p for p in raw_dir.rglob("*") if p.is_file() and is_supported_file(p)])

    if not files:
        print(f"[ERROR] No encontré archivos JSON/JSONL/BSON en: {raw_dir}")
        print("Revisa con:")
        print(f"find {raw_dir} -type f | head -50")
        sys.exit(1)

    print(f"[INFO] Archivos encontrados: {len(files)}")
    print(f"[INFO] Salida: {out_dir}")
    print(f"[INFO] Batch events: {args.batch_events}")

    nodes = {}
    edges = []
    stats = defaultdict(int)
    manifest_rows = []

    batch_index = 1
    total_records = 0
    total_edges = 0

    for file_index, file_path in enumerate(files, start=1):
        print(f"[INFO] Procesando archivo {file_index}/{len(files)}: {file_path}")

        try:
            for record in iter_records(file_path):
                total_records += 1

                raw_type, body = get_record_type_and_body(record)

                if not raw_type or body is None:
                    stats["UNKNOWN_RECORD"] += 1
                    continue

                stats[raw_type] += 1

                node = extract_node(raw_type, body)
                if node:
                    upsert_node(nodes, node)

                new_edges = extract_edges(raw_type, body)
                for edge in new_edges:
                    add_placeholder_node(nodes, edge["source_id"])
                    add_placeholder_node(nodes, edge["target_id"])
                    edges.append(edge)

                total_edges += len(new_edges)

                if len(edges) >= args.batch_events:
                    row = flush_batch(out_dir, batch_index, nodes, edges)
                    if row:
                        manifest_rows.append(row)
                        print(
                            f"[OK] Batch {batch_index:06d}: "
                            f"{row['nodes_count']} nodos, {row['edges_count']} aristas"
                        )

                    batch_index += 1
                    nodes = {}
                    edges = []

        except Exception as e:
            print(f"[ERROR] Falló el archivo {file_path}: {e}", file=sys.stderr)
            continue

    row = flush_batch(out_dir, batch_index, nodes, edges)
    if row:
        manifest_rows.append(row)
        print(
            f"[OK] Batch {batch_index:06d}: "
            f"{row['nodes_count']} nodos, {row['edges_count']} aristas"
        )

    manifest_path = out_dir / "processing_manifest.csv"

    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["batch", "nodes_file", "edges_file", "nodes_count", "edges_count"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
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
    print(f"Manifest: {manifest_path}")
    print(f"Stats: {stats_path}")


if __name__ == "__main__":
    main()