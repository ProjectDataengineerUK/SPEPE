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
    sqls = {
        "fact_municipio_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_municipio_eleicao` AS
            SELECT
                sg_uf, cd_municipio, nm_municipio, cd_cargo, ds_cargo,
                ano_eleicao,
                SUM(qt_votos) AS total_votos,
                COUNT(DISTINCT nr_candidato) AS n_candidatos,
                COUNT(DISTINCT CONCAT(CAST(nr_zona AS STRING), '-', CAST(nr_secao AS STRING))) AS n_secoes,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM {silver_wc}
            GROUP BY sg_uf, cd_municipio, nm_municipio, cd_cargo, ds_cargo, ano_eleicao
        """,
        "fact_secao_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_secao_eleicao` AS
            SELECT
                sg_uf, cd_municipio, nm_municipio, nr_zona, nr_secao,
                nm_candidato, sg_partido, cd_cargo, ds_cargo, nr_turno, ano_eleicao,
                SUM(qt_votos) AS total_votos,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM {silver_wc}
            GROUP BY sg_uf, cd_municipio, nm_municipio, nr_zona, nr_secao,
                     nm_candidato, sg_partido, cd_cargo, ds_cargo, nr_turno, ano_eleicao
        """,
        "fact_candidato_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_candidato_eleicao` AS
            SELECT
                sg_uf, nr_candidato, nm_candidato, sg_partido, cd_cargo, ds_cargo,
                ano_eleicao,
                SUM(qt_votos) AS total_votos,
                COUNT(DISTINCT cd_municipio) AS n_municipios,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM {silver_wc}
            GROUP BY sg_uf, nr_candidato, nm_candidato, sg_partido, cd_cargo, ds_cargo, ano_eleicao
        """,
        "fact_municipio_candidato_eleicao": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_municipio_candidato_eleicao` AS
            SELECT
                sg_uf, cd_municipio, nm_municipio, cd_municipio_ibge,
                nm_candidato, sg_partido, cd_cargo, ds_cargo, nr_turno, ano_eleicao,
                SUM(qt_votos) AS total_votos,
                ROUND(
                    SUM(qt_votos) / NULLIF(SUM(SUM(qt_votos)) OVER (
                        PARTITION BY sg_uf, cd_municipio, cd_cargo, nr_turno, ano_eleicao
                    ), 0) * 100, 1
                ) AS pct_votos_municipio,
                ROW_NUMBER() OVER (
                    PARTITION BY sg_uf, cd_municipio, cd_cargo, nr_turno, ano_eleicao
                    ORDER BY SUM(qt_votos) DESC
                ) AS rn_municipio,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM {silver_wc}
            GROUP BY sg_uf, cd_municipio, nm_municipio, cd_municipio_ibge,
                     nm_candidato, sg_partido, cd_cargo, ds_cargo, nr_turno, ano_eleicao
        """,
        "fact_ibge_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_ibge_municipio` AS
            SELECT
                SAFE_CAST(cd_municipio_ibge AS INT64)          AS cd_municipio_ibge,
                sg_uf,
                ANY_VALUE(nm_municipio)                         AS nm_municipio,
                MAX(SAFE_CAST(ano AS INT64))                    AS ano,
                MAX(SAFE_CAST(idhm AS FLOAT64))                 AS idhm,
                MAX(SAFE_CAST(renda_per_capita AS FLOAT64))     AS renda_per_capita,
                MAX(SAFE_CAST(gini AS FLOAT64))                 AS gini,
                MAX(SAFE_CAST(pct_extrema_pobreza AS FLOAT64))  AS pct_extrema_pobreza,
                MAX(SAFE_CAST(taxa_analfabetismo AS FLOAT64))   AS taxa_analfabetismo,
                MAX(SAFE_CAST(pct_urbano AS FLOAT64))           AS pct_urbano,
                MAX(SAFE_CAST(populacao_total AS FLOAT64))      AS populacao_total,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM `{silver}.ibge_*`
            WHERE cd_municipio_ibge IS NOT NULL
            GROUP BY cd_municipio_ibge, sg_uf
        """,
        "fact_seguranca_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_seguranca_municipio` AS
            SELECT
                CAST(cd_municipio_ibge AS INT64)                    AS cd_municipio_ibge,
                sg_uf,
                COALESCE(SAFE_CAST(ano AS INT64), 2022)             AS ano,
                COALESCE(
                    SAFE_CAST(ivs_total AS FLOAT64),
                    SAFE_CAST(ivs_valor AS FLOAT64)
                )                                                   AS ivs_total,
                SAFE_CAST(ivs_infraestrutura AS FLOAT64)            AS ivs_infraestrutura,
                SAFE_CAST(ivs_capital_humano AS FLOAT64)            AS ivs_capital_humano,
                SAFE_CAST(ivs_renda_trabalho AS FLOAT64)            AS ivs_renda_trabalho,
                COALESCE(
                    SAFE_CAST(taxa_homicidio_100k AS FLOAT64),
                    SAFE_CAST(taxa_homicidio AS FLOAT64)
                )                                                   AS taxa_homicidio_100k,
                SAFE_CAST(taxa_roubo_100k AS FLOAT64)               AS taxa_roubo_100k,
                SAFE_CAST(qt_feminicidio AS INT64)                  AS qt_feminicidio,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM `{silver}.seguranca_municipal`
            WHERE cd_municipio_ibge IS NOT NULL
        """,
        "fact_saude_municipio": f"""
            CREATE OR REPLACE TABLE `{gold}.fact_saude_municipio` AS
            SELECT
                CAST(cd_municipio_ibge AS INT64)             AS cd_municipio_ibge,
                sg_uf,
                COALESCE(SAFE_CAST(ano AS INT64), 2022)      AS ano,
                COALESCE(
                    SAFE_CAST(taxa_mortalidade_infantil_1000 AS FLOAT64),
                    SAFE_CAST(tx_mortalidade_infantil AS FLOAT64)
                )                                            AS taxa_mortalidade_infantil_1000,
                SAFE_CAST(taxa_mortalidade_materna_100k AS FLOAT64) AS taxa_mortalidade_materna_100k,
                COALESCE(
                    SAFE_CAST(pct_cobertura_plano_saude AS FLOAT64),
                    SAFE_CAST(cobertura_esf_pct AS FLOAT64)
                )                                            AS pct_cobertura_plano_saude,
                SAFE_CAST(idsus_score AS FLOAT64)            AS idsus_score,
                CURRENT_TIMESTAMP() AS ingested_at
            FROM `{silver}.saude_municipal`
            WHERE cd_municipio_ibge IS NOT NULL
        """,
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
                COALESCE(SUM(retweet_count), 0)             AS total_retweets,
                COALESCE(SUM(reply_count), 0)               AS total_comments,
                COALESCE(SUM(view_count), 0)                AS total_views,
                COALESCE(SUM(like_count), 0) + COALESCE(SUM(retweet_count), 0)
                    + COALESCE(SUM(reply_count), 0)         AS total_engajamento,
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
            FROM `{silver}.google_trends_uf_BR`
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
                fonte_sistema,
                sg_uf_sancionador                   AS sg_uf,
                tp_sancao,
                tp_pessoa,
                EXTRACT(YEAR FROM dt_inicio_sancao) AS ano_sancao,
                COUNT(*)                            AS qt_sancoes,
                SUM(valor_multa)                    AS vl_multa_total,
                AVG(valor_multa)                    AS vl_multa_medio,
                COUNT(DISTINCT nm_orgao_sancionador) AS qt_orgaos_sancionadores,
                CURRENT_TIMESTAMP()                 AS ingested_at
            FROM `{silver}.sancoes_empresas`
            WHERE sg_uf_sancionador IS NOT NULL AND sg_uf_sancionador != ''
            GROUP BY
                fonte_sistema, sg_uf_sancionador, tp_sancao, tp_pessoa,
                EXTRACT(YEAR FROM dt_inicio_sancao)
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
    }

    # Tables that may have no Silver source yet — skip without failing the job
    _OPTIONAL = {
        "fact_pesquisa",
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
    df["score_confiabilidade"] = pd.to_numeric(df["score_confiabilidade"], errors="coerce").fillna(5.0)

    group_cols = [
        "sg_uf", "candidato", "fonte", "tipo_fonte", "vies_politico",
        "ano_semana", "semana", "ano", "data_referencia",
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

    group_cols = [c for c in ["candidato", "sg_uf", "ano", "ano_semana", "semana"] if c in df.columns]
    fact = df.groupby(group_cols, as_index=False, dropna=False).agg(
        qt_anuncios=("ad_id", "nunique") if "ad_id" in df.columns else ("vl_gasto_estimado_uf", "count"),
        vl_gasto_total_uf=("vl_gasto_estimado_uf", "sum"),
        qt_impressoes_total_uf=("qt_impressoes_estimadas_uf", "sum"),
    )
    fact["custo_por_mil_impressoes"] = fact.apply(
        lambda r: r["vl_gasto_total_uf"] / r["qt_impressoes_total_uf"] * 1000
        if r["qt_impressoes_total_uf"] > 0 else 0.0,
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
        qt_anuncios=("ad_id", "nunique") if "ad_id" in df.columns else ("vl_gasto_estimado_demo", "count"),
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
    parl_cols = [c for c in [
        "ano", "sg_uf", "sg_uf_parlamentar", "nm_parlamentar",
        "sg_partido", "ds_cargo_parlamentar", "tp_emenda", "ds_area",
    ] if c in df_pago.columns]

    fact_parl = df_pago.groupby(parl_cols, as_index=False, dropna=False).agg(
        qt_emendas=("vl_pago", "count"),
        vl_empenhado_total=("vl_empenhado", "sum"),
        vl_liquidado_total=("vl_liquidado", "sum"),
        vl_pago_total=("vl_pago", "sum"),
        vl_pago_medio=("vl_pago", "mean"),
        qt_municipios_atendidos=("cd_municipio_ibge", "nunique") if "cd_municipio_ibge" in df_pago.columns else ("vl_pago", "count"),
    )
    fact_parl["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("fact_emendas_parlamentar: %d rows", len(fact_parl))

    # ── por município ─────────────────────────────────────────────────────────
    mun_cols = [c for c in [
        "ano", "cd_municipio_ibge", "nm_municipio", "sg_uf", "ds_area", "tp_emenda",
    ] if c in df.columns]

    df_mun = df[df["cd_municipio_ibge"].notna()] if "cd_municipio_ibge" in df.columns else df
    fact_mun = df_mun.groupby(mun_cols, as_index=False, dropna=False).agg(
        qt_emendas=("vl_pago", "count"),
        qt_parlamentares_distintos=("nm_parlamentar", "nunique") if "nm_parlamentar" in df_mun.columns else ("vl_pago", "count"),
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

    df_filtered = df[
        df["sg_uf_sancionador"].notna() & (df["sg_uf_sancionador"] != "")
    ] if "sg_uf_sancionador" in df.columns else df

    group_cols = [c for c in [
        "fonte_sistema", "sg_uf_sancionador", "tp_sancao", "tp_pessoa", "ano_sancao",
    ] if c in df_filtered.columns]

    fact = df_filtered.groupby(group_cols, as_index=False, dropna=False).agg(
        qt_sancoes=("valor_multa", "count"),
        vl_multa_total=("valor_multa", "sum"),
        vl_multa_medio=("valor_multa", "mean"),
        qt_orgaos_sancionadores=("nm_orgao_sancionador", "nunique") if "nm_orgao_sancionador" in df_filtered.columns else ("valor_multa", "count"),
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
