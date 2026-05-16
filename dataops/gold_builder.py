"""Gold layer builder: Silver → 3 Gold tables (~200 features each)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

logger = logging.getLogger("spepe.dataops.gold")

LOCAL_GOLD_DIR = Path(os.environ.get("DATA_DIR", "data")) / "gold"
LOCAL_SILVER_DIR = Path(os.environ.get("DATA_DIR", "data")) / "silver"
_BQ_SILVER_DATASET = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")
_GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "")


def _build_gold_via_bigquery_sql() -> dict:
    """Build all Gold tables using BigQuery SQL — no data movement to Python."""
    from google.cloud import bigquery

    client = bigquery.Client(project=_GCP_PROJECT)
    _BQ_GOLD_DATASET = os.environ.get("BIGQUERY_DATASET_GOLD", "spepe_gold")
    silver_wc = f"`{_GCP_PROJECT}.{_BQ_SILVER_DATASET}.tse_*`"
    gold = f"{_GCP_PROJECT}.{_BQ_GOLD_DATASET}"
    silver = f"{_GCP_PROJECT}.{_BQ_SILVER_DATASET}"

    # Check if dim_candidato is available in Silver → enables sg_partido JOIN
    try:
        client.get_table(f"{_GCP_PROJECT}.{_BQ_SILVER_DATASET}.dim_candidato")
        _has_dim_cand = True
        logger.info("Silver dim_candidato encontrado — verificando sq_candidato em tse_*")
    except Exception:
        _has_dim_cand = False
        logger.info("Silver dim_candidato ausente — sg_partido=NULL (rode silver_transform)")

    # Validate that tse_* tables have sq_candidato — column absent → skip partido JOIN
    if _has_dim_cand:
        try:
            _sq_check = client.query(
                f"SELECT 1 FROM `{_GCP_PROJECT}.{_BQ_SILVER_DATASET}.INFORMATION_SCHEMA.COLUMNS`"
                f" WHERE table_name LIKE 'tse_%' AND column_name = 'sq_candidato' LIMIT 1"
            ).to_dataframe(create_bqstorage_client=False)
            if _sq_check.empty:
                logger.info("Silver tse_* sem sq_candidato — partido JOIN desativado")
                _has_dim_cand = False
            else:
                logger.info("sq_candidato confirmado — sg_partido via JOIN ativo")
        except Exception as _e:
            logger.warning("Falha ao verificar sq_candidato: %s — partido JOIN desativado", _e)
            _has_dim_cand = False

    if _has_dim_cand:
        _partido_cols = """c.sg_partido              AS sg_partido,
                    c.nm_partido               AS nm_partido,"""
        _partido_join = f"""LEFT JOIN `{silver}.dim_candidato` c
                ON CAST(s.sq_candidato AS STRING) = CAST(c.sq_candidato AS STRING)
                AND SAFE_CAST(s.ano_eleicao AS INT64) = SAFE_CAST(c.ano AS INT64)"""
        _s = "s."
        _from_tse_s = f"{silver_wc} s"
        _partido_grp = "c.sg_partido, c.nm_partido,"
    else:
        _partido_cols = """CAST(NULL AS STRING)       AS sg_partido,
                    CAST(NULL AS STRING)       AS nm_partido,"""
        _partido_join = ""
        _s = ""
        _from_tse_s = silver_wc
        _partido_grp = ""

    # Silver per-UF tables have nm_municipio_x/y (from TSE+IBGE join); tse_presidente has same
    # after schema alignment in transform_presidente_to_silver.
    _nm_mun_sel = f"COALESCE({_s}nm_municipio_x, {_s}nm_municipio_y) AS nm_municipio"
    _nm_mun_grp = f"{_s}nm_municipio_x, {_s}nm_municipio_y"

    sqls = {
        "fact_municipio_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_municipio_eleicao` AS
            SELECT
                {_s}sg_uf,
                SAFE_CAST({_s}cd_municipio AS INT64)        AS cd_municipio,
                {_nm_mun_sel},
                SAFE_CAST({_s}cd_municipio_ibge AS INT64)   AS cd_municipio_ibge,
                {_s}nm_candidato,
                {_partido_cols}
                SAFE_CAST({_s}cd_cargo AS INT64)             AS cd_cargo,
                {_s}ds_cargo,
                SAFE_CAST({_s}ano_eleicao AS INT64)          AS ano_eleicao,
                SAFE_CAST(SUM({_s}qt_votos) AS INT64)        AS total_votos,
                ROUND(
                    SUM({_s}qt_votos) / NULLIF(SUM(SUM({_s}qt_votos)) OVER (
                        PARTITION BY {_s}sg_uf, {_s}cd_municipio, {_s}cd_cargo, {_s}ano_eleicao
                    ), 0) * 100, 2
                ) AS pct_votos_municipio,
                CURRENT_TIMESTAMP()                          AS ingested_at
            FROM {_from_tse_s}
            {_partido_join}
            GROUP BY {_s}sg_uf, {_s}cd_municipio, {_nm_mun_grp}, {_s}cd_municipio_ibge,
                     {_s}nm_candidato, {_partido_grp}
                     {_s}cd_cargo, {_s}ds_cargo, {_s}ano_eleicao
        """,
        "fact_secao_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_secao_eleicao` AS
            SELECT
                {_s}sg_uf,
                SAFE_CAST({_s}cd_municipio AS INT64)    AS cd_municipio,
                {_nm_mun_sel},
                SAFE_CAST({_s}nr_zona AS INT64)          AS nr_zona,
                SAFE_CAST({_s}nr_secao AS INT64)         AS nr_secao,
                {_s}nm_candidato,
                {_partido_cols}
                SAFE_CAST({_s}cd_cargo AS INT64)         AS cd_cargo,
                {_s}ds_cargo,
                SAFE_CAST({_s}nr_turno AS INT64)         AS nr_turno,
                SAFE_CAST({_s}ano_eleicao AS INT64)      AS ano_eleicao,
                SAFE_CAST(SUM({_s}qt_votos) AS INT64)    AS total_votos,
                CURRENT_TIMESTAMP()                      AS ingested_at
            FROM {_from_tse_s}
            {_partido_join}
            GROUP BY {_s}sg_uf, {_s}cd_municipio, {_nm_mun_grp}, {_s}nr_zona, {_s}nr_secao,
                     {_s}nm_candidato, {_partido_grp}
                     {_s}cd_cargo, {_s}ds_cargo, {_s}nr_turno, {_s}ano_eleicao
        """,
        "fact_candidato_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_candidato_eleicao` AS
            SELECT
                {_s}sg_uf,
                SAFE_CAST({_s}nr_candidato AS INT64)    AS nr_candidato,
                {_s}nm_candidato,
                {_partido_cols}
                SAFE_CAST({_s}cd_cargo AS INT64)        AS cd_cargo,
                {_s}ds_cargo,
                SAFE_CAST({_s}ano_eleicao AS INT64)     AS ano_eleicao,
                SAFE_CAST(SUM({_s}qt_votos) AS INT64)   AS total_votos,
                COUNT(DISTINCT {_s}cd_municipio)        AS n_municipios,
                CURRENT_TIMESTAMP()                     AS ingested_at
            FROM {_from_tse_s}
            {_partido_join}
            GROUP BY {_s}sg_uf, {_s}nr_candidato, {_s}nm_candidato,
                     {_partido_grp}
                     {_s}cd_cargo, {_s}ds_cargo, {_s}ano_eleicao
        """,
        "fact_municipio_candidato_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_municipio_candidato_eleicao` AS
            SELECT
                *,
                ROUND(
                    total_votos / NULLIF(SUM(total_votos) OVER (
                        PARTITION BY sg_uf, cd_municipio, cd_cargo, nr_turno, ano_eleicao
                    ), 0) * 100, 1
                )                                   AS pct_votos_municipio,
                ROW_NUMBER() OVER (
                    PARTITION BY sg_uf, cd_municipio, cd_cargo, nr_turno, ano_eleicao
                    ORDER BY total_votos DESC
                )                                   AS rn_municipio
            FROM (
                SELECT
                    {_s}sg_uf,
                    SAFE_CAST({_s}cd_municipio AS INT64)        AS cd_municipio,
                    {_nm_mun_sel},
                    SAFE_CAST({_s}cd_municipio_ibge AS INT64)   AS cd_municipio_ibge,
                    {_s}nm_candidato,
                    {_partido_cols}
                    SAFE_CAST({_s}cd_cargo AS INT64)             AS cd_cargo,
                    {_s}ds_cargo,
                    SAFE_CAST({_s}nr_turno AS INT64)             AS nr_turno,
                    SAFE_CAST({_s}ano_eleicao AS INT64)          AS ano_eleicao,
                    SAFE_CAST(SUM({_s}qt_votos) AS INT64)        AS total_votos,
                    ANY_VALUE({_s}ds_situacao)                   AS ds_situacao,
                    CURRENT_TIMESTAMP()                          AS ingested_at
                FROM {_from_tse_s}
                {_partido_join}
                WHERE ({_s}nm_candidato IS NOT NULL
                  AND UPPER(TRIM({_s}nm_candidato)) NOT IN (
                      'VOTO BRANCO','VOTO NULO','#NULO#','#NULO',
                      'VOTO EM BRANCO','NULO','BRANCO'
                  )
                  AND {_s}nm_candidato NOT LIKE '#%')
                GROUP BY {_s}sg_uf, {_s}cd_municipio, {_nm_mun_grp}, {_s}cd_municipio_ibge,
                         {_s}nm_candidato, {_partido_grp}
                         {_s}cd_cargo, {_s}ds_cargo, {_s}nr_turno, {_s}ano_eleicao
            )
        """,
        "fact_presidente_resultado": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_presidente_resultado` AS
            SELECT
                sg_uf,
                SAFE_CAST(cd_municipio AS INT64)                        AS cd_municipio,
                COALESCE(nm_municipio_x, nm_municipio_y)                AS nm_municipio,
                SAFE_CAST(cd_municipio_ibge AS INT64)                   AS cd_municipio_ibge,
                nm_candidato,
                SAFE_CAST(nr_candidato AS INT64)                        AS nr_candidato,
                SAFE_CAST(cd_cargo AS INT64)                            AS cd_cargo,
                ds_cargo,
                SAFE_CAST(nr_turno AS INT64)                            AS nr_turno,
                SAFE_CAST(ano_eleicao AS INT64)                         AS ano_eleicao,
                SAFE_CAST(SUM(qt_votos) AS INT64)                       AS total_votos,
                ROUND(
                    SUM(qt_votos) / NULLIF(SUM(SUM(qt_votos)) OVER (
                        PARTITION BY sg_uf, nr_turno, ano_eleicao
                    ), 0) * 100, 1
                )                                                        AS pct_votos_uf,
                CURRENT_TIMESTAMP()                                      AS ingested_at
            FROM `{silver}.tse_presidente_*`
            WHERE nm_candidato IS NOT NULL
              AND qt_votos IS NOT NULL
            GROUP BY
                sg_uf, cd_municipio, nm_municipio_x, nm_municipio_y, cd_municipio_ibge,
                nm_candidato, nr_candidato, cd_cargo, ds_cargo, nr_turno, ano_eleicao
        """,
        "fact_ibge_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_ibge_municipio` AS
            SELECT
                SAFE_CAST(cd_municipio_ibge AS INT64)            AS cd_municipio_ibge,
                ANY_VALUE(sg_uf)                                  AS sg_uf,
                ANY_VALUE(
                    COALESCE(nm_municipio_x, nm_municipio_y)
                )                                                 AS nm_municipio,
                SAFE_CAST(ANY_VALUE(ano_eleicao) AS INT64)        AS ano,
                CAST(NULL AS FLOAT64)                             AS populacao_total,
                CAST(NULL AS FLOAT64)                             AS taxa_alfabetizacao,
                CAST(NULL AS FLOAT64)                             AS taxa_analfabetismo,
                CAST(NULL AS FLOAT64)                             AS renda_per_capita,
                CAST(NULL AS FLOAT64)                             AS pct_urbano,
                CAST(NULL AS FLOAT64)                             AS pct_0_14,
                CAST(NULL AS FLOAT64)                             AS pct_60_mais,
                CAST(NULL AS FLOAT64)                             AS pct_catolico,
                CAST(NULL AS FLOAT64)                             AS idhm,
                CAST(NULL AS FLOAT64)                             AS gini,
                CAST(NULL AS FLOAT64)                             AS pct_extrema_pobreza,
                CURRENT_TIMESTAMP()                               AS ingested_at
            FROM {silver_wc}
            WHERE REGEXP_CONTAINS(LOWER(_TABLE_SUFFIX), r'^[a-z]{{2}}_20\d\d$')
              AND cd_municipio_ibge IS NOT NULL
            GROUP BY cd_municipio_ibge
        """,
        "dim_territorio": f"""
            CREATE OR REPLACE TABLE `{gold}.dim_territorio` AS
            WITH uf_meta AS (
                SELECT sg_uf, nm_uf, sg_regiao, nm_regiao
                FROM UNNEST(ARRAY<STRUCT<sg_uf STRING, nm_uf STRING, sg_regiao STRING, nm_regiao STRING>>[
                    ('AC','Acre','N','Norte'),
                    ('AL','Alagoas','NE','Nordeste'),
                    ('AM','Amazonas','N','Norte'),
                    ('AP','Amapá','N','Norte'),
                    ('BA','Bahia','NE','Nordeste'),
                    ('CE','Ceará','NE','Nordeste'),
                    ('DF','Distrito Federal','CO','Centro-Oeste'),
                    ('ES','Espírito Santo','SE','Sudeste'),
                    ('GO','Goiás','CO','Centro-Oeste'),
                    ('MA','Maranhão','NE','Nordeste'),
                    ('MG','Minas Gerais','SE','Sudeste'),
                    ('MS','Mato Grosso do Sul','CO','Centro-Oeste'),
                    ('MT','Mato Grosso','CO','Centro-Oeste'),
                    ('PA','Pará','N','Norte'),
                    ('PB','Paraíba','NE','Nordeste'),
                    ('PE','Pernambuco','NE','Nordeste'),
                    ('PI','Piauí','NE','Nordeste'),
                    ('PR','Paraná','S','Sul'),
                    ('RJ','Rio de Janeiro','SE','Sudeste'),
                    ('RN','Rio Grande do Norte','NE','Nordeste'),
                    ('RO','Rondônia','N','Norte'),
                    ('RR','Roraima','N','Norte'),
                    ('RS','Rio Grande do Sul','S','Sul'),
                    ('SC','Santa Catarina','S','Sul'),
                    ('SE','Sergipe','NE','Nordeste'),
                    ('SP','São Paulo','SE','Sudeste'),
                    ('TO','Tocantins','N','Norte')
                ])
            ),
            base AS (
                SELECT
                    SAFE_CAST(cd_municipio AS INT64)              AS cd_municipio,
                    ANY_VALUE(nm_municipio)                        AS nm_municipio,
                    SAFE_CAST(ANY_VALUE(cd_municipio_ibge) AS INT64) AS cd_ibge,
                    sg_uf
                FROM `{gold}.fact_municipio_candidato_eleicao`
                WHERE cd_municipio IS NOT NULL
                  AND sg_uf IS NOT NULL
                GROUP BY cd_municipio, sg_uf
            )
            SELECT
                b.cd_municipio,
                b.nm_municipio,
                b.cd_ibge,
                b.sg_uf,
                r.nm_uf,
                r.sg_regiao,
                r.nm_regiao,
                CAST(NULL AS FLOAT64) AS latitude,
                CAST(NULL AS FLOAT64) AS longitude,
                CURRENT_TIMESTAMP()   AS ingested_at
            FROM base b
            JOIN uf_meta r USING (sg_uf)
        """,
        "fact_seguranca_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_seguranca_municipio` AS
            SELECT
                CAST(cd_municipio_ibge AS INT64)         AS cd_municipio_ibge,
                sg_uf,
                COALESCE(SAFE_CAST(ano AS INT64), 2022)  AS ano,
                SAFE_CAST(ivs_total AS FLOAT64)          AS ivs_total,
                SAFE_CAST(ivs_infraestrutura AS FLOAT64) AS ivs_infraestrutura,
                SAFE_CAST(ivs_capital_humano AS FLOAT64) AS ivs_capital_humano,
                SAFE_CAST(ivs_renda_trabalho AS FLOAT64) AS ivs_renda_trabalho,
                CAST(NULL AS FLOAT64)                    AS taxa_homicidio_100k,
                CAST(NULL AS FLOAT64)                    AS taxa_roubo_100k,
                CAST(NULL AS INT64)                      AS qt_feminicidio,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM `{silver}.seguranca_municipal`
            WHERE cd_municipio_ibge IS NOT NULL
        """,
        "fact_saude_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_saude_municipio` AS
            SELECT
                CAST(cd_municipio_ibge AS INT64)                              AS cd_municipio_ibge,
                sg_uf,
                COALESCE(SAFE_CAST(ano AS INT64), 2022)                       AS ano,
                SAFE_CAST(taxa_mortalidade_infantil_1000 AS FLOAT64)          AS taxa_mortalidade_infantil_1000,
                SAFE_CAST(taxa_mortalidade_materna_100k AS FLOAT64)           AS taxa_mortalidade_materna_100k,
                SAFE_CAST(pct_cobertura_plano_saude AS FLOAT64)               AS pct_cobertura_plano_saude,
                SAFE_CAST(qt_obitos_total AS FLOAT64)                         AS qt_obitos_total,
                SAFE_CAST(qt_nascimentos AS FLOAT64)                          AS qt_nascimentos,
                SAFE_CAST(COALESCE(idsus, NULL) AS FLOAT64)                   AS idsus_score,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM `{silver}.saude_municipal`
            WHERE cd_municipio_ibge IS NOT NULL
        """,
        # Silver social_mencoes_br schema: like_count, view_count, comment_count (sem retweet_count/reply_count)
        "fact_social_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_social_municipio` AS
            SELECT
                COALESCE(sg_uf, '')                         AS sg_uf,
                COALESCE(candidato, 'desconhecido')         AS candidato,
                fonte,
                COALESCE(tipo_fonte, 'desconhecido')        AS tipo_fonte,
                COALESCE(vies_politico, 'variado')          AS vies_politico,
                ano_semana,
                SAFE_CAST(semana AS INT64)                  AS semana,
                SAFE_CAST(ano AS INT64)                     AS ano,
                DATE(created_at)                            AS data_referencia,
                COUNT(*)                                    AS qt_posts,
                COALESCE(SUM(like_count), 0)                AS total_likes,
                CAST(0 AS INT64)                            AS total_retweets,
                COALESCE(SUM(comment_count), 0)             AS total_comments,
                COALESCE(SUM(view_count), 0)                AS total_views,
                COALESCE(SUM(like_count), 0) + COALESCE(SUM(comment_count), 0)
                                                            AS total_engajamento,
                COUNTIF(sentiment = 'positivo')             AS qt_positivo,
                COUNTIF(sentiment = 'negativo')             AS qt_negativo,
                COUNTIF(sentiment = 'neutro')               AS qt_neutro,
                SAFE_DIVIDE(
                    COUNTIF(sentiment = 'positivo') - COUNTIF(sentiment = 'negativo'),
                    COUNT(*)
                ) * 100                                     AS score_liquido_sentimento,
                -- Média ponderada: sentimento × score_confiabilidade da fonte
                SAFE_DIVIDE(
                    SUM(
                        CASE sentiment
                            WHEN 'positivo' THEN  COALESCE(score_confiabilidade, 5.0)
                            WHEN 'negativo' THEN -COALESCE(score_confiabilidade, 5.0)
                            ELSE 0
                        END
                    ),
                    SUM(COALESCE(score_confiabilidade, 5.0))
                ) * 100                                     AS score_ponderado_sentimento,
                AVG(COALESCE(score_confiabilidade, 5.0))    AS score_medio_confiabilidade,
                CURRENT_TIMESTAMP()                         AS ingested_at
            FROM `{silver}.social_mencoes_br`
            WHERE (confianca_nlp >= 0.70 OR confianca_nlp IS NULL)
            GROUP BY
                sg_uf, candidato, fonte, tipo_fonte, vies_politico,
                ano_semana, semana, ano, DATE(created_at)
        """,
        # fact_pesquisa: promote Silver fact_pesquisa (multi-ano) → Gold
        # Skipped silently if Silver table does not exist yet
        "fact_pesquisa": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_pesquisa` AS
            SELECT * FROM `{silver}.fact_pesquisa`
        """,
        # fact_intencao_voto: intenção agregada por data × candidato × UF × cargo
        # Fonte: Silver fact_pesquisa_intencao (gerado pelo polls_client + Poder360)
        "fact_intencao_voto": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_intencao_voto`
            PARTITION BY RANGE_BUCKET(ano_eleitoral, GENERATE_ARRAY(2018, 2030, 1))
            CLUSTER BY uf, cd_cargo, candidato_normalizado
            AS
            SELECT
                CAST(data_pesquisa_fim AS DATE)                              AS data_referencia,
                EXTRACT(YEAR FROM CAST(data_pesquisa_fim AS DATE))           AS ano_eleitoral,
                candidato_normalizado,
                COALESCE(uf, 'BR')                                           AS uf,
                SAFE_CAST(cd_cargo AS INT64)                                 AS cd_cargo,
                COUNT(*)                                                     AS n_pesquisas,
                AVG(intencao_pct)                                            AS intencao_media,
                SAFE_DIVIDE(
                    SUM(intencao_pct * COALESCE(SAFE_CAST(n_entrevistados AS FLOAT64), 1)),
                    NULLIF(SUM(COALESCE(SAFE_CAST(n_entrevistados AS FLOAT64), 1)), 0)
                )                                                            AS intencao_ponderada,
                AVG(intencao_ajustada)                                       AS intencao_ajustada_media,
                AVG(SAFE_CAST(margem_erro AS FLOAT64))                       AS margem_erro_media,
                MIN(intencao_pct)                                            AS intencao_min,
                MAX(intencao_pct)                                            AS intencao_max,
                TO_JSON_STRING(
                    ARRAY_AGG(DISTINCT instituto IGNORE NULLS)
                )                                                            AS institutos,
                CURRENT_TIMESTAMP()                                          AS updated_at
            FROM `{silver}.fact_pesquisa_intencao`
            WHERE intencao_pct IS NOT NULL
            GROUP BY
                CAST(data_pesquisa_fim AS DATE),
                EXTRACT(YEAR FROM CAST(data_pesquisa_fim AS DATE)),
                candidato_normalizado,
                COALESCE(uf, 'BR'),
                SAFE_CAST(cd_cargo AS INT64)
        """,
        "vw_pesquisa_intencao_detalhada": f"""
            CREATE OR REPLACE VIEW `{gold}.vw_pesquisa_intencao_detalhada` AS
            SELECT
                p.poll_id,
                p.data_pesquisa_inicio,
                p.data_pesquisa_fim,
                p.instituto,
                i.candidato AS candidato,
                SAFE_CAST(i.intencao_pct AS FLOAT64)                         AS intencao_pct,
                SAFE_CAST(i.intencao_ajustada AS FLOAT64)                    AS intencao_ajustada,
                SAFE_CAST(i.house_effect AS FLOAT64)                         AS house_effect,
                SAFE_CAST(p.margem_erro AS FLOAT64)                          AS margem_erro,
                SAFE_CAST(p.n_entrevistados AS INT64)                        AS n_entrevistados,
                COALESCE(i.uf, p.uf, 'BR')                                   AS uf,
                SAFE_CAST(COALESCE(i.cd_cargo, p.cd_cargo, '1') AS INT64)   AS cd_cargo,
                COALESCE(i.tipo_pesquisa, p.tipo_pesquisa, 'corrente')      AS tipo_pesquisa,
                i.candidato_normalizado,
                i.record_confidence_score,
                EXTRACT(YEAR FROM COALESCE(i.data_pesquisa_fim, p.data_pesquisa_fim))
                    AS ano_eleitoral,
                CURRENT_TIMESTAMP()                                          AS updated_at
            FROM `{silver}.fact_pesquisa` p
            LEFT JOIN `{silver}.fact_pesquisa_intencao` i
              ON p.poll_id = i.poll_id
              AND COALESCE(p.uf, 'BR') = COALESCE(i.uf, 'BR')
              AND COALESCE(p.cd_cargo, '1') = COALESCE(i.cd_cargo, '1')
            WHERE i.candidato IS NOT NULL
              AND SAFE_CAST(i.intencao_pct AS FLOAT64) IS NOT NULL
        """,
        "fact_transferencias_sociais": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_transferencias_sociais` AS
            SELECT
                CAST(cd_municipio_ibge AS INT64)                            AS cd_municipio_ibge,
                nm_municipio,
                sg_uf,
                CAST(ano AS INT64)                                          AS ano,
                COALESCE(SAFE_CAST(qtd_beneficiarios_bolsa_familia AS INT64), 0)
                                                                            AS qtd_beneficiarios_bolsa_familia,
                COALESCE(SAFE_CAST(valor_total_bolsa_familia_reais AS FLOAT64), 0.0)
                                                                            AS valor_total_bolsa_familia_reais,
                COALESCE(SAFE_CAST(qtd_familias_cadunico AS INT64), 0)      AS qtd_familias_cadunico,
                COALESCE(SAFE_CAST(qtd_familias_extrema_pobreza AS INT64), 0)
                                                                            AS qtd_familias_extrema_pobreza,
                COALESCE(SAFE_CAST(qtd_familias_baixa_renda AS INT64), 0)   AS qtd_familias_baixa_renda,
                fonte,
                CURRENT_TIMESTAMP()                                         AS ingested_at
            FROM `{silver}.transferencias_sociais`
            WHERE cd_municipio_ibge IS NOT NULL
        """,
        # ── Meta Ad Library por UF (gasto × impressões × candidato × semana) ──
        "fact_meta_ads_uf": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_meta_ads_uf` AS
            WITH base AS (
                SELECT
                    candidato,
                    sg_uf,
                    SAFE_CAST(ano AS INT64)                                  AS ano,
                    DATE(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S%Ez', dt_inicio)) AS dt_inicio,
                    FORMAT_DATE('%Y-W%V', DATE(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S%Ez', dt_inicio)))
                                                                             AS ano_semana,
                    SAFE_CAST(FORMAT_DATE('%V', DATE(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S%Ez', dt_inicio))) AS INT64)
                                                                             AS semana,
                    COALESCE(vl_gasto_estimado_uf, 0.0)                     AS vl_gasto_estimado_uf,
                    COALESCE(qt_impressoes_estimadas_uf, 0.0)               AS qt_impressoes_estimadas_uf
                FROM `{silver}.meta_ads_regioes_BR`
                WHERE sg_uf IS NOT NULL AND sg_uf != ''
            )
            SELECT
                candidato,
                sg_uf,
                ano,
                ano_semana,
                semana,
                COUNT(DISTINCT dt_inicio)                                    AS qt_anuncios,
                SUM(vl_gasto_estimado_uf)                                   AS vl_gasto_total_uf,
                SUM(qt_impressoes_estimadas_uf)                             AS qt_impressoes_total_uf,
                AVG(vl_gasto_estimado_uf)                                   AS vl_gasto_medio_por_anuncio,
                SAFE_DIVIDE(
                    SUM(vl_gasto_estimado_uf),
                    NULLIF(SUM(qt_impressoes_estimadas_uf), 0) / 1000.0
                )                                                            AS custo_por_mil_impressoes,
                CURRENT_TIMESTAMP()                                         AS ingested_at
            FROM base
            GROUP BY candidato, sg_uf, ano, ano_semana, semana
        """,
        # ── Meta Ads demográfico (perfil etário/gênero por candidato × UF) ────
        "fact_meta_ads_demografico": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_meta_ads_demografico` AS
            SELECT
                candidato,
                faixa_etaria,
                genero,
                SAFE_CAST(ano AS INT64)                                     AS ano,
                COUNT(DISTINCT ad_id)                                       AS qt_anuncios,
                SUM(COALESCE(vl_gasto_estimado_demo, 0.0))                  AS vl_gasto_estimado_total,
                AVG(COALESCE(pct_demografico, 0.0))                         AS pct_demografico_medio,
                CURRENT_TIMESTAMP()                                         AS ingested_at
            FROM `{silver}.meta_ads_demograficos_BR`
            WHERE faixa_etaria IS NOT NULL
            GROUP BY candidato, faixa_etaria, genero, ano
        """,
        # ── Google Trends por UF (interesse de busca × candidato × semana) ───
        "fact_google_trends_uf": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_google_trends_uf` AS
            SELECT
                candidato,
                sg_uf,
                SAFE_CAST(ano AS INT64)                                     AS ano,
                AVG(COALESCE(SAFE_CAST(interesse_busca AS FLOAT64), 0.0))   AS interesse_busca_medio,
                MAX(COALESCE(SAFE_CAST(interesse_busca AS FLOAT64), 0.0))   AS interesse_busca_max,
                COUNT(*)                                                    AS qt_semanas,
                CURRENT_TIMESTAMP()                                         AS ingested_at
            FROM `{silver}.google_trends_uf_*`
            WHERE sg_uf IS NOT NULL
            GROUP BY candidato, sg_uf, ano
        """,
        # ── Emendas por UF × parlamentar × área ──────────────────────────────────
        "fact_emendas_parlamentar": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_emendas_parlamentar` AS
            SELECT
                ano,
                sg_uf,
                sg_uf_parlamentar,
                nm_parlamentar,
                sg_partido,
                ds_cargo_parlamentar,
                tp_emenda,
                ds_area,
                COUNT(*)                        AS qt_emendas,
                SUM(vl_empenhado)               AS vl_empenhado_total,
                SUM(vl_liquidado)               AS vl_liquidado_total,
                SUM(vl_pago)                    AS vl_pago_total,
                AVG(vl_pago)                    AS vl_pago_medio,
                COUNT(DISTINCT cd_municipio_ibge) AS qt_municipios_atendidos,
                CURRENT_TIMESTAMP()             AS ingested_at
            FROM `{silver}.emendas_parlamentares`
            WHERE vl_pago > 0
            GROUP BY
                ano, sg_uf, sg_uf_parlamentar, nm_parlamentar, sg_partido,
                ds_cargo_parlamentar, tp_emenda, ds_area
        """,
        # ── Emendas por município × área (cruzamento territorial) ─────────────
        "fact_emendas_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_emendas_municipio` AS
            SELECT
                ano,
                cd_municipio_ibge,
                nm_municipio,
                sg_uf,
                ds_area,
                tp_emenda,
                COUNT(*)                        AS qt_emendas,
                COUNT(DISTINCT nm_parlamentar)  AS qt_parlamentares_distintos,
                SUM(vl_empenhado)               AS vl_empenhado_total,
                SUM(vl_liquidado)               AS vl_liquidado_total,
                SUM(vl_pago)                    AS vl_pago_total,
                CURRENT_TIMESTAMP()             AS ingested_at
            FROM `{silver}.emendas_parlamentares`
            WHERE cd_municipio_ibge IS NOT NULL
            GROUP BY ano, cd_municipio_ibge, nm_municipio, sg_uf, ds_area, tp_emenda
        """,
        # ── Sanções por UF × tipo ─────────────────────────────────────────────
        "fact_sancoes_uf": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_sancoes_uf` AS
            SELECT
                COALESCE(fonte_sistema, 'CEIS') AS fonte_sistema,
                COALESCE(NULLIF(sg_uf_sancionado, ''), NULLIF(sg_uf_orgao, ''), 'NACIONAL') AS sg_uf,
                tp_sancao,
                tp_pessoa,
                EXTRACT(YEAR FROM dt_inicio_sancao) AS ano_sancao,
                COUNT(*)                            AS qt_sancoes,
                CAST(NULL AS FLOAT64)               AS vl_multa_total,
                CAST(NULL AS FLOAT64)               AS vl_multa_medio,
                COUNT(DISTINCT nm_orgao_sancionador) AS qt_orgaos_sancionadores,
                CURRENT_TIMESTAMP()                 AS ingested_at
            FROM `{silver}.sancoes_empresas`
            WHERE tp_sancao IS NOT NULL
            GROUP BY
                COALESCE(fonte_sistema, 'CEIS'),
                COALESCE(NULLIF(sg_uf_sancionado, ''), NULLIF(sg_uf_orgao, ''), 'NACIONAL'),
                tp_sancao, tp_pessoa,
                EXTRACT(YEAR FROM dt_inicio_sancao)
        """,
        # ── Endividamento familiar BACEN (série nacional mensal) ─────────────────
        "fact_endividamento_nacional": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_endividamento_nacional` AS
            SELECT
                SAFE_CAST(ano AS INT64)                              AS ano,
                SAFE_CAST(mes AS INT64)                              AS mes,
                CAST(data_referencia AS DATE)                        AS data_referencia,
                SAFE_CAST(endividamento_familias_pct AS FLOAT64)     AS endividamento_familias_pct,
                SAFE_CAST(comprometimento_renda_pct AS FLOAT64)      AS comprometimento_renda_pct,
                SAFE_CAST(inadimplencia_pf_pct AS FLOAT64)           AS inadimplencia_pf_pct,
                SAFE_CAST(inadimplencia_pf_credito AS FLOAT64)       AS inadimplencia_pf_credito,
                COALESCE(granularidade, 'Nacional')                  AS granularidade,
                fontes,
                CURRENT_TIMESTAMP()                                  AS ingested_at
            FROM `{silver}.endividamento_nacional`
            WHERE ano IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (PARTITION BY data_referencia ORDER BY ingested_at DESC) = 1
        """,
        # ── Votações parlamentares (Câmara + Senado) ──────────────────────────
        "fact_votacoes_parlamentar": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_votacoes_parlamentar` AS
            SELECT
                SAFE_CAST(COALESCE(ano_ref, 2024) AS INT64)  AS ano,
                CAST(NULL AS INT64)                           AS mes,
                COALESCE(sg_uf, 'BR')                        AS sg_uf,
                COALESCE(sg_partido, 'N/A')                  AS sg_partido,
                COALESCE(casa, 'Câmara')                     AS casa,
                'Geral'                                      AS tema,
                COUNT(*)                                     AS qt_parlamentares,
                CAST(NULL AS INT64)                          AS qt_votacoes,
                CAST(NULL AS INT64)                          AS qt_sim,
                CAST(NULL AS INT64)                          AS qt_nao,
                CAST(NULL AS INT64)                          AS qt_abstencao,
                CURRENT_TIMESTAMP()                          AS ingested_at
            FROM `{silver}.parlamentares_federais`
            WHERE sg_uf IS NOT NULL
            GROUP BY ano_ref, sg_uf, sg_partido, casa
        """,
        # ── Perfil do Eleitorado TSE ──────────────────────────────────────────
        "fact_perfil_eleitorado": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_perfil_eleitorado` AS
            SELECT
                sg_uf,
                SAFE_CAST(ano AS INT64)                              AS ano,
                COALESCE(ds_genero, 'Não informado')                 AS ds_genero,
                COALESCE(ds_faixa_etaria, 'Não informado')           AS ds_faixa_etaria,
                COALESCE(ds_grau_escolaridade, 'Não informado')      AS ds_grau_escolaridade,
                COALESCE(ds_estado_civil, 'Não informado')           AS ds_estado_civil,
                SUM(SAFE_CAST(qt_eleitores AS INT64))                AS qt_eleitores,
                CURRENT_TIMESTAMP()                                  AS ingested_at
            FROM `{silver}.perfil_eleitorado`
            WHERE sg_uf IS NOT NULL
            GROUP BY sg_uf, ano, ds_genero, ds_faixa_etaria, ds_grau_escolaridade, ds_estado_civil
        """,
        # ── Índice combinado: gasto Meta Ads + interesse Trends + sentimento social ──
        "fact_indice_digital_candidato": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_indice_digital_candidato` AS
            WITH ads AS (
                SELECT
                    candidato, sg_uf, ano,
                    SUM(vl_gasto_total_uf)       AS vl_gasto_total,
                    SUM(qt_impressoes_total_uf)  AS qt_impressoes,
                    SUM(qt_anuncios)             AS qt_anuncios
                FROM `{gold}.fact_meta_ads_uf`
                GROUP BY candidato, sg_uf, ano
            ),
            trends AS (
                SELECT candidato, sg_uf, ano,
                    AVG(interesse_busca_medio)   AS interesse_busca
                FROM `{gold}.fact_google_trends_uf`
                GROUP BY candidato, sg_uf, ano
            ),
            social AS (
                SELECT
                    COALESCE(candidato, 'desconhecido') AS candidato,
                    COALESCE(sg_uf, '')                 AS sg_uf,
                    SAFE_CAST(ano AS INT64)             AS ano,
                    SUM(qt_posts)                       AS qt_mencoes,
                    AVG(score_liquido_sentimento)        AS score_sentimento,
                    AVG(score_medio_confiabilidade)      AS score_confiabilidade_medio
                FROM `{gold}.fact_social_municipio`
                GROUP BY candidato, sg_uf, ano
            )
            SELECT
                COALESCE(ads.candidato, trends.candidato, social.candidato) AS candidato,
                COALESCE(ads.sg_uf, trends.sg_uf, social.sg_uf)             AS sg_uf,
                COALESCE(ads.ano, trends.ano, social.ano)                   AS ano,
                COALESCE(ads.vl_gasto_total, 0.0)                           AS vl_gasto_meta_ads,
                COALESCE(ads.qt_impressoes, 0.0)                            AS qt_impressoes_ads,
                COALESCE(ads.qt_anuncios, 0)                                AS qt_anuncios,
                COALESCE(trends.interesse_busca, 0.0)                       AS interesse_busca_google,
                COALESCE(social.qt_mencoes, 0)                              AS qt_mencoes_sociais,
                COALESCE(social.score_sentimento, 0.0)                      AS score_sentimento_social,
                COALESCE(social.score_confiabilidade_medio, 0.0)            AS score_confiabilidade_social,
                CURRENT_TIMESTAMP()                                         AS ingested_at
            FROM ads
            FULL OUTER JOIN trends USING (candidato, sg_uf, ano)
            FULL OUTER JOIN social USING (candidato, sg_uf, ano)
        """,
        "fact_locais_votacao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_locais_votacao`
            CLUSTER BY sg_uf, cd_municipio
            AS
            SELECT
                sg_uf,
                SAFE_CAST(cd_municipio AS INT64)             AS cd_municipio,
                ANY_VALUE(nm_municipio)                       AS nm_municipio,
                SAFE_CAST(nr_zona AS INT64)                   AS nr_zona,
                SAFE_CAST(nr_local_votacao AS INT64)          AS nr_local_votacao,
                ANY_VALUE(nm_local_votacao)                   AS nm_local_votacao,
                ANY_VALUE(ds_endereco)                        AS ds_endereco,
                ANY_VALUE(nm_bairro)                          AS nm_bairro,
                ANY_VALUE(nr_cep)                             AS nr_cep,
                AVG(SAFE_CAST(nr_latitude AS FLOAT64))        AS nr_latitude,
                AVG(SAFE_CAST(nr_longitude AS FLOAT64))       AS nr_longitude,
                COUNT(DISTINCT SAFE_CAST(nr_secao AS INT64))  AS qt_secoes,
                (AVG(SAFE_CAST(nr_latitude AS FLOAT64)) IS NOT NULL) AS has_coordinates,
                MAX(SAFE_CAST(snapshot_year AS INT64))        AS snapshot_year,
                CURRENT_TIMESTAMP()                           AS ingested_at
            FROM `{silver}.locais_votacao`
            WHERE sg_uf IS NOT NULL
              AND cd_municipio IS NOT NULL
              AND nr_local_votacao IS NOT NULL
            GROUP BY
                sg_uf,
                SAFE_CAST(cd_municipio AS INT64),
                SAFE_CAST(nr_zona AS INT64),
                SAFE_CAST(nr_local_votacao AS INT64)
        """,
    }

    # Tables that may have no Silver source yet — skip without failing the job
    _OPTIONAL = {
        "fact_presidente_resultado",
        "fact_saude_municipio",
        "fact_seguranca_municipio",
        "fact_pesquisa",
        "fact_intencao_voto",
        "vw_pesquisa_intencao_detalhada",
        "fact_social_municipio",
        "fact_economico_municipio",
        "fact_transferencias_sociais",
        "fact_meta_ads_uf",
        "fact_meta_ads_demografico",
        "fact_google_trends_uf",
        "fact_indice_digital_candidato",
        "fact_emendas_parlamentar",
        "fact_emendas_municipio",
        "fact_sancoes_uf",
        "fact_endividamento_nacional",
        "fact_votacoes_parlamentar",
        "fact_perfil_eleitorado",
        "fact_locais_votacao",
    }

    results = {}
    for table_name, sql in sqls.items():
        try:
            client.query(f"DROP TABLE IF EXISTS `{gold}.{table_name}`").result()
            job = client.query(sql)
            job.result()
            row_count = (
                client.query(f"SELECT COUNT(*) FROM `{gold}.{table_name}`")
                .to_dataframe(create_bqstorage_client=False)
                .iloc[0, 0]
            )
            results[table_name] = {"path": f"{gold}.{table_name}", "rows": int(row_count)}
            logger.info("Gold BQ SQL: %s (%d rows)", table_name, row_count)
        except Exception as exc:
            if table_name in _OPTIONAL:
                logger.warning("Gold BQ SQL skipped %s (Silver ainda vazio): %s", table_name, exc)
                results[table_name] = {"status": "skipped", "message": str(exc)}
            else:
                logger.error("Gold BQ SQL falhou para %s: %s", table_name, exc)
                results[table_name] = {"status": "error", "message": str(exc)}

    return {"status": "ok", "tables": results}


def _load_silver_from_bigquery() -> pd.DataFrame:
    """Read all tse_* tables from BigQuery Silver dataset (local/small UFs only)."""
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=_GCP_PROJECT)
        tables = list(client.list_tables(f"{_GCP_PROJECT}.{_BQ_SILVER_DATASET}"))
        tse_tables = [t for t in tables if t.table_id.startswith("tse_")]
        if not tse_tables:
            return pd.DataFrame()
        frames = []
        for t in tse_tables:
            table_ref = f"{_GCP_PROJECT}.{_BQ_SILVER_DATASET}.{t.table_id}"
            df = client.query(f"SELECT * FROM `{table_ref}`").to_dataframe(
                create_bqstorage_client=False
            )
            frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    except Exception as exc:
        logger.warning("BigQuery Silver read failed: %s", exc)
        return pd.DataFrame()


ELECTION_YEARS = [2014, 2018, 2022]


def build_gold(use_bigquery: bool = False) -> dict:
    """Build all Gold tables from Silver layer."""
    LOCAL_GOLD_DIR.mkdir(parents=True, exist_ok=True)

    # BQ path: run aggregations as SQL — no data movement to Python
    if use_bigquery and _GCP_PROJECT:
        return _build_gold_via_bigquery_sql()

    df_all = pd.DataFrame()
    if df_all.empty:
        silver_files = list(LOCAL_SILVER_DIR.glob("tse_*.parquet"))
        if not silver_files:
            return {
                "status": "error",
                "message": "Nenhum arquivo Silver disponível. Execute silver_transform primeiro.",
            }
        df_all = pd.concat([pd.read_parquet(f) for f in silver_files], ignore_index=True)

    result = {}

    # ── Eleitoral (TSE) ───────────────────────────────────────────────────────
    fact_mun = _build_fact_municipio_eleicao(df_all)
    result["fact_municipio_eleicao"] = _write_gold(fact_mun, "fact_municipio_eleicao", use_bigquery)

    fact_sec = _build_fact_secao_eleicao(df_all)
    result["fact_secao_eleicao"] = _write_gold(fact_sec, "fact_secao_eleicao", use_bigquery)

    fact_cand = _build_fact_candidato_dia(df_all)
    result["fact_candidato_dia"] = _write_gold(fact_cand, "fact_candidato_dia", use_bigquery)

    # ── Pesquisas ─────────────────────────────────────────────────────────────
    fact_pesq = _build_fact_pesquisa()
    result["fact_pesquisa"] = _write_gold(fact_pesq, "fact_pesquisa", use_bigquery)

    # ── IBGE indicadores municipais ───────────────────────────────────────────
    ibge_data = _load_ibge_silver()
    fact_ibge = _build_fact_ibge_municipio(ibge_data)
    result["fact_ibge_municipio"] = _write_gold(fact_ibge, "fact_ibge_municipio", use_bigquery)

    # ── Segurança pública ─────────────────────────────────────────────────────
    fact_seg = _build_fact_seguranca()
    result["fact_seguranca_municipio"] = _write_gold(
        fact_seg, "fact_seguranca_municipio", use_bigquery
    )

    # ── Saúde / DataSUS ───────────────────────────────────────────────────────
    fact_saude = _build_fact_saude()
    result["fact_saude_municipio"] = _write_gold(fact_saude, "fact_saude_municipio", use_bigquery)

    # ── Social ────────────────────────────────────────────────────────────────
    fact_social = _build_fact_social()
    result["fact_social_municipio"] = _write_gold(
        fact_social, "fact_social_municipio", use_bigquery
    )

    # ── Economia (DIEESE + CETIC) ─────────────────────────────────────────────
    fact_eco = _build_fact_economico()
    result["fact_economico_municipio"] = _write_gold(
        fact_eco, "fact_economico_municipio", use_bigquery
    )

    # ── Emendas Parlamentares ─────────────────────────────────────────────────
    fact_emendas_parl, fact_emendas_mun = _build_fact_emendas()
    result["fact_emendas_parlamentar"] = _write_gold(
        fact_emendas_parl, "fact_emendas_parlamentar", use_bigquery
    )
    result["fact_emendas_municipio"] = _write_gold(
        fact_emendas_mun, "fact_emendas_municipio", use_bigquery
    )

    # ── Sanções CEIS + CNEP ────────────────────────────────────────────────────
    fact_sancoes = _build_fact_sancoes_uf()
    result["fact_sancoes_uf"] = _write_gold(fact_sancoes, "fact_sancoes_uf", use_bigquery)

    # ── Digital (Meta Ads + Google Trends) ───────────────────────────────────
    fact_meta_uf = _build_fact_meta_ads_uf()
    result["fact_meta_ads_uf"] = _write_gold(fact_meta_uf, "fact_meta_ads_uf", use_bigquery)

    fact_meta_demo = _build_fact_meta_ads_demografico()
    result["fact_meta_ads_demografico"] = _write_gold(
        fact_meta_demo, "fact_meta_ads_demografico", use_bigquery
    )

    fact_trends_uf = _build_fact_google_trends_uf()
    result["fact_google_trends_uf"] = _write_gold(
        fact_trends_uf, "fact_google_trends_uf", use_bigquery
    )

    return {"status": "ok", "tables": result}


def _build_fact_municipio_eleicao(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate TSE + IBGE data per municipality × election into ~200 features.

    Uses `cod_municipio_ibge` (7-digit IBGE code) as the global municipality key,
    falling back to `cd_municipio` (TSE code) if IBGE code not yet joined.
    """
    if df.empty:
        return pd.DataFrame()

    # Convert Arrow strings to regular strings to avoid pandas dtype issues
    df = df.copy()
    for col in df.columns:
        if hasattr(df[col].dtype, "name") and "string" in str(df[col].dtype):
            df[col] = df[col].astype(str)

    preferred_keys = ["cod_municipio_ibge", "cd_municipio"]
    municipality_key = next((k for k in preferred_keys if k in df.columns), None)
    if municipality_key is None:
        logger.warning("fact_municipio_eleicao: no municipality key found in Silver data")
        return pd.DataFrame()

    group_cols = [municipality_key, "sg_uf", "ano_eleicao"]
    if "cd_cargo" in df.columns:
        group_cols.append("cd_cargo")
    if "nr_turno" in df.columns:
        group_cols.append("nr_turno")

    agg_cols = {}

    if "qt_votos" in df.columns and "nm_candidato" in df.columns:
        candidates = df["nm_candidato"].value_counts().head(10).index.tolist()
        for cand in candidates:
            col_name = f"qt_votos_{cand[:20].replace(' ', '_').lower()}"
            df[col_name] = df["qt_votos"].where(df["nm_candidato"] == cand, 0)
            agg_cols[col_name] = "sum"

        agg_cols["qt_votos"] = "sum"

    ibge_cols = [
        c
        for c in df.columns
        if any(ind in c for ind in ["idhm", "renda", "estudo", "populacao", "ibge"])
    ]
    for col in ibge_cols:
        if col in df.columns:
            agg_cols[col] = "first"

    if not agg_cols:
        return df

    avail_group = [c for c in group_cols if c in df.columns]
    avail_agg = {k: v for k, v in agg_cols.items() if k in df.columns}

    if not avail_group or not avail_agg:
        return df

    # Use as_index=False to keep groupby columns as regular columns (avoid index conflicts)
    fact = df.groupby(avail_group, as_index=False).agg(avail_agg)
    logger.info(f"fact_municipio_eleicao: {len(fact)} rows, {len(fact.columns)} colunas")
    return fact


def _build_fact_candidato_dia(df: pd.DataFrame) -> pd.DataFrame:
    """Build candidate × day time series (placeholder from static data)."""
    if df.empty:
        return pd.DataFrame()

    if "nm_candidato" not in df.columns:
        return pd.DataFrame()

    group_cols = ["nm_candidato"]
    if "ano_eleicao" in df.columns:
        group_cols.append("ano_eleicao")
    if "sg_uf" in df.columns:
        group_cols.append("sg_uf")

    agg_kwargs = {
        "qt_votos_total": ("qt_votos", "sum")
        if "qt_votos" in df.columns
        else ("nm_candidato", "count"),
    }
    cand_agg = df.groupby(group_cols).agg(**agg_kwargs).reset_index()

    # Partition field required by the Terraform table schema
    cand_agg["data"] = pd.Timestamp.utcnow().date()

    logger.info(f"fact_candidato_dia: {len(cand_agg)} candidatos")
    return cand_agg


def _load_ibge_silver() -> pd.DataFrame:
    ibge_files = list(LOCAL_SILVER_DIR.glob("ibge_*.parquet"))
    if not ibge_files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in ibge_files], ignore_index=True)


_FACT_PESQUISA_EMPTY_COLS = [
    "uf",
    "candidato",
    "instituto",
    "cd_cargo",
    "data_pesquisa_inicio",
    "data_pesquisa_fim",
    "intencao_pct",
    "house_effect",
    "intencao_ajustada",
    "margem_erro",
    "record_confidence_score",
    "poll_id",
    "ano",
    "tipo_pesquisa",
    "ingested_at",
]


def _build_fact_pesquisa() -> pd.DataFrame:
    """Promote Silver fact_pesquisa_*.parquet files to Gold."""
    silver_files = sorted(LOCAL_SILVER_DIR.glob("fact_pesquisa_*.parquet"))
    if not silver_files:
        logger.info("fact_pesquisa: nenhum Silver disponível — retornando vazio")
        return pd.DataFrame(columns=_FACT_PESQUISA_EMPTY_COLS)

    dfs = []
    for f in silver_files:
        try:
            dfs.append(pd.read_parquet(f))
            logger.info("fact_pesquisa Silver: %s (%d rows)", f.name, len(dfs[-1]))
        except Exception as exc:
            logger.warning("fact_pesquisa: falha ao ler %s: %s", f, exc)

    if not dfs:
        return pd.DataFrame(columns=_FACT_PESQUISA_EMPTY_COLS)

    df = pd.concat(dfs, ignore_index=True)
    logger.info("fact_pesquisa Gold: %d rows de %d arquivos Silver", len(df), len(silver_files))
    return df


def _build_fact_ibge_municipio(df_ibge: pd.DataFrame) -> pd.DataFrame:
    """Promote IBGE Silver indicators to Gold fact_ibge_municipio."""
    if df_ibge.empty:
        return pd.DataFrame()

    ibge_cols = [
        "cd_municipio_ibge",
        "nm_municipio",
        "sg_uf",
        "ano",
        "idhm",
        "idhm_educacao",
        "idhm_longevidade",
        "idhm_renda",
        "renda_per_capita",
        "gini",
        "pct_extrema_pobreza",
        "taxa_analfabetismo",
        "anos_estudo_medio",
        "pct_domicilios_agua",
        "pct_domicilios_esgoto",
        "pct_domicilios_energia",
        "populacao_total",
        "densidade_demografica",
        "pct_urbano",
    ]
    cols = [c for c in ibge_cols if c in df_ibge.columns]
    if "cd_municipio_ibge" not in cols:
        logger.warning("fact_ibge_municipio: cd_municipio_ibge ausente no Silver IBGE")
        return pd.DataFrame()

    df = df_ibge[cols].copy()
    if "sg_uf" in df.columns:
        df["sg_regiao"] = df["sg_uf"].map(UF_REGIAO).fillna("Desconhecida")
    if "ano" not in df.columns:
        df["ano"] = 0
    df["cd_municipio_ibge"] = pd.to_numeric(df["cd_municipio_ibge"], errors="coerce").astype(
        "Int64"
    )
    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_ibge_municipio: %d rows", len(df))
    return df


def _build_fact_seguranca() -> pd.DataFrame:
    """Aggregate Silver seguranca_municipal files into Gold fact_seguranca_municipio."""
    files = list(LOCAL_SILVER_DIR.glob("seguranca_municipal_*.parquet"))
    if not files:
        logger.info("fact_seguranca_municipio: nenhum Silver de segurança disponível")
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_seguranca_municipio: %d rows de %d arquivos Silver", len(df), len(files))
    return df


def _build_fact_saude() -> pd.DataFrame:
    """Aggregate Silver saude_municipal files into Gold fact_saude_municipio."""
    files = list(LOCAL_SILVER_DIR.glob("saude_municipal_*.parquet"))
    if not files:
        logger.info("fact_saude_municipio: nenhum Silver de saúde disponível")
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_saude_municipio: %d rows de %d arquivos Silver", len(df), len(files))
    return df


def _build_fact_social() -> pd.DataFrame:
    """Aggregate Silver social_mencoes_br files into Gold fact_social_municipio (local path)."""
    files = list(LOCAL_SILVER_DIR.glob("social_mencoes_br_*.parquet"))
    if not files:
        logger.info("fact_social_municipio: nenhum Silver social disponível")
        return pd.DataFrame()

    import numpy as np

    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)

    if "confianca_nlp" in df.columns:
        df = df[(df["confianca_nlp"].isna()) | (df["confianca_nlp"] >= 0.70)]

    if "candidato" not in df.columns:
        df["candidato"] = "desconhecido"
    if "sg_uf" not in df.columns:
        df["sg_uf"] = ""
    if "sentiment" not in df.columns:
        df["sentiment"] = "neutro"
    if "ano_semana" not in df.columns:
        df["ano_semana"] = ""
    if "semana" not in df.columns:
        df["semana"] = 0

    ts = pd.to_datetime(df.get("created_at", pd.NaT), errors="coerce")
    df["data_referencia"] = ts.dt.date
    if df["ano_semana"].eq("").all():
        df["ano_semana"] = ts.dt.strftime("%Y-W%V").fillna("")

    for col in ("like_count", "retweet_count", "reply_count", "view_count"):
        if col not in df.columns:
            df[col] = 0

    # Default source score if not present in Silver
    if "score_confiabilidade" not in df.columns:
        df["score_confiabilidade"] = 5.0
    if "tipo_fonte" not in df.columns:
        df["tipo_fonte"] = "desconhecido"
    if "vies_politico" not in df.columns:
        df["vies_politico"] = "variado"
    df["score_confiabilidade"] = pd.to_numeric(df["score_confiabilidade"], errors="coerce").fillna(
        5.0
    )

    group_cols = [
        "sg_uf",
        "candidato",
        "fonte",
        "tipo_fonte",
        "vies_politico",
        "ano_semana",
        "semana",
        "ano",
        "data_referencia",
    ]
    group_cols = [c for c in group_cols if c in df.columns]

    fact = df.groupby(group_cols, as_index=False, dropna=False).agg(
        qt_posts=("fonte", "count"),
        total_likes=("like_count", "sum"),
        total_retweets=("retweet_count", "sum"),
        total_comments=("reply_count", "sum"),
        total_views=("view_count", "sum"),
        qt_positivo=("sentiment", lambda s: (s == "positivo").sum()),
        qt_negativo=("sentiment", lambda s: (s == "negativo").sum()),
        qt_neutro=("sentiment", lambda s: (s == "neutro").sum()),
        score_medio_confiabilidade=("score_confiabilidade", "mean"),
    )
    fact["total_engajamento"] = (
        fact["total_likes"] + fact["total_retweets"] + fact["total_comments"]
    )
    fact["score_liquido_sentimento"] = np.where(
        fact["qt_posts"] > 0,
        (fact["qt_positivo"] - fact["qt_negativo"]) / fact["qt_posts"] * 100,
        0.0,
    )

    # Weighted sentiment: positive/negative weighted by source score
    def _weighted_sentiment(sub: pd.DataFrame) -> float:
        scores = sub["score_confiabilidade"].values
        signs = sub["sentiment"].map({"positivo": 1, "negativo": -1, "neutro": 0}).fillna(0).values
        total_weight = scores.sum()
        return float((signs * scores).sum() / total_weight * 100) if total_weight > 0 else 0.0

    weighted = df.groupby(group_cols, dropna=False).apply(_weighted_sentiment).reset_index()
    weighted.columns = list(group_cols) + ["score_ponderado_sentimento"]
    fact = fact.merge(weighted, on=group_cols, how="left")

    fact["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_social_municipio: %d rows", len(fact))
    return fact


def _build_fact_economico() -> pd.DataFrame:
    """Aggregate Silver economia_municipal files into Gold fact_economico_municipio."""
    files = list(LOCAL_SILVER_DIR.glob("economia_municipal_*.parquet"))
    if not files:
        logger.info("fact_economico_municipio: nenhum Silver de economia disponível")
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_economico_municipio: %d rows de %d arquivos Silver", len(df), len(files))
    return df


def _build_fact_meta_ads_uf() -> pd.DataFrame:
    """Aggregate Silver meta_ads_regioes into Gold fact_meta_ads_uf (local path)."""
    files = list(LOCAL_SILVER_DIR.glob("meta_ads_regioes_BR_*.parquet"))
    if not files:
        logger.info("fact_meta_ads_uf: nenhum Silver meta_ads_regioes disponível")
        return pd.DataFrame()

    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    if "dt_inicio" in df.columns:
        ts = pd.to_datetime(df["dt_inicio"], errors="coerce")
        df["ano_semana"] = ts.dt.strftime("%Y-W%V").fillna("")
        df["semana"] = ts.dt.isocalendar().week.astype("Int64")

    for col in ("vl_gasto_estimado_uf", "qt_impressoes_estimadas_uf"):
        if col not in df.columns:
            df[col] = 0.0

    group_cols = [
        c for c in ["candidato", "sg_uf", "ano", "ano_semana", "semana"] if c in df.columns
    ]
    fact = df.groupby(group_cols, as_index=False, dropna=False).agg(
        qt_anuncios=("ad_id", "nunique")
        if "ad_id" in df.columns
        else ("vl_gasto_estimado_uf", "count"),
        vl_gasto_total_uf=("vl_gasto_estimado_uf", "sum"),
        qt_impressoes_total_uf=("qt_impressoes_estimadas_uf", "sum"),
    )
    fact["custo_por_mil_impressoes"] = fact.apply(
        lambda r: (
            r["vl_gasto_total_uf"] / r["qt_impressoes_total_uf"] * 1000
            if r["qt_impressoes_total_uf"] > 0
            else 0.0
        ),
        axis=1,
    )
    fact["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_meta_ads_uf: %d rows", len(fact))
    return fact


def _build_fact_meta_ads_demografico() -> pd.DataFrame:
    """Aggregate Silver meta_ads_demograficos into Gold fact_meta_ads_demografico (local path)."""
    files = list(LOCAL_SILVER_DIR.glob("meta_ads_demograficos_BR_*.parquet"))
    if not files:
        logger.info("fact_meta_ads_demografico: nenhum Silver meta_ads_demograficos disponível")
        return pd.DataFrame()

    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    for col in ("vl_gasto_estimado_demo", "pct_demografico"):
        if col not in df.columns:
            df[col] = 0.0
    for col in ("faixa_etaria", "genero"):
        if col not in df.columns:
            df[col] = "desconhecido"

    group_cols = [c for c in ["candidato", "faixa_etaria", "genero", "ano"] if c in df.columns]
    fact = df.groupby(group_cols, as_index=False, dropna=False).agg(
        qt_anuncios=("ad_id", "nunique")
        if "ad_id" in df.columns
        else ("vl_gasto_estimado_demo", "count"),
        vl_gasto_estimado_total=("vl_gasto_estimado_demo", "sum"),
        pct_demografico_medio=("pct_demografico", "mean"),
    )
    fact["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_meta_ads_demografico: %d rows", len(fact))
    return fact


def _build_fact_emendas() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aggregate Silver emendas_parlamentares into two Gold facts (local path)."""
    files = list(LOCAL_SILVER_DIR.glob("emendas_parlamentares_*.parquet"))
    if not files:
        logger.info("fact_emendas_*: nenhum Silver emendas disponível")
        return pd.DataFrame(), pd.DataFrame()

    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    for col in ("vl_empenhado", "vl_liquidado", "vl_pago"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df_pago = df[df["vl_pago"] > 0] if "vl_pago" in df.columns else df

    # ── por parlamentar ───────────────────────────────────────────────────────
    parl_cols = [
        c
        for c in [
            "ano",
            "sg_uf",
            "sg_uf_parlamentar",
            "nm_parlamentar",
            "sg_partido",
            "ds_cargo_parlamentar",
            "tp_emenda",
            "ds_area",
        ]
        if c in df_pago.columns
    ]

    fact_parl = df_pago.groupby(parl_cols, as_index=False, dropna=False).agg(
        qt_emendas=("vl_pago", "count"),
        vl_empenhado_total=("vl_empenhado", "sum"),
        vl_liquidado_total=("vl_liquidado", "sum"),
        vl_pago_total=("vl_pago", "sum"),
        vl_pago_medio=("vl_pago", "mean"),
        qt_municipios_atendidos=("cd_municipio_ibge", "nunique")
        if "cd_municipio_ibge" in df_pago.columns
        else ("vl_pago", "count"),
    )
    fact_parl["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_emendas_parlamentar: %d rows", len(fact_parl))

    # ── por município ─────────────────────────────────────────────────────────
    mun_cols = [
        c
        for c in [
            "ano",
            "cd_municipio_ibge",
            "nm_municipio",
            "sg_uf",
            "ds_area",
            "tp_emenda",
        ]
        if c in df.columns
    ]

    df_mun = df[df["cd_municipio_ibge"].notna()] if "cd_municipio_ibge" in df.columns else df
    fact_mun = df_mun.groupby(mun_cols, as_index=False, dropna=False).agg(
        qt_emendas=("vl_pago", "count"),
        qt_parlamentares_distintos=("nm_parlamentar", "nunique")
        if "nm_parlamentar" in df_mun.columns
        else ("vl_pago", "count"),
        vl_empenhado_total=("vl_empenhado", "sum"),
        vl_liquidado_total=("vl_liquidado", "sum"),
        vl_pago_total=("vl_pago", "sum"),
    )
    fact_mun["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_emendas_municipio: %d rows", len(fact_mun))

    return fact_parl, fact_mun


def _build_fact_sancoes_uf() -> pd.DataFrame:
    """Aggregate Silver sancoes_empresas into Gold fact_sancoes_uf (local path)."""
    files = list(LOCAL_SILVER_DIR.glob("sancoes_empresas_*.parquet"))
    if not files:
        logger.info("fact_sancoes_uf: nenhum Silver sanções disponível")
        return pd.DataFrame()

    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    if "dt_inicio_sancao" in df.columns:
        ts = pd.to_datetime(df["dt_inicio_sancao"], errors="coerce")
        df["ano_sancao"] = ts.dt.year.astype("Int64")
    if "valor_multa" not in df.columns:
        df["valor_multa"] = 0.0
    df["valor_multa"] = pd.to_numeric(df["valor_multa"], errors="coerce").fillna(0.0)

    df_filtered = (
        df[df["sg_uf_sancionador"].notna() & (df["sg_uf_sancionador"] != "")]
        if "sg_uf_sancionador" in df.columns
        else df
    )

    group_cols = [
        c
        for c in [
            "fonte_sistema",
            "sg_uf_sancionador",
            "tp_sancao",
            "tp_pessoa",
            "ano_sancao",
        ]
        if c in df_filtered.columns
    ]

    fact = df_filtered.groupby(group_cols, as_index=False, dropna=False).agg(
        qt_sancoes=("valor_multa", "count"),
        vl_multa_total=("valor_multa", "sum"),
        vl_multa_medio=("valor_multa", "mean"),
        qt_orgaos_sancionadores=("nm_orgao_sancionador", "nunique")
        if "nm_orgao_sancionador" in df_filtered.columns
        else ("valor_multa", "count"),
    )
    fact.rename(columns={"sg_uf_sancionador": "sg_uf"}, inplace=True)
    fact["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_sancoes_uf: %d rows", len(fact))
    return fact


def _build_fact_google_trends_uf() -> pd.DataFrame:
    """Aggregate Silver google_trends_uf into Gold fact_google_trends_uf (local path)."""
    files = list(LOCAL_SILVER_DIR.glob("google_trends_uf_BR_*.parquet"))
    if not files:
        logger.info("fact_google_trends_uf: nenhum Silver google_trends_uf disponível")
        return pd.DataFrame()

    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    if "interesse_busca" not in df.columns:
        df["interesse_busca"] = 0
    df["interesse_busca"] = pd.to_numeric(df["interesse_busca"], errors="coerce").fillna(0)

    group_cols = [c for c in ["candidato", "sg_uf", "ano"] if c in df.columns]
    fact = df.groupby(group_cols, as_index=False, dropna=False).agg(
        interesse_busca_medio=("interesse_busca", "mean"),
        interesse_busca_max=("interesse_busca", "max"),
    )
    fact["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_google_trends_uf: %d rows", len(fact))
    return fact


def _write_gold(df: pd.DataFrame, table_name: str, use_bigquery: bool) -> str:
    if df.empty:
        logger.warning(f"Gold {table_name}: DataFrame vazio, não escrito.")
        return ""

    if use_bigquery:
        return _write_bigquery_gold(df, table_name)

    path = LOCAL_GOLD_DIR / f"{table_name}.parquet"
    df.to_parquet(path, index=False, compression="zstd")
    logger.info(f"Gold escrito: {path} ({len(df)} rows, {len(df.columns)} colunas)")
    return str(path)


_GOLD_PARTITION_FIELD = {
    "fact_municipio_eleicao": "ano_eleicao",
    "fact_secao_eleicao": "ano_eleicao",
    "fact_candidato_dia": "data",
    "fact_pesquisa": "data_pesquisa",
}

_GOLD_CLUSTER_FIELDS = {
    "fact_municipio_eleicao": ["sg_uf", "cod_municipio_ibge", "ano_eleicao"],
    "fact_secao_eleicao": ["sg_uf", "cod_municipio_ibge", "nr_zona"],
    "fact_candidato_dia": ["nm_candidato", "sg_uf", "ano_eleicao"],
    "fact_pesquisa": ["instituto", "candidato", "sg_uf"],
}


def _normalize_for_bq(df: pd.DataFrame) -> pd.DataFrame:
    """Convert pandas extension types (Int64, Float64, boolean) to numpy types for BQ upload."""
    df = df.copy()
    for col in df.columns:
        dtype = df[col].dtype
        if hasattr(dtype, "numpy_dtype"):
            if pd.api.types.is_integer_dtype(dtype):
                # Prefer int64 to match BQ INTEGER; fall back to float64 only if NaN present
                if df[col].isna().any():
                    df[col] = df[col].astype("float64")
                else:
                    df[col] = df[col].astype("int64")
            elif pd.api.types.is_float_dtype(dtype):
                df[col] = df[col].astype("float64")
            elif pd.api.types.is_bool_dtype(dtype):
                df[col] = df[col].astype("object")
        elif hasattr(df[col], "cat"):
            df[col] = df[col].astype("object")
    return df


def _write_bigquery_gold(df: pd.DataFrame, table_name: str) -> str:
    project = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
    dataset = os.environ.get("BIGQUERY_DATASET_GOLD", "spepe_gold")
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project)
        table_id = f"{project}.{dataset}.{table_name}"

        partition_field = _GOLD_PARTITION_FIELD.get(table_name)
        cluster_fields = _GOLD_CLUSTER_FIELDS.get(table_name)

        df = _normalize_for_bq(df)

        # Determine partitioning strategy: integer fields need RangePartitioning
        time_partitioning = None
        range_partitioning = None
        if partition_field and partition_field in df.columns:
            dtype_str = str(df[partition_field].dtype)
            if dtype_str in ("int64", "float64"):
                df[partition_field] = df[partition_field].fillna(0).astype("int64")
                range_partitioning = bigquery.RangePartitioning(
                    field=partition_field,
                    range_=bigquery.PartitionRange(start=2010, end=2031, interval=1),
                )
            else:
                time_partitioning = bigquery.TimePartitioning(field=partition_field)

        # When table already exists (e.g. Terraform-managed), don't supply schema —
        # BQ validates against the existing definition and rejects mode mismatches.
        try:
            client.get_table(table_id)
            table_exists = True
        except Exception:
            table_exists = False

        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            create_disposition="CREATE_IF_NEEDED",
            time_partitioning=time_partitioning if not table_exists else None,
            range_partitioning=range_partitioning if not table_exists else None,
            clustering_fields=(
                ([f for f in cluster_fields if f in df.columns] if cluster_fields else None)
                if not table_exists
                else None
            ),
            autodetect=not table_exists,
            schema=_dataframe_to_bq_schema(df) if not table_exists else None,
        )

        if "ingested_at" not in df.columns:
            df = df.copy()
            df["ingested_at"] = pd.Timestamp.utcnow()

        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        logger.info("Gold BigQuery: %s (%d rows)", table_id, len(df))
        return table_id
    except ImportError:
        logger.warning("BigQuery não disponível. Usando local.")
        return _write_gold(df, table_name, use_bigquery=False)


def _dataframe_to_bq_schema(df: pd.DataFrame) -> list:
    from google.cloud import bigquery

    _type_map = {
        "int64": "INT64",
        "int32": "INT64",
        "Int64": "FLOAT64",
        "Int32": "FLOAT64",
        "float64": "FLOAT64",
        "float32": "FLOAT64",
        "Float64": "FLOAT64",
        "Float32": "FLOAT64",
        "bool": "BOOL",
        "boolean": "BOOL",
        "object": "STRING",
    }
    fields = []
    for col, dtype in df.dtypes.items():
        dtype_str = str(dtype)
        if dtype_str.startswith("datetime64"):
            bq_type = "TIMESTAMP"
        elif dtype_str == "date":
            bq_type = "DATE"
        else:
            bq_type = _type_map.get(dtype_str, "STRING")
        fields.append(bigquery.SchemaField(col, bq_type, mode="NULLABLE"))
    return fields


UF_REGIAO = {
    "AC": "Norte",
    "AM": "Norte",
    "AP": "Norte",
    "PA": "Norte",
    "RO": "Norte",
    "RR": "Norte",
    "TO": "Norte",
    "AL": "Nordeste",
    "BA": "Nordeste",
    "CE": "Nordeste",
    "MA": "Nordeste",
    "PB": "Nordeste",
    "PE": "Nordeste",
    "PI": "Nordeste",
    "RN": "Nordeste",
    "SE": "Nordeste",
    "DF": "Centro-Oeste",
    "GO": "Centro-Oeste",
    "MS": "Centro-Oeste",
    "MT": "Centro-Oeste",
    "ES": "Sudeste",
    "MG": "Sudeste",
    "RJ": "Sudeste",
    "SP": "Sudeste",
    "PR": "Sul",
    "RS": "Sul",
    "SC": "Sul",
}


def _build_fact_secao_eleicao(df: pd.DataFrame) -> pd.DataFrame:
    """Tabela granular — mantém nr_zona e nr_secao sem agregar."""
    if df.empty:
        return pd.DataFrame()
    required = [
        "sg_uf",
        "cd_municipio",
        "nr_zona",
        "nr_secao",
        "nm_candidato",
        "sg_partido",
        "cd_cargo",
        "nr_turno",
        "qt_votos",
        "ano_eleicao",
    ]
    cols = [c for c in required if c in df.columns]
    if len(cols) < 5:
        return pd.DataFrame()
    result = df[cols].copy()
    if "sg_uf" in result.columns:
        result["sg_regiao"] = result["sg_uf"].map(UF_REGIAO).fillna("Desconhecida")
    return result
