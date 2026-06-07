from pathlib import Path
import re
import csv

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BASE_DIR / "data" / "raw" / "theia"
OUT_FILE = BASE_DIR / "data" / "processed" / "batches_manifest.csv"

OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

pattern = re.compile(r"^(?P<batch>.+?\.bin)(?:\.\d+)?\.gz$")

batches = {}

for file in RAW_DIR.glob("*.gz"):
    match = pattern.match(file.name)
    if not match:
        continue

    batch = match.group("batch")
    batches.setdefault(batch, {"files": [], "size": 0})
    batches[batch]["files"].append(file.name)
    batches[batch]["size"] += file.stat().st_size

rows = []

for batch, info in batches.items():
    rows.append({
        "batch": batch,
        "num_files": len(info["files"]),
        "size_gb": round(info["size"] / (1024 ** 3), 3),
        "files": "|".join(sorted(info["files"]))
    })

rows = sorted(rows, key=lambda x: (x["batch"]))

with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["batch", "num_files", "size_gb", "files"])
    writer.writeheader()
    writer.writerows(rows)

print(f"Total batches encontrados: {len(rows)}")
print(f"Manifest guardado en: {OUT_FILE}")

print("\nPrimeros batches:")
for row in rows[:20]:
    print(f"{row['batch']} | archivos={row['num_files']} | tamaño={row['size_gb']} GB")
