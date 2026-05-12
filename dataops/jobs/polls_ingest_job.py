"""Cloud Run Job: TSE Pesquisas Eleitorais → Bronze (Polls source)."""

import logging
import os

from dataops.bronze_writer import write_bronze
from dataops.clients.polls_client import fetch_tse_pesquisas_eleitorais

logger = logging.getLogger("spepe.jobs.polls_ingest")


def main():
    """Ingest pesquisas eleitorais 2026 from TSE."""
    logger.info("Pesquisas eleitorais ingest job (polls): começando")

    try:
        # Baixar pesquisas do TSE (pesquisa_eleitoral_2026.zip)
        pesquisas_df = fetch_tse_pesquisas_eleitorais(year=2026)
        logger.info(f"TSE pesquisas: {len(pesquisas_df)} registros baixados")

        # Salvar em Bronze
        write_bronze(
            df=pesquisas_df,
            source="polls",
            year=2026,
            filename="pesquisas_eleitorais_2026.parquet",
            use_gcs=bool(os.environ.get("GCS_BUCKET")),
        )
        logger.info("Bronze escrito com sucesso")

    except Exception as e:
        logger.error(f"Erro no job: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
