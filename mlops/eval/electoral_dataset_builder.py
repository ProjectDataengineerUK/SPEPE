"""Build ELECTORAL training dataset — TSE historical + polls + IBGE + party.

This replaces the demographic-only builder with a full 4-source integration:
  - 30%  Historical vote share (2018 → 2022 lag, strongest predictor)
  - 25%  Polls aggregated per (candidato, UF, ano)
  - 25%  IBGE socioeconomic indicators (demographic context)
  - 15%  Candidate + party identity (partial pooling)
  -  5%  UF random effects (pooling)

SQL structure:
  votos_base         → all UFs via tse_* wildcard, per (mun, cand, year)
  votos_pct          → pct_votos = votos / total_municipio (simplex per mun×year)
  historical_lag     → 2018 vote share matched to 2022 rows (same cand, same mun)
  candidato_partido  → sg_partido from dim_candidato (with fallback)
  pesquisas_agg      → avg poll intention per (candidato, UF, ano)
  FINAL              → joined with IBGE, assertions, quality gates
"""

from __future__ import annotations

import logging
import os

import pandas as pd
from google.cloud import bigquery

logger = logging.getLogger("spepe.mlops.electoral_dataset_builder")

_IMPUTATION_DEFAULTS = {
    "populacao_total": 0,
    "renda_per_capita": 1000,
    "taxa_alfabetizacao": 50.0,
    "taxa_analfabetismo": 10.0,
}

_BRIER_GATE = 0.10
_HISTORY_MATCH_MIN = 0.40
_IBGE_JOIN_MIN = 0.95
_ROW_COUNT_MIN = 30_000
_ROW_COUNT_MAX = 200_000


def build_electoral_dataset(
    project_id: str | None = None,
    dataset_id: str = "spepe_mlops",
    write_to_bq: bool = True,
    split_mode: str = "all",
) -> pd.DataFrame:
    """Build full electoral training dataset for real electoral prediction.

    Temporal splits:
      split_mode='all'   → 2018 (train) + 2022 (validate)
      split_mode='train' → 2018 only
      split_mode='validate' → 2022 only

    Returns DataFrame with columns:
      cd_municipio_ibge, sg_uf, ano_eleicao, candidato, split_type,
      y (target pct_votos), qt_votos, total_municipio,
      pct_votos_historico, is_new_candidate,
      media_intencao_voto_uf, n_pesquisas, poll_missing,
      sg_partido, ds_cargo,
      populacao_total, renda_per_capita, taxa_alfabetizacao, taxa_analfabetismo
    """
    split_filters = {
        "all": "(v.ano_eleicao IN (2018, 2022))",
        "train": "(v.ano_eleicao = 2018)",
        "validate": "(v.ano_eleicao = 2022)",
    }
    if split_mode not in split_filters:
        raise ValueError(f"split_mode must be one of {list(split_filters.keys())}")

    year_filter = split_filters[split_mode]
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-prod")
    client = bigquery.Client(project=project_id)

    # Detect whether dim_candidato and pesquisas_* exist
    _has_dim_candidato = _table_exists(client, project_id, "spepe_silver", "dim_candidato")
    _has_pesquisas = _table_exists(
        client, project_id, "spepe_silver", "pesquisas_2018"
    ) or _table_exists(client, project_id, "spepe_silver", "pesquisas_2022")

    if not _has_dim_candidato:
        logger.warning("dim_candidato not found — sg_partido will be DESCONHECIDO for all rows")
    if not _has_pesquisas:
        logger.warning("pesquisas_* not found — poll feature will be 0 for all rows")

    dim_cand_join = (
        f"""
    LEFT JOIN (
        SELECT nm_candidato AS candidato, ano_eleicao,
               ANY_VALUE(sg_partido) AS sg_partido,
               ANY_VALUE(ds_cargo)   AS ds_cargo
        FROM `{project_id}.spepe_silver.dim_candidato`
        GROUP BY nm_candidato, ano_eleicao
    ) c ON c.candidato = v.candidato AND c.ano_eleicao = v.ano_eleicao
    """
        if _has_dim_candidato
        else ""
    )

    pesquisas_cte = (
        f"""
    pesquisas_agg AS (
        SELECT
            candidato,
            sg_uf,
            EXTRACT(YEAR FROM data_pesquisa) AS ano_eleicao,
            AVG(CAST(intencao_voto AS FLOAT64)) AS media_intencao_voto_uf,
            COUNT(*)                            AS n_pesquisas
        FROM `{project_id}.spepe_silver.pesquisas_*`
        WHERE intencao_voto IS NOT NULL
          AND EXTRACT(YEAR FROM data_pesquisa) IN (2018, 2022)
        GROUP BY candidato, sg_uf, EXTRACT(YEAR FROM data_pesquisa)
    ),
    """
        if _has_pesquisas
        else "pesquisas_agg AS (SELECT '' AS candidato, '' AS sg_uf, 0 AS ano_eleicao, 0.0 AS media_intencao_voto_uf, 0 AS n_pesquisas WHERE FALSE),"
    )

    pesquisas_join = """
    LEFT JOIN pesquisas_agg p
           ON p.candidato   = v.candidato
          AND p.sg_uf       = v.sg_uf
          AND p.ano_eleicao = v.ano_eleicao
    """

    query = f"""
    WITH votos_base AS (
        -- All 27 UFs via wildcard; tse_XX_YYYY pattern only
        SELECT
            CAST(cd_municipio_ibge AS STRING)                       AS cd_municipio_ibge,
            COALESCE(sg_uf_y, sg_uf_x)                             AS sg_uf,
            CAST(ano_eleicao AS INT64)                              AS ano_eleicao,
            UPPER(TRIM(REGEXP_REPLACE(nm_candidato, r'\\s+', ' '))) AS candidato,
            SUM(CAST(qt_votos AS FLOAT64))                          AS qt_votos,
            SUM(SUM(CAST(qt_votos AS FLOAT64)))
                OVER (PARTITION BY CAST(cd_municipio_ibge AS STRING),
                                   CAST(ano_eleicao AS INT64))      AS total_municipio,
        FROM `{project_id}.spepe_silver.tse_*`
        WHERE REGEXP_CONTAINS(_TABLE_SUFFIX, r'^[a-z]{{2}}_(2018|2022)$')
          AND nm_candidato IS NOT NULL
          AND qt_votos IS NOT NULL
          AND CAST(qt_votos AS FLOAT64) > 0
        GROUP BY cd_municipio_ibge, sg_uf_y, sg_uf_x, ano_eleicao, nm_candidato
    ),

    votos_pct AS (
        SELECT *,
               SAFE_DIVIDE(qt_votos, total_municipio) AS pct_votos
        FROM votos_base
        WHERE total_municipio > 0
    ),

    -- Historical lag: 2018 pct_votos → matched to 2022 rows
    -- For 2018 rows: no prior election in this dataset → pct_votos_historico = 0
    historical_lag AS (
        SELECT
            cd_municipio_ibge,
            candidato,
            ano_eleicao + 4     AS target_ano,
            pct_votos           AS pct_votos_historico
        FROM votos_pct
        WHERE ano_eleicao = 2018
    ),

    {pesquisas_cte}

    ibge AS (
        SELECT
            CAST(cd_municipio_ibge AS STRING)       AS cd_municipio_ibge,
            CAST(populacao_total   AS FLOAT64)      AS populacao_total,
            CAST(renda_per_capita  AS FLOAT64)      AS renda_per_capita,
            CAST(taxa_alfabetizacao AS FLOAT64)     AS taxa_alfabetizacao,
            CAST(taxa_analfabetismo AS FLOAT64)     AS taxa_analfabetismo
        FROM `{project_id}.spepe_gold.fact_ibge_municipio`
    )

    SELECT
        v.cd_municipio_ibge,
        v.sg_uf,
        v.ano_eleicao,
        v.candidato,
        CASE
            WHEN v.ano_eleicao = 2018 THEN 'train'
            WHEN v.ano_eleicao = 2022 THEN 'validate'
            ELSE 'unknown'
        END AS split_type,

        -- Target (Beta likelihood: must be in open (0,1))
        ROUND(v.pct_votos, 6)                                       AS y,
        v.qt_votos,
        v.total_municipio,

        -- Historical feature (30% weight) — strongest predictor
        COALESCE(h.pct_votos_historico, 0.0)                        AS pct_votos_historico,
        CAST(h.pct_votos_historico IS NULL AS INT64)                 AS is_new_candidate,

        -- Poll feature (25% weight)
        COALESCE(p.media_intencao_voto_uf, 0.0)                     AS media_intencao_voto_uf,
        COALESCE(p.n_pesquisas, 0)                                   AS n_pesquisas,
        CAST(p.media_intencao_voto_uf IS NULL AS INT64)              AS poll_missing,

        -- Party / cargo identity
        COALESCE(c.sg_partido, 'DESCONHECIDO')                      AS sg_partido,
        COALESCE(c.ds_cargo,   'DESCONHECIDO')                      AS ds_cargo,

        -- IBGE demographics (25% weight — log1p applied in Python)
        COALESCE(i.populacao_total,    0)     AS populacao_total,
        COALESCE(i.renda_per_capita,   1000)  AS renda_per_capita,
        COALESCE(i.taxa_alfabetizacao, 50.0)  AS taxa_alfabetizacao,
        COALESCE(i.taxa_analfabetismo, 10.0)  AS taxa_analfabetismo

    FROM votos_pct v
    LEFT JOIN historical_lag h
           ON h.cd_municipio_ibge = v.cd_municipio_ibge
          AND h.candidato         = v.candidato
          AND h.target_ano        = v.ano_eleicao
    {pesquisas_join}
    {dim_cand_join}
    LEFT JOIN ibge i ON i.cd_municipio_ibge = v.cd_municipio_ibge

    WHERE {year_filter}
      AND v.pct_votos BETWEEN 0.0001 AND 0.9999
      AND i.populacao_total IS NOT NULL
    ORDER BY v.sg_uf, v.cd_municipio_ibge, v.ano_eleicao, v.candidato
    """

    logger.info("Building electoral dataset (split_mode=%s)...", split_mode)
    try:
        df = client.query(query).to_dataframe()
    except Exception as exc:
        logger.error("BQ query failed: %s", exc)
        return pd.DataFrame()

    if df.empty:
        logger.error("Electoral dataset is empty — check Silver data")
        return df

    logger.info(
        "Raw shape: %s | UFs: %d | municípios: %d | candidatos: %d",
        df.shape,
        df["sg_uf"].nunique(),
        df["cd_municipio_ibge"].nunique(),
        df["candidato"].nunique(),
    )

    # ── Data quality gates ────────────────────────────────────────────────────
    _assert_data_quality(df)

    # ── IBGE imputation rate logging ─────────────────────────────────────────
    for col, default in _IMPUTATION_DEFAULTS.items():
        if col in df.columns:
            imputed = (df[col] == default).mean()
            if imputed > 0.10:
                raise ValueError(
                    f"Imputation rate for {col} is {imputed:.1%} (>10%). "
                    "Fix IBGE join before training."
                )
            logger.info("Imputation rate %s: %.1f%%", col, imputed * 100)

    # ── Log feature fill rates ────────────────────────────────────────────────
    hist_fill = (df["is_new_candidate"] == 0).mean()
    poll_fill = (df["poll_missing"] == 0).mean()
    party_fill = (df["sg_partido"] != "DESCONHECIDO").mean()
    logger.info(
        "Feature fill rates — histórico: %.1f%% | pesquisas: %.1f%% | partido: %.1f%%",
        hist_fill * 100,
        poll_fill * 100,
        party_fill * 100,
    )
    if hist_fill < _HISTORY_MATCH_MIN:
        raise ValueError(
            f"Historical match rate {hist_fill:.1%} < {_HISTORY_MATCH_MIN:.0%}. "
            "Check that 2018 Silver data exists and candidato names are consistent."
        )

    logger.info("Split distribution: %s", df["split_type"].value_counts().to_dict())

    if write_to_bq:
        table_id = f"{project_id}.{dataset_id}.electoral_training_dataset"
        logger.info("Writing electoral dataset to %s...", table_id)
        try:
            client.load_table_from_dataframe(
                df,
                table_id,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
            ).result()
            logger.info("Electoral dataset saved → %s", table_id)
        except Exception as exc:
            logger.error("BQ write failed (non-fatal): %s", exc)

    return df


def _assert_data_quality(df: pd.DataFrame) -> None:
    n = len(df)
    if not (_ROW_COUNT_MIN <= n <= _ROW_COUNT_MAX):
        raise ValueError(
            f"Row count {n:,} outside expected range "
            f"[{_ROW_COUNT_MIN:,}, {_ROW_COUNT_MAX:,}]. "
            "Likely a cartesian join or empty join — check SQL."
        )

    n_mun = df["cd_municipio_ibge"].nunique()
    if n_mun < 5000:
        raise ValueError(
            f"Only {n_mun} municipalities — IBGE join collapsed. "
            "Verify cd_municipio_ibge code format matches across tables."
        )

    # Verify population is unique per municipality (detects wrong join)
    pop_nunique = df.groupby("cd_municipio_ibge")["populacao_total"].nunique().max()
    if pop_nunique > 1:
        raise ValueError(
            "Same municipality has multiple populacao_total values — "
            "IBGE join is producing duplicates (possible code collision)."
        )

    y_median = df["y"].median()
    if not (0.001 < y_median < 0.5):
        raise ValueError(
            f"Target y median={y_median:.4f} is outside [0.001, 0.5]. "
            "Check pct_votos denominator scope."
        )

    ibge_coverage = df["populacao_total"].gt(0).mean()
    if ibge_coverage < _IBGE_JOIN_MIN:
        raise ValueError(
            f"IBGE join coverage {ibge_coverage:.1%} < {_IBGE_JOIN_MIN:.0%}. "
            "IBGE table may be incomplete."
        )


def _table_exists(client: bigquery.Client, project: str, dataset: str, table: str) -> bool:
    try:
        client.get_table(f"{project}.{dataset}.{table}")
        return True
    except Exception:
        return False
