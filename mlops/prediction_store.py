"""Stores model predictions in BigQuery for deferred evaluation post-election."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

TABLE_ID_TEMPLATE = "{project_id}.spepe_mlops.fact_predictions"


@dataclass
class PredictionRecord:
    candidato: str
    p_mean: float
    p_lower: float
    p_upper: float
    model_version: str
    session_id: str
    sg_uf: str | None = None
    features: dict[str, Any] | None = None

    def to_bq_row(self) -> dict[str, Any]:
        features_hash = None
        if self.features:
            features_hash = hashlib.md5(
                json.dumps(self.features, sort_keys=True).encode()
            ).hexdigest()

        return {
            "prediction_id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "candidato": self.candidato,
            "sg_uf": self.sg_uf,
            "prediction_date": datetime.now(timezone.utc).isoformat(),
            "p_mean": self.p_mean,
            "p_lower": self.p_lower,
            "p_upper": self.p_upper,
            "model_version": self.model_version,
            "features_hash": features_hash,
            "actual_result": None,
            "brier_score": None,
            "evaluated_at": None,
        }


def store_prediction(record: PredictionRecord, project_id: str | None = None) -> bool:
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
    if not project_id:
        logger.debug("GCP_PROJECT_ID not set — prediction not stored in BigQuery")
        return False

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project_id)
        table_id = TABLE_ID_TEMPLATE.format(project_id=project_id)
        row = record.to_bq_row()
        errors = client.insert_rows_json(table_id, [row])
        if errors:
            logger.error("Prediction store insert error: %s", errors)
            return False
        logger.debug(
            "Prediction stored: %s → %s (%.1f%%)",
            record.candidato,
            record.session_id,
            record.p_mean * 100,
        )
        return True
    except Exception as exc:
        logger.warning("Failed to store prediction: %s", exc)
        return False


def evaluate_deferred(
    candidato: str,
    actual_vote_share: float,
    model_version: str | None = None,
    project_id: str | None = None,
) -> int:
    """Cross predictions × actual result. Returns count of rows updated."""
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
    if not project_id:
        return 0

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project_id)
        table = f"`{project_id}.spepe_mlops.fact_predictions`"

        query_params = [
            bigquery.ScalarQueryParameter("candidato", "STRING", candidato),
            bigquery.ScalarQueryParameter("actual_vote_share", "FLOAT64", actual_vote_share),
        ]

        version_filter = ""
        if model_version:
            query_params.append(
                bigquery.ScalarQueryParameter("model_version", "STRING", model_version)
            )
            version_filter = "AND model_version = @model_version"

        query = f"""
            UPDATE {table}
            SET
                actual_result = @actual_vote_share,
                brier_score   = POWER(@actual_vote_share - p_mean, 2),
                evaluated_at  = CURRENT_TIMESTAMP()
            WHERE candidato = @candidato
              AND actual_result IS NULL
              {version_filter}
        """
        job_config = bigquery.QueryJobConfig(query_parameters=query_params)
        job = client.query(query, job_config=job_config)
        job.result()
        rows_updated = job.num_dml_affected_rows or 0
        logger.info(
            "Deferred evaluation: %d predictions updated for %s",
            rows_updated,
            candidato,
        )
        return rows_updated
    except Exception as exc:
        logger.error("Deferred evaluation failed: %s", exc)
        return 0


def load_predictions_for_eval(
    model_version: str,
    project_id: str | None = None,
) -> Any | None:
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
    if not project_id:
        return None

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project_id)
        query = f"""
            SELECT prediction_id, candidato, sg_uf, p_mean, actual_result, brier_score
            FROM `{project_id}.spepe_mlops.fact_predictions`
            WHERE model_version = @model_version
              AND actual_result IS NOT NULL
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("model_version", "STRING", model_version),
            ]
        )
        return client.query(query, job_config=job_config).to_dataframe()
    except Exception as exc:
        logger.warning("Failed to load predictions: %s", exc)
        return None
