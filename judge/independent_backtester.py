from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    brier_score: float
    calibration_error: float
    ic_coverage: float
    n_samples: int
    mae: float
    residuals: list[float]


def _bq_client(project_id: str):
    try:
        from google.cloud import bigquery

        return bigquery.Client(project=project_id)
    except Exception as exc:
        logger.info("bigquery_unavailable: %s", exc)
        return None


def _fetch_predictions(
    project_id: str, model_version: str, mlops_dataset: str = "spepe_mlops"
) -> list[dict[str, Any]]:
    client = _bq_client(project_id)
    if client is None:
        return []
    query = f"""
        SELECT
          p.cod_municipio_ibge,
          p.nm_candidato,
          p.ano_eleicao,
          p.p_mean,
          p.p_lower,
          p.p_upper,
          e.resultado_real
        FROM `{project_id}.{mlops_dataset}.fact_predictions` p
        JOIN `{project_id}.{mlops_dataset}.ground_truth` e
          ON p.cod_municipio_ibge = e.cod_municipio_ibge
         AND p.nm_candidato = e.nm_candidato
         AND p.ano_eleicao = e.ano_eleicao
        WHERE p.model_version = @model_version
          AND p.shadow = true
          AND e.resultado_real IS NOT NULL
    """
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("model_version", "STRING", model_version)
        ]
    )
    return [dict(row) for row in client.query(query, job_config=job_config).result()]


def run_independent_backtest(
    project_id: str,
    model_version: str,
    rows: list[dict[str, Any]] | None = None,
) -> BacktestResult:
    """Runs a fully independent backtest on shadow predictions.

    If `rows` is provided, uses those directly (useful for tests).
    Otherwise queries BigQuery for shadow=true predictions joined with
    ground truth.
    """
    data = rows if rows is not None else _fetch_predictions(project_id, model_version)
    if not data:
        return BacktestResult(
            brier_score=float("nan"),
            calibration_error=float("nan"),
            ic_coverage=float("nan"),
            n_samples=0,
            mae=float("nan"),
            residuals=[],
        )

    p_mean = np.array([float(r["p_mean"]) for r in data])
    p_lower = np.array([float(r["p_lower"]) for r in data])
    p_upper = np.array([float(r["p_upper"]) for r in data])
    y_true = np.array([float(r["resultado_real"]) for r in data])

    brier = float(np.mean((p_mean - y_true) ** 2))
    mae = float(np.mean(np.abs(p_mean - y_true)))
    coverage = float(np.mean((y_true >= p_lower) & (y_true <= p_upper)))
    calibration_error = _expected_calibration_error(p_mean, y_true)

    return BacktestResult(
        brier_score=brier,
        calibration_error=calibration_error,
        ic_coverage=coverage,
        n_samples=len(data),
        mae=mae,
        residuals=(p_mean - y_true).tolist(),
    )


def _expected_calibration_error(
    p_mean: np.ndarray, y_true: np.ndarray, n_bins: int = 10
) -> float:
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(p_mean)
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (p_mean >= lo) & (p_mean < hi) if i < n_bins - 1 else (
            (p_mean >= lo) & (p_mean <= hi)
        )
        if not mask.any():
            continue
        avg_p = float(p_mean[mask].mean())
        avg_y = float(y_true[mask].mean())
        ece += (mask.sum() / n) * abs(avg_p - avg_y)
    return float(ece)
