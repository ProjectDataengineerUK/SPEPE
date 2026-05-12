"""Cloud Run Job: BlueSky Posts → Bronze."""

import logging
import os

from dataops.bronze_writer import write_bronze
from dataops.clients.bluesky import fetch_bluesky_posts

logger = logging.getLogger("spepe.jobs.bluesky_ingest")


def main():
    """Ingest BlueSky posts from Decentralized Social network."""
    logger.info("BlueSky ingest job: começando")

    try:
        # Baixar posts recentes da BlueSky
        bluesky_df = fetch_bluesky_posts(lookback_days=30)
        logger.info(f"BlueSky: {len(bluesky_df)} posts baixados")

        # Salvar em Bronze
        write_bronze(
            df=bluesky_df,
            source="bluesky",
            year=2026,
            filename="bluesky_posts_2026.parquet",
            use_gcs=bool(os.environ.get("GCS_BUCKET")),
        )
        logger.info("Bronze escrito com sucesso")

    except Exception as e:
        logger.error(f"Erro no job: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
