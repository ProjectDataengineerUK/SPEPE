"""Cloud Run Job: Bronze → Silver with DQ gate."""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.silver_transform")

YEARS = [2014, 2018, 2022]
DQ_THRESHOLD = float(os.environ.get("DQ_SCORE_THRESHOLD", "95.0"))


def main(uf: str, years: list[int] | None = None, include_social: bool = True) -> None:
    from dataops.silver_transformer import (
        transform_cadunico_to_silver,
        transform_digital_to_silver,
        transform_economia_to_silver,
        transform_emendas_to_silver,
        transform_pesquisas_to_silver,
        transform_saude_to_silver,
        transform_sancoes_to_silver,
        transform_seguranca_to_silver,
        transform_social_to_silver,
        transform_to_silver,
    )

    use_bq = bool(os.environ.get("GCP_PROJECT_ID"))

    target_years = years or YEARS
    all_ok = True

    # ── TSE + IBGE (core eleitoral) ─────────────────────────────────────────
    for year in target_years:
        logger.info("Silver TSE+IBGE: %s/%d", uf, year)
        result = transform_to_silver(uf, year, use_bigquery=use_bq)

        if result.get("status") == "error":
            logger.warning("Skipped %s/%d: %s", uf, year, result.get("message"))
            continue

        dq_score = result.get("dq_score", 0.0)
        if dq_score < DQ_THRESHOLD:
            logger.error(
                "DQ FALHOU %s/%d: score=%.1f%% < %.1f%%. Bloqueando Gold build.",
                uf,
                year,
                dq_score,
                DQ_THRESHOLD,
            )
            all_ok = False
        else:
            logger.info(
                "Silver TSE OK %s/%d: %d rows, DQ=%.1f%%", uf, year, result.get("rows", 0), dq_score
            )

    # ── Pesquisas eleitorais (nacional — BR, multi-ano) ─────────────────────
    # PESQUISA_YEARS: lista separada por vírgula; default = 2018,2022,2026
    _py_env = os.environ.get("PESQUISA_YEARS", os.environ.get("PESQUISA_YEAR", "2018,2022,2026"))
    pesquisa_years = [int(y.strip()) for y in _py_env.split(",") if y.strip()]
    for pesquisa_year in pesquisa_years:
        logger.info("Silver pesquisas: ano=%d", pesquisa_year)
        r = transform_pesquisas_to_silver(pesquisa_year, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Pesquisas Silver OK ano=%d: %d rows", pesquisa_year, r.get("rows", 0))
        else:
            logger.warning(
                "Pesquisas Silver ano=%d: %s (Bronze pode estar vazio)",
                pesquisa_year,
                r.get("message"),
            )

    # ── Segurança pública (por UF × ano) ────────────────────────────────────
    for year in target_years:
        logger.info("Silver segurança: %s/%d", uf, year)
        r = transform_seguranca_to_silver(uf, year, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Segurança Silver OK %s/%d: %d rows", uf, year, r.get("rows", 0))
        else:
            logger.warning("Segurança Silver %s/%d: %s", uf, year, r.get("message"))

    # ── Saúde / DataSUS (por UF × ano) ─────────────────────────────────────
    for year in target_years:
        logger.info("Silver saúde: %s/%d", uf, year)
        r = transform_saude_to_silver(uf, year, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Saúde Silver OK %s/%d: %d rows", uf, year, r.get("rows", 0))
        else:
            logger.warning("Saúde Silver %s/%d: %s", uf, year, r.get("message"))

    # ── Economia (DIEESE + CETIC — por UF × ano) ───────────────────────────
    for year in target_years:
        logger.info("Silver economia: %s/%d", uf, year)
        r = transform_economia_to_silver(uf, year, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Economia Silver OK %s/%d: %d rows", uf, year, r.get("rows", 0))
        else:
            logger.warning("Economia Silver %s/%d: %s", uf, year, r.get("message"))

    # ── CadÚnico + Bolsa Família (nacional — BR, multi-ano) ─────────────────
    _cy_env = os.environ.get("CADUNICO_YEARS", "2018,2022,2024,2025")
    cadunico_years = [int(y.strip()) for y in _cy_env.split(",") if y.strip()]
    for cadunico_year in cadunico_years:
        logger.info("Silver CadÚnico: ano=%d", cadunico_year)
        r = transform_cadunico_to_silver(cadunico_year, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("CadÚnico Silver OK ano=%d: %d rows", cadunico_year, r.get("rows", 0))
        else:
            logger.warning("CadÚnico Silver ano=%d: %s", cadunico_year, r.get("message"))

    # ── Social (Twitter/Facebook/YouTube — BR) ──────────────────────────────
    if include_social:
        social_year = int(os.environ.get("SOCIAL_YEAR", "2026"))
        logger.info("Silver social: ano=%d", social_year)
        r = transform_social_to_silver(social_year, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Social Silver OK: %d rows", r.get("rows", 0))
        else:
            logger.warning(
                "Social Silver: %s (pode ser vazio se social_ingest não rodou)", r.get("message")
            )

    # ── Digital (Meta Ads + Google Trends — BR, multi-ano) ──────────────────
    _dy_env = os.environ.get("DIGITAL_YEARS", "2018,2022,2026")
    digital_years = [int(y.strip()) for y in _dy_env.split(",") if y.strip()]
    for digital_year in digital_years:
        logger.info("Silver digital: ano=%d", digital_year)
        r = transform_digital_to_silver(digital_year, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Digital Silver OK ano=%d: %d rows", digital_year, r.get("rows", 0))
        else:
            logger.warning(
                "Digital Silver ano=%d: %s (pode ser vazio se digital_ingest não rodou)",
                digital_year, r.get("message"),
            )

    # ── Emendas Parlamentares (nacional — BR, multi-ano) ────────────────────
    _ey_env = os.environ.get("EMENDAS_YEARS", "2018,2022,2025")
    emendas_years = [int(y.strip()) for y in _ey_env.split(",") if y.strip()]
    for emendas_year in emendas_years:
        logger.info("Silver emendas: ano=%d", emendas_year)
        r = transform_emendas_to_silver(emendas_year, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Emendas Silver OK ano=%d: %d rows", emendas_year, r.get("rows", 0))
        else:
            logger.warning(
                "Emendas Silver ano=%d: %s (Bronze pode estar vazio — 403 API)",
                emendas_year, r.get("message"),
            )

    # ── Sanções CEIS + CNEP (snapshot único) ────────────────────────────────
    logger.info("Silver sanções CEIS+CNEP")
    r = transform_sancoes_to_silver(use_bigquery=use_bq)
    if r.get("status") == "ok":
        logger.info("Sanções Silver OK: %d rows", r.get("rows", 0))
    else:
        logger.warning("Sanções Silver: %s (Bronze pode estar vazio)", r.get("message"))

    if not all_ok:
        sys.exit(1)


_ALL_UFS = [
    "AC",
    "AL",
    "AP",
    "AM",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MT",
    "MS",
    "MG",
    "PA",
    "PB",
    "PR",
    "PE",
    "PI",
    "RJ",
    "RN",
    "RS",
    "RO",
    "RR",
    "SC",
    "SP",
    "SE",
    "TO",
]

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    parser.add_argument("--years", nargs="+", type=int, default=YEARS)
    args = parser.parse_args()
    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    for uf in ufs:
        main(uf, args.years)
