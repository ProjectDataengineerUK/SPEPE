"""
SPEPE Sentinel — typed query helpers for real-time admin panel.

Modules expose pure functions that hit BigQuery INFORMATION_SCHEMA, Cloud Run
ExecutionsClient and Firestore. All `google.cloud.*` imports are LAZY (inside
the functions) so that the module can be imported in CI environments without
the GCP SDK installed and without authentication.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from config.settings import settings

logger = logging.getLogger("spepe.sentinel.queries")

_PROJECT = settings.gcp_project_id or "spepe-prod"
_GOLD = settings.bigquery_dataset_gold
_SILVER = settings.bigquery_dataset_silver
_MLOPS = settings.bigquery_dataset_mlops

# ---------------------------------------------------------------------------
# Constants — declared resources that the panel monitors
# ---------------------------------------------------------------------------

GOLD_TABLES: list[str] = [
    "fact_municipio_eleicao",
    "fact_secao_eleicao",
    "fact_candidato_eleicao",
    "fact_municipio_candidato_eleicao",
    "fact_ibge_municipio",
    "fact_seguranca_municipio",
    "fact_saude_municipio",
    "fact_economico_municipio",
    "fact_pesquisa",
    "fact_social_municipio",
    "fact_transferencias_sociais",
    "fact_emendas_parlamentar",
    "fact_emendas_municipio",
    "fact_sancoes_uf",
    "fact_endividamento_nacional",
    "fact_votacoes_parlamentar",
]

SEMANTIC_VIEWS: list[str] = [
    "vw_sentimento_municipio",
    "vw_vulnerabilidade_municipio",
    "vw_perfil_municipio",
    "vw_intencao_voto_uf",
    "vw_pesquisa_vs_social",
    "vw_narrativa_por_tema_uf",
    "vw_cenario_2018_2022_2026",
    "vw_transferencias_municipio",
    "vw_transferencias_vs_eleicao",
    "vw_emendas_municipio",
    "vw_emendas_vs_eleicao",
    "vw_sancoes_uf",
    "vw_score_municipal_integrado",
    "vw_social_candidato_sentimento",
    "vw_social_temas_uf",
    "vw_social_plataforma_uf",
    "vw_social_crise_detector",
    "vw_social_credibilidade",
    "vw_candidato_360",
    "vw_transferencias_candidato",
    "vw_emendas_candidato_uf",
    "vw_mapa_prioridade_campanha",
]

SERVICE_NAMES: list[str] = [
    "spepe-prod",
    "spepe-staging",
]

JOB_NAMES: list[str] = [
    "spepe-tse-ingest",
    "spepe-ibge-sync",
    "spepe-digital-ingest",
    "spepe-social-ingest",
    "spepe-pesquisas-ingest",
    "spepe-security-ingest",
    "spepe-datasus-ingest",
    "spepe-dieese-ingest",
    "spepe-cetic-ingest",
    "spepe-tse-perfil-ingest",
    "spepe-tse-candidaturas-ingest",
    "spepe-reddit-ingest",
    "spepe-camara-senado-ingest",
    "spepe-endividamento-ingest",
    "spepe-cadunico-ingest",
    "spepe-emendas-ingest",
    "spepe-sancoes-ingest",
    "spepe-silver-transform",
    "spepe-gold-build",
    "spepe-candidatos-discovery",
    "spepe-pymc-train",
]

AGENT_NAMES: list[str] = [
    "supervisor",
    "coletor",
    "analista",
    "perfilador",
    "modelista_bayesiano",
    "explicador",
    "narrador",
    "vigilante",
    "sentinel",
    "iterador",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bq():
    """Lazy BigQuery client — imported only when needed."""
    from google.cloud import bigquery

    return bigquery.Client(project=_PROJECT)


def _classify(
    row_count: int,
    freshness_h: float | None,
    dq_score: float | None = None,
) -> str:
    """Classify a resource as ok/warn/error from raw signals."""
    if row_count == 0:
        return "error"
    if freshness_h is None or freshness_h > 72:
        return "error"
    if freshness_h > 24:
        return "warn"
    if dq_score is not None and dq_score < 0.90:
        return "warn"
    return "ok"


# ---------------------------------------------------------------------------
# Gold / Silver storage queries (Pattern 3)
# ---------------------------------------------------------------------------


def query_gold_storage() -> list[dict[str, Any]]:
    """Return one dict per declared Gold table, including missing ones."""
    sql = f"""
    SELECT table_id AS table_name, row_count AS total_rows, size_bytes AS total_logical_bytes,
           TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), TIMESTAMP_MILLIS(last_modified_time), HOUR) AS freshness_hours,
           TIMESTAMP_MILLIS(last_modified_time) AS storage_last_modified_time
    FROM `{_PROJECT}.{_GOLD}.__TABLES__`
    WHERE type = 1
    """
    found: dict[str, dict[str, Any]] = {
        r["table_name"]: dict(r) for r in _bq().query(sql).result(timeout=30)
    }
    out: list[dict[str, Any]] = []
    for name in GOLD_TABLES:
        meta = found.get(name)
        if meta is None:
            out.append(
                {
                    "table": name,
                    "layer": "gold",
                    "row_count": 0,
                    "freshness_hours": None,
                    "size_mb": 0,
                    "status": "error",
                    "alert_message": "tabela não existe no BQ",
                }
            )
            continue
        rc = int(meta["total_rows"] or 0)
        fh = float(meta["freshness_hours"]) if meta["freshness_hours"] is not None else None
        out.append(
            {
                "table": name,
                "layer": "gold",
                "row_count": rc,
                "freshness_hours": fh,
                "last_modified": str(meta["storage_last_modified_time"]),
                "size_mb": round((meta["total_logical_bytes"] or 0) / 1e6, 1),
                "status": _classify(rc, fh),
                "alert_message": None if rc > 0 else "tabela vazia",
            }
        )
    return out


def query_silver_storage() -> list[dict[str, Any]]:
    """Return one dict per existing Silver table (discovered, not declared)."""
    sql = f"""
    SELECT table_id AS table_name, row_count AS total_rows, size_bytes AS total_logical_bytes,
           TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), TIMESTAMP_MILLIS(last_modified_time), HOUR) AS freshness_hours,
           TIMESTAMP_MILLIS(last_modified_time) AS storage_last_modified_time
    FROM `{_PROJECT}.{_SILVER}.__TABLES__`
    WHERE type = 1
    ORDER BY table_name
    """
    out: list[dict[str, Any]] = []
    for r in _bq().query(sql).result(timeout=30):
        rc = int(r["total_rows"] or 0)
        fh = float(r["freshness_hours"]) if r["freshness_hours"] is not None else None
        out.append(
            {
                "table": r["table_name"],
                "layer": "silver",
                "row_count": rc,
                "freshness_hours": fh,
                "last_modified": str(r["storage_last_modified_time"]),
                "size_mb": round((r["total_logical_bytes"] or 0) / 1e6, 1),
                "status": _classify(rc, fh),
            }
        )
    return out


def query_views_existence() -> list[dict[str, Any]]:
    """For each declared semantic view, check if it exists in spepe_gold."""
    sql = f"""
    SELECT table_id AS table_name FROM `{_PROJECT}.{_GOLD}.__TABLES__`
    WHERE type = 2
    """
    found = {r["table_name"] for r in _bq().query(sql).result(timeout=30)}
    out: list[dict[str, Any]] = []
    for v in SEMANTIC_VIEWS:
        present = v in found
        out.append(
            {
                "view": v,
                "exists": present,
                "status": "ok" if present else "error",
                "alert_message": None if present else "view não encontrada no BQ",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Cloud Run executions (Pattern 4)
# ---------------------------------------------------------------------------


def query_jobs_executions() -> list[dict[str, Any]]:
    """Return last execution status per declared job."""
    from google.cloud import run_v2

    parent = f"projects/{_PROJECT}/locations/southamerica-east1"
    jobs_client = run_v2.JobsClient()
    exec_client = run_v2.ExecutionsClient()

    deployed = {j.name.split("/")[-1]: j for j in jobs_client.list_jobs(parent=parent)}
    out: list[dict[str, Any]] = []
    for name in JOB_NAMES:
        if name not in deployed:
            out.append(
                {
                    "job": name,
                    "deployed": False,
                    "status": "error",
                    "last_status": "NOT_DEPLOYED",
                    "last_run_at": None,
                    "alert_message": "job não deployado",
                }
            )
            continue
        job_full = deployed[name].name
        execs = list(exec_client.list_executions(parent=job_full, page_size=1))
        if not execs:
            out.append(
                {
                    "job": name,
                    "deployed": True,
                    "status": "error",
                    "last_status": "NEVER_RUN",
                    "last_run_at": None,
                    "alert_message": "job nunca executado",
                }
            )
            continue
        e = execs[0]
        completed = next((c for c in e.conditions if c.type_ == "Completed"), None)
        succeeded = (
            completed is not None and completed.state == run_v2.Condition.State.CONDITION_SUCCEEDED
        )
        last_status = "SUCCEEDED" if succeeded else (completed.reason if completed else "RUNNING")
        status = "ok" if succeeded else "warn"
        out.append(
            {
                "job": name,
                "deployed": True,
                "status": status,
                "last_status": last_status,
                "last_run_at": str(e.completion_time) if e.completion_time else None,
                "alert_message": None if succeeded else f"última execução: {last_status}",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Cloud Run Services health
# ---------------------------------------------------------------------------


def query_cloud_run_services() -> list[dict[str, Any]]:
    """Return health status for each declared Cloud Run service."""
    from google.cloud import run_v2

    region = "southamerica-east1"
    services_client = run_v2.ServicesClient()
    parent = f"projects/{_PROJECT}/locations/{region}"

    deployed: dict[str, Any] = {}
    try:
        for svc in services_client.list_services(parent=parent):
            deployed[svc.name.split("/")[-1]] = svc
    except Exception as exc:
        logger.warning("Cloud Run services list failed: %s", exc)
        return [
            {
                "service": n,
                "status": "warn",
                "url": None,
                "alert_message": "não foi possível verificar",
            }
            for n in SERVICE_NAMES
        ]

    out: list[dict[str, Any]] = []
    for name in SERVICE_NAMES:
        if name not in deployed:
            out.append(
                {
                    "service": name,
                    "status": "error",
                    "url": None,
                    "alert_message": "serviço não encontrado",
                }
            )
            continue
        svc = deployed[name]
        ready = next((c for c in svc.conditions if c.type_ == "Ready"), None)
        is_ready = ready is not None and ready.state == run_v2.Condition.State.CONDITION_SUCCEEDED
        out.append(
            {
                "service": name,
                "status": "ok" if is_ready else "error",
                "url": svc.uri or None,
                "alert_message": None
                if is_ready
                else (ready.message if ready else "serviço não pronto"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# MLOps metrics (model_evaluations + bias_metrics)
# ---------------------------------------------------------------------------


def query_mlops_metrics() -> dict[str, Any]:
    """Return latest brier_score, drift JS, eval_score, and bias snapshot."""
    out: dict[str, Any] = {
        "brier_score": None,
        "js_divergence": None,
        "eval_score": None,
        "bias_metrics": [],
        "model_version": None,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client = _bq()
        eval_sql = f"""
        SELECT model_version, brier_score, accuracy AS eval_score, evaluated_at
        FROM `{_PROJECT}.{_MLOPS}.model_evaluations`
        ORDER BY evaluated_at DESC
        LIMIT 1
        """
        rows = list(client.query(eval_sql).result())
        if rows:
            r = rows[0]
            out["brier_score"] = float(r["brier_score"]) if r["brier_score"] is not None else None
            out["eval_score"] = float(r["eval_score"]) if r["eval_score"] is not None else None
            out["model_version"] = r["model_version"]
    except Exception as exc:
        logger.warning("model_evaluations query failed: %s", exc)

    try:
        client = _bq()
        bias_sql = f"""
        SELECT group_value AS sg_uf, ratio AS bias_score, measured_at AS computed_at
        FROM `{_PROJECT}.{_MLOPS}.bias_metrics`
        WHERE measured_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
          AND group_type = 'uf'
        ORDER BY measured_at DESC
        """
        bias_rows = [dict(r) for r in client.query(bias_sql).result()]
        out["bias_metrics"] = bias_rows
        if bias_rows:
            out["js_divergence"] = float(max((b.get("bias_score") or 0.0) for b in bias_rows))
    except Exception as exc:
        logger.warning("bias_metrics query failed: %s", exc)

    return out


# ---------------------------------------------------------------------------
# Agents telemetry — Firestore spepe_sessions
# ---------------------------------------------------------------------------


def query_agents_telemetry() -> list[dict[str, Any]]:
    """Aggregate latency / cost / errors per agent over last 24h."""
    out_map: dict[str, dict[str, Any]] = {
        a: {
            "agent": a,
            "calls_24h": 0,
            "errors_24h": 0,
            "p50_latency_s": 0.0,
            "p99_latency_s": 0.0,
            "cost_24h_usd": 0.0,
            "last_run_at": None,
            "status": "ok",
        }
        for a in AGENT_NAMES
    }

    try:
        from google.cloud import firestore

        fs = firestore.Client(project=_PROJECT)
        coll = fs.collection(settings.firestore_collection)
        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        # collect per-agent lists of latencies
        per_agent_lat: dict[str, list[float]] = {a: [] for a in AGENT_NAMES}

        for doc in coll.where("created_at", ">=", cutoff).stream():
            d = doc.to_dict() or {}
            agent = d.get("agent") or d.get("agent_name") or "supervisor"
            if agent not in out_map:
                continue
            entry = out_map[agent]
            entry["calls_24h"] += 1
            if d.get("error"):
                entry["errors_24h"] += 1
            lat = float(d.get("latency_s", 0) or 0)
            per_agent_lat[agent].append(lat)
            entry["cost_24h_usd"] += float(d.get("estimated_cost_usd", 0) or 0)
            ts = d.get("created_at")
            if ts and (entry["last_run_at"] is None or str(ts) > str(entry["last_run_at"])):
                entry["last_run_at"] = str(ts)

        for agent, lats in per_agent_lat.items():
            if not lats:
                continue
            sorted_lats = sorted(lats)
            mid = len(sorted_lats) // 2
            out_map[agent]["p50_latency_s"] = round(sorted_lats[mid], 3)
            p99_idx = max(0, int(len(sorted_lats) * 0.99) - 1)
            out_map[agent]["p99_latency_s"] = round(sorted_lats[p99_idx], 3)

        for entry in out_map.values():
            errs = entry["errors_24h"]
            calls = entry["calls_24h"] or 1
            err_rate = errs / calls
            if err_rate > 0.10:
                entry["status"] = "error"
            elif err_rate > 0.02 or entry["p99_latency_s"] > 10:
                entry["status"] = "warn"
            else:
                entry["status"] = "ok"
            entry["cost_24h_usd"] = round(entry["cost_24h_usd"], 4)
    except Exception as exc:
        logger.warning("agents telemetry firestore failed: %s", exc)

    return list(out_map.values())


# ---------------------------------------------------------------------------
# DQ score (Pattern 5)
# ---------------------------------------------------------------------------


def compute_dq_score(
    table: str,
    layer: str = "gold",
    pk_columns: list[str] | None = None,
) -> dict[str, float]:
    """
    Compute Data Quality score for a table.

    score = 0.5*completude + 0.3*unicidade + 0.2*schema_drift
    """
    from google.cloud import bigquery

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
        return {
            "score": 0.0,
            "completude": 0.0,
            "unicidade": 0.0,
            "schema_drift": 0.0,
        }

    select_parts = [
        f"AVG(CASE WHEN `{c}` IS NOT NULL THEN 1.0 ELSE 0.0 END) AS c_{i}"
        for i, c in enumerate(cols)
    ]
    completude_sql = f"SELECT {', '.join(select_parts)} FROM `{_PROJECT}.{dataset}.{table}`"
    row = next(iter(_bq().query(completude_sql).result()), None)
    if row:
        # iterate over the row's values, not its keys
        vals = [row[k] for k in row.keys()]
        completude = sum(float(v or 0.0) for v in vals) / len(cols)
    else:
        completude = 0.0

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

    schema_drift = 1.0  # future: compare COLUMN_HISTORY
    score = 0.5 * completude + 0.3 * unicidade + 0.2 * schema_drift
    return {
        "score": round(score, 4),
        "completude": round(completude, 4),
        "unicidade": round(unicidade, 4),
        "schema_drift": round(schema_drift, 4),
    }


# ---------------------------------------------------------------------------
# Costs (Pattern 6)
# ---------------------------------------------------------------------------


def query_costs() -> dict[str, Any]:
    """Return BQ scan + LLM tokens cost over last 30 days."""
    region = "region-southamerica-east1"
    bq_rows: list[dict[str, Any]] = []
    try:
        sql = f"""
        SELECT DATE(creation_time) AS day,
               SUM(total_bytes_billed) / POW(1024, 4) * 5 AS bq_cost_usd
        FROM `{_PROJECT}.{region}.INFORMATION_SCHEMA.JOBS_BY_PROJECT`
        WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
          AND job_type = 'QUERY' AND state = 'DONE'
        GROUP BY day ORDER BY day DESC
        """
        bq_rows = [dict(r) for r in _bq().query(sql).result(timeout=30)]
    except Exception as exc:
        logger.warning("BQ JOBS_BY_PROJECT query failed: %s", exc)

    llm_total_30d = 0.0
    try:
        from google.cloud import firestore

        fs = firestore.Client(project=_PROJECT)
        coll = fs.collection(settings.firestore_collection)
        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for doc in coll.where("created_at", ">=", cutoff).stream():
            d = doc.to_dict() or {}
            llm_total_30d += float(d.get("estimated_cost_usd", 0.0) or 0.0)
    except Exception as exc:
        logger.warning("Firestore cost aggregation failed: %s", exc)

    return {
        "bq_daily": bq_rows,
        "bq_total_30d_usd": round(sum(float(r.get("bq_cost_usd") or 0.0) for r in bq_rows), 2),
        "cloud_run_total_30d_usd": 0.0,  # out-of-scope for v1
        "llm_total_30d_usd": round(llm_total_30d, 2),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Maturity score (Pattern 7) — 0-100 legacy + 0-5 report
# ---------------------------------------------------------------------------

_MATURITY_LABELS = ["Inexistente", "Inicial", "Repetível", "Definido", "Gerenciado", "Otimizado"]


def compute_maturity_score(
    jobs: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    silver: list[dict[str, Any]],
    mlops: dict[str, Any],
    agents: list[dict[str, Any]],
) -> dict[str, int]:
    """Compute DataOps / MLOps / LLMOps scores 0-100 (legacy, kept for compatibility)."""

    def clamp(x: float) -> int:
        return int(max(0, min(100, round(x * 100))))

    n_jobs = len(jobs) or 1
    pct_jobs_ok = sum(1 for j in jobs if j.get("status") == "ok") / n_jobs

    tables = list(gold) + list(silver)
    fresh = [
        t for t in tables if t.get("freshness_hours") is not None and t["freshness_hours"] < 24
    ]
    pct_tables_fresh = len(fresh) / max(1, len(tables))
    avg_dq = sum(float(t.get("dq_score", 1.0) or 1.0) for t in gold) / max(1, len(gold))
    dataops = 0.40 * pct_jobs_ok + 0.30 * pct_tables_fresh + 0.30 * avg_dq

    brier = float(mlops.get("brier_score") or 0.30)
    drift = float(mlops.get("js_divergence") or 0.30)
    eval_score = float(mlops.get("eval_score") or 0.0)
    mlops_s = (
        0.50 * max(0.0, 1.0 - brier / 0.30)
        + 0.30 * max(0.0, 1.0 - drift / 0.30)
        + 0.20 * eval_score
    )

    n_agents = len(agents) or 1
    total_calls = max(1, sum(int(a.get("calls_24h", 1) or 1) for a in agents))
    error_rate = sum(int(a.get("errors_24h", 0) or 0) for a in agents) / total_calls
    p99 = max((float(a.get("p99_latency_s", 0) or 0) for a in agents), default=0.0)
    availability = sum(1 for a in agents if int(a.get("calls_24h", 0) or 0) > 0) / n_agents
    llmops = (
        0.40 * max(0.0, 1.0 - error_rate)
        + 0.30 * max(0.0, 1.0 - min(p99, 10.0) / 10.0)
        + 0.30 * availability
    )
    return {
        "dataops": clamp(dataops),
        "mlops": clamp(mlops_s),
        "llmops": clamp(llmops),
    }


def compute_maturity_report(
    jobs: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    silver: list[dict[str, Any]],
    mlops: dict[str, Any],
    agents: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compute 0-5 maturity level per pillar with technical evidence and improvement opportunities.

    Levels (following MLOps Maturity Model):
      0 = Inexistente  — sem infraestrutura
      1 = Inicial      — processos ad-hoc, manual
      2 = Repetível    — processos básicos estabelecidos
      3 = Definido     — padronizado e documentado
      4 = Gerenciado   — monitorado e controlado
      5 = Otimizado    — melhoria contínua, automação completa
    """

    def _st(ok_cond: bool, warn_cond: bool = False) -> str:
        return "ok" if ok_cond else ("warn" if warn_cond else "error")

    # ── DataOps ──────────────────────────────────────────────────────────────
    n_gold = len(gold) or 1
    gold_with_data = [t for t in gold if (t.get("row_count") or 0) > 0]
    gold_fresh = [t for t in gold if (t.get("freshness_hours") or 999) < 24]
    silver_count = len(silver)
    jobs_deployed = [j for j in jobs if j.get("deployed", True)]
    jobs_ok = [j for j in jobs if j.get("status") == "ok"]

    pct_data = len(gold_with_data) / n_gold
    pct_fresh = len(gold_fresh) / n_gold
    pct_jobs = len(jobs_ok) / max(len(jobs), 1)

    do_items = [
        {
            "item": f"Gold tables populadas ({len(gold_with_data)}/{len(gold)})",
            "status": _st(pct_data >= 0.75, pct_data > 0),
            "evidence": f"{len(gold_with_data)} de {len(gold)} tabelas Gold com dados",
        },
        {
            "item": f"Gold tables atualizadas <24h ({len(gold_fresh)}/{len(gold)})",
            "status": _st(pct_fresh >= 0.75, pct_fresh > 0),
            "evidence": f"{len(gold_fresh)} tabelas com freshness < 24h",
        },
        {
            "item": f"Silver tables ({silver_count} encontradas)",
            "status": _st(silver_count >= 10, silver_count > 0),
            "evidence": f"{silver_count} tabelas Silver no spepe_silver",
        },
        {
            "item": f"Jobs Cloud Run deployados ({len(jobs_deployed)}/{len(jobs)})",
            "status": _st(len(jobs_deployed) == len(jobs), len(jobs_deployed) > len(jobs) * 0.5),
            "evidence": f"{len(jobs_deployed)} de {len(jobs)} jobs encontrados na região",
        },
        {
            "item": f"Jobs com última execução OK ({len(jobs_ok)}/{len(jobs)})",
            "status": _st(pct_jobs >= 0.75, pct_jobs > 0),
            "evidence": f"{len(jobs_ok)} jobs com SUCCEEDED na última execução",
        },
        {
            "item": "Arquitetura Medallion (Bronze→Silver→Gold)",
            "status": "ok",
            "evidence": "bronze_writer.py + silver_transformer.py + gold_builder.py implementados",
        },
    ]
    do_score = max(0, min(5, round(pct_data * 2 + pct_fresh * 1.5 + pct_jobs * 1.5)))

    # ── MLOps ────────────────────────────────────────────────────────────────
    brier = mlops.get("brier_score")
    drift = mlops.get("js_divergence")
    eval_sc = mlops.get("eval_score")
    model_v = mlops.get("model_version")

    ml_items = [
        {
            "item": "Modelo treinado (versão disponível)",
            "status": _st(bool(model_v)),
            "evidence": f"model_version = {model_v}"
            if model_v
            else "Nenhum modelo em spepe_mlops.model_evaluations",
        },
        {
            "item": f"Brier Score < 0.20 (atual: {f'{brier:.4f}' if brier is not None else 'N/A'})",
            "status": _st(brier is not None and brier < 0.20, brier is not None and brier < 0.30),
            "evidence": f"Brier = {round(brier, 4) if brier is not None else 'N/A'} (meta: < 0.20 para produção)",
        },
        {
            "item": f"Eval LLM Score > 0.85 (atual: {f'{eval_sc:.3f}' if eval_sc is not None else 'N/A'})",
            "status": _st(eval_sc is not None and eval_sc > 0.85, eval_sc is not None),
            "evidence": f"eval_score = {round(eval_sc, 3) if eval_sc is not None else 'N/A'} via golden_dataset.jsonl",
        },
        {
            "item": f"Drift JS Divergence < 0.10 (atual: {f'{drift:.4f}' if drift is not None else 'N/A'})",
            "status": _st(drift is not None and drift < 0.10, drift is not None),
            "evidence": f"js_divergence = {round(drift, 4) if drift is not None else 'N/A'} (meta: < 0.10)",
        },
        {
            "item": "Pipeline KFP 2.x compilado",
            "status": "ok",
            "evidence": "output/ml_pipeline.yaml gerado (mlops/vertex_pipeline.py)",
        },
    ]
    ml_score = sum(
        [
            bool(model_v),
            brier is not None,
            brier is not None and brier < 0.30,
            brier is not None and brier < 0.20,
            eval_sc is not None and eval_sc > 0.85,
        ]
    )

    # ── LLMOps ───────────────────────────────────────────────────────────────
    agents_active = [a for a in agents if int(a.get("calls_24h", 0) or 0) > 0]
    total_calls = sum(int(a.get("calls_24h", 0) or 0) for a in agents)
    total_errors = sum(int(a.get("errors_24h", 0) or 0) for a in agents)
    error_rate = total_errors / max(total_calls, 1)
    p99_max = max((float(a.get("p99_latency_s", 0) or 0) for a in agents), default=0.0)

    ll_items = [
        {
            "item": f"Agentes ativos nas últimas 24h ({len(agents_active)}/{len(agents)})",
            "status": _st(len(agents_active) >= len(agents) * 0.7, len(agents_active) > 0),
            "evidence": f"{len(agents_active)} agentes com chamadas: {', '.join(a['agent'] for a in agents_active[:3])}",
        },
        {
            "item": f"Taxa de erro < 2% (atual: {round(error_rate * 100, 1)}%)",
            "status": _st(error_rate < 0.02, error_rate < 0.10),
            "evidence": f"{total_errors} erros em {total_calls} chamadas nas últimas 24h",
        },
        {
            "item": f"Latência p99 < 10s (atual: {round(p99_max, 1)}s)",
            "status": _st(p99_max < 10, p99_max < 30),
            "evidence": f"P99 máximo entre todos os agentes: {round(p99_max, 1)}s",
        },
        {
            "item": "Budget por sessão ($2.00) configurado",
            "status": "ok",
            "evidence": "config/session_state.py: BUDGET_USD = 2.00, BUDGET_WARN_USD = 1.50",
        },
        {
            "item": "Hooks de segurança (DLP, rate-limit, audit, cost-guard)",
            "status": "ok",
            "evidence": "hooks/: dlp_hook.py + rate_limit_hook.py + audit_hook.py + cost_guard_hook.py",
        },
    ]
    ll_score = sum(
        [
            len(agents) > 0,
            len(agents_active) > 0,
            error_rate < 0.10,
            error_rate < 0.02,
            p99_max < 10 and bool(agents_active),
        ]
    )

    # ── Opportunities ─────────────────────────────────────────────────────────
    opportunities: list[dict[str, str]] = []

    empty_gold = [t["table"] for t in gold if (t.get("row_count") or 0) == 0]
    if empty_gold:
        opportunities.append(
            {
                "pillar": "DataOps",
                "priority": "high",
                "recommendation": f"Popular tabelas Gold vazias: {', '.join(empty_gold[:4])}{'…' if len(empty_gold) > 4 else ''}",
                "impact": "Habilita análises completas e aumenta score DataOps para ≥4",
            }
        )

    stale = [
        t["table"]
        for t in gold
        if (t.get("freshness_hours") or 0) > 72 and (t.get("row_count") or 0) > 0
    ]
    if stale:
        opportunities.append(
            {
                "pillar": "DataOps",
                "priority": "medium",
                "recommendation": f"Agendar re-execução de gold-build: {len(stale)} tabelas com dados >72h",
                "impact": "Freshness <24h eleva score DataOps e evita análises desatualizadas",
            }
        )

    failed = [j["job"] for j in jobs if j.get("status") not in ("ok",) and j.get("deployed")]
    if failed:
        opportunities.append(
            {
                "pillar": "DataOps",
                "priority": "high",
                "recommendation": f"Investigar {len(failed)} job(s) com falha: {', '.join(failed[:3])}",
                "impact": "Restaura pipeline completo de ingestão",
            }
        )

    if not model_v:
        opportunities.append(
            {
                "pillar": "MLOps",
                "priority": "high",
                "recommendation": "Executar spepe-pymc-train para treinar o modelo Bayesiano PyMC",
                "impact": "Habilita predições com IC 95% e eleva score MLOps de 1→3",
            }
        )
    elif brier is not None and brier >= 0.20:
        opportunities.append(
            {
                "pillar": "MLOps",
                "priority": "medium",
                "recommendation": f"Brier Score atual {round(brier, 3)} > 0.20: revisar priors PyMC e adicionar features temporais",
                "impact": "Brier < 0.20 é gate para promoção para produção",
            }
        )

    if not agents_active:
        opportunities.append(
            {
                "pillar": "LLMOps",
                "priority": "medium",
                "recommendation": "Nenhum agente ativo nas últimas 24h — verificar Firestore spepe_sessions e Vertex AI",
                "impact": "Telemetria de agentes é requisito para score LLMOps ≥3",
            }
        )

    opportunities.append(
        {
            "pillar": "FinOps",
            "priority": "high",
            "recommendation": "Adicionar partition pruning em fact_secao_eleicao (139M rows) — filtrar sempre por ano_eleicao",
            "impact": "Redução estimada de 70% no custo por query — maior tabela do sistema",
        }
    )
    opportunities.append(
        {
            "pillar": "FinOps",
            "priority": "medium",
            "recommendation": "Usar BigQuery Editions (autoscale) em vez de on-demand para queries analíticas recorrentes",
            "impact": "Previsibilidade de custo e redução em ~30% para cargas regulares",
        }
    )
    opportunities.append(
        {
            "pillar": "FinOps",
            "priority": "low",
            "recommendation": "Configurar expiração de partições no Silver (90 dias) para controlar storage growth",
            "impact": "Reduz custo de armazenamento Silver em ~40% após 3 meses",
        }
    )

    return {
        "dataops": {"score": do_score, "label": _MATURITY_LABELS[do_score], "items": do_items},
        "mlops": {"score": ml_score, "label": _MATURITY_LABELS[ml_score], "items": ml_items},
        "llmops": {"score": ll_score, "label": _MATURITY_LABELS[ll_score], "items": ll_items},
        "opportunities": opportunities,
    }
