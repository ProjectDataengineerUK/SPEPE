"""Semantic layer — cria BigQuery views de consumo sobre Gold + Silver."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("spepe.dataops.semantic_layer")

_PROJECT = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
_GOLD = os.environ.get("BIGQUERY_DATASET_GOLD", "spepe_gold")
_SILVER = os.environ.get("BIGQUERY_DATASET_SILVER", "spepe_silver")


_VIEWS: dict[str, str] = {
    # ── Sentimento social agregado por candidato ───────────────────────────
    "vw_sentimento_candidato": f"""
        SELECT
            candidato,
            DATE(created_at)                                                AS data,
            COUNT(*)                                                        AS total_mencoes,
            COUNTIF(sentiment = 'positivo')                                 AS positivo,
            COUNTIF(sentiment = 'negativo')                                 AS negativo,
            COUNTIF(sentiment = 'neutro')                                   AS neutro,
            ROUND(COUNTIF(sentiment = 'positivo') / COUNT(*) * 100, 1)     AS pct_positivo,
            ROUND(COUNTIF(sentiment = 'negativo') / COUNT(*) * 100, 1)     AS pct_negativo,
            ROUND(
                (COUNTIF(sentiment = 'positivo') - COUNTIF(sentiment = 'negativo'))
                / COUNT(*) * 100, 1
            )                                                               AS score_liquido,
            SUM(like_count)                                                 AS total_likes,
            SUM(retweet_count)                                              AS total_retweets
        FROM `{_PROJECT}.{_SILVER}.social_mencoes_br`
        WHERE fonte = 'twitter_x'
        GROUP BY candidato, DATE(created_at)
    """,
    # ── Vulnerabilidade sanitária × eleição por município ─────────────────
    "vw_vulnerabilidade_municipio": f"""
        SELECT
            e.cd_municipio_ibge,
            e.nm_municipio,
            e.sg_uf,
            e.ano_eleicao,
            e.nm_candidato                                                  AS vencedor,
            e.sg_partido                                                    AS partido_vencedor,
            ROUND(e.pct_votos_municipio, 1)                                 AS pct_vencedor,
            d.taxa_mortalidade_infantil_1000,
            d.taxa_mortalidade_materna_100k,
            d.pct_cobertura_plano_saude,
            i.pct_urbano,
            i.populacao,
            ROUND(
                0.4 * COALESCE(d.taxa_mortalidade_infantil_1000 / 50.0, 0.5)
                + 0.3 * COALESCE(1 - d.pct_cobertura_plano_saude / 100.0, 0.5)
                + 0.3 * COALESCE(1 - i.pct_urbano / 100.0, 0.5)
            , 3)                                                            AS svs_score
        FROM `{_PROJECT}.{_GOLD}.fact_municipio_eleicao` e
        LEFT JOIN `{_PROJECT}.{_SILVER}.datasus_municipios` d
            ON e.cd_municipio_ibge = d.cd_municipio_ibge AND e.ano_eleicao = d.ano
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
            i.populacao,
            i.taxa_alfabetizacao,
            i.pct_analfabetos,
            i.pct_urbano,
            i.pct_0_14,
            i.pct_15_29,
            i.pct_30_59,
            i.pct_60_mais,
            i.pct_catolico,
            i.pct_sem_religiao
        FROM `{_PROJECT}.{_GOLD}.fact_municipio_eleicao` e
        LEFT JOIN `{_PROJECT}.{_GOLD}.fact_ibge_municipio` i
            ON e.cd_municipio_ibge = i.cd_municipio_ibge
        WHERE e.nr_turno = 1
          AND e.rn_municipio = 1
    """,
    # ── Evolução de intenção de voto por UF ────────────────────────────────
    "vw_intencao_voto_uf": f"""
        SELECT
            sg_uf,
            nm_candidato,
            sg_partido,
            ano_eleicao,
            nr_turno,
            SUM(qt_votos)                                                   AS total_votos,
            ROUND(
                SUM(qt_votos) / SUM(SUM(qt_votos)) OVER (
                    PARTITION BY sg_uf, ano_eleicao, nr_turno
                ) * 100, 1
            )                                                               AS pct_uf
        FROM `{_PROJECT}.{_GOLD}.fact_municipio_eleicao`
        GROUP BY sg_uf, nm_candidato, sg_partido, ano_eleicao, nr_turno
    """,
}


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

    return results


if __name__ == "__main__":
    import json

    results = create_semantic_views()
    print(json.dumps(results, indent=2, ensure_ascii=False))
