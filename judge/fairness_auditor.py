from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FairnessFinding:
    dimension: str
    bucket: str
    tpr: float
    fpr: float
    gap_pp: float


@dataclass
class FairnessReport:
    metric: str
    findings: list[FairnessFinding] = field(default_factory=list)
    max_gap_pp: float = 0.0
    dimensions: list[str] = field(default_factory=list)
    has_violations: bool = False


def _bq_client(project_id: str):
    try:
        from google.cloud import bigquery

        return bigquery.Client(project=project_id)
    except Exception as exc:
        logger.info("bigquery_unavailable: %s", exc)
        return None


def _fetch_audit_rows(
    project_id: str,
    model_version: str,
    dimensions: list[str],
    mlops_dataset: str = "spepe_mlops",
    gold_dataset: str = "spepe_gold",
) -> list[dict[str, Any]]:
    client = _bq_client(project_id)
    if client is None:
        return []
    select_dims = ", ".join(f"m.{d}" for d in dimensions)
    query = f"""
        SELECT
          p.p_mean,
          e.resultado_real AS y_true,
          {select_dims}
        FROM `{project_id}.{mlops_dataset}.fact_predictions` p
        JOIN `{project_id}.{mlops_dataset}.ground_truth` e
          ON p.cod_municipio_ibge = e.cod_municipio_ibge
         AND p.nm_candidato = e.nm_candidato
         AND p.ano_eleicao = e.ano_eleicao
        JOIN `{project_id}.{gold_dataset}.fact_ibge_municipio` m
          ON p.cod_municipio_ibge = m.cod_municipio_ibge
        WHERE p.model_version = @model_version AND p.shadow = true
    """
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("model_version", "STRING", model_version)
        ]
    )
    return [dict(row) for row in client.query(query, job_config=job_config).result()]


def audit_fairness(
    project_id: str,
    model_version: str,
    dimensions: list[str] | None = None,
    rows: list[dict[str, Any]] | None = None,
    gap_threshold_pp: float = 15.0,
    bucket_min_size: int = 50,
) -> FairnessReport:
    """Equalized Odds audit across protected dimensions.

    Flags dimension/bucket pairs whose TPR or FPR deviates by more than
    `gap_threshold_pp` percentage points from the overall average.
    """
    dimensions = dimensions or ["quintil_renda", "sg_uf", "pct_zona_rural"]
    data = (
        rows
        if rows is not None
        else _fetch_audit_rows(project_id, model_version, dimensions)
    )
    report = FairnessReport(metric="equalized_odds", dimensions=dimensions)
    if not data:
        return report

    all_preds = np.array([float(r["p_mean"]) >= 0.5 for r in data])
    all_true = np.array([float(r["y_true"]) >= 0.5 for r in data])
    overall_tpr = _tpr(all_preds, all_true)
    overall_fpr = _fpr(all_preds, all_true)

    for dim in dimensions:
        buckets: dict[Any, list[int]] = {}
        for i, r in enumerate(data):
            key = r.get(dim, "unknown")
            buckets.setdefault(str(key), []).append(i)
        for bucket, idxs in buckets.items():
            if len(idxs) < bucket_min_size:
                continue
            preds_b = all_preds[idxs]
            true_b = all_true[idxs]
            tpr = _tpr(preds_b, true_b)
            fpr = _fpr(preds_b, true_b)
            gap = max(abs(tpr - overall_tpr), abs(fpr - overall_fpr)) * 100.0
            finding = FairnessFinding(
                dimension=dim, bucket=bucket, tpr=tpr, fpr=fpr, gap_pp=gap
            )
            report.findings.append(finding)
            if gap > report.max_gap_pp:
                report.max_gap_pp = gap
            if gap > gap_threshold_pp:
                report.has_violations = True
    return report


def _tpr(pred: np.ndarray, true: np.ndarray) -> float:
    pos = true.sum()
    if pos == 0:
        return 0.0
    return float((pred & true).sum() / pos)


def _fpr(pred: np.ndarray, true: np.ndarray) -> float:
    neg = (~true).sum()
    if neg == 0:
        return 0.0
    return float((pred & ~true).sum() / neg)
