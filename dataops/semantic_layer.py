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
