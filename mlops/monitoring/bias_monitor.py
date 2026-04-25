"""Fairness monitoring: Brier score by sg_uf, income quintile, and rural pct."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

BIAS_ALERT_THRESHOLD = 1.15  # Flag if group Brier > global * 1.15


@dataclass
class BiasResult:
    model_version: str
    group_type: str
    group_value: str
    brier_score: float
    global_brier: float
    ratio: float
    alert_triggered: bool
    n_samples: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "group_type": self.group_type,
            "group_value": self.group_value,
            "brier_score": self.brier_score,
            "global_brier": self.global_brier,
            "ratio": self.ratio,
            "alert_triggered": self.alert_triggered,
            "n_samples": self.n_samples,
        }


def _brier_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean((y_true - y_pred) ** 2))


def _assign_income_quintile(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["income_quintile"] = pd.qcut(
        df["renda_media_domiciliar"],
        q=5,
        labels=["Q1_baixo", "Q2", "Q3", "Q4", "Q5_alto"],
        duplicates="drop",
    ).astype(str)
    return df


def run_bias_monitor(
    predictions_df: pd.DataFrame,
    model_version: str,
    project_id: str | None = None,
) -> list[BiasResult]:
    """Compute bias metrics. predictions_df must have: y_true, y_pred, sg_uf, renda_media_domiciliar, pct_zona_rural."""
    required = {"y_true", "y_pred", "sg_uf", "renda_media_domiciliar", "pct_zona_rural"}
    missing = required - set(predictions_df.columns)
    if missing:
        logger.warning("Missing columns for bias monitor: %s", missing)
        return []

    df = predictions_df.dropna(subset=list(required))
    global_brier = _brier_score(df["y_true"].values, df["y_pred"].values)
    results: list[BiasResult] = []

    # Group 1: by sg_uf
    for uf, group in df.groupby("sg_uf"):
        if len(group) < 10:
            continue
        bs = _brier_score(group["y_true"].values, group["y_pred"].values)
        ratio = bs / (global_brier + 1e-10)
        alert = ratio > BIAS_ALERT_THRESHOLD
        results.append(
            BiasResult(
                model_version=model_version,
                group_type="sg_uf",
                group_value=str(uf),
                brier_score=bs,
                global_brier=global_brier,
                ratio=ratio,
                alert_triggered=alert,
                n_samples=len(group),
            )
        )
        if alert:
            logger.warning(
                "BIAS ALERT: sg_uf=%s  Brier=%.4f  ratio=%.2f×global", uf, bs, ratio
            )

    # Group 2: income quintile
    df = _assign_income_quintile(df)
    for quintile, group in df.groupby("income_quintile"):
        if len(group) < 10:
            continue
        bs = _brier_score(group["y_true"].values, group["y_pred"].values)
        ratio = bs / (global_brier + 1e-10)
        alert = ratio > BIAS_ALERT_THRESHOLD
        results.append(
            BiasResult(
                model_version=model_version,
                group_type="income_quintile",
                group_value=str(quintile),
                brier_score=bs,
                global_brier=global_brier,
                ratio=ratio,
                alert_triggered=alert,
                n_samples=len(group),
            )
        )

    # Group 3: rural vs urban (pct_zona_rural > 50%)
    for label, mask in [
        ("rural", df["pct_zona_rural"] > 50),
        ("urban", df["pct_zona_rural"] <= 50),
    ]:
        group = df[mask]
        if len(group) < 10:
            continue
        bs = _brier_score(group["y_true"].values, group["y_pred"].values)
        ratio = bs / (global_brier + 1e-10)
        alert = ratio > BIAS_ALERT_THRESHOLD
        results.append(
            BiasResult(
                model_version=model_version,
                group_type="rural_urban",
                group_value=label,
                brier_score=bs,
                global_brier=global_brier,
                ratio=ratio,
                alert_triggered=alert,
                n_samples=len(group),
            )
        )

    _export_to_bigquery(results, project_id)
    return results


def _export_to_bigquery(results: list[BiasResult], project_id: str | None) -> None:
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
    if not project_id or not results:
        return

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project_id)
        table_id = f"{project_id}.spepe_mlops.bias_metrics"
        rows = [r.to_dict() for r in results]
        errors = client.insert_rows_json(table_id, rows)
        if errors:
            logger.error("BigQuery insert errors: %s", errors)
    except Exception as exc:
        logger.warning("Failed to export bias metrics to BigQuery: %s", exc)
