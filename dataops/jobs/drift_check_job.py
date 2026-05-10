"""Drift check job — invocado diariamente pelo Cloud Scheduler.

Carrega snapshot de referência (Gold 2022, cd_cargo=3) e dados recentes
(fact_predictions últimos 90 dias, ou mesmo Gold como proxy).
Chama detect_drift e publica em 'drift-detected' se threshold excedido.
Exit code 0 sempre — falhas são logadas, não propagadas.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("spepe.drift_check")

FEATURE_COLS = [
    "idhm",
    "renda_per_capita",
    "taxa_analfabetismo",
    "taxa_alfabetizacao",
    "populacao_total",
    "pct_votos_municipio",
    "sg_uf",
]

REFERENCE_QUERY = """
    SELECT
        f.sg_uf,
        f.pct_votos_municipio,
        i.populacao_total,
        i.taxa_alfabetizacao,
        i.idhm,
        i.renda_per_capita,
        i.taxa_analfabetismo
    FROM `{project}.spepe_gold.fact_municipio_candidato_eleicao` f
    LEFT JOIN `{project}.spepe_gold.fact_ibge_municipio` i
        ON f.cd_municipio_ibge = i.cd_municipio_ibge
    WHERE f.cd_cargo = @cd_cargo
      AND f.ano_eleicao = @ano_eleicao
      AND f.pct_votos_municipio IS NOT NULL
"""

RECENT_PREDICTIONS_QUERY = """
    SELECT
        sg_uf,
        p_mean AS pct_votos_municipio
    FROM `{project}.spepe_mlops.fact_predictions`
    WHERE prediction_date >= @cutoff_date
"""


def _load_reference(client, project: str, cd_cargo: int, ano_eleicao: int):
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("ano_eleicao", "INT64", ano_eleicao),
        ]
    )
    query = REFERENCE_QUERY.format(project=project)
    df = client.query(query, job_config=job_config).to_dataframe()
    logger.info("Referência carregada: %d linhas", len(df))
    return df


def _load_recent(client, project: str, lookback_days: int = 90):
    """Tenta carregar fact_predictions recentes; usa Gold como proxy se vazio."""
    from google.cloud import bigquery

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("cutoff_date", "STRING", cutoff),
        ]
    )
    try:
        df = client.query(
            RECENT_PREDICTIONS_QUERY.format(project=project), job_config=job_config
        ).to_dataframe()
        if len(df) >= 30:
            logger.info("Dados recentes (fact_predictions): %d linhas", len(df))
            return df
        logger.warning(
            "fact_predictions tem apenas %d linhas — usando Gold 2024 como proxy", len(df)
        )
    except Exception as exc:
        logger.warning("Não foi possível ler fact_predictions: %s — usando proxy Gold", exc)

    # Proxy: Gold mais recente disponível (cd_cargo=3, ano != 2022)
    from google.cloud import bigquery as bq

    proxy_query = """
        SELECT
            f.sg_uf,
            f.pct_votos_municipio,
            i.populacao_total,
            i.taxa_alfabetizacao,
            i.idhm,
            i.renda_per_capita,
            i.taxa_analfabetismo
        FROM `{project}.spepe_gold.fact_municipio_candidato_eleicao` f
        LEFT JOIN `{project}.spepe_gold.fact_ibge_municipio` i
            ON f.cd_municipio_ibge = i.cd_municipio_ibge
        WHERE f.cd_cargo = 3
          AND f.ano_eleicao != 2022
          AND f.pct_votos_municipio IS NOT NULL
        LIMIT 50000
    """.format(project=project)
    try:
        df = client.query(proxy_query).to_dataframe()
        logger.info("Proxy Gold (anos != 2022): %d linhas", len(df))
        return df
    except Exception as exc:
        logger.warning("Proxy Gold também falhou: %s — retornando referência como proxy", exc)
        return None


def main() -> None:
    project = os.environ.get("GCP_PROJECT_ID", "spepe-prod")
    threshold = float(os.environ.get("DRIFT_THRESHOLD", "0.10"))
    cd_cargo = int(os.environ.get("DRIFT_CD_CARGO", "3"))
    ano_eleicao = int(os.environ.get("DRIFT_ANO_ELEICAO", "2022"))

    try:
        from google.cloud import bigquery
        from mlops.monitoring.drift_monitor import detect_drift, drift_summary

        client = bigquery.Client(project=project)

        reference_df = _load_reference(client, project, cd_cargo, ano_eleicao)
        if reference_df.empty:
            logger.error("Referência vazia — abortando drift check")
            sys.exit(0)

        current_df = _load_recent(client, project)
        if current_df is None or current_df.empty:
            logger.warning("Dados recentes indisponíveis — usando referência como proxy (drift=0)")
            current_df = reference_df.copy()

        results = detect_drift(
            reference_df=reference_df,
            current_df=current_df,
            publish=True,
            project_id=project,
        )

        summary = drift_summary(results)
        logger.info(
            "Drift check concluído: features_total=%d drifted=%d max_js=%.4f triggered=%s",
            summary["total_features"],
            summary["drifted_features"],
            summary["max_score"],
            summary["drift_detected"],
        )

    except ImportError as exc:
        logger.error("Dependência ausente: %s — pule este job em ambiente local", exc)
    except Exception as exc:
        logger.error("Drift check falhou com erro inesperado: %s", exc, exc_info=True)

    # Exit 0 sempre — Cloud Run Job não deve reiniciar por falha de drift check
    sys.exit(0)


if __name__ == "__main__":
    main()
