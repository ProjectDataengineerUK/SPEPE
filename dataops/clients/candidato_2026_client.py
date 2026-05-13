"""Fetch and validate 2026 candidates for temporal validation in model phase 4."""

from __future__ import annotations

import logging
import os

import pandas as pd
from google.cloud import bigquery

logger = logging.getLogger("spepe.dataops.candidato_2026_client")


def fetch_candidatos_oficiais_2026(
    project_id: str | None = None,
    dataset_id: str = "spepe_gold",
    min_intencao_pct: float = 1.0,
) -> pd.DataFrame:
    """Fetch 2026 presidential candidates from TSE or fallback to polls.

    Phase 4 feature: Validate that model predictions include only registered
    candidates, using TSE official data when available, falling back to
    candidates with >min_intencao_pct in polling data.

    Args:
        project_id: GCP project ID (default: env GCP_PROJECT_ID or spepe-prod)
        dataset_id: BigQuery dataset (default: spepe_gold)
        min_intencao_pct: Minimum polling percentage to include fallback candidates

    Returns:
        DataFrame with columns:
        - candidato (str): candidate name
        - sg_partido (str): party abbreviation
        - eligibilidade (str): "elegível", "inelegível", "unknown"
        - data_registro (datetime): registration date or None for fallback
        - source (str): "tse_oficial" or "pesquisa_fallback"
    """
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-prod")
    client = bigquery.Client(project=project_id)

    # Try to fetch from TSE official candidatos table (2026)
    query_tse = f"""
    SELECT
        candidato,
        sg_partido,
        CASE
            WHEN inelegibilidade = '' OR inelegibilidade IS NULL THEN 'elegível'
            WHEN inelegibilidade LIKE '%Lei da Ficha Limpa%' THEN 'inelegível'
            ELSE 'inelegível'
        END as eligibilidade,
        CAST(data_registro AS DATETIME) as data_registro,
        'tse_oficial' as source,
    FROM `{project_id}.{dataset_id}.fact_candidato_eleicao`
    WHERE ano_eleicao = 2026
      AND cd_cargo = 1  -- president only
    ORDER BY candidato
    """

    try:
        df_tse = client.query(query_tse).to_dataframe()
        logger.info(f"Fetched {len(df_tse)} candidates from TSE official data")
        if not df_tse.empty:
            return df_tse
    except Exception as e:
        logger.warning(f"TSE official query failed: {e}. Falling back to polling data.")

    # Fallback: Use polling data with >min_intencao_pct
    query_fallback = f"""
    SELECT
        candidato,
        sg_partido,
        'unknown' as eligibilidade,
        CAST(NULL AS DATETIME) as data_registro,
        'pesquisa_fallback' as source,
    FROM `{project_id}.{dataset_id}.fact_pesquisa_intencao`
    WHERE ano_eleicao = 2026
      AND pct_intencao >= {min_intencao_pct}
    GROUP BY candidato, sg_partido
    ORDER BY candidato
    """

    try:
        df_fallback = client.query(query_fallback).to_dataframe()
        logger.info(
            f"Falling back to polling data: {len(df_fallback)} candidates with "
            f">={min_intencao_pct}% polling"
        )
        return df_fallback
    except Exception as e:
        logger.error(f"Both TSE and polling queries failed: {e}")
        return pd.DataFrame(
            columns=["candidato", "sg_partido", "eligibilidade", "data_registro", "source"]
        )


def validate_2026_candidates(
    predictions_df: pd.DataFrame,
    project_id: str | None = None,
) -> dict:
    """Validate that predictions include only registered 2026 candidates.

    Phase 4 gate: Check for predictions on non-existent candidates (drift indicator).

    Args:
        predictions_df: DataFrame with 'candidato' column from model output
        project_id: GCP project ID

    Returns:
        Dict with:
        - valid_count: predictions on registered candidates
        - invalid_count: predictions on non-registered candidates
        - invalid_candidatos: list of non-registered candidates predicted
        - pass_gate: bool - True if invalid_count == 0
    """
    candidatos_2026 = fetch_candidatos_oficiais_2026(project_id=project_id)

    if candidatos_2026.empty:
        logger.warning("No candidates found for 2026 validation")
        return {
            "valid_count": 0,
            "invalid_count": 0,
            "invalid_candidatos": [],
            "pass_gate": False,
        }

    registered_set = set(candidatos_2026["candidato"].unique())
    predicted_set = set(predictions_df["candidato"].unique())

    invalid_candidatos = list(predicted_set - registered_set)
    valid_count = len(predicted_set & registered_set)
    invalid_count = len(invalid_candidatos)

    logger.info(
        f"2026 candidate validation: {valid_count} valid, {invalid_count} invalid (ghost candidates)"
    )

    return {
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "invalid_candidatos": invalid_candidatos,
        "pass_gate": invalid_count == 0,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Example: fetch 2026 candidates
    df_candidatos = fetch_candidatos_oficiais_2026()
    print(f"Fetched {len(df_candidatos)} candidates:")
    print(df_candidatos)

    # Example: validate predictions (dummy data)
    dummy_predictions = pd.DataFrame({
        "candidato": ["Lula", "Bolsonaro", "Ciro Gomes", "Fantasma"],
        "sg_partido": ["PT", "PL", "PDT", "FAKE"],
    })
    validation_result = validate_2026_candidates(dummy_predictions)
    print(f"\nValidation result: {validation_result}")
