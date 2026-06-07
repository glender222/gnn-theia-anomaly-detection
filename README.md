# gnn-theia-anomaly-detection

Proyecto reproducible para deteccion de anomalias en grafos de procedencia Linux/THEIA.

Este repositorio procesa data cruda THEIA, construye grafos temporales, los convierte a tensores PyTorch/PyG y ejecuta un baseline no supervisado con GraphSAGE + Isolation Forest.

El objetivo principal es que el proyecto se pueda clonar en otra computadora y restaurar todo el dataset con Git + DVC + rclone, sin subir data pesada directamente a Git.

## Que hace este proyecto

El flujo completo es:

```text
raw THEIA
-> v2: nodes_global.csv + edges_batch_*.csv
-> v3: edges compactadas por ventana temporal
-> v4: grafos independientes por ventana
-> pyg: tensores .pt listos para GNN
-> GraphSAGE auto-supervisado
-> embeddings de nodos
-> Isolation Forest
-> anomaly_score por nodo
```

El baseline oficial es no supervisado / auto-supervisado. GraphSAGE aprende embeddings reconstruyendo estructura de aristas y luego Isolation Forest calcula un score de anomalia sobre esos embeddings.

Importante: todavia no se aplican labels de ground truth. Por eso este baseline no entrena un clasificador supervisado.

## Que se implemento en este repo

Se dejo el proyecto organizado para reproducibilidad completa:

- Pipeline oficial numerado en `src/00_...` a `src/06_...`.
- Script `src/06_train_graphsage_iforest.py` para entrenamiento CPU/GPU.
- Config central en `configs/pipeline_theia.yaml`.
- Script para reconstruir desde raw: `scripts/run_pipeline_from_raw.sh`.
- Script para subir el store DVC a Google Drive: `scripts/push_to_drive.sh`.
- Script para restaurar raw + procesados + PyG en otra computadora: `scripts/restore_from_drive.sh`.
- Versionado DVC para raw y procesados:
  - `data/raw/theia.dvc`
  - `data/processed/theia/v2.dvc`
  - `data/processed/theia/v3.dvc`
  - `data/processed/theia/v4.dvc`
  - `data/processed/theia/pyg.dvc`
- `.gitignore` ajustado para ignorar data pesada, resultados, logs y `.dvc_remote/`.
- README actualizado con comandos de uso y verificacion.
- Script antiguo con espacio en el nombre archivado en `scripts/archive/`.

## Estructura del repo

```text
gnn-theia-anomaly-detection/
  configs/
    pipeline_theia.yaml

  src/
    00_list_theia_batches.py
    01_process_theia_global.py
    02_quality_check_theia.py
    03_compact_theia_edges.py
    04_build_graph_windows.py
    05_convert_windows_to_pyg.py
    06_train_graphsage_iforest.py

  scripts/
    run_pipeline_from_raw.sh
    push_to_drive.sh
    restore_from_drive.sh
    start_log.sh
    archive/
      01_process_theia_batches_fallo.py

  data/
    raw/
      theia.dvc
      theia/                         # data pesada, no Git
    processed/
      theia/
        v2.dvc
        v3.dvc
        v4.dvc
        pyg.dvc
        v2/                          # data pesada, no Git
        v3/                          # data pesada, no Git
        v4/                          # data pesada, no Git
        pyg/                         # data pesada, no Git

  results/                           # generado localmente, no Git
  logs/                              # generado localmente, no Git
  .dvc_remote/                       # store DVC local, no Git
```

## Git, DVC y rclone

Git guarda codigo y metadatos:

```text
src/
scripts/
configs/
README.md
requirements.txt
.gitignore
.dvcignore
.dvc/config
*.dvc
```

DVC guarda data pesada:

```bash
dvc add data/raw/theia
dvc add data/processed/theia/v2
dvc add data/processed/theia/v3
dvc add data/processed/theia/v4
dvc add data/processed/theia/pyg
```

No se debe hacer `git add` directo de estas carpetas:

```text
data/raw/theia/
data/processed/theia/v2/
data/processed/theia/v3/
data/processed/theia/v4/
data/processed/theia/pyg/
results/
logs/
.dvc_remote/
```

El store DVC local esta en:

```text
.dvc_remote/
```

Ese store se sincroniza con Google Drive usando rclone:

```text
nubeglenp:gnn-theia-dvc-store
```

## Requisitos

Recomendado:

- Linux o WSL.
- Python 3.10+.
- `rclone` instalado.
- Acceso al remote rclone `nubeglenp:`.
- Espacio en disco suficiente para raw + procesados + store DVC.

Instalacion local:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo apt install -y rclone
```

Si el remote `nubeglenp:` no existe en esa computadora:

```bash
rclone config
```

Crear o reconectar un remote llamado exactamente:

```text
nubeglenp
```

## Como clonar en una computadora nueva

Este es el flujo principal para una maquina nueva.

```bash
git clone <repo>
cd gnn-theia-anomaly-detection
```

Crear y activar entorno:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Instalar rclone:

```bash
sudo apt install -y rclone
```

Configurar Google Drive en rclone si todavia no existe:

```bash
rclone config
```

Verificar que exista el remote:

```bash
rclone listremotes
```

Debe aparecer:

```text
nubeglenp:
```

Restaurar raw + procesados + PyG desde Drive/DVC:

```bash
bash scripts/restore_from_drive.sh
```

Probar entrenamiento CPU:

```bash
python3 src/06_train_graphsage_iforest.py \
  --pyg data/processed/theia/pyg \
  --out results/first_training_test \
  --epochs 2 \
  --hidden 32 \
  --embedding-dim 16 \
  --device cpu
```

## Modo A: restaurar y entrenar

Usar este modo si solo quieres trabajar con el dataset ya procesado.

```bash
git clone <repo>
cd gnn-theia-anomaly-detection
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo apt install -y rclone
bash scripts/restore_from_drive.sh
python3 src/06_train_graphsage_iforest.py --pyg data/processed/theia/pyg --out results/first_training_test --epochs 2 --hidden 32 --embedding-dim 16 --device cpu
```

`restore_from_drive.sh` hace lo siguiente:

```text
1. Verifica que existan rclone y dvc.
2. Verifica acceso a nubeglenp:.
3. Descarga nubeglenp:gnn-theia-dvc-store hacia .dvc_remote.
4. Configura DVC para usar .dvc_remote.
5. Ejecuta dvc pull.
6. Valida que existan raw, v2, v3, v4, pyg y archivos resumen.
```

Al final del restore deben existir:

```text
data/raw/theia/
data/processed/theia/v2/
data/processed/theia/v3/
data/processed/theia/v4/
data/processed/theia/pyg/
data/processed/theia/pyg/pyg_dataset_summary.csv
data/processed/theia/pyg/split_windows.json
```

## Modo B: reconstruir desde raw

Usar este modo si quieres regenerar todo desde data cruda.

Primero restaura raw desde Drive/DVC:

```bash
bash scripts/restore_from_drive.sh
```

Luego reconstruye todo:

```bash
bash scripts/run_pipeline_from_raw.sh
```

El script ejecuta exactamente:

```bash
python3 src/01_process_theia_global.py --raw data/raw/theia --out data/processed/theia/v2 --batch-edges 500000
python3 src/02_quality_check_theia.py --processed data/processed/theia/v2 --out results/theia_qc --top 50
python3 src/03_compact_theia_edges.py --processed data/processed/theia/v2 --out data/processed/theia/v3 --window-seconds 10
python3 src/04_build_graph_windows.py --nodes data/processed/theia/v2/nodes_global.csv --edges data/processed/theia/v3/edges_compacted_w10s.csv --out data/processed/theia/v4/windows --summary data/processed/theia/v4/graph_windows_summary.csv
python3 src/05_convert_windows_to_pyg.py --windows data/processed/theia/v4/windows --out data/processed/theia/pyg
```

Al final valida:

```text
data/raw/theia/
data/processed/theia/v2/nodes_global.csv
data/processed/theia/v3/edges_compacted_w10s.csv
data/processed/theia/v4/graph_windows_summary.csv
data/processed/theia/pyg/pyg_dataset_summary.csv
data/processed/theia/pyg/split_windows.json
```

## Que hace cada etapa

### 00 - listar batches THEIA

```bash
python3 src/00_list_theia_batches.py
```

Lista archivos raw THEIA y genera un manifest simple de batches.

### 01 - procesar raw global

```bash
python3 src/01_process_theia_global.py --raw data/raw/theia --out data/processed/theia/v2 --batch-edges 500000
```

Lee raw THEIA en JSON/JSONL/BSON comprimido, extrae nodos globales y aristas por batch.

Salida principal:

```text
data/processed/theia/v2/nodes_global.csv
data/processed/theia/v2/edges_batch_*.csv
```

### 02 - quality check

```bash
python3 src/02_quality_check_theia.py --processed data/processed/theia/v2 --out results/theia_qc --top 50
```

Calcula estadisticas de nodos, aristas, referencias faltantes, grados y tipos de eventos.

Salida:

```text
results/theia_qc/qc_summary.txt
results/theia_qc/*.csv
```

### 03 - compactar aristas

```bash
python3 src/03_compact_theia_edges.py --processed data/processed/theia/v2 --out data/processed/theia/v3 --window-seconds 10
```

Compacta aristas repetidas por:

```text
window_id + source_id + target_id + edge_type
```

Salida:

```text
data/processed/theia/v3/edges_compacted_w10s.csv
data/processed/theia/v3/window_stats.csv
data/processed/theia/v3/compact_stats.txt
```

### 04 - construir grafos por ventana

```bash
python3 src/04_build_graph_windows.py \
  --nodes data/processed/theia/v2/nodes_global.csv \
  --edges data/processed/theia/v3/edges_compacted_w10s.csv \
  --out data/processed/theia/v4/windows \
  --summary data/processed/theia/v4/graph_windows_summary.csv
```

Genera un grafo independiente por ventana temporal.

Salida:

```text
data/processed/theia/v4/windows/window_000000/nodes.csv
data/processed/theia/v4/windows/window_000000/edges.csv
data/processed/theia/v4/graph_windows_summary.csv
```

### 05 - convertir ventanas a PyG

```bash
python3 src/05_convert_windows_to_pyg.py --windows data/processed/theia/v4/windows --out data/processed/theia/pyg
```

Convierte cada ventana a `.pt` con tensores:

```text
x
edge_index
edge_attr
y
metadata
```

Salida:

```text
data/processed/theia/pyg/window_000000.pt
data/processed/theia/pyg/pyg_dataset_summary.csv
data/processed/theia/pyg/split_windows.json
```

### 06 - entrenar GraphSAGE + Isolation Forest

```bash
python3 src/06_train_graphsage_iforest.py \
  --pyg data/processed/theia/pyg \
  --out results/first_training_test \
  --epochs 2 \
  --hidden 32 \
  --embedding-dim 16 \
  --device cpu
```

Entrena GraphSAGE auto-supervisado por reconstruccion de aristas, extrae embeddings de nodos y ajusta Isolation Forest.

Salida:

```text
results/first_training_test/graphsage_encoder.pt
results/first_training_test/isolation_forest.joblib
results/first_training_test/node_embeddings.npy
results/first_training_test/node_anomaly_scores.csv
results/first_training_test/training_summary.json
```

## Subir cambios de data a Drive

Cuando regeneres raw/procesados/PyG y quieras subir el store DVC:

```bash
bash scripts/push_to_drive.sh
```

El script hace:

```text
1. Verifica dvc y rclone.
2. Configura DVC con .dvc_remote.
3. Ejecuta dvc push.
4. Verifica nubeglenp:.
5. Ejecuta rclone sync hacia Google Drive.
```

El comando rclone usado es:

```bash
rclone sync .dvc_remote nubeglenp:gnn-theia-dvc-store --progress --transfers 8 --checkers 16
```

## Verificacion antes de hacer commit

Antes de hacer commit, revisar que no haya data pesada en Git.

```bash
git status --short --untracked-files=all
find data/raw -maxdepth 2 -name "*.dvc" -print
find data/processed/theia -maxdepth 1 -name "*.dvc" -print
git check-ignore -v data/raw/theia/ta1-theia-1-e5-official-1.json.gz
git check-ignore -v data/processed/theia/pyg/window_000000.pt
git check-ignore -v data/processed/theia/v4/windows/window_000000/nodes.csv
```

Resultado esperado:

```text
data/raw/theia.dvc
data/processed/theia/v2.dvc
data/processed/theia/v3.dvc
data/processed/theia/v4.dvc
data/processed/theia/pyg.dvc
```

Y los archivos pesados deben aparecer ignorados por `.gitignore`.

## Archivos que si debes subir con Git

Ejemplo de `git add` seguro:

```bash
git add \
  .dvc/config \
  .dvcignore \
  .gitignore \
  README.md \
  requirements.txt \
  configs/pipeline_theia.yaml \
  data/raw/theia.dvc \
  data/processed/theia/v2.dvc \
  data/processed/theia/v3.dvc \
  data/processed/theia/v4.dvc \
  data/processed/theia/pyg.dvc \
  scripts/ \
  src/
```

No usar:

```bash
git add data/raw/theia/
git add data/processed/theia/v2/
git add data/processed/theia/v3/
git add data/processed/theia/v4/
git add data/processed/theia/pyg/
git add results/
git add logs/
git add .dvc_remote/
```

## Validacion local realizada

En esta maquina se valido el flujo completo:

```bash
bash scripts/run_pipeline_from_raw.sh
python3 src/06_train_graphsage_iforest.py --pyg data/processed/theia/pyg --out results/first_training_test --epochs 2 --hidden 32 --embedding-dim 16 --device cpu
dvc status
```

Resumen observado:

```text
Registros THEIA leidos: 10,000,000
Aristas extraidas v2: 9,991,109
Nodos globales v2: 16,329
Aristas compactadas v3: 39,969
Ventanas v4/PyG: 34
```

El entrenamiento CPU de 2 epocas se ejecuto correctamente y genero resultados en:

```text
results/first_training_test/
```

## Notas importantes

- `push_to_drive.sh` necesita que `nubeglenp:` exista en rclone.
- Si el script se ejecuta en una terminal interactiva y no existe `nubeglenp:`, abrira `rclone config`.
- Si se ejecuta en una shell no interactiva sin `nubeglenp:`, fallara con un mensaje claro.
- `.dvc_remote/` es local y no debe ir a Git.
- `results/` y `logs/` son generados localmente y no deben ir a Git.
- Si se reconstruye el pipeline desde raw y cambian los outputs, volver a ejecutar los `dvc add` correspondientes antes de subir el store.
