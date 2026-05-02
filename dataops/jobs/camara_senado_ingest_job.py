"""Cloud Run Job: Portal Câmara + Senado (votações, gastos CEAP, parlamentares) → Bronze."""

from __future__ import annotations

import logging
import os

from dataops.bronze_writer import write_bronze
from dataops.clients.camara_senado_client import (
    build_parlamentares_dataframe,
    fetch_gastos_gabinete_camara,
    fetch_votacoes_camara,
    fetch_votacoes_senado,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.camara_senado_ingest")

_DEFAULT_YEARS = [2023, 2024, 2025]
_LEGISLATURE = 57  # 57ª Legislatura: 2023-2027


def main(years: list[int], legislature: int) -> None:
    use_gcs = bool(os.environ.get("GCS_BUCKET"))

    # ── Parlamentares (lista base) ─────────────────────────────────────────
    df_parl = build_parlamentares_dataframe(legislature=legislature)
    if not df_parl.empty:
        write_bronze(
            df=df_parl,
            source="camara_senado",
            year=years[-1],
            uf="BR",
            filename=f"parlamentares_leg{legislature}.parquet",
            use_gcs=use_gcs,
        )
        logger.info("Parlamentares Bronze: %d linhas", len(df_parl))

    for year in years:
        # ── Votações Câmara ────────────────────────────────────────────────
        df_vot_cam = fetch_votacoes_camara(year)
        if not df_vot_cam.empty:
            write_bronze(
                df=df_vot_cam,
                source="camara_senado",
                year=year,
                uf="BR",
                filename=f"votacoes_camara_{year}.parquet",
                use_gcs=use_gcs,
            )
            logger.info("Câmara votações Bronze: %d em %d", len(df_vot_cam), year)

        # ── Votações Senado ────────────────────────────────────────────────
        df_vot_sen = fetch_votacoes_senado(year)
        if not df_vot_sen.empty:
            write_bronze(
                df=df_vot_sen,
                source="camara_senado",
                year=year,
                uf="BR",
                filename=f"votacoes_senado_{year}.parquet",
                use_gcs=use_gcs,
            )
            logger.info("Senado votações Bronze: %d em %d", len(df_vot_sen), year)

        # ── Gastos CEAP — amostra (sem id_deputado = todos, lento) ────────
        # Limitado a 20 deputados por chamada no cliente para não estourar quota
        df_ceap = fetch_gastos_gabinete_camara(year)
        if not df_ceap.empty:
            write_bronze(
                df=df_ceap,
                source="camara_senado",
                year=year,
                uf="BR",
                filename=f"ceap_camara_{year}.parquet",
                use_gcs=use_gcs,
            )
            logger.info("CEAP Bronze: %d despesas em %d", len(df_ceap), year)

    logger.info("Câmara/Senado ingest concluído para %s", years)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", type=int, default=_DEFAULT_YEARS)
    parser.add_argument("--legislature", type=int, default=_LEGISLATURE)
    args = parser.parse_args()

    main(args.years, args.legislature)
