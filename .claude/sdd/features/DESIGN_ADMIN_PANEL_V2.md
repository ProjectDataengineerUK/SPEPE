# DESIGN: Admin Panel v2 — Sentinel Real-Time + Monitoramento Completo

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | ADMIN_PANEL_V2 |
| **Date** | 2026-05-08 |
| **Author** | design-agent |
| **DEFINE** | `.claude/sdd/features/DEFINE_ADMIN_PANEL_V2.md` |
| **Status** | Ready for Build |
| **Confidence** | 0.95 |

---

## Architecture Overview

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                       SPEPE ADMIN PANEL v2 — ARCHITECTURE                    │
├──────────────────────────────────────────────────────────────────────────────┤
│   ┌──────────────────┐        ┌──────────────────────────────────────────┐  │
│   │  Cloud Run Jobs  │ event  │       Pub/Sub: spepe-sentinel-events     │  │
│   │  (20 ingest +    ├───────►│  (NEW — created in pubsub.tf)            │  │
│   │   transform/gold)│        │  + spepe-drift-detected (existing)       │  │
│   └──────────────────┘        └────────────┬─────────────────────────────┘  │
│                                            │ pull subscription              │
│                                            ▼                                 │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │           ui/dashboard_api.py (FastAPI on Cloud Run)                │   │
│   │   GET /admin/api/sentinel/stream  (StreamingResponse SSE)           │   │
│   │     async generator yields:                                          │   │
│   │       event: heartbeat            every 30s                          │   │
│   │       event: table_freshness      every 15s (BQ poll)                │   │
│   │       event: cost_updated         every 60s (BQ JOBS poll)           │   │
│   │       event: job_status_changed   immediate (Pub/Sub queue)          │   │
│   │       event: agent_execution_done immediate (in-proc hook)           │   │
│   │                                                                      │   │
│   │   ui/sentinel_queries.py (NEW)                                       │   │
│   │      ├─ query_gold_storage()       INFORMATION_SCHEMA.TABLE_STORAGE  │   │
│   │      ├─ query_silver_storage()     INFORMATION_SCHEMA.TABLE_STORAGE  │   │
│   │      ├─ query_views_existence()    INFORMATION_SCHEMA.VIEWS          │   │
│   │      ├─ query_jobs_executions()    Cloud Run executions.list()       │   │
│   │      ├─ query_mlops_metrics()      spepe_mlops.model_evaluations     │   │
│   │      ├─ query_bias_metrics()       spepe_mlops.bias_metrics          │   │
│   │      ├─ query_costs()              INFORMATION_SCHEMA.JOBS_BY_PROJECT│   │
│   │      ├─ query_agents_telemetry()   Firestore + audit jsonl tail      │   │
│   │      ├─ compute_dq_score()         completude+unicidade per table    │   │
│   │      └─ compute_maturity_score()   DataOps/MLOps/LLMOps 0-100        │   │
│   │                                                                      │   │
│   │   ui/sentinel_pubsub.py (NEW)                                        │   │
│   │      └─ consume_sentinel_pubsub()  async Pub/Sub pull loop           │   │
│   │                                                                      │   │
│   │   Fallback: GET /admin/api/sentinel/status  (existing — keep)        │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                            │ EventSource SSE                 │
│                                            ▼                                 │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │              ui/static/spepe-app.html  (Browser)                    │   │
│   │   const es = new EventSource('/admin/api/sentinel/stream')          │   │
│   │   Tabs (8): Sentinel | DataOps | ValidDados | ValidModelo |         │   │
│   │             LLMOps  | Custos  | Maturidade  | Arquitetura            │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│   Data sources:                                                              │
│     • spepe-prod.spepe_gold.INFORMATION_SCHEMA.TABLE_STORAGE   (free)       │
│     • spepe-prod.spepe_silver.INFORMATION_SCHEMA.TABLE_STORAGE (free)       │
│     • spepe-prod.spepe_gold.INFORMATION_SCHEMA.VIEWS           (free)       │
│     • region-southamerica-east1.INFORMATION_SCHEMA.JOBS_BY_PROJECT          │
│     • spepe_mlops.model_evaluations | bias_metrics                           │
│     • Cloud Run JobsClient / ExecutionsClient (run_v2)                       │
│     • Firestore spepe_sessions + audit jsonl tail                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Components

| Component | Purpose | Technology |
|-----------|---------|------------|
| `/admin/api/sentinel/stream` | SSE endpoint — push real-time events ao browser admin | FastAPI `StreamingResponse` + `asyncio.Queue` |
| `ui/sentinel_queries.py` | Módulo de helpers tipados por fonte de dados; DQ + maturidade | google-cloud-bigquery, google-cloud-run-v2, google-cloud-firestore |
| `ui/sentinel_pubsub.py` | Task asyncio — pull `spepe-sentinel-events`, broadcast para queues | google-cloud-pubsub |
| EventSource client (`spepe-app.html`) | 8 tabs + Chart.js radar/line/bar + reconnect automático | EventSource API + Chart.js |
| Pub/Sub topic `spepe-sentinel-events` | Canal para jobs/hooks publicarem mudanças de estado | Pub/Sub (Terraform) |
| Maturity scorer | Score ponderado 0-100 DataOps/MLOps/LLMOps | Python puro |

---

## Key Decisions (ADRs inline)

### Decision 1: SSE over WebSocket / polling

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-05-08 |

**Context:** Admin precisa de atualizações em tempo real (≤ 5s após mudança de estado de job).

**Choice:** SSE via `StreamingResponse` (`text/event-stream`) + fallback polling 30s em `/status`.

**Rationale:**
- Direção server → client apenas; `EventSource` tem reconexão automática nativa.
- Cloud Run suporta streaming até 60min por conexão.
- Polling 5s multiplicaria chamadas BQ por admin × seção.
- WebSocket atual em `/ws/sentinel` é frágil sob Cloud Run LB.

**Alternatives Rejected:**
1. WebSocket — bidirecional desnecessário; reconexão mais fraca.
2. Polling 5s — O(admins × sections) calls BQ vs O(1) com SSE shared poll.

**Consequences:** Uma slot de concorrência Cloud Run por admin (máx ~5 admins, bem abaixo de 80 concurrency).

---

### Decision 2: BQ INFORMATION_SCHEMA direto vs populate sentinel_state

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-05-08 |

**Choice:** Query `INFORMATION_SCHEMA.*` direta. Manter `/status` legado inalterado.

**Rationale:** INFORMATION_SCHEMA é gratuito (sem bytes escaneados). Job de populate adicionaria infra (viola constraint DEFINE). BQ direto é sempre fresco.

**Consequences:** `sentinel_state` se torna deprecável na Fase 2.

---

### Decision 3: Novo topic `spepe-sentinel-events` (não reutilizar `spepe-drift-detected`)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-05-08 |

**Choice:** Criar `spepe-sentinel-events` com subscription PULL consumida pelo `dashboard_api.py`.

**Rationale:** `spepe-drift-detected` tem push subscription para retrain via Eventarc — reutilizá-lo arriscaria retrain espúrio. Eventos sentinel são frequentes e efêmeros; eventos de drift são raros e consequentes — SLOs diferentes.

**Consequences:** Um topic adicional Pub/Sub (gratuito abaixo de 10GB/mês).

---

### Decision 4: Maturity score = aggregador ponderado Python puro

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-05-08 |

**Choice:** Fórmula explícita em Python (ver Pattern 7). Sem ML.

**Rationale:** Transparente, debuggável, sem dependência de artefato de modelo, compatível com AT-013.

---

## File Manifest

| # | File | Action | Purpose | Dependencies |
|---|------|--------|---------|--------------|
| 1 | `ui/sentinel_queries.py` | **Create** | Funções tipadas por fonte de dados; DQ + maturidade | None |
| 2 | `ui/sentinel_pubsub.py` | **Create** | Async Pub/Sub pull + broadcast | None |
| 3 | `ui/dashboard_api.py` | **Modify** | SSE endpoint + lifespan pollers + rotas por aba | 1, 2 |
| 4 | `ui/static/spepe-app.html` | **Modify** | 8 abas admin + EventSource + Chart.js radar/bar/line | 3 |
| 5 | `infra/terraform/pubsub.tf` | **Modify** | Topic + sub pull + IAM publisher/subscriber | None |
| 6 | `tests/test_sentinel_queries.py` | **Create** | Unit tests DQ score, maturity, status rules (BQ mockado) | 1 |
| 7 | `tests/test_sentinel_stream.py` | **Create** | Integration test SSE endpoint via FastAPI TestClient | 3 |

**Total: 7 arquivos (2 novos módulos, 2 novos testes, 3 modificados)**

---

## Code Patterns

### Pattern 1 — SSE endpoint com fan-out asyncio.Queue

```python
# ui/dashboard_api.py — adicionar junto às rotas admin existentes
import asyncio
import json as _json
import time
from fastapi.responses import StreamingResponse

_sentinel_subscribers: list[asyncio.Queue] = []


async def _sentinel_broadcast(event_type: str, payload: dict) -> None:
    msg = (event_type, payload)
    dead = []
    for q in _sentinel_subscribers:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _sentinel_subscribers.remove(q)


@app.get("/admin/api/sentinel/stream", dependencies=[Depends(require_auth)])
async def admin_sentinel_stream() -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue(maxsize=128)
    _sentinel_subscribers.append(queue)

    async def event_generator():
        snapshot = await _build_full_snapshot()
        yield _format_sse("snapshot", snapshot)
        try:
            while True:
                try:
                    event_type, payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield _format_sse(event_type, payload)
                except asyncio.TimeoutError:
                    yield _format_sse("heartbeat", {"ts": time.time()})
        except asyncio.CancelledError:
            pass
        finally:
            if queue in _sentinel_subscribers:
                _sentinel_subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _format_sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {_json.dumps(data, default=str)}\n\n"


async def _build_full_snapshot() -> dict:
    from ui.sentinel_queries import (
        query_gold_storage, query_silver_storage, query_views_existence,
        query_jobs_executions, query_mlops_metrics, query_costs,
        query_agents_telemetry, compute_maturity_score,
    )
    gold = await asyncio.to_thread(query_gold_storage)
    silver = await asyncio.to_thread(query_silver_storage)
    views = await asyncio.to_thread(query_views_existence)
    jobs = await asyncio.to_thread(query_jobs_executions)
    mlops = await asyncio.to_thread(query_mlops_metrics)
    costs = await asyncio.to_thread(query_costs)
    agents = await asyncio.to_thread(query_agents_telemetry)
    maturity = compute_maturity_score(jobs, gold, silver, mlops, agents)
    return {
        "dataops": {"gold": gold, "silver": silver, "views": views},
        "jobs": jobs, "mlops": mlops, "llmops": agents,
        "costs": costs, "maturity": maturity,
        "ts": time.time(),
    }
```

### Pattern 2 — Background pollers (lifespan)

```python
# ui/dashboard_api.py — modificar lifespan existente
@asynccontextmanager
async def lifespan(application: FastAPI):
    setup_logging(log_level=settings.log_level, console_log_level="WARNING")
    poller_tasks = [
        asyncio.create_task(_poll_table_freshness(interval=15)),
        asyncio.create_task(_poll_costs(interval=60)),
        asyncio.create_task(_consume_sentinel_pubsub()),
    ]
    try:
        yield
    finally:
        for t in poller_tasks:
            t.cancel()


async def _poll_table_freshness(interval: int) -> None:
    from ui.sentinel_queries import query_gold_storage, query_silver_storage, query_views_existence
    while True:
        try:
            gold = await asyncio.to_thread(query_gold_storage)
            silver = await asyncio.to_thread(query_silver_storage)
            views = await asyncio.to_thread(query_views_existence)
            await _sentinel_broadcast(
                "table_freshness_updated",
                {"gold": gold, "silver": silver, "views": views},
            )
        except Exception as exc:
            logger.warning("table freshness poll failed: %s", exc)
        await asyncio.sleep(interval)


async def _poll_costs(interval: int) -> None:
    from ui.sentinel_queries import query_costs
    while True:
        try:
            costs = await asyncio.to_thread(query_costs)
            await _sentinel_broadcast("cost_updated", costs)
        except Exception as exc:
            logger.warning("cost poll failed: %s", exc)
        await asyncio.sleep(interval)


async def _consume_sentinel_pubsub() -> None:
    from ui.sentinel_pubsub import consume_sentinel_pubsub
    await consume_sentinel_pubsub(_sentinel_broadcast)
```

### Pattern 3 — INFORMATION_SCHEMA queries (gratuitas)

```python
# ui/sentinel_queries.py
from __future__ import annotations
from datetime import datetime, timezone
import logging
from typing import Any

from google.cloud import bigquery
from config.settings import settings

logger = logging.getLogger("spepe.sentinel.queries")

_PROJECT = settings.gcp_project_id or "spepe-prod"
_GOLD = settings.bigquery_dataset_gold
_SILVER = settings.bigquery_dataset_silver
_MLOPS = settings.bigquery_dataset_mlops

GOLD_TABLES: list[str] = [
    "fact_municipio_eleicao", "fact_secao_eleicao", "fact_candidato_eleicao",
    "fact_municipio_candidato_eleicao", "fact_ibge_municipio",
    "fact_seguranca_municipio", "fact_saude_municipio", "fact_economico_municipio",
    "fact_pesquisa", "fact_social_municipio", "fact_transferencias_sociais",
    "fact_emendas", "fact_emendas_municipio", "fact_sancoes",
    "fact_endividamento_municipio", "fact_camara_senado", "fact_candidatos_discovery",
]

SEMANTIC_VIEWS: list[str] = [
    "vw_sentimento_municipio", "vw_vulnerabilidade_municipio", "vw_perfil_municipio",
    "vw_intencao_voto_uf", "vw_pesquisa_vs_social", "vw_narrativa_por_tema_uf",
    "vw_cenario_2018_2022_2026", "vw_transferencias_municipio", "vw_transferencias_vs_eleicao",
    "vw_emendas_municipio", "vw_emendas_vs_eleicao", "vw_sancoes_uf",
    "vw_score_municipal_integrado", "vw_social_candidato_sentimento", "vw_social_temas_uf",
    "vw_social_plataforma_uf", "vw_social_crise_detector", "vw_social_credibilidade",
    "vw_candidato_360", "vw_transferencias_candidato", "vw_emendas_candidato_uf",
    "vw_mapa_prioridade_campanha",
]


def _bq() -> bigquery.Client:
    return bigquery.Client(project=_PROJECT)


def _classify(row_count: int, freshness_h: float | None, dq_score: float | None = None) -> str:
    if row_count == 0:
        return "error"
    if freshness_h is None or freshness_h > 72:
        return "error"
    if freshness_h > 24:
        return "warn"
    if dq_score is not None and dq_score < 0.90:
        return "warn"
    return "ok"


def query_gold_storage() -> list[dict[str, Any]]:
    sql = f"""
    SELECT table_name, total_rows, total_logical_bytes,
           TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), storage_last_modified_time, HOUR) AS freshness_hours,
           storage_last_modified_time
    FROM `{_PROJECT}.{_GOLD}.INFORMATION_SCHEMA.TABLE_STORAGE`
    WHERE table_type = 'BASE TABLE'
    """
    found = {r["table_name"]: dict(r) for r in _bq().query(sql).result()}
    out = []
    for name in GOLD_TABLES:
        meta = found.get(name)
        if meta is None:
            out.append({
                "table": name, "layer": "gold", "row_count": 0,
                "freshness_hours": None, "size_mb": 0,
                "status": "error", "alert_message": "tabela não existe no BQ",
            })
            continue
        rc = int(meta["total_rows"] or 0)
        fh = float(meta["freshness_hours"]) if meta["freshness_hours"] is not None else None
        out.append({
            "table": name, "layer": "gold",
            "row_count": rc, "freshness_hours": fh,
            "last_modified": str(meta["storage_last_modified_time"]),
            "size_mb": round((meta["total_logical_bytes"] or 0) / 1e6, 1),
            "status": _classify(rc, fh),
            "alert_message": None if rc > 0 else "tabela vazia",
        })
    return out


def query_silver_storage() -> list[dict[str, Any]]:
    sql = f"""
    SELECT table_name, total_rows, total_logical_bytes,
           TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), storage_last_modified_time, HOUR) AS freshness_hours,
           storage_last_modified_time
    FROM `{_PROJECT}.{_SILVER}.INFORMATION_SCHEMA.TABLE_STORAGE`
    WHERE table_type = 'BASE TABLE'
    ORDER BY table_name
    """
    out = []
    for r in _bq().query(sql).result():
        rc = int(r["total_rows"] or 0)
        fh = float(r["freshness_hours"]) if r["freshness_hours"] is not None else None
        out.append({
            "table": r["table_name"], "layer": "silver",
            "row_count": rc, "freshness_hours": fh,
            "last_modified": str(r["storage_last_modified_time"]),
            "size_mb": round((r["total_logical_bytes"] or 0) / 1e6, 1),
            "status": _classify(rc, fh),
        })
    return out


def query_views_existence() -> list[dict[str, Any]]:
    sql = f"""
    SELECT table_name FROM `{_PROJECT}.{_GOLD}.INFORMATION_SCHEMA.VIEWS`
    """
    found = {r["table_name"] for r in _bq().query(sql).result()}
    out = []
    for v in SEMANTIC_VIEWS:
        present = v in found
        out.append({
            "view": v, "exists": present,
            "status": "ok" if present else "error",
            "alert_message": None if present else "view não encontrada no BQ",
        })
    return out
```

### Pattern 4 — Cloud Run executions list

```python
# ui/sentinel_queries.py (continuação)
JOB_NAMES: list[str] = [
    "spepe-tse-ingest", "spepe-ibge-sync", "spepe-digital-ingest",
    "spepe-social-ingest", "spepe-pesquisas-ingest", "spepe-security-ingest",
    "spepe-datasus-ingest", "spepe-dieese-ingest", "spepe-cetic-ingest",
    "spepe-tse-perfil-ingest", "spepe-tse-candidaturas-ingest", "spepe-reddit-ingest",
    "spepe-camara-senado-ingest", "spepe-endividamento-ingest", "spepe-cadunico-ingest",
    "spepe-emendas-ingest", "spepe-sancoes-ingest",
    "spepe-silver-transform", "spepe-gold-build", "spepe-candidatos-discovery",
]


def query_jobs_executions() -> list[dict[str, Any]]:
    from google.cloud import run_v2

    parent = f"projects/{_PROJECT}/locations/southamerica-east1"
    jobs_client = run_v2.JobsClient()
    exec_client = run_v2.ExecutionsClient()

    deployed = {j.name.split("/")[-1]: j for j in jobs_client.list_jobs(parent=parent)}
    out = []
    for name in JOB_NAMES:
        if name not in deployed:
            out.append({
                "job": name, "deployed": False, "status": "error",
                "last_status": "NOT_DEPLOYED", "last_run_at": None,
                "alert_message": "job não deployado",
            })
            continue
        job_full = deployed[name].name
        execs = list(exec_client.list_executions(parent=job_full, page_size=1))
        if not execs:
            out.append({
                "job": name, "deployed": True, "status": "error",
                "last_status": "NEVER_RUN", "last_run_at": None,
                "alert_message": "job nunca executado",
            })
            continue
        e = execs[0]
        completed = next((c for c in e.conditions if c.type_ == "Completed"), None)
        succeeded = completed and completed.state == run_v2.Condition.State.CONDITION_SUCCEEDED
        last_status = "SUCCEEDED" if succeeded else (completed.reason if completed else "RUNNING")
        status = "ok" if succeeded else "warn"
        out.append({
            "job": name, "deployed": True, "status": status,
            "last_status": last_status,
            "last_run_at": str(e.completion_time) if e.completion_time else None,
            "alert_message": None if succeeded else f"última execução: {last_status}",
        })
    return out
```

### Pattern 5 — DQ score (completude + unicidade)

```python
# ui/sentinel_queries.py (continuação)
def compute_dq_score(table: str, layer: str = "gold",
                     pk_columns: list[str] | None = None) -> dict[str, float]:
    """
    completude   = mean(non-null ratio) por coluna
    unicidade    = COUNT(DISTINCT pk) / COUNT(*)
    score        = 0.5*completude + 0.3*unicidade + 0.2*schema_drift
    """
    dataset = _GOLD if layer == "gold" else _SILVER
    col_sql = f"""
    SELECT column_name FROM `{_PROJECT}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
    WHERE table_name = @t
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("t", "STRING", table)]
    )
    cols = [r["column_name"] for r in _bq().query(col_sql, job_config=job_config).result()]
    if not cols:
        return {"score": 0.0, "completude": 0.0, "unicidade": 0.0, "schema_drift": 0.0}

    select_parts = [
        f"AVG(CASE WHEN `{c}` IS NOT NULL THEN 1.0 ELSE 0.0 END) AS c_{i}"
        for i, c in enumerate(cols)
    ]
    completude_sql = f"SELECT {', '.join(select_parts)} FROM `{_PROJECT}.{dataset}.{table}`"
    row = next(iter(_bq().query(completude_sql).result()), None)
    completude = (sum(row[k] for k in row.keys()) / len(cols)) if row else 0.0

    unicidade = 1.0
    if pk_columns:
        pk_concat = " || '|' || ".join(f"CAST(`{c}` AS STRING)" for c in pk_columns)
        u_sql = f"""
        SELECT COUNT(*) AS total, COUNT(DISTINCT {pk_concat}) AS uniq
        FROM `{_PROJECT}.{dataset}.{table}`
        """
        u_row = next(iter(_bq().query(u_sql).result()), None)
        if u_row and u_row["total"] > 0:
            unicidade = float(u_row["uniq"]) / float(u_row["total"])

    schema_drift = 1.0  # futuro: comparar COLUMN_HISTORY
    score = 0.5 * completude + 0.3 * unicidade + 0.2 * schema_drift
    return {
        "score": round(score, 4),
        "completude": round(completude, 4),
        "unicidade": round(unicidade, 4),
        "schema_drift": round(schema_drift, 4),
    }
```

### Pattern 6 — Custos (BQ scan + LLM tokens)

```python
# ui/sentinel_queries.py (continuação)
def query_costs() -> dict[str, Any]:
    region = "region-southamerica-east1"
    sql = f"""
    SELECT DATE(creation_time) AS day,
           SUM(total_bytes_billed) / POW(1024, 4) * 5 AS bq_cost_usd
    FROM `{_PROJECT}.{region}.INFORMATION_SCHEMA.JOBS_BY_PROJECT`
    WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
      AND job_type = 'QUERY' AND state = 'DONE'
    GROUP BY day ORDER BY day DESC
    """
    bq_rows = [dict(r) for r in _bq().query(sql).result()]

    from google.cloud import firestore
    fs = firestore.Client(project=_PROJECT)
    coll = fs.collection(settings.firestore_collection)
    cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    llm_total_30d = 0.0
    for doc in coll.where("created_at", ">=", cutoff).stream():
        d = doc.to_dict() or {}
        llm_total_30d += float(d.get("estimated_cost_usd", 0.0))

    return {
        "bq_daily": bq_rows,
        "bq_total_30d_usd": round(sum(r["bq_cost_usd"] for r in bq_rows), 2),
        "cloud_run_total_30d_usd": 0.0,  # Cloud Monitoring out-of-scope nesta versão
        "llm_total_30d_usd": round(llm_total_30d, 2),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
```

### Pattern 7 — Maturity score (DataOps/MLOps/LLMOps 0-100)

```python
# ui/sentinel_queries.py (continuação)
def compute_maturity_score(jobs: list[dict], gold: list[dict],
                           silver: list[dict], mlops: dict,
                           agents: list[dict]) -> dict[str, int]:
    """
    DataOps = 0.40*pct_jobs_ok + 0.30*pct_tables_fresh + 0.30*avg_dq
    MLOps   = 0.50*(1 - brier/0.30) + 0.30*(1 - drift/0.30) + 0.20*eval_score
    LLMOps  = 0.40*(1 - error_rate) + 0.30*(1 - p99/10s) + 0.30*availability
    """
    def clamp(x: float) -> int:
        return int(max(0, min(100, round(x * 100))))

    n_jobs = len(jobs) or 1
    pct_jobs_ok = sum(1 for j in jobs if j["status"] == "ok") / n_jobs
    fresh = [t for t in gold + silver
             if t.get("freshness_hours") is not None and t["freshness_hours"] < 24]
    pct_tables_fresh = len(fresh) / max(1, len(gold) + len(silver))
    avg_dq = sum(t.get("dq_score", 1.0) for t in gold) / max(1, len(gold))
    dataops = 0.40 * pct_jobs_ok + 0.30 * pct_tables_fresh + 0.30 * avg_dq

    brier = float(mlops.get("brier_score", 0.30))
    drift = float(mlops.get("js_divergence", 0.30))
    eval_score = float(mlops.get("eval_score", 0.0))
    mlops_s = (
        0.50 * max(0.0, 1.0 - brier / 0.30)
        + 0.30 * max(0.0, 1.0 - drift / 0.30)
        + 0.20 * eval_score
    )

    n_agents = len(agents) or 1
    total_calls = max(1, sum(a.get("calls_24h", 1) for a in agents))
    error_rate = sum(a.get("errors_24h", 0) for a in agents) / total_calls
    p99 = max((a.get("p99_latency_s", 0) for a in agents), default=0.0)
    availability = sum(1 for a in agents if a.get("calls_24h", 0) > 0) / n_agents
    llmops = (
        0.40 * max(0.0, 1.0 - error_rate)
        + 0.30 * max(0.0, 1.0 - min(p99, 10.0) / 10.0)
        + 0.30 * availability
    )
    return {"dataops": clamp(dataops), "mlops": clamp(mlops_s), "llmops": clamp(llmops)}
```

### Pattern 8 — Pub/Sub pull listener

```python
# ui/sentinel_pubsub.py
from __future__ import annotations
import asyncio
import json
import logging
from config.settings import settings

logger = logging.getLogger("spepe.sentinel.pubsub")
SUBSCRIPTION_ID = "spepe-sentinel-events-sub"


async def consume_sentinel_pubsub(broadcast) -> None:
    if not settings.gcp_project_id or settings.gcp_project_id == "local":
        logger.info("Sentinel Pub/Sub disabled (no gcp_project_id)")
        return
    from google.cloud import pubsub_v1

    subscriber = pubsub_v1.SubscriberClient()
    sub_path = subscriber.subscription_path(settings.gcp_project_id, SUBSCRIPTION_ID)

    while True:
        try:
            response = await asyncio.to_thread(
                subscriber.pull,
                request={"subscription": sub_path, "max_messages": 10},
                timeout=20.0,
            )
            ack_ids = []
            for received in response.received_messages:
                payload = json.loads(received.message.data.decode("utf-8"))
                event_type = received.message.attributes.get("event_type", "job_status_changed")
                await broadcast(event_type, payload)
                ack_ids.append(received.ack_id)
            if ack_ids:
                await asyncio.to_thread(
                    subscriber.acknowledge,
                    request={"subscription": sub_path, "ack_ids": ack_ids},
                )
        except Exception as exc:
            logger.warning("sentinel pubsub pull failed: %s", exc)
            await asyncio.sleep(5)
```

### Pattern 9 — EventSource JS com reconnect + fallback polling

```javascript
// ui/static/spepe-app.html — dentro da seção admin
(function () {
  const POLL_FALLBACK_MS = 30000;
  let es = null;
  let pollTimer = null;
  let failureCount = 0;

  function startSSE() {
    es = new EventSource('/admin/api/sentinel/stream');

    es.addEventListener('snapshot',                e => applySnapshot(JSON.parse(e.data)));
    es.addEventListener('table_freshness_updated', e => updateDataOpsTab(JSON.parse(e.data)));
    es.addEventListener('job_status_changed',      e => updateJobsTab(JSON.parse(e.data)));
    es.addEventListener('agent_execution_done',    e => updateLLMOpsTab(JSON.parse(e.data)));
    es.addEventListener('cost_updated',            e => updateCostsTab(JSON.parse(e.data)));
    es.addEventListener('heartbeat',               () => { failureCount = 0; });

    es.onerror = () => {
      failureCount++;
      es.close();
      if (failureCount >= 3) {
        console.warn('SSE falhou 3x — fallback para polling 30s');
        startPollingFallback();
      } else {
        setTimeout(startSSE, 1000 * failureCount);
      }
    };
  }

  function startPollingFallback() {
    if (pollTimer) return;
    const tick = async () => {
      try {
        const r = await fetch('/admin/api/sentinel/status', { headers: authHeaders() });
        if (r.ok) applySnapshot(await r.json());
      } catch (_) {}
    };
    tick();
    pollTimer = setInterval(tick, POLL_FALLBACK_MS);
  }

  function applySnapshot(s) {
    updateDataOpsTab(s.dataops || {});
    updateJobsTab(s.jobs || {});
    updateLLMOpsTab(s.llmops || {});
    updateCostsTab(s.costs || {});
    updateMaturityRadar(s.maturity || {});
    updateValidacaoDados(s.dq || {});
    updateValidacaoModelo(s.mlops || {});
    updateArquiteturaHealthMap(s);
  }

  function updateMaturityRadar(m) {
    if (!window._maturityChart) {
      const ctx = document.getElementById('chart-maturity').getContext('2d');
      window._maturityChart = new Chart(ctx, {
        type: 'radar',
        data: { labels: ['DataOps', 'MLOps', 'LLMOps'],
                datasets: [{ label: 'Score 0-100',
                             data: [m.dataops || 0, m.mlops || 0, m.llmops || 0],
                             backgroundColor: 'rgba(54,162,235,0.2)',
                             borderColor: 'rgba(54,162,235,1)' }] },
        options: { scales: { r: { min: 0, max: 100 } } }
      });
    } else {
      window._maturityChart.data.datasets[0].data =
        [m.dataops || 0, m.mlops || 0, m.llmops || 0];
      window._maturityChart.update();
    }
  }

  window.startSentinelStream = startSSE;
})();
```

### Pattern 10 — Terraform Pub/Sub topic + subscription

```hcl
# infra/terraform/pubsub.tf — APPEND após recursos drift_detected existentes

resource "google_pubsub_topic" "sentinel_events" {
  name   = "spepe-sentinel-events"
  labels = local.labels
  message_retention_duration = "3600s"
}

resource "google_pubsub_subscription" "sentinel_events_pull" {
  name   = "spepe-sentinel-events-sub"
  topic  = google_pubsub_topic.sentinel_events.name
  labels = local.labels

  ack_deadline_seconds       = 30
  message_retention_duration = "3600s"
  expiration_policy { ttl = "" }
}

resource "google_pubsub_topic_iam_member" "sentinel_publisher" {
  topic  = google_pubsub_topic.sentinel_events.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_pubsub_subscription_iam_member" "sentinel_subscriber" {
  subscription = google_pubsub_subscription.sentinel_events_pull.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.cloud_run.email}"
}
```

---

## Data Flow

```text
1. Admin abre /admin no browser
   ▼
2. spepe-app.html chama startSentinelStream() → EventSource /admin/api/sentinel/stream
   ▼
3. FastAPI cria asyncio.Queue, registra em _sentinel_subscribers
   ▼
4. Snapshot inicial: Gold + Silver + Views + Jobs + MLOps + Costs + Agents
   → enviado como event: snapshot
   ▼
5. Background pollers (lifespan) fazem fan-out:
     _poll_table_freshness()  a cada 15s → table_freshness_updated
     _poll_costs()            a cada 60s → cost_updated
     consume_sentinel_pubsub()           → job_status_changed (imediato)
     audit_hook in-proc                  → agent_execution_done (imediato)
   ▼
6. Cada evento é broadcast para todas as Queues → yield como linha SSE
   ▼
7. EventSource listener no browser atualiza DOM + Chart.js por aba
   ▼
8. Se 3 erros SSE consecutivos → startPollingFallback() GET /status a cada 30s
```

---

## Integration Points

| Sistema | Tipo | Autenticação |
|---------|------|-------------|
| BigQuery `INFORMATION_SCHEMA` | google-cloud-bigquery | ADC / WIF (Cloud Run SA) |
| Cloud Run Admin API (`run_v2`) | JobsClient + ExecutionsClient | ADC + `roles/run.viewer` |
| Pub/Sub `spepe-sentinel-events` | PullRequest | ADC + `roles/pubsub.subscriber` |
| Firestore `spepe_sessions` | google-cloud-firestore | ADC + `roles/datastore.viewer` |
| Browser → SSE | EventSource HTTPS | Bearer token (require_auth existente) |

---

## IAM Requirements (novas permissões necessárias)

| SA | Role adicional | Para |
|----|---------------|------|
| `cloud_run_sa` | `roles/run.viewer` | `executions.list()` nos 20 jobs |
| `cloud_run_sa` | `roles/pubsub.subscriber` | Pull `spepe-sentinel-events-sub` |
| `cloud_run_sa` | `roles/pubsub.publisher` | Publish para `spepe-sentinel-events` |

---

## Testing Strategy

| Tipo | Escopo | Arquivo | Ferramentas |
|------|--------|---------|-------------|
| Unit | DQ formula, maturity, _classify | `tests/test_sentinel_queries.py` | pytest + mock BQ |
| Unit | INFORMATION_SCHEMA query shape | `tests/test_sentinel_queries.py` | unittest.mock |
| Integration | SSE snapshot + heartbeat | `tests/test_sentinel_stream.py` | FastAPI TestClient async |
| Integration | Pub/Sub pull + ack + broadcast | `tests/test_sentinel_stream.py` | SubscriberClient mockado |
| E2E | Browser → SSE round-trip | Manual em staging | curl -N + DevTools |

**Acceptance test mapping (DEFINE → teste):**

| AT-ID | Teste |
|-------|-------|
| AT-001..003 | `test_classify_status_rules` + `test_query_gold_storage_mocked` |
| AT-004 | `test_fallback_stub_when_use_bigquery_false` |
| AT-005, AT-006 | `test_query_jobs_never_run_and_failed` |
| AT-007 | `test_pubsub_event_in_sse_stream` |
| AT-008 | `test_query_silver_storage` |
| AT-009, AT-010 | `test_query_views_includes_missing` |
| AT-011 | `test_query_agents_telemetry_p99` |
| AT-012 | `test_query_costs_bq_aggregation` |
| AT-013 | `test_compute_maturity_score_examples` |

---

## Error Handling

| Erro | Estratégia |
|------|-----------|
| BQ query falha (transiente) | Log warning, skip ciclo, yield ciclo anterior |
| Cloud Run API 403 | Log error uma vez; todos jobs → status="error", alert="IAM run.executions.list ausente" |
| Pub/Sub pull timeout | Sleep 5s e re-pull |
| SSE client desconecta | Remove queue de `_sentinel_subscribers` |
| `gcp_project_id` vazio / "local" | Skip todas chamadas GCP; yield snapshot stub |
| Firestore permission denied | LLMOps tab vazia; demais tabs inalteradas |
| INFORMATION_SCHEMA sem rows | Tratar como row_count=0 → status="error" |

---

## Configuration

| Var | Default | Descrição |
|-----|---------|-----------|
| `SENTINEL_POLL_FRESHNESS_S` | `15` | Intervalo BQ TABLE_STORAGE poll |
| `SENTINEL_POLL_COSTS_S` | `60` | Intervalo JOBS_BY_PROJECT poll |
| `SENTINEL_HEARTBEAT_S` | `30` | Cadência keepalive SSE |
| `SENTINEL_QUEUE_MAXSIZE` | `128` | Tamanho máx da queue por cliente |
| `SENTINEL_DQ_THRESHOLD_WARN` | `0.90` | DQ abaixo → warn |
| `SENTINEL_FRESHNESS_WARN_HOURS` | `24.0` | Freshness acima → warn |
| `SENTINEL_FRESHNESS_ERROR_HOURS` | `72.0` | Freshness acima → error |

---

## Security Considerations

- Todas as rotas `/admin/api/sentinel/*` protegidas por `require_auth` (Bearer token existente).
- `EventSource` não suporta custom headers nativamente — frontend usa cookie de auth ou passa token como query param (padrão existente em `/api/auth/me`).
- `INFORMATION_SCHEMA` retorna apenas metadata — sem PII.
- `compute_dq_score` escaneia tabelas — chamado apenas on-demand na aba Validação (não no poll automático).
- IAM Pub/Sub scoped para a subscription específica.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-08 | design-agent | Initial — SSE + 2 novos módulos + Pub/Sub topic; 4 ADRs; 10 patterns; 7 arquivos |

---

## Next Step

**Ready for:** `/build .claude/sdd/features/DESIGN_ADMIN_PANEL_V2.md`
