"""Cloud Run Job: TSE Pesquisas Eleitorais → Bronze (pesquisas source)."""

import logging
import os

from dataops.bronze_writer import write_bronze
from dataops.clients.polls_client import fetch_tse_pesquisas_eleitorais

logger = logging.getLogger("spepe.jobs.polls_ingest")


def main():
    """Ingest pesquisas eleitorais 2026 from TSE."""
    year = int(os.environ.get("POLLS_YEAR", "2026"))
    logger.info("Pesquisas eleitorais ingest job: começando ano=%d", year)

    try:
        pesquisas_df = fetch_tse_pesquisas_eleitorais(year=year)
        logger.info("TSE pesquisas: %d registros baixados", len(pesquisas_df))

        # Write under source="pesquisas" so transform_pesquisas_to_silver picks it up
        write_bronze(
            df=pesquisas_df,
            source="pesquisas",
            year=year,
            uf="BR",
            filename=f"pesquisas_tse_{year}.parquet",
            use_gcs=bool(os.environ.get("GCS_BUCKET")),
        )
        logger.info("Bronze escrito com sucesso")

    except Exception as e:
        logger.error("Erro no job: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
