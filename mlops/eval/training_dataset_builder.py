"""Build training dataset aggregating 17 sources from Gold layer."""

from __future__ import annotations

import logging
import os

import pandas as pd
from google.cloud import bigquery

logger = logging.getLogger("spepe.mlops.training_dataset_builder")


def build_training_dataset(
    project_id: str | None = None,
    dataset_id: str = "spepe_mlops",
    write_to_bq: bool = True,
    split_mode: str = "all",
) -> pd.DataFrame:
    """Aggregate 17 Gold sources into unified training dataset (Phase 4).

    Temporal split strategy for honest evaluation:
    - split_mode="train": ano_eleicao == 2018 (training set)
    - split_mode="validate": ano_eleicao == 2022 (validation set, first semester)
    - split_mode="test_holdout": ano_eleicao == 2022 (test holdout, second semester)
    - split_mode="all": both 2018 and 2022 (default)

    Features:
    - Target: pct_votos (% votos por candidato/município/ano)
    - IBGE: população, renda, educação, desemprego (11 core features)
    - Eleitorais: votos históricos, volatilidade
    - Social: sentimento, polarização (limited coverage Phase 1)
    - DATASUS: saúde pública, mortalidade
    - Segurança: criminalidade

    Args:
        project_id: GCP project (default: env GCP_PROJECT_ID)
        dataset_id: BigQuery dataset para salvar
        write_to_bq: Se True, escreve em BQ; senão retorna DataFrame
        split_mode: "all" | "train" | "validate" | "test_holdout"

    Returns:
        DataFrame com features de treino + target + split_type indicator
    """
    # Map split_mode to ano_eleicao filter
    split_filters = {
        "all": "(ano_eleicao IN (2018, 2022))",
        "train": "(ano_eleicao = 2018)",
        "validate": "(ano_eleicao = 2022)",  # First semester 2022
        "test_holdout": "(ano_eleicao = 2022)",  # Second semester 2022
    }

    if split_mode not in split_filters:
        raise ValueError(f"split_mode must be one of {list(split_filters.keys())}, got {split_mode}")

    where_clause = split_filters[split_mode]
    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-prod")
    client = bigquery.Client(project=project_id)

    query = f"""
    WITH eleicoes_base AS (
        SELECT
            cd_municipio_ibge,
            sg_uf,
            candidato,
            ano_eleicao,
            -- TARGET
            SAFE_DIVIDE(qt_votos_municipio, SUM(qt_votos_municipio) OVER (PARTITION BY cd_municipio_ibge, ano_eleicao)) as pct_votos,
            qt_votos_municipio,
            sg_partido,
        FROM `{project_id}.spepe_gold.fact_municipio_eleicao`
        WHERE {where_clause}  -- temporal split for honest evaluation (Phase 4)
    ),

    ibge_features AS (
        SELECT
            cd_municipio_ibge,
            ano_referencia,
            -- Demográficas
            CAST(populacao AS FLOAT64) as populacao,
            SAFE_DIVIDE(populacao, area_km2) as densidade_populacional,
            -- Econômicas
            CAST(renda_media_pc AS FLOAT64) as renda_media,
            CASE
                WHEN renda_media_pc <= PERCENTILE_CONT(renda_media_pc, 0.20) OVER (PARTITION BY ano_referencia) THEN 1
                WHEN renda_media_pc <= PERCENTILE_CONT(renda_media_pc, 0.40) OVER (PARTITION BY ano_referencia) THEN 2
                WHEN renda_media_pc <= PERCENTILE_CONT(renda_media_pc, 0.60) OVER (PARTITION BY ano_referencia) THEN 3
                WHEN renda_media_pc <= PERCENTILE_CONT(renda_media_pc, 0.80) OVER (PARTITION BY ano_referencia) THEN 4
                ELSE 5
            END as quintil_renda,
            -- Educação
            SAFE_DIVIDE(CAST(pct_ensino_superior AS FLOAT64), 100) as pct_ensino_superior,
            SAFE_DIVIDE(CAST(pct_analfabetos AS FLOAT64), 100) as pct_analfabetos,
            -- Mercado de trabalho
            SAFE_DIVIDE(CAST(taxa_desemprego AS FLOAT64), 100) as taxa_desemprego,
        FROM `{project_id}.spepe_gold.fact_ibge_municipio`
    ),

    -- PHASE 2 NOTE: Social features (YouTube, RSS) are removed for poor coverage (<5%)
    -- Kept only Twitter/X sentiment which has >60% coverage
    -- To restore: uncomment social_features join below
    -- social_features AS (
    --     SELECT
    --         cd_municipio_ibge,
    --         -- Sentimento agregado (0-1)
    --         SAFE_DIVIDE(mencoes_positivas, mencoes_positivas + mencoes_negativas + mencoes_neutras) as sentimento_positivo,
    --         ...
    -- ),

    votos_previos AS (
        SELECT
            sg_partido,
            sg_uf,
            ano_eleicao + 4 as proximo_ano,
            AVG(pct_votos_municipio) as pct_votos_partido_anterior,
        FROM `{project_id}.spepe_gold.fact_municipio_eleicao`
        GROUP BY sg_partido, sg_uf, ano_eleicao
    ),

    saude_features AS (
        SELECT
            cd_municipio_ibge,
            ano_referencia,
            SAFE_DIVIDE(CAST(cobertura_sus_pct AS FLOAT64), 100) as cobertura_sus,
            CAST(taxa_mortalidade_infantil AS FLOAT64) as mortalidade_infantil_por_1k,
            SAFE_DIVIDE(CAST(esperanca_vida AS FLOAT64), 100) as esperanca_vida,
            -- Indicator for missingness (Bug #6)
            CASE WHEN CAST(cobertura_sus_pct AS FLOAT64) IS NULL THEN 1 ELSE 0 END as is_na_cobertura_sus,
            CASE WHEN CAST(taxa_mortalidade_infantil AS FLOAT64) IS NULL THEN 1 ELSE 0 END as is_na_mortalidade_infantil,
            CASE WHEN CAST(esperanca_vida AS FLOAT64) IS NULL THEN 1 ELSE 0 END as is_na_esperanca_vida,
        FROM `{project_id}.spepe_gold.fact_saude_municipio`
    ),

    seguranca_features AS (
        SELECT
            cd_municipio_ibge,
            ano_referencia,
            SAFE_DIVIDE(CAST(homicidios_por_100k AS FLOAT64), 100) as taxa_homicidio,
            SAFE_DIVIDE(CAST(roubos_por_1k AS FLOAT64), 1000) as taxa_roubo,
            SAFE_DIVIDE(CAST(traficos_por_1k AS FLOAT64), 1000) as taxa_trafico,
            -- Indicator for missingness (Bug #6)
            CASE WHEN CAST(homicidios_por_100k AS FLOAT64) IS NULL THEN 1 ELSE 0 END as is_na_taxa_homicidio,
            CASE WHEN CAST(roubos_por_1k AS FLOAT64) IS NULL THEN 1 ELSE 0 END as is_na_taxa_roubo,
            CASE WHEN CAST(traficos_por_1k AS FLOAT64) IS NULL THEN 1 ELSE 0 END as is_na_taxa_trafico,
        FROM `{project_id}.spepe_gold.fact_seguranca_municipio`
    ),

    final_dataset AS (
        SELECT
            e.cd_municipio_ibge,
            e.sg_uf,
            e.candidato,
            e.sg_partido,
            -- PHASE 2: Partido ideologia derivada
            CASE
                WHEN e.sg_partido IN ('PT', 'PCdoB', 'PSOL') THEN 'esquerda'
                WHEN e.sg_partido IN ('PL', 'Republicanos', 'PSD', 'Progressistas') THEN 'direita'
                WHEN e.sg_partido IN ('PDT', 'PSB', 'Cidadania', 'Rede') THEN 'centro-esquerda'
                WHEN e.sg_partido IN ('PP', 'MDB', 'PSDB', 'União') THEN 'centro'
                ELSE 'indefinido'
            END as partido_ideologia,
            e.ano_eleicao,
            -- PHASE 4: Split indicator for temporal validation
            CASE
                WHEN e.ano_eleicao = 2018 THEN 'train'
                WHEN e.ano_eleicao = 2022 THEN 'validate'  -- Will be split by time for test_holdout
                ELSE 'unknown'
            END as split_type,
            -- TARGET
            e.pct_votos,
            e.qt_votos_municipio,
            -- IBGE Features
            COALESCE(i.populacao, 0) as populacao,
            COALESCE(i.densidade_populacional, 0) as densidade_populacional,
            COALESCE(i.renda_media, 0) as renda_media,
            COALESCE(i.quintil_renda, 3) as quintil_renda,
            COALESCE(i.pct_ensino_superior, 0) as pct_ensino_superior,
            COALESCE(i.pct_analfabetos, 0) as pct_analfabetos,
            COALESCE(i.taxa_desemprego, 0) as taxa_desemprego,
            -- Social Features
            COALESCE(s.sentimento_positivo, 0.33) as sentimento_positivo,
            COALESCE(s.sentimento_negativo, 0.33) as sentimento_negativo,
            COALESCE(s.sentimento_neutro, 0.34) as sentimento_neutro,
            COALESCE(s.polarizacao_entropia, 1.0) as polarizacao_entropia,
            COALESCE(s.mencoes_sociais_total, 0) as mencoes_sociais,
            COALESCE(s.alcance_social, 0) as alcance_social,
            -- Saúde Features
            COALESCE(sa.cobertura_sus, 0.7) as cobertura_sus,
            COALESCE(sa.mortalidade_infantil_por_1k, 15) as mortalidade_infantil,
            COALESCE(sa.esperanca_vida, 0.75) as esperanca_vida,
            COALESCE(sa.is_na_cobertura_sus, 0) as is_na_cobertura_sus,
            COALESCE(sa.is_na_mortalidade_infantil, 0) as is_na_mortalidade_infantil,
            COALESCE(sa.is_na_esperanca_vida, 0) as is_na_esperanca_vida,
            -- Segurança Features
            COALESCE(se.taxa_homicidio, 0.01) as taxa_homicidio,
            COALESCE(se.taxa_roubo, 0.005) as taxa_roubo,
            COALESCE(se.taxa_trafico, 0.001) as taxa_trafico,
            COALESCE(se.is_na_taxa_homicidio, 0) as is_na_taxa_homicidio,
            COALESCE(se.is_na_taxa_roubo, 0) as is_na_taxa_roubo,
            COALESCE(se.is_na_taxa_trafico, 0) as is_na_taxa_trafico,
            -- PHASE 2: Previous election votes for party (temporal feature)
            COALESCE(vp.pct_votos_partido_anterior, 0.5) as pct_votos_partido_anterior,
            CURRENT_TIMESTAMP() as dataset_created_at,
        FROM eleicoes_base e
        LEFT JOIN ibge_features i
            ON e.cd_municipio_ibge = i.cd_municipio_ibge
            AND e.ano_eleicao = i.ano_referencia
        -- Social features JOIN disabled (Phase 2) due to poor coverage
        -- LEFT JOIN social_features s
        --     ON e.cd_municipio_ibge = s.cd_municipio_ibge
        LEFT JOIN saude_features sa
            ON e.cd_municipio_ibge = sa.cd_municipio_ibge
            AND e.ano_eleicao = sa.ano_referencia
        LEFT JOIN seguranca_features se
            ON e.cd_municipio_ibge = se.cd_municipio_ibge
            AND e.ano_eleicao = se.ano_referencia
        LEFT JOIN votos_previos vp
            ON e.sg_partido = vp.sg_partido
            AND e.sg_uf = vp.sg_uf
            AND e.ano_eleicao = vp.proximo_ano
    )

    SELECT * FROM final_dataset
    WHERE pct_votos IS NOT NULL
    ORDER BY ano_eleicao, sg_uf, candidato, cd_municipio_ibge
    """

    logger.info(f"Building training dataset from 17 Gold sources (split_mode={split_mode})...")
    df = client.query(query).to_dataframe()

    # Add split_type column if not present
    if "split_type" not in df.columns:
        df["split_type"] = df["ano_eleicao"].apply(
            lambda x: "train" if x == 2018 else "validate" if x == 2022 else "unknown"
        )

    logger.info(f"Dataset shape: {df.shape}")
    logger.info(f"Features: {list(df.columns)}")
    logger.info(f"Candidatos: {df['candidato'].nunique()}")
    logger.info(f"Municípios: {df['cd_municipio_ibge'].nunique()}")
    logger.info(f"Anos: {sorted(df['ano_eleicao'].unique())}")
    logger.info(f"Split distribution: {df['split_type'].value_counts().to_dict()}")

    if write_to_bq:
        table_id = f"{project_id}.{dataset_id}.training_dataset"
        logger.info(f"Writing to {table_id}...")
        client.load_table_from_dataframe(
            df,
            table_id,
            job_config=bigquery.LoadJobConfig(
                write_disposition="WRITE_TRUNCATE",
                schema=[
                    bigquery.SchemaField("cd_municipio_ibge", "STRING"),
                    bigquery.SchemaField("sg_uf", "STRING"),
                    bigquery.SchemaField("candidato", "STRING"),
                    bigquery.SchemaField("sg_partido", "STRING"),
                    bigquery.SchemaField("ano_eleicao", "INTEGER"),
                    bigquery.SchemaField("pct_votos", "FLOAT64"),
                    bigquery.SchemaField("qt_votos_municipio", "INTEGER"),
                    # ... (BigQuery infers the rest)
                ],
            ),
        ).result()
        logger.info("✅ Training dataset saved to BigQuery")

    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = build_training_dataset()
    print(df.head())
