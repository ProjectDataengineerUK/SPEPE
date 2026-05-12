"""Cloud Run Job: GDELT Global Events → Bronze."""

import logging
import os

from dataops.bronze_writer import write_bronze
from dataops.clients.gdelt import fetch_gdelt_events

logger = logging.getLogger("spepe.jobs.gdelt_ingest")


def main():
    """Ingest GDELT global events data."""
    logger.info("GDELT ingest job: começando")

    try:
        # Baixar eventos globais de interesse (últimos 30 dias)
        gdelt_df = fetch_gdelt_events(lookback_days=30)
        logger.info(f"GDELT: {len(gdelt_df)} eventos baixados")

        # Salvar em Bronze
        write_bronze(
            df=gdelt_df,
            source="gdelt",
            year=2026,
            filename="gdelt_eventos_2026.parquet",
            use_gcs=bool(os.environ.get("GCS_BUCKET")),
        )
        logger.info("Bronze escrito com sucesso")

    except Exception as e:
        logger.error(f"Erro no job: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
