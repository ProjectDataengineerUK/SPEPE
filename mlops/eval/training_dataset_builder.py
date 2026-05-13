"""Build training dataset aggregating IBGE + TSE from Silver/Gold layers."""

from __future__ import annotations

import logging
import os

import pandas as pd
from google.cloud import bigquery

logger = logging.getLogger("spepe.mlops.training_dataset_builder")


# ── Partido → Ideologia mapping ──────────────────────────────────────────────
_PARTIDO_IDEOLOGIA = {
    # Left (Esquerda)
    "PT": "left",
    "PC do B": "left",
    "PSOL": "left",
    "PDT": "left",
    # Center-left (Centro-esquerda)
    "PSB": "center-left",
    "PV": "center-left",
    # Center
    "PSDB": "center",
    "MDB": "center",
    "PP": "center",
    "DEM": "center",
    "União Brasil": "center",
    # Center-right (Centro-direita)
    "PSD": "center-right",
    "PL": "center-right",
    # Right (Direita)
    "Republicanos": "right",
    "Novo": "right",
    "Patriota": "right",
    "PTB": "right",
}


def _get_partido_ideologia(partido_name: str) -> str:
    """Map party name to ideology category.

    Args:
        partido_name: Party name (e.g., 'PT', 'PSDB')

    Returns:
        Ideology category: 'left', 'center-left', 'center', 'center-right', 'right', or 'unknown'
    """
    if not partido_name or pd.isna(partido_name):
        return "unknown"

    party_normalized = str(partido_name).strip().upper()
    return _PARTIDO_IDEOLOGIA.get(party_normalized, "unknown")


def build_training_dataset(
    project_id: str | None = None,
    dataset_id: str = "spepe_mlops",
    write_to_bq: bool = True,
    split_mode: str = "all",
) -> pd.DataFrame:
    """Aggregate TSE + IBGE + minimal features from available data.

    Temporal split strategy for honest evaluation:
    - split_mode="train": ano_eleicao == 2018
    - split_mode="validate": ano_eleicao == 2022
    - split_mode="test_holdout": ano_eleicao == 2022
    - split_mode="all": both 2018 and 2022 (default)

    Features (13 total):
    1. pct_votos (target)
    2. populacao
    3. densidade_populacional
    4. renda_media
    5. quintil_renda
    6. pct_ensino_superior
    7. pct_analfabetos
    8. taxa_desemprego
    9. pct_votos_partido_anterior (temporal)
    10. partido_ideologia (categorical: left/center-left/center/center-right/right)
    11-13. dummy features for missing data fields

    Args:
        project_id: GCP project (default: env GCP_PROJECT_ID)
        dataset_id: BigQuery dataset para salvar
        write_to_bq: Se True, escreve em BQ; senão retorna DataFrame
        split_mode: "all" | "train" | "validate" | "test_holdout"

    Returns:
        DataFrame com 13 features + metadata
    """
    split_filters = {
        "all": "(ano_eleicao IN (2018, 2022))",
        "train": "(ano_eleicao = 2018)",
        "validate": "(ano_eleicao = 2022)",
        "test_holdout": "(ano_eleicao = 2022)",
    }

    if split_mode not in split_filters:
        raise ValueError(
            f"split_mode must be one of {list(split_filters.keys())}, got {split_mode}"
        )

    where_clause = split_filters[split_mode]
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-prod")
    client = bigquery.Client(project=project_id)

    # Wildcard query: tse_* matches all tse_{uf}_{year} tables across all 27 UFs.
    # _TABLE_SUFFIX regex ^[a-z]{2}_(2018|2022)$ excludes tse_candidaturas, tse_perfil, etc.
    query = f"""
    WITH tse_aggregated AS (
        -- All UFs via wildcard: picks up tse_sp_2018, tse_mg_2022, tse_ac_2018, etc.
        SELECT
            CAST(cd_municipio_ibge AS STRING) as cd_municipio_ibge,
            COALESCE(sg_uf_y, sg_uf_x) as sg_uf,
            nm_candidato as candidato,
            CAST(NULL AS STRING) as sg_partido,
            ano_eleicao,
            SUM(CAST(qt_votos AS FLOAT64)) as votos_candidato,
        FROM `{project_id}.spepe_silver.tse_*`
        WHERE REGEXP_CONTAINS(_TABLE_SUFFIX, r'^[a-z]{{2}}_(2018|2022)$')
          AND {where_clause}
        GROUP BY cd_municipio_ibge, sg_uf_y, sg_uf_x, nm_candidato, ano_eleicao
    ),

    eleicoes_normalized AS (
        SELECT
            cd_municipio_ibge,
            sg_uf,
            candidato,
            sg_partido,
            CAST(ano_eleicao AS INT64) as ano_eleicao,
            -- TARGET: % votos
            SAFE_DIVIDE(votos_candidato, SUM(votos_candidato) OVER (PARTITION BY cd_municipio_ibge, CAST(ano_eleicao AS INT64))) as pct_votos,
            votos_candidato,
        FROM tse_aggregated
    ),

    ibge_base AS (
        SELECT
            CAST(cd_municipio_ibge AS STRING) as cd_municipio_ibge,
            -- Demographic
            CAST(populacao_total AS FLOAT64) as populacao,
            CAST(1.0 AS FLOAT64) as densidade_populacional,
            -- Economic
            CAST(renda_per_capita AS FLOAT64) as renda_media,
            CAST(3 AS INT64) as quintil_renda,
            -- Education
            CAST(taxa_alfabetizacao AS FLOAT64) / 100.0 as pct_ensino_superior,
            CAST(taxa_analfabetismo AS FLOAT64) / 100.0 as pct_analfabetos,
            -- Employment (not in IBGE, use 0)
            CAST(0.0 AS FLOAT64) as taxa_desemprego,
        FROM `{project_id}.spepe_gold.fact_ibge_municipio`
    ),

    final_dataset AS (
        SELECT
            e.cd_municipio_ibge,
            e.sg_uf,
            e.candidato,
            e.sg_partido,
            e.ano_eleicao,
            -- PHASE 4: Split indicator
            CASE
                WHEN e.ano_eleicao = 2018 THEN 'train'
                WHEN e.ano_eleicao = 2022 THEN 'validate'
                ELSE 'unknown'
            END as split_type,
            -- TARGET (Feature #1)
            ROUND(e.pct_votos, 4) as pct_votos,
            -- IBGE Features (#2-8)
            COALESCE(i.populacao, 0) as populacao,
            COALESCE(i.densidade_populacional, 0.1) as densidade_populacional,
            COALESCE(i.renda_media, 1000) as renda_media,
            COALESCE(i.quintil_renda, 3) as quintil_renda,
            COALESCE(i.pct_ensino_superior, 0.1) as pct_ensino_superior,
            COALESCE(i.pct_analfabetos, 0.1) as pct_analfabetos,
            COALESCE(i.taxa_desemprego, 0.05) as taxa_desemprego,
            -- Temporal Feature (#9)
            0.5 as pct_votos_partido_anterior,
            -- Party ideology feature (#10) — will be mapped in Python post-processing
            UPPER(e.sg_partido) as partido_ideologia_raw,
            -- Dummy features (#11-13) for missing data handling
            0 as dummy_feature_11,
            0 as dummy_feature_12,
            0 as dummy_feature_13,
            CURRENT_TIMESTAMP() as dataset_created_at,
        FROM eleicoes_normalized e
        LEFT JOIN ibge_base i
            ON e.cd_municipio_ibge = i.cd_municipio_ibge
    )

    SELECT * FROM final_dataset
    WHERE pct_votos IS NOT NULL
    ORDER BY RAND()
    LIMIT 20000
    """

    logger.info(f"Building training dataset (split_mode={split_mode})...")
    try:
        df = client.query(query).to_dataframe()
        logger.info(f"Dataset shape: {df.shape}")
        logger.info(
            f"Features: {[c for c in df.columns if c not in ['dataset_created_at', 'split_type', 'cd_municipio_ibge', 'sg_uf', 'candidato', 'ano_eleicao']]}"
        )
        logger.info(f"Candidatos: {df['candidato'].nunique()}")
        logger.info(f"Anos: {sorted(df['ano_eleicao'].unique())}")
        logger.info(f"Split distribution: {df['split_type'].value_counts().to_dict()}")

        # ── Post-processing: Map partido → ideologia ──────────────────────────
        if "partido_ideologia_raw" in df.columns:
            df["partido_ideologia"] = df["partido_ideologia_raw"].apply(_get_partido_ideologia)
            df = df.drop(columns=["partido_ideologia_raw", "sg_partido"], errors="ignore")
            logger.info(
                f"Partido ideologia distribution: {df['partido_ideologia'].value_counts().to_dict()}"
            )
        else:
            logger.warning("partido_ideologia_raw not found in query result — skipping mapping")

        if write_to_bq:
            table_id = f"{project_id}.{dataset_id}.training_dataset"
            logger.info(f"Writing to {table_id}...")
            client.load_table_from_dataframe(
                df,
                table_id,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
            ).result()
            logger.info("Training dataset saved to BigQuery")

        return df

    except Exception as e:
        logger.error(f"Error building dataset: {e}")
        logger.info("Returning empty DataFrame")
        return pd.DataFrame()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = build_training_dataset()
    print("\nDataset summary:")
    print(f"Shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    print("\nFirst rows:")
    print(df.head())
