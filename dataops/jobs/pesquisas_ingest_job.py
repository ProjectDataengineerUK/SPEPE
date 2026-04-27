"""Cloud Run Job: TSE PesqEle + Atlas Político → Bronze + dim_instituto seed."""

from __future__ import annotations

import logging
import os
import sys

import pandas as pd

from dataops.bronze_writer import write_bronze
from dataops.clients.polls_client import (
    build_dim_instituto,
    enrich_with_pdfs,
    fetch_atlas_polls,
    fetch_pesqele_csv,
    reconcile_atlas_with_pesqele,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.pesquisas_ingest")

_CARGOS_DEFAULT = [1, 3]  # Presidente=1, Governador=3


def main(year: int, cargos: list[int], enrich_pdf: bool, uf: str | None) -> None:
    use_gcs = bool(os.environ.get("GCS_BUCKET"))
    all_ok = True

    # ── 1. TSE PesqEle ─────────────────────────────────────────────────────
    frames_tse: list[pd.DataFrame] = []
    for cargo in cargos:
        logger.info("TSE PesqEle: ano=%d cargo=%d", year, cargo)
        df_cargo = fetch_pesqele_csv(year, cargo=cargo)
        if df_cargo.empty:
            logger.warning("TSE PesqEle: sem dados para cargo=%d ano=%d", cargo, year)
            continue
        frames_tse.append(df_cargo)

    df_pesqele = pd.concat(frames_tse, ignore_index=True) if frames_tse else pd.DataFrame()

    if uf and not df_pesqele.empty and "uf" in df_pesqele.columns:
        df_pesqele = df_pesqele[
            df_pesqele["uf"].str.upper().isin([uf.upper(), "BR", ""]) | df_pesqele["uf"].isna()
        ]

    # ── 2. PDF enrichment (optional) ───────────────────────────────────────
    if enrich_pdf and not df_pesqele.empty:
        logger.info("Enriquecendo com PDFs (max 200)")
        df_pesqele = enrich_with_pdfs(df_pesqele)

    # ── 3. Atlas Político secondary ────────────────────────────────────────
    logger.info("Atlas Político: ano=%d", year)
    df_atlas = fetch_atlas_polls(year)

    if not df_atlas.empty and not df_pesqele.empty:
        df_atlas = reconcile_atlas_with_pesqele(df_atlas, df_pesqele)

    # ── 4. Write Bronze ────────────────────────────────────────────────────
    bronze_uf = uf.upper() if uf else "BR"

    if not df_pesqele.empty:
        path_tse = write_bronze(
            df=df_pesqele,
            source="pesquisas",
            year=year,
            uf=bronze_uf,
            filename=f"pesquisas_tse_{year}.parquet",
            use_gcs=use_gcs,
        )
        logger.info(
            "Bronze TSE PesqEle: %s (%d rows, score médio=%.2f)",
            path_tse,
            len(df_pesqele),
            df_pesqele["record_confidence_score"].mean()
            if "record_confidence_score" in df_pesqele.columns
            else 0,
        )
    else:
        logger.warning("TSE PesqEle: nenhum dado para escrever no Bronze")
        all_ok = False

    if not df_atlas.empty:
        path_atlas = write_bronze(
            df=df_atlas,
            source="pesquisas",
            year=year,
            uf=bronze_uf,
            filename=f"pesquisas_atlas_{year}.parquet",
            use_gcs=use_gcs,
        )
        logger.info(
            "Bronze Atlas: %s (%d rows, score médio=%.2f)",
            path_atlas,
            len(df_atlas),
            df_atlas["record_confidence_score"].mean()
            if "record_confidence_score" in df_atlas.columns
            else 0,
        )

    # ── 5. dim_instituto seed ──────────────────────────────────────────────
    df_dim = build_dim_instituto()
    path_dim = write_bronze(
        df=df_dim,
        source="pesquisas",
        year=year,
        uf="BR",
        filename="dim_instituto.parquet",
        use_gcs=use_gcs,
    )
    logger.info("dim_instituto seed: %s (%d institutos)", path_dim, len(df_dim))

    # ── 6. Summary ─────────────────────────────────────────────────────────
    total_rows = len(df_pesqele) + len(df_atlas)
    logger.info(
        "Pesquisas ingest concluído: %d pesquisas total (TSE=%d Atlas=%d)",
        total_rows,
        len(df_pesqele),
        len(df_atlas),
    )

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Polls ingest job — TSE PesqEle + Atlas")
    parser.add_argument(
        "--year",
        type=int,
        default=int(os.environ.get("PESQUISAS_YEAR", "2026")),
        help="Ano eleitoral",
    )
    parser.add_argument(
        "--cargos",
        nargs="+",
        type=int,
        default=list(map(int, os.environ.get("PESQUISAS_CARGOS", "1 3").split())),
        help="Códigos de cargo TSE (1=Presidente, 3=Governador)",
    )
    parser.add_argument(
        "--enrich-pdf",
        action="store_true",
        default=os.environ.get("PESQUISAS_ENRICH_PDF", "false").lower() == "true",
        help="Baixar e parsear PDFs do TSE PesqEle",
    )
    parser.add_argument(
        "--uf",
        default=os.environ.get("DEFAULT_UF"),
        help="Filtrar por UF (vazio = todas)",
    )
    args = parser.parse_args()
    main(args.year, args.cargos, args.enrich_pdf, args.uf)
