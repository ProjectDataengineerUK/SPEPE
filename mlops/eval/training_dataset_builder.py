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
) -> pd.DataFrame:
    """Aggregate 17 Gold sources into unified training dataset.

    Features:
    - Target: pct_votos (% votos por candidato/município/ano)
    - IBGE: população, renda, educação, desemprego
    - Eleitorais: votos 2018/2022, volatilidade, base de apoio
    - Social: sentimento, polarização, menções
    - DATASUS: saúde pública
    - Segurança: criminalidade

    Args:
        project_id: GCP project (default: env GCP_PROJECT_ID)
        dataset_id: BigQuery dataset para salvar
        write_to_bq: Se True, escreve em BQ; senão retorna DataFrame

    Returns:
        DataFrame com features de treino + target
    """
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
        WHERE ano_eleicao IN (2018, 2022)  -- histórico para validação
    ),

    ibge_features AS (
        SELECT
            cd_municipio_ibge,
            -- Demográficas
            CAST(populacao AS FLOAT64) as populacao,
            SAFE_DIVIDE(populacao, area_km2) as densidade_populacional,
            -- Econômicas
            CAST(renda_media_pc AS FLOAT64) as renda_media,
            CASE
                WHEN renda_media_pc <= PERCENTILE_CONT(renda_media_pc, 0.20) OVER () THEN 1
                WHEN renda_media_pc <= PERCENTILE_CONT(renda_media_pc, 0.40) OVER () THEN 2
                WHEN renda_media_pc <= PERCENTILE_CONT(renda_media_pc, 0.60) OVER () THEN 3
                WHEN renda_media_pc <= PERCENTILE_CONT(renda_media_pc, 0.80) OVER () THEN 4
                ELSE 5
            END as quintil_renda,
            -- Educação
            SAFE_DIVIDE(CAST(pct_ensino_superior AS FLOAT64), 100) as pct_ensino_superior,
            SAFE_DIVIDE(CAST(pct_analfabetos AS FLOAT64), 100) as pct_analfabetos,
            -- Mercado de trabalho
            SAFE_DIVIDE(CAST(taxa_desemprego AS FLOAT64), 100) as taxa_desemprego,
        FROM `{project_id}.spepe_gold.fact_ibge_municipio`
    ),

    social_features AS (
        SELECT
            cd_municipio_ibge,
            -- Sentimento agregado (0-1)
            SAFE_DIVIDE(mencoes_positivas, mencoes_positivas + mencoes_negativas + mencoes_neutras) as sentimento_positivo,
            SAFE_DIVIDE(mencoes_negativas, mencoes_positivas + mencoes_negativas + mencoes_neutras) as sentimento_negativo,
            SAFE_DIVIDE(mencoes_neutras, mencoes_positivas + mencoes_negativas + mencoes_neutras) as sentimento_neutro,
            -- Polarização (entropia)
            -1 * (
                SAFE_DIVIDE(mencoes_positivas, mencoes_positivas + mencoes_negativas + mencoes_neutras) *
                LOG(SAFE_DIVIDE(mencoes_positivas, mencoes_positivas + mencoes_negativas + mencoes_neutras) + 0.001) +
                SAFE_DIVIDE(mencoes_negativas, mencoes_positivas + mencoes_negativas + mencoes_neutras) *
                LOG(SAFE_DIVIDE(mencoes_negativas, mencoes_positivas + mencoes_negativas + mencoes_neutras) + 0.001) +
                SAFE_DIVIDE(mencoes_neutras, mencoes_positivas + mencoes_negativas + mencoes_neutras) *
                LOG(SAFE_DIVIDE(mencoes_neutras, mencoes_positivas + mencoes_negativas + mencoes_neutras) + 0.001)
            ) as polarizacao_entropia,
            CAST(mencoes_total AS FLOAT64) as mencoes_sociais_total,
            CAST(alcance_total AS FLOAT64) as alcance_social,
        FROM `{project_id}.spepe_gold.fact_social_municipio`
    ),

    saude_features AS (
        SELECT
            cd_municipio_ibge,
            SAFE_DIVIDE(CAST(cobertura_sus_pct AS FLOAT64), 100) as cobertura_sus,
            CAST(taxa_mortalidade_infantil AS FLOAT64) as mortalidade_infantil_por_1k,
            SAFE_DIVIDE(CAST(esperanca_vida AS FLOAT64), 100) as esperanca_vida,
        FROM `{project_id}.spepe_gold.fact_saude_municipio`
    ),

    seguranca_features AS (
        SELECT
            cd_municipio_ibge,
            SAFE_DIVIDE(CAST(homicidios_por_100k AS FLOAT64), 100) as taxa_homicidio,
            SAFE_DIVIDE(CAST(roubos_por_1k AS FLOAT64), 1000) as taxa_roubo,
            SAFE_DIVIDE(CAST(traficos_por_1k AS FLOAT64), 1000) as taxa_trafico,
        FROM `{project_id}.spepe_gold.fact_seguranca_municipio`
    ),

    final_dataset AS (
        SELECT
            e.cd_municipio_ibge,
            e.sg_uf,
            e.candidato,
            e.sg_partido,
            e.ano_eleicao,
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
            -- Segurança Features
            COALESCE(se.taxa_homicidio, 0.01) as taxa_homicidio,
            COALESCE(se.taxa_roubo, 0.005) as taxa_roubo,
            COALESCE(se.taxa_trafico, 0.001) as taxa_trafico,
            CURRENT_TIMESTAMP() as dataset_created_at,
        FROM eleicoes_base e
        LEFT JOIN ibge_features i USING (cd_municipio_ibge)
        LEFT JOIN social_features s USING (cd_municipio_ibge)
        LEFT JOIN saude_features sa USING (cd_municipio_ibge)
        LEFT JOIN seguranca_features se USING (cd_municipio_ibge)
    )

    SELECT * FROM final_dataset
    WHERE pct_votos IS NOT NULL
    ORDER BY ano_eleicao, sg_uf, candidato, cd_municipio_ibge
    """

    logger.info("Building training dataset from 17 Gold sources...")
    df = client.query(query).to_dataframe()

    logger.info(f"Dataset shape: {df.shape}")
    logger.info(f"Features: {list(df.columns)}")
    logger.info(f"Candidatos: {df['candidato'].nunique()}")
    logger.info(f"Municípios: {df['cd_municipio_ibge'].nunique()}")
    logger.info(f"Anos: {sorted(df['ano_eleicao'].unique())}")

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
