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
    fetch_atlas_politico,
    fetch_atlas_polls,
    fetch_pesqele_csv,
    reconcile_atlas_with_pesqele,
    scrape_poder360,
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

    # ── 2b. Cross-validate with external agencies ──────────────────────────
    if not df_pesqele.empty:
        from dataops.clients.agencies_client import cross_validate_with_agencies

        _CARGO_STR_MAP = {1: "presidente", 3: "governador", 13: "senador"}
        cargo_col = next(
            (c for c in ("cd_cargo", "nr_cargo", "ds_cargo") if c in df_pesqele.columns),
            None,
        )
        for cargo_int, cargo_str in _CARGO_STR_MAP.items():
            if cargo_col is None:
                df_pesqele = cross_validate_with_agencies(df_pesqele, year, cargo_str)
                break
            mask = df_pesqele[cargo_col].astype(str).str.upper() == str(cargo_int)
            if not mask.any() and cargo_col == "ds_cargo":
                mask = df_pesqele[cargo_col].str.upper().str.contains(cargo_str.upper(), na=False)
            if mask.any():
                df_pesqele.loc[mask] = cross_validate_with_agencies(
                    df_pesqele[mask].copy(), year, cargo_str
                )

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

    # ── 5. Intenção de voto — Atlas Político + Poder360 + Agências ───────
    from dataops.clients.agencies_client import fetch_all_agencies

    _CARGO_STRS = ["presidente", "governador", "senador"]
    frames_intencao: list[pd.DataFrame] = []
    for cargo_str in _CARGO_STRS:
        try:
            df_ap = fetch_atlas_politico(year, cargo=cargo_str)
            if not df_ap.empty:
                df_ap["cargo"] = cargo_str
                frames_intencao.append(df_ap)
        except Exception as exc:
            logger.warning("fetch_atlas_politico cargo=%s: %s", cargo_str, exc)
        try:
            df_p3 = scrape_poder360(year, cargo=cargo_str)
            if not df_p3.empty:
                df_p3["cargo"] = cargo_str
                frames_intencao.append(df_p3)
        except Exception as exc:
            logger.warning("scrape_poder360 cargo=%s: %s", cargo_str, exc)
        # Agências (Datafolha, Quaest, etc.) como fonte primária quando Atlas/Poder360 falham
        try:
            df_ag = fetch_all_agencies(year, cargo=cargo_str)
            if not df_ag.empty and "intencao_pct" in df_ag.columns:
                df_ag = df_ag[df_ag["intencao_pct"].notna()]
                if not df_ag.empty:
                    frames_intencao.append(df_ag)
                    logger.info(
                        "Agências %s: %d rows de intenção adicionados", cargo_str, len(df_ag)
                    )
        except Exception as exc:
            logger.warning("fetch_all_agencies cargo=%s: %s", cargo_str, exc)

    # ── 5b. Fallback: expandir pdf_data do enrich_with_pdfs ───────────────────
    if (
        not frames_intencao
        and enrich_pdf
        and not df_pesqele.empty
        and "pdf_data" in df_pesqele.columns
    ):
        pdf_rows: list[dict] = []
        for _, row in df_pesqele.iterrows():
            pdf_data = row.get("pdf_data")
            if not isinstance(pdf_data, list) or not pdf_data:
                continue
            for entry in pdf_data:
                if not isinstance(entry, dict):
                    continue
                intencao = entry.get("intencao_pct")
                candidato = entry.get("candidato")
                if intencao is None or candidato is None:
                    continue
                pdf_rows.append(
                    {
                        "candidato": candidato,
                        "intencao_pct": float(intencao),
                        "data_pesquisa_fim": row.get("data_pesquisa_fim"),
                        "data_pesquisa_inicio": row.get("data_pesquisa_inicio"),
                        "instituto": row.get("instituto"),
                        "uf": row.get("uf", "BR"),
                        "cd_cargo": row.get("cd_cargo"),
                        "n_entrevistados": row.get("n_entrevistados"),
                        "poll_id": row.get("poll_id"),
                        "ano": year,
                        "fonte": "pdf_tse",
                    }
                )
        if pdf_rows:
            frames_intencao.append(pd.DataFrame(pdf_rows))
            logger.info("Fallback PDF: %d registros de intenção extraídos dos PDFs", len(pdf_rows))
        else:
            logger.warning(
                "Fallback PDF: nenhum dado de intenção extraído (pdf_data vazio ou sem candidatos)"
            )

    if frames_intencao:
        df_intencao = pd.concat(frames_intencao, ignore_index=True)
        path_intencao = write_bronze(
            df=df_intencao,
            source="pesquisas",
            year=year,
            uf=bronze_uf,
            filename=f"pesquisas_intencao_{year}.parquet",
            use_gcs=use_gcs,
        )
        logger.info(
            "Bronze pesquisas_intencao: %s (%d rows)",
            path_intencao,
            len(df_intencao),
        )
    else:
        logger.warning("Nenhum dado de intenção de voto coletado (Atlas+Poder360+PDF)")

    # ── 6. dim_instituto seed ──────────────────────────────────────────────
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

    # ── 7. Summary ─────────────────────────────────────────────────────────
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
