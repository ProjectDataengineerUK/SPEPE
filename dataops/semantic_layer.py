"""Semantic layer — cria BigQuery views de consumo sobre Gold + Silver."""

from __future__ import annotations

import datetime
import logging
import os

logger = logging.getLogger("spepe.dataops.semantic_layer")

_PROJECT = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
_GOLD = os.environ.get("BIGQUERY_DATASET_GOLD", "spepe_gold")
_SILVER = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")


_VIEWS: dict[str, str] = {
    # ── Sentimento social por fonte × UF × dia ────────────────────────────
    # Nota: social_mencoes_br não tem candidato populado — agregação por fonte/UF
    "vw_sentimento_municipio": f"""
        SELECT
            fonte,
            sg_uf,
            DATE(created_at)                                                AS data,
            COUNT(*)                                                        AS total_mencoes,
            COALESCE(SUM(like_count), 0)                                    AS total_likes,
            COALESCE(SUM(retweet_count), 0)                                 AS total_retweets,
            COALESCE(SUM(reply_count), 0)                                   AS total_replies,
            COALESCE(SUM(like_count), 0) + COALESCE(SUM(retweet_count), 0)
                + COALESCE(SUM(reply_count), 0)                             AS total_engajamento
        FROM `{_PROJECT}.{_SILVER}.social_mencoes_br`
        GROUP BY fonte, sg_uf, DATE(created_at)
    """,
    # ── Vulnerabilidade: eleição × segurança por município ────────────────
    "vw_vulnerabilidade_municipio": f"""
        SELECT
            e.cd_municipio_ibge,
            e.nm_municipio,
            e.sg_uf,
            e.ano_eleicao,
            e.nm_candidato                                                  AS vencedor,
            e.sg_partido                                                    AS partido_vencedor,
            ROUND(e.pct_votos_municipio, 1)                                 AS pct_vencedor,
            s.ivs_total,
            s.ivs_capital_humano,
            s.ivs_infraestrutura,
            s.ivs_renda_trabalho,
            s.taxa_homicidio_100k,
            i.populacao_total                                               AS populacao,
            i.taxa_analfabetismo
        FROM `{_PROJECT}.{_GOLD}.fact_municipio_candidato_eleicao` e
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_seguranca_municipio` s
            ON e.cd_municipio_ibge = s.cd_municipio_ibge AND s.ano = e.ano_eleicao
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_ibge_municipio` i
            ON e.cd_municipio_ibge = i.cd_municipio_ibge
        WHERE e.nr_turno = 1
          AND e.rn_municipio = 1
    """,
    # ── Perfil socioeconômico + resultado eleitoral por município ──────────
    "vw_perfil_municipio": f"""
        SELECT
            e.cd_municipio_ibge,
            e.nm_municipio,
            e.sg_uf,
            e.ano_eleicao,
            e.nm_candidato                                                  AS vencedor,
            e.sg_partido                                                    AS partido_vencedor,
            ROUND(e.pct_votos_municipio, 1)                                 AS pct_vencedor,
            i.populacao_total                                               AS populacao,
            i.taxa_analfabetismo,
            i.taxa_alfabetizacao,
            i.idhm,
            i.renda_per_capita,
            i.gini,
            i.pct_extrema_pobreza,
            i.pct_urbano
        FROM `{_PROJECT}.{_GOLD}.fact_municipio_candidato_eleicao` e
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_ibge_municipio` i
            ON e.cd_municipio_ibge = i.cd_municipio_ibge
        WHERE e.nr_turno = 1
          AND e.rn_municipio = 1
    """,
    # ── % de votos por candidato × UF ─────────────────────────────────────
    "vw_intencao_voto_uf": f"""
        SELECT
            sg_uf,
            nm_candidato,
            sg_partido,
            cd_cargo,
            ds_cargo,
            ano_eleicao,
            total_votos,
            ROUND(
                total_votos / NULLIF(SUM(total_votos) OVER (
                    PARTITION BY sg_uf, cd_cargo, ano_eleicao
                ), 0) * 100, 1
            )                                                               AS pct_uf
        FROM `{_PROJECT}.{_GOLD}.fact_candidato_eleicao`
    """,
    # ── Atividade de pesquisas × engajamento social por UF × semana ──────────
    # Fase 1: fact_pesquisa tem schema raw TSE PesqEle (registro de pesquisas,
    # sem intenção por candidato). Fase 2: adicionar candidato × intencao_ajustada
    # quando Silver pesquisas for processado com dados de resultados.
    "vw_pesquisa_vs_social": f"""
        WITH pesquisa_semanal AS (
            SELECT
                uf                                                              AS sg_uf,
                ds_cargo,
                DATE_TRUNC(DATE(data_pesquisa_inicio), WEEK(MONDAY))           AS semana,
                COUNT(*)                                                        AS qt_pesquisas,
                COUNT(DISTINCT nm_empresa)                                      AS qt_institutos
            FROM `{_PROJECT}.{_GOLD}.fact_pesquisa`
            WHERE record_confidence_score >= 0.80
              AND data_pesquisa_inicio IS NOT NULL
            GROUP BY uf, ds_cargo,
                     DATE_TRUNC(DATE(data_pesquisa_inicio), WEEK(MONDAY))
        ),
        social_semanal AS (
            SELECT
                sg_uf,
                DATE_TRUNC(DATE(created_at), WEEK(MONDAY))                     AS semana,
                COUNT(*)                                                        AS total_mencoes,
                COALESCE(SUM(like_count), 0)
                    + COALESCE(SUM(retweet_count), 0)
                    + COALESCE(SUM(reply_count), 0)                            AS total_engajamento
            FROM `{_PROJECT}.{_SILVER}.social_mencoes_br`
            GROUP BY sg_uf, DATE_TRUNC(DATE(created_at), WEEK(MONDAY))
        )
        SELECT
            p.sg_uf,
            p.ds_cargo,
            p.semana,
            p.qt_pesquisas,
            p.qt_institutos,
            COALESCE(s.total_mencoes, 0)                                        AS total_mencoes,
            COALESCE(s.total_engajamento, 0)                                    AS total_engajamento
        FROM pesquisa_semanal p
        LEFT JOIN social_semanal s
            ON p.sg_uf = s.sg_uf AND p.semana = s.semana
    """,
    # ── Narrativa por plataforma × UF × semana ────────────────────────────
    # Fase 1: 'fonte' (plataforma) como proxy de tema.
    # Fase 2: substituir por coluna 'tema' gerada por NLP Vertex AI.
    # Subquery necessária: BQ não aceita window function sobre COUNT(*) no mesmo nível do GROUP BY.
    "vw_narrativa_por_tema_uf": f"""
        SELECT
            plataforma,
            sg_uf,
            semana,
            volume_mencoes,
            engajamento_total,
            engajamento_por_mencao,
            ROUND(
                volume_mencoes / NULLIF(
                    SUM(volume_mencoes) OVER (PARTITION BY sg_uf, semana),
                    0
                ) * 100, 1
            )                                                                   AS share_plataforma_pct
        FROM (
            SELECT
                fonte                                                           AS plataforma,
                sg_uf,
                DATE_TRUNC(DATE(created_at), WEEK(MONDAY))                     AS semana,
                COUNT(*)                                                        AS volume_mencoes,
                COALESCE(SUM(like_count), 0)
                    + COALESCE(SUM(retweet_count), 0)
                    + COALESCE(SUM(reply_count), 0)                            AS engajamento_total,
                ROUND(
                    (COALESCE(SUM(like_count), 0)
                        + COALESCE(SUM(retweet_count), 0)
                        + COALESCE(SUM(reply_count), 0))
                    / NULLIF(COUNT(*), 0), 1
                )                                                               AS engajamento_por_mencao
            FROM `{_PROJECT}.{_SILVER}.social_mencoes_br`
            GROUP BY fonte, sg_uf, DATE_TRUNC(DATE(created_at), WEEK(MONDAY))
        )
    """,
    # ── Trajetória 2018 / 2022 (resultado) + 2026 (pesquisa) por UF ───────
    "vw_cenario_2018_2022_2026": f"""
        SELECT
            sg_uf,
            nm_candidato,
            sg_partido,
            cd_cargo,
            ds_cargo,
            ano_eleicao,
            total_votos,
            ROUND(
                total_votos / NULLIF(SUM(total_votos) OVER (
                    PARTITION BY sg_uf, cd_cargo, ano_eleicao
                ), 0) * 100, 1
            )                                                                   AS pct_voto_uf,
            'resultado'                                                         AS tipo_dado,
            CAST(NULL AS INT64)                                                 AS qt_pesquisas,
            CAST(NULL AS FLOAT64)                                               AS margem_erro_pp
        FROM `{_PROJECT}.{_GOLD}.fact_candidato_eleicao`
        WHERE ano_eleicao IN (2018, 2022)

        UNION ALL

        SELECT
            uf                                                                  AS sg_uf,
            candidato                                                           AS nm_candidato,
            CAST(NULL AS STRING)                                                AS sg_partido,
            cd_cargo,
            CAST(NULL AS STRING)                                                AS ds_cargo,
            2026                                                                AS ano_eleicao,
            CAST(NULL AS INT64)                                                 AS total_votos,
            ROUND(AVG(intencao_ajustada), 1)                                    AS pct_voto_uf,
            'pesquisa'                                                          AS tipo_dado,
            COUNT(*)                                                            AS qt_pesquisas,
            ROUND(AVG(margem_erro), 1)                                          AS margem_erro_pp
        FROM `{_PROJECT}.{_GOLD}.fact_pesquisa`
        WHERE record_confidence_score >= 0.80
          AND tipo_pesquisa = 'corrente'
        GROUP BY uf, candidato, cd_cargo
    """,
    # ── Transferências sociais por município × ano ───────────────────────
    "vw_transferencias_municipio": f"""
        SELECT
            t.cd_municipio_ibge,
            t.nm_municipio,
            t.sg_uf,
            t.ano,
            t.qtd_beneficiarios_bolsa_familia,
            t.valor_total_bolsa_familia_reais,
            t.qtd_familias_cadunico,
            t.qtd_familias_extrema_pobreza,
            t.qtd_familias_baixa_renda,
            i.populacao_total,
            ROUND(
                SAFE_DIVIDE(t.qtd_beneficiarios_bolsa_familia, NULLIF(i.populacao_total, 0)) * 100,
                1
            )                                               AS pct_pop_beneficiaria_bf,
            ROUND(
                SAFE_DIVIDE(t.valor_total_bolsa_familia_reais, NULLIF(i.populacao_total, 0)),
                2
            )                                               AS vl_bf_per_capita,
            ROUND(
                SAFE_DIVIDE(
                    t.qtd_familias_extrema_pobreza,
                    NULLIF(t.qtd_familias_cadunico, 0)
                ) * 100, 1
            )                                               AS pct_extrema_no_cadunico
        FROM `{_PROJECT}.{_GOLD}.fact_transferencias_sociais` t
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_ibge_municipio` i
            ON t.cd_municipio_ibge = i.cd_municipio_ibge
    """,
    # ── Transferências sociais × resultado eleitoral ──────────────────────
    "vw_transferencias_vs_eleicao": f"""
        WITH eleicao AS (
            SELECT
                cd_municipio_ibge, nm_municipio, sg_uf, ano_eleicao,
                nm_candidato    AS vencedor,
                sg_partido      AS partido_vencedor,
                ROUND(pct_votos_municipio, 1) AS pct_vencedor
            FROM `{_PROJECT}.{_GOLD}.fact_municipio_candidato_eleicao`
            WHERE nr_turno = 1 AND rn_municipio = 1
        ),
        transferencias_max AS (
            SELECT
                cd_municipio_ibge,
                MAX(ano)                                     AS ano_ref,
                SUM(qtd_beneficiarios_bolsa_familia)         AS qtd_beneficiarios_bf,
                SUM(valor_total_bolsa_familia_reais)         AS vl_bf_total,
                SUM(qtd_familias_cadunico)                   AS qtd_familias_cadunico,
                SUM(qtd_familias_extrema_pobreza)            AS qtd_familias_extrema_pobreza
            FROM `{_PROJECT}.{_GOLD}.fact_transferencias_sociais`
            GROUP BY cd_municipio_ibge
        )
        SELECT
            e.cd_municipio_ibge,
            e.nm_municipio,
            e.sg_uf,
            e.ano_eleicao,
            e.vencedor,
            e.partido_vencedor,
            e.pct_vencedor,
            t.ano_ref                                        AS ano_transferencia,
            t.qtd_beneficiarios_bf,
            t.vl_bf_total,
            t.qtd_familias_cadunico,
            t.qtd_familias_extrema_pobreza,
            i.populacao_total,
            i.idhm,
            i.renda_per_capita,
            i.pct_extrema_pobreza,
            ROUND(
                SAFE_DIVIDE(t.qtd_beneficiarios_bf, NULLIF(i.populacao_total, 0)) * 100,
                1
            )                                               AS pct_dependencia_bf,
            ROUND(
                SAFE_DIVIDE(t.vl_bf_total, NULLIF(i.populacao_total, 0)),
                2
            )                                               AS vl_bf_per_capita
        FROM eleicao e
        LEFT JOIN transferencias_max t USING (cd_municipio_ibge)
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_ibge_municipio` i USING (cd_municipio_ibge)
    """,
    # ── Emendas parlamentares por município × área (+ per capita) ────────
    "vw_emendas_municipio": f"""
        SELECT
            em.ano,
            em.cd_municipio_ibge,
            em.nm_municipio,
            em.sg_uf,
            em.ds_area,
            em.tp_emenda,
            em.qt_emendas,
            em.qt_parlamentares_distintos,
            em.vl_empenhado_total,
            em.vl_liquidado_total,
            em.vl_pago_total,
            i.populacao_total,
            ROUND(
                SAFE_DIVIDE(em.vl_pago_total, NULLIF(i.populacao_total, 0)),
                2
            )                                               AS vl_pago_per_capita,
            ROUND(
                SAFE_DIVIDE(em.vl_pago_total, NULLIF(em.vl_empenhado_total, 0)) * 100,
                1
            )                                               AS taxa_execucao_pct
        FROM `{_PROJECT}.{_GOLD}.fact_emendas_municipio` em
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_ibge_municipio` i USING (cd_municipio_ibge)
    """,
    # ── Emendas por partido × UF × eleição (influência eleitoral) ─────────
    "vw_emendas_vs_eleicao": f"""
        WITH emendas_partido_uf AS (
            SELECT
                ano,
                sg_uf,
                sg_partido,
                SUM(qt_emendas)    AS qt_emendas_total,
                SUM(vl_pago_total) AS vl_pago_total,
                COUNT(DISTINCT nm_parlamentar) AS qt_parlamentares
            FROM `{_PROJECT}.{_GOLD}.fact_emendas_parlamentar`
            GROUP BY ano, sg_uf, sg_partido
        ),
        votos_uf AS (
            SELECT
                sg_uf,
                nm_candidato,
                sg_partido,
                cd_cargo,
                ds_cargo,
                ano_eleicao,
                total_votos,
                ROUND(
                    total_votos / NULLIF(
                        SUM(total_votos) OVER (PARTITION BY sg_uf, cd_cargo, ano_eleicao),
                        0
                    ) * 100, 1
                )                  AS pct_votos_uf
            FROM `{_PROJECT}.{_GOLD}.fact_candidato_eleicao`
        )
        SELECT
            v.sg_uf,
            v.nm_candidato,
            v.sg_partido,
            v.cd_cargo,
            v.ds_cargo,
            v.ano_eleicao,
            v.total_votos,
            v.pct_votos_uf,
            COALESCE(em.qt_emendas_total, 0)    AS qt_emendas_partido_uf,
            COALESCE(em.vl_pago_total, 0.0)     AS vl_emendas_partido_uf,
            COALESCE(em.qt_parlamentares, 0)    AS qt_parlamentares_ativos
        FROM votos_uf v
        LEFT JOIN emendas_partido_uf em
            ON v.sg_uf = em.sg_uf
            AND v.sg_partido = em.sg_partido
            AND v.ano_eleicao = em.ano
    """,
    # ── Sanções federais por UF (proxy de governança) ─────────────────────
    "vw_sancoes_uf": f"""
        SELECT
            sg_uf,
            fonte_sistema,
            tp_sancao,
            tp_pessoa,
            ano_sancao,
            qt_sancoes,
            vl_multa_total,
            ROUND(SAFE_DIVIDE(vl_multa_total, NULLIF(qt_sancoes, 0)), 2) AS vl_multa_medio,
            qt_orgaos_sancionadores
        FROM `{_PROJECT}.{_GOLD}.fact_sancoes_uf`
        WHERE sg_uf IS NOT NULL AND sg_uf != ''
    """,
    # ── Score municipal integrado: todas as dimensões em 1 view ───────────
    # Ponto de entrada principal para analista e perfilador
    "vw_score_municipal_integrado": f"""
        WITH eleicao_ref AS (
            SELECT
                cd_municipio_ibge,
                nm_municipio,
                sg_uf,
                MAX(ano_eleicao)                             AS ano_eleicao_ref
            FROM `{_PROJECT}.{_GOLD}.fact_municipio_candidato_eleicao`
            WHERE nr_turno = 1 AND rn_municipio = 1
            GROUP BY cd_municipio_ibge, nm_municipio, sg_uf
        ),
        vencedor_ref AS (
            SELECT
                cd_municipio_ibge,
                nm_candidato    AS vencedor,
                sg_partido      AS partido_vencedor,
                cd_cargo,
                pct_votos_municipio AS pct_vencedor
            FROM `{_PROJECT}.{_GOLD}.fact_municipio_candidato_eleicao`
            WHERE nr_turno = 1 AND rn_municipio = 1
              AND (cd_municipio_ibge, ano_eleicao) IN (
                  SELECT cd_municipio_ibge, MAX(ano_eleicao)
                  FROM `{_PROJECT}.{_GOLD}.fact_municipio_candidato_eleicao`
                  GROUP BY cd_municipio_ibge
              )
        ),
        emendas_acum AS (
            SELECT
                cd_municipio_ibge,
                SUM(vl_pago_total)  AS vl_emendas_total,
                SUM(qt_emendas)     AS qt_emendas_total
            FROM `{_PROJECT}.{_GOLD}.fact_emendas_municipio`
            GROUP BY cd_municipio_ibge
        ),
        transferencias_acum AS (
            SELECT
                cd_municipio_ibge,
                SUM(qtd_beneficiarios_bolsa_familia)    AS qtd_beneficiarios_bf,
                SUM(valor_total_bolsa_familia_reais)    AS vl_bf_total,
                SUM(qtd_familias_cadunico)              AS qtd_familias_cadunico,
                SUM(qtd_familias_extrema_pobreza)       AS qtd_familias_extrema_pobreza
            FROM `{_PROJECT}.{_GOLD}.fact_transferencias_sociais`
            GROUP BY cd_municipio_ibge
        )
        SELECT
            er.cd_municipio_ibge,
            er.nm_municipio,
            er.sg_uf,
            er.ano_eleicao_ref,
            vr.vencedor,
            vr.partido_vencedor,
            ROUND(vr.pct_vencedor, 1)                       AS pct_vencedor,

            -- Socioeconômico IBGE
            i.idhm,
            i.renda_per_capita,
            i.gini,
            i.pct_extrema_pobreza,
            i.taxa_analfabetismo,
            i.pct_urbano,
            CAST(i.populacao_total AS INT64)                AS populacao_total,

            -- Segurança pública
            s.ivs_total,
            s.ivs_capital_humano,
            s.ivs_infraestrutura,
            s.ivs_renda_trabalho,
            s.taxa_homicidio_100k,

            -- Saúde
            sa.taxa_mortalidade_infantil_1000,
            sa.idsus_score,

            -- Transferências sociais
            t.qtd_beneficiarios_bf,
            t.qtd_familias_cadunico,
            t.qtd_familias_extrema_pobreza,
            ROUND(
                SAFE_DIVIDE(t.qtd_beneficiarios_bf, NULLIF(i.populacao_total, 0)) * 100,
                1
            )                                               AS pct_pop_beneficiaria_bf,
            ROUND(
                SAFE_DIVIDE(t.vl_bf_total, NULLIF(i.populacao_total, 0)),
                2
            )                                               AS vl_bf_per_capita,

            -- Emendas parlamentares
            COALESCE(em.vl_emendas_total, 0.0)              AS vl_emendas_total,
            COALESCE(em.qt_emendas_total, 0)                AS qt_emendas_total,
            ROUND(
                SAFE_DIVIDE(em.vl_emendas_total, NULLIF(i.populacao_total, 0)),
                2
            )                                               AS vl_emendas_per_capita,

            -- Score de desenvolvimento composto (0–100)
            -- Componentes: IDHM (40%) + segurança (20%) + saúde (20%) + renda (20%)
            ROUND(
                COALESCE(i.idhm, 0.5) * 40.0
                + (1.0 - LEAST(1.0, COALESCE(SAFE_DIVIDE(s.ivs_total, 1.0), 0.5))) * 20.0
                + (1.0 - LEAST(1.0, COALESCE(SAFE_DIVIDE(sa.taxa_mortalidade_infantil_1000, 50.0), 0.5))) * 20.0
                + (1.0 - LEAST(1.0, COALESCE(SAFE_DIVIDE(i.pct_extrema_pobreza, 50.0), 0.5))) * 20.0
            , 1)                                            AS score_desenvolvimento,

            CURRENT_TIMESTAMP()                             AS ingested_at

        FROM eleicao_ref er
        LEFT JOIN vencedor_ref vr USING (cd_municipio_ibge)
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_ibge_municipio` i USING (cd_municipio_ibge)
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_seguranca_municipio` s USING (cd_municipio_ibge)
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_saude_municipio` sa USING (cd_municipio_ibge)
        LEFT JOIN transferencias_acum t USING (cd_municipio_ibge)
        LEFT JOIN emendas_acum em USING (cd_municipio_ibge)
    """,
    # ── Mapa de prioridade de campanha: municípios por competitividade ─────
    "vw_mapa_prioridade_campanha": f"""
        WITH top2 AS (
            SELECT
                cd_municipio_ibge,
                nm_municipio,
                sg_uf,
                ano_eleicao,
                cd_cargo,
                nm_candidato,
                sg_partido,
                pct_votos_municipio,
                rn_municipio
            FROM `{_PROJECT}.{_GOLD}.fact_municipio_candidato_eleicao`
            WHERE nr_turno = 1 AND rn_municipio <= 2
        ),
        margem AS (
            SELECT
                cd_municipio_ibge,
                nm_municipio,
                sg_uf,
                ano_eleicao,
                cd_cargo,
                MAX(CASE WHEN rn_municipio = 1 THEN nm_candidato  END)          AS lider,
                MAX(CASE WHEN rn_municipio = 1 THEN sg_partido    END)          AS partido_lider,
                ROUND(MAX(CASE WHEN rn_municipio = 1 THEN pct_votos_municipio END), 1)
                                                                                AS pct_lider,
                MAX(CASE WHEN rn_municipio = 2 THEN nm_candidato  END)          AS segundo,
                ROUND(MAX(CASE WHEN rn_municipio = 2 THEN pct_votos_municipio END), 1)
                                                                                AS pct_segundo,
                ROUND(
                    MAX(CASE WHEN rn_municipio = 1 THEN pct_votos_municipio END) -
                    MAX(CASE WHEN rn_municipio = 2 THEN pct_votos_municipio END), 1
                )                                                               AS margem_pp
            FROM top2
            GROUP BY cd_municipio_ibge, nm_municipio, sg_uf, ano_eleicao, cd_cargo
        )
        SELECT
            m.cd_municipio_ibge,
            m.nm_municipio,
            m.sg_uf,
            m.ano_eleicao,
            m.cd_cargo,
            m.lider,
            m.partido_lider,
            m.pct_lider,
            m.segundo,
            m.pct_segundo,
            m.margem_pp,
            COALESCE(i.populacao_total, 0)                                      AS populacao,
            i.idhm,
            ROUND(GREATEST(0.0, 100.0 - m.margem_pp * 2.0), 1)                AS score_competitividade,
            CASE
                WHEN m.margem_pp <= 5  THEN 'Disputado'
                WHEN m.margem_pp <= 15 THEN 'Competitivo'
                WHEN m.margem_pp <= 30 THEN 'Inclinado'
                ELSE                        'Definido'
            END                                                                 AS classificacao_disputa
        FROM margem m
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_ibge_municipio` i
            ON m.cd_municipio_ibge = i.cd_municipio_ibge
    """,
}


_MV_ZONA_SQL = """
    SELECT
        sg_uf,
        cd_municipio,
        nm_municipio,
        nr_zona,
        cd_cargo,
        nm_candidato,
        sg_partido,
        nr_turno,
        ano_eleicao,
        SUM(qt_votos) AS qt_votos_total,
        COUNT(*)         AS qt_secoes
    FROM `{project}.{gold}.fact_secao_eleicao`
    GROUP BY sg_uf, cd_municipio, nm_municipio, nr_zona,
             cd_cargo, nm_candidato, sg_partido, nr_turno, ano_eleicao
"""


def create_semantic_views(project: str | None = None, replace: bool = True) -> dict[str, str]:
    """Cria ou substitui as views semânticas no BigQuery Gold dataset.

    Returns: {view_name: status}
    """
    try:
        from google.cloud import bigquery
    except ImportError:
        logger.error("google-cloud-bigquery não instalado")
        return {}

    proj = project or _PROJECT
    client = bigquery.Client(project=proj)
    results: dict[str, str] = {}

    for view_name, sql in _VIEWS.items():
        full_id = f"{proj}.{_GOLD}.{view_name}"
        view = bigquery.Table(full_id)
        view.view_query = sql.strip()

        try:
            if replace:
                client.delete_table(full_id, not_found_ok=True)
            client.create_table(view)
            logger.info("View criada: %s", full_id)
            results[view_name] = "ok"
        except Exception as exc:
            logger.error("Falha ao criar view %s: %s", view_name, exc)
            results[view_name] = f"erro: {exc}"

    # ── Recria mv_zona_eleicao (MV depende de fact_secao_eleicao) ──────────
    mv_id = f"{proj}.{_GOLD}.mv_zona_eleicao"
    try:
        client.delete_table(mv_id, not_found_ok=True)
        mv_sql = _MV_ZONA_SQL.format(project=proj, gold=_GOLD)
        mv_table = bigquery.Table(mv_id)
        mv_table.mview_query = mv_sql.strip()
        mv_table.mview_enable_refresh = True
        mv_table.mview_refresh_interval = datetime.timedelta(hours=1)
        client.create_table(mv_table)
        logger.info("MV recriada: %s", mv_id)
        results["mv_zona_eleicao"] = "ok"
    except Exception as exc:
        logger.error("Falha ao recriar mv_zona_eleicao: %s", exc)
        results["mv_zona_eleicao"] = f"erro: {exc}"

    return results


if __name__ == "__main__":
    import json

    results = create_semantic_views()
    print(json.dumps(results, indent=2, ensure_ascii=False))
