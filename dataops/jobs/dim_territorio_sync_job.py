"""Cloud Run Job: popula spepe_gold.dim_territorio a partir de fact_ibge_municipio.

dim_territorio fica vazia por padrão porque nenhum job a preenche automaticamente.
Este job resolve o bug de 0 rows executando um MERGE a partir dos dados já presentes
em fact_ibge_municipio (que tem cd_municipio_ibge, nm_municipio, sg_uf, sg_regiao).

Execução: semanal (ou manual após ibge_sync) — ~5570 linhas, operação rápida (<30s).
"""

from __future__ import annotations

import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.dim_territorio_sync")

_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "spepe-prod")

_MERGE_SQL = """
MERGE `{project}.spepe_gold.dim_territorio` AS T
USING (
    SELECT
        cd_municipio_ibge,
        ANY_VALUE(nm_municipio) AS nm_municipio,
        sg_uf,
        CURRENT_TIMESTAMP()     AS ingested_at
    FROM `{project}.spepe_gold.fact_ibge_municipio`
    WHERE cd_municipio_ibge IS NOT NULL
      AND nm_municipio IS NOT NULL
    GROUP BY cd_municipio_ibge, sg_uf
) AS S
ON T.cd_municipio_ibge = S.cd_municipio_ibge AND T.sg_uf = S.sg_uf
WHEN MATCHED THEN UPDATE SET
    nm_municipio = S.nm_municipio,
    ingested_at  = S.ingested_at
WHEN NOT MATCHED THEN INSERT (
    cd_municipio_ibge,
    nm_municipio,
    cd_ibge,
    sg_uf,
    nm_uf,
    sg_regiao,
    nm_regiao,
    latitude,
    longitude,
    ingested_at
) VALUES (
    S.cd_municipio_ibge,
    S.nm_municipio,
    S.cd_municipio_ibge,
    S.sg_uf,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    S.ingested_at
)
"""

_COUNT_SQL = "SELECT COUNT(*) AS n FROM `{project}.spepe_gold.dim_territorio`"


def main() -> None:
    try:
        from google.cloud import bigquery
    except ImportError:
        logger.error("google-cloud-bigquery não instalado")
        sys.exit(1)

    logger.info("dim_territorio_sync_job: project=%s", _PROJECT_ID)
    t0 = time.monotonic()

    bq = bigquery.Client(project=_PROJECT_ID)

    rows_before = 0
    try:
        result = bq.query(_COUNT_SQL.format(project=_PROJECT_ID)).result()
        rows_before = next(iter(result))["n"]
        logger.info("dim_territorio antes: %d rows", rows_before)
    except Exception as exc:
        logger.warning("Não foi possível contar rows antes: %s", exc)

    try:
        merge_job = bq.query(_MERGE_SQL.format(project=_PROJECT_ID))
        merge_job.result()
        logger.info(
            "MERGE concluído | rows_affected=%s",
            getattr(merge_job, "num_dml_affected_rows", "desconhecido"),
        )
    except Exception as exc:
        logger.error("MERGE dim_territorio falhou: %s", exc)
        sys.exit(1)

    rows_after = 0
    try:
        result = bq.query(_COUNT_SQL.format(project=_PROJECT_ID)).result()
        rows_after = next(iter(result))["n"]
    except Exception as exc:
        logger.warning("Não foi possível contar rows após merge: %s", exc)

    elapsed = time.monotonic() - t0
    logger.info(
        "SUMMARY | rows_antes=%d | rows_depois=%d | inseridos/atualizados=%d | elapsed=%.1fs",
        rows_before,
        rows_after,
        rows_after - rows_before,
        elapsed,
    )


if __name__ == "__main__":
    main()
