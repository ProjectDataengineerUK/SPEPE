"""Cloud Run Job: Bronze → Silver with DQ gate."""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.silver_transform")

YEARS = [2014, 2018, 2022]
DQ_THRESHOLD = float(os.environ.get("DQ_SCORE_THRESHOLD", "95.0"))


def main(
    uf: str,
    years: list[int] | None = None,
    include_social: bool = True,
    only: set[str] | None = None,
) -> None:
    """Run Silver transforms.

    only: if provided, run only the named transforms (e.g. {"locais", "ibge"}).
    Valid names: tse, ibge, presidente, pesquisas, seguranca, saude, economia,
                 cadunico, social, digital, emendas, sancoes, endividamento,
                 camara_senado, perfil, locais, candidaturas.
    """
    from dataops.silver_transformer import (
        transform_cadunico_to_silver,
        transform_camara_senado_to_silver,
        transform_candidaturas_to_silver,
        transform_digital_to_silver,
        transform_economia_to_silver,
        transform_emendas_to_silver,
        transform_endividamento_to_silver,
        transform_ibge_to_silver,
        transform_locais_votacao_to_silver,
        transform_pesquisas_to_silver,
        transform_presidente_to_silver,
        transform_saude_to_silver,
        transform_sancoes_to_silver,
        transform_seguranca_to_silver,
        transform_social_to_silver,
        transform_to_silver,
        transform_tse_perfil_to_silver,
    )

    use_bq = bool(os.environ.get("GCP_PROJECT_ID"))
    target_years = years or YEARS
    all_ok = True

    def _run(name: str) -> bool:
        return only is None or name in only

    # ── TSE + IBGE (core eleitoral) ─────────────────────────────────────────
    if _run("tse"):
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

    # ── IBGE indicadores municipais (SIDRA + Atlas IPEADATA) ───────────────
    if _run("ibge"):
        for year in target_years:
            logger.info("Silver IBGE indicadores: %s/%d", uf, year)
            r = transform_ibge_to_silver(uf, year, use_bigquery=use_bq)
            if r.get("status") == "ok":
                logger.info("IBGE Silver OK %s/%d: %d rows", uf, year, r.get("rows", 0))
            else:
                logger.warning("IBGE Silver %s/%d: %s", uf, year, r.get("message"))

    # ── Presidente TSE (nacional — BR, multi-ano, expandido para municípios) ──
    if _run("presidente"):
        _pres_years_env = os.environ.get("PRESIDENTE_YEARS", "2018,2022")
        pres_years = [int(y.strip()) for y in _pres_years_env.split(",") if y.strip()]
        for pres_year in pres_years:
            logger.info("Silver presidente: ano=%d", pres_year)
            r = transform_presidente_to_silver(pres_year, use_bigquery=use_bq)
            if r.get("status") == "ok":
                logger.info("Presidente Silver OK ano=%d: %d rows", pres_year, r.get("rows", 0))
            else:
                logger.warning(
                    "Presidente Silver ano=%d: %s (Bronze pode estar vazio)",
                    pres_year,
                    r.get("message"),
                )

    # ── Pesquisas eleitorais (nacional — BR, multi-ano) ─────────────────────
    if _run("pesquisas"):
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
    if _run("seguranca"):
        for year in target_years:
            logger.info("Silver segurança: %s/%d", uf, year)
            r = transform_seguranca_to_silver(uf, year, use_bigquery=use_bq)
            if r.get("status") == "ok":
                logger.info("Segurança Silver OK %s/%d: %d rows", uf, year, r.get("rows", 0))
            else:
                logger.warning("Segurança Silver %s/%d: %s", uf, year, r.get("message"))

    # ── Saúde / DataSUS (por UF × ano) ─────────────────────────────────────
    if _run("saude"):
        for year in target_years:
            logger.info("Silver saúde: %s/%d", uf, year)
            r = transform_saude_to_silver(uf, year, use_bigquery=use_bq)
            if r.get("status") == "ok":
                logger.info("Saúde Silver OK %s/%d: %d rows", uf, year, r.get("rows", 0))
            else:
                logger.warning("Saúde Silver %s/%d: %s", uf, year, r.get("message"))

    # ── Economia (DIEESE + CETIC — por UF × ano) ───────────────────────────
    if _run("economia"):
        for year in target_years:
            logger.info("Silver economia: %s/%d", uf, year)
            r = transform_economia_to_silver(uf, year, use_bigquery=use_bq)
            if r.get("status") == "ok":
                logger.info("Economia Silver OK %s/%d: %d rows", uf, year, r.get("rows", 0))
            else:
                logger.warning("Economia Silver %s/%d: %s", uf, year, r.get("message"))

    # ── CadÚnico + Bolsa Família (nacional — BR, multi-ano) ─────────────────
    if _run("cadunico"):
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
    if _run("social") and include_social:
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
    if _run("digital"):
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
                    digital_year,
                    r.get("message"),
                )

    # ── Emendas Parlamentares (nacional — BR, multi-ano) ────────────────────
    if _run("emendas"):
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
                    emendas_year,
                    r.get("message"),
                )

    # ── Sanções CEIS + CNEP (snapshot único) ────────────────────────────────
    if _run("sancoes"):
        logger.info("Silver sanções CEIS+CNEP")
        r = transform_sancoes_to_silver(use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Sanções Silver OK: %d rows", r.get("rows", 0))
        else:
            logger.warning("Sanções Silver: %s (Bronze pode estar vazio)", r.get("message"))

    # ── Endividamento BACEN (nacional, série mensal) ────────────────────────
    if _run("endividamento"):
        _ey_start = int(os.environ.get("ENDIVIDAMENTO_YEAR_START", "2025"))
        _ey_end = int(os.environ.get("ENDIVIDAMENTO_YEAR_END", "2026"))
        logger.info("Silver endividamento: %d-%d", _ey_start, _ey_end)
        r = transform_endividamento_to_silver(_ey_start, _ey_end, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Endividamento Silver OK: %d rows", r.get("rows", 0))
        else:
            logger.warning("Endividamento Silver: %s (Bronze pode estar vazio)", r.get("message"))

    # ── Câmara + Senado (votações, parlamentares) ────────────────────────────
    if _run("camara_senado"):
        _cam_years_env = os.environ.get("CAMARA_SENADO_YEARS", "2023,2024,2025")
        cam_years = [int(y.strip()) for y in _cam_years_env.split(",") if y.strip()]
        _legislature = int(os.environ.get("LEGISLATURE", "57"))
        logger.info("Silver câmara/senado: anos=%s leg=%d", cam_years, _legislature)
        r = transform_camara_senado_to_silver(cam_years, _legislature, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Câmara/Senado Silver OK: %s", r.get("tables", {}).keys())
        else:
            logger.warning("Câmara/Senado Silver: %s (Bronze pode estar vazio)", r.get("message"))

    # ── TSE Perfil Eleitorado (por UF × ano) ─────────────────────────────────
    if _run("perfil"):
        _perfil_years_env = os.environ.get("PERFIL_YEARS", "2022")
        perfil_years = [int(y.strip()) for y in _perfil_years_env.split(",") if y.strip()]
        for perfil_year in perfil_years:
            logger.info("Silver TSE perfil: %s/%d", uf, perfil_year)
            r = transform_tse_perfil_to_silver(uf, perfil_year, use_bigquery=use_bq)
            if r.get("status") == "ok":
                logger.info("TSE Perfil Silver OK %s/%d: %d rows", uf, perfil_year, r.get("rows", 0))
            else:
                logger.warning(
                    "TSE Perfil Silver %s/%d: %s (CDN TSE pode estar em manutenção)",
                    uf,
                    perfil_year,
                    r.get("message"),
                )

    # ── Locais de Votação TSE (cadastro ATUAL, por UF) ───────────────────────
    if _run("locais"):
        import datetime as _dt

        _locais_year = int(os.environ.get("LOCAIS_YEAR", str(_dt.date.today().year)))
        logger.info("Silver locais votação: %s/%d", uf, _locais_year)
        r = transform_locais_votacao_to_silver(uf, _locais_year, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("Locais Silver OK %s/%d: %d rows", uf, _locais_year, r.get("rows", 0))
        else:
            logger.warning(
                "Locais Silver %s/%d: %s (CDN TSE pode estar em manutenção)",
                uf,
                _locais_year,
                r.get("message"),
            )

    # ── Candidaturas TSE (dim_candidato — partido lookup para Gold JOIN) ────
    if _run("candidaturas"):
        _cand_years_env = os.environ.get("CANDIDATURAS_YEARS", "2018,2022")
        cand_years = [int(y.strip()) for y in _cand_years_env.split(",") if y.strip()]
        logger.info("Silver candidaturas → dim_candidato: anos=%s", cand_years)
        r = transform_candidaturas_to_silver(cand_years, use_bigquery=use_bq)
        if r.get("status") == "ok":
            logger.info("dim_candidato Silver OK: %d rows", r.get("rows", 0))
        else:
            logger.warning(
                "dim_candidato Silver: %s (Bronze tse_candidaturas pode estar vazio)",
                r.get("message"),
            )

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
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        metavar="TRANSFORM",
        help=(
            "Run only these transforms. Valid: tse ibge presidente pesquisas seguranca saude "
            "economia cadunico social digital emendas sancoes endividamento camara_senado "
            "perfil locais candidaturas"
        ),
    )
    args = parser.parse_args()
    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    only_set = set(args.only) if args.only else None
    for uf in ufs:
        main(uf, args.years, only=only_set)
