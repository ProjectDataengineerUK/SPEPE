"""Semantic layer — cria BigQuery views de consumo sobre Gold + Silver."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("spepe.dataops.semantic_layer")

_PROJECT = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
_GOLD = os.environ.get("BIGQUERY_DATASET_GOLD", "spepe_gold")
_SILVER = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")


_VIEWS: dict[str, str] = {
    # ── Sentimento social por fonte × UF × dia ────────────────────────────
    # (candidato não disponível em social_mencoes_br — agregação por fonte/UF)
    "vw_sentimento_candidato": f"""
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
    # ── Pesquisa vs sinal digital: intenção × engajamento por UF × semana ──
    # fact_pesquisa usa colunas: uf, candidato, data_pesquisa_inicio, intencao_ajustada
    # social_mencoes_br usa: sg_uf, created_at, like_count, retweet_count, reply_count
    "vw_pesquisa_vs_social": f"""
        WITH pesquisa_semanal AS (
            SELECT
                uf                                                              AS sg_uf,
                candidato                                                       AS nm_candidato,
                cd_cargo,
                DATE_TRUNC(data_pesquisa_inicio, WEEK(MONDAY))                 AS semana,
                ROUND(AVG(intencao_ajustada), 1)                               AS intencao_media_pct,
                COUNT(*)                                                        AS qt_pesquisas,
                ROUND(AVG(margem_erro), 1)                                      AS margem_media_pp
            FROM `{_PROJECT}.{_GOLD}.fact_pesquisa`
            WHERE record_confidence_score >= 0.80
              AND data_pesquisa_inicio IS NOT NULL
            GROUP BY uf, candidato, cd_cargo,
                     DATE_TRUNC(data_pesquisa_inicio, WEEK(MONDAY))
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
            p.nm_candidato,
            p.cd_cargo,
            p.semana,
            p.intencao_media_pct,
            p.qt_pesquisas,
            p.margem_media_pp,
            COALESCE(s.total_mencoes, 0)                                        AS total_mencoes,
            COALESCE(s.total_engajamento, 0)                                    AS total_engajamento,
            ROUND(
                p.intencao_media_pct - LAG(p.intencao_media_pct) OVER (
                    PARTITION BY p.sg_uf, p.nm_candidato, p.cd_cargo
                    ORDER BY p.semana
                ), 1
            )                                                                   AS delta_intencao_pp
        FROM pesquisa_semanal p
        LEFT JOIN social_semanal s
            ON p.sg_uf = s.sg_uf AND p.semana = s.semana
    """,
    # ── Narrativa por plataforma × UF × semana ────────────────────────────
    # Fase 1: 'fonte' (plataforma) como proxy de tema.
    # Fase 2: substituir por coluna 'tema' gerada por NLP Vertex AI.
    "vw_narrativa_por_tema_uf": f"""
        SELECT
            fonte                                                               AS plataforma,
            sg_uf,
            DATE_TRUNC(DATE(created_at), WEEK(MONDAY))                         AS semana,
            COUNT(*)                                                            AS volume_mencoes,
            COALESCE(SUM(like_count), 0)
                + COALESCE(SUM(retweet_count), 0)
                + COALESCE(SUM(reply_count), 0)                                AS engajamento_total,
            ROUND(
                (COALESCE(SUM(like_count), 0)
                    + COALESCE(SUM(retweet_count), 0)
                    + COALESCE(SUM(reply_count), 0))
                / NULLIF(COUNT(*), 0), 1
            )                                                                   AS engajamento_por_mencao,
            ROUND(
                COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (
                    PARTITION BY sg_uf, DATE_TRUNC(DATE(created_at), WEEK(MONDAY))
                ), 0) * 100, 1
            )                                                                   AS share_plataforma_pct
        FROM `{_PROJECT}.{_SILVER}.social_mencoes_br`
        GROUP BY fonte, sg_uf, DATE_TRUNC(DATE(created_at), WEEK(MONDAY))
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
        ds_cargo,
        nm_candidato,
        sg_partido,
        nr_turno,
        ano_eleicao,
        SUM(total_votos) AS qt_votos_total,
        COUNT(*)         AS qt_secoes
    FROM `{project}.{gold}.fact_secao_eleicao`
    GROUP BY sg_uf, cd_municipio, nm_municipio, nr_zona,
             cd_cargo, ds_cargo, nm_candidato, sg_partido, nr_turno, ano_eleicao
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
        mv_table.mview_refresh_interval = "3600s"
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
