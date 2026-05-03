"""TSE Candidaturas client — bens declarados, financiamento, coligações por candidato.

Fontes TSE CDN:
  - consulta_cand_{year}_{uf}.zip       → dados cadastrais do candidato
  - bem_candidato_{year}_{uf}.zip       → bens declarados (valor em R$)
  - receitas_candidatos_{year}_{uf}.zip → fontes de financiamento
"""

from __future__ import annotations

import io
import logging
import zipfile

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.tse_candidaturas")

_CDN_BASE = "https://cdn.tse.jus.br/estatistica/sead/odsele"
# National files (no UF suffix) — filter by sg_uf after download
_ENDPOINTS = {
    "candidatos": "{base}/consulta_cand/consulta_cand_{year}.zip",
    "bens": "{base}/bem_candidato/bem_candidato_{year}.zip",
    "receitas": "{base}/prestacao_contas/receitas_candidatos_{year}.zip",
}

_COL_MAP_CAND = {
    "SQ_CANDIDATO": "sq_candidato",
    "NM_CANDIDATO": "nm_candidato",
    "NM_URNA_CANDIDATO": "nm_urna",
    "NR_CANDIDATO": "nr_candidato",
    "SG_PARTIDO": "sg_partido",
    "NM_PARTIDO": "nm_partido",
    "CD_CARGO": "cd_cargo",
    "DS_CARGO": "ds_cargo",
    "SG_UF": "sg_uf",
    "CD_MUNICIPIO": "cd_municipio",
    "NM_MUNICIPIO": "nm_municipio",
    "DS_GENERO": "ds_genero",
    "DS_GRAU_INSTRUCAO": "ds_grau_instrucao",
    "DS_OCUPACAO": "ds_ocupacao",
    "DS_NACIONALIDADE": "ds_nacionalidade",
    "DS_COR_RACA": "ds_cor_raca",
    "NR_CPF_CANDIDATO": "nr_cpf",
    "NR_TITULO_ELEITORAL_CANDIDATO": "nr_titulo_eleitoral",
    "DS_SIT_TOT_TURNO": "ds_situacao",
    "CD_SIT_TOT_TURNO": "cd_situacao",
}

_COL_MAP_BENS = {
    "SQ_CANDIDATO": "sq_candidato",
    "DS_TIPO_BEM_CANDIDATO": "ds_tipo_bem",
    "DS_BEM_CANDIDATO": "ds_bem",
    "VR_BEM_CANDIDATO": "vr_bem",
    "SG_UF": "sg_uf",
}

_COL_MAP_RECEITAS = {
    "SQ_CANDIDATO": "sq_candidato",
    "NM_CANDIDATO": "nm_candidato",
    "SG_PARTIDO": "sg_partido",
    "CD_CARGO": "cd_cargo",
    "SG_UF": "sg_uf",
    "DS_ORIGEM_RECEITA": "ds_origem_receita",
    "DS_ESPECIE_RECEITA": "ds_especie_receita",
    "VR_RECEITA": "vr_receita",
    "NM_DOADOR": "nm_doador",
    "NR_CPF_CNPJ_DOADOR": "nr_cpf_cnpj_doador",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _download_zip(url: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        return resp.content
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def fetch_candidaturas_raw_zips(year: int) -> tuple[bytes | None, bytes | None, bytes | None]:
    """Download all 3 national candidaturas ZIPs once.

    Returns (raw_cand, raw_bens, raw_rec) — any element is None on 404.
    Use with build_candidaturas_from_raw to avoid re-downloading per UF.
    """
    raw_cand = _download_zip(_ENDPOINTS["candidatos"].format(base=_CDN_BASE, year=year))
    raw_bens = _download_zip(_ENDPOINTS["bens"].format(base=_CDN_BASE, year=year))
    raw_rec = _download_zip(_ENDPOINTS["receitas"].format(base=_CDN_BASE, year=year))
    return raw_cand, raw_bens, raw_rec


def build_candidaturas_from_raw(
    raw_cand: bytes | None,
    raw_bens: bytes | None,
    raw_rec: bytes | None,
    uf: str,
    year: int,
) -> pd.DataFrame:
    """Build enriched candidaturas DataFrame from pre-downloaded ZIP bytes."""
    if raw_cand is None:
        logger.warning("TSE candidatos não disponível: UF=%s ano=%d", uf, year)
        return pd.DataFrame()

    df_cand = _read_zip_csvs(raw_cand, _COL_MAP_CAND)
    if "sg_uf" in df_cand.columns:
        df_cand = df_cand[df_cand["sg_uf"].str.upper() == uf.upper()].copy()
    df_cand["ano"] = year

    if df_cand.empty:
        return pd.DataFrame()

    if raw_bens is not None:
        df_bens = _read_zip_csvs(raw_bens, _COL_MAP_BENS)
        if "sg_uf" in df_bens.columns:
            df_bens = df_bens[df_bens["sg_uf"].str.upper() == uf.upper()]
        if "vr_bem" in df_bens.columns:
            df_bens["vr_bem"] = pd.to_numeric(
                df_bens["vr_bem"].str.replace(",", "."), errors="coerce"
            )
            df_bens = df_bens.groupby("sq_candidato", as_index=False).agg(
                vr_bem_total=("vr_bem", "sum"),
                qt_bens=("vr_bem", "count"),
            )
        if not df_bens.empty and "sq_candidato" in df_bens.columns:
            df_cand = df_cand.merge(df_bens, on="sq_candidato", how="left", suffixes=("", "_bens"))

    if raw_rec is not None:
        df_rec = _read_zip_csvs(raw_rec, _COL_MAP_RECEITAS)
        if "sg_uf" in df_rec.columns:
            df_rec = df_rec[df_rec["sg_uf"].str.upper() == uf.upper()]
        if "vr_receita" in df_rec.columns:
            df_rec["vr_receita"] = pd.to_numeric(
                df_rec["vr_receita"].str.replace(",", "."), errors="coerce"
            )
        if not df_rec.empty and "sq_candidato" in df_rec.columns:
            qt_doadores_agg = (
                ("nr_cpf_cnpj_doador", "nunique")
                if "nr_cpf_cnpj_doador" in df_rec.columns
                else ("vr_receita", "count")
            )
            rec_agg = df_rec.groupby("sq_candidato", as_index=False).agg(
                vr_receita_total=("vr_receita", "sum"),
                qt_doadores=qt_doadores_agg,
            )
            df_cand = df_cand.merge(rec_agg, on="sq_candidato", how="left")

    for col in ("nr_cpf", "nr_titulo_eleitoral", "nr_cpf_cnpj_doador"):
        if col in df_cand.columns:
            df_cand.drop(columns=[col], inplace=True)

    df_cand["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("Candidaturas from raw: %d candidatos UF=%s ano=%d", len(df_cand), uf, year)
    return df_cand


def _read_zip_csvs(raw: bytes, col_map: dict[str, str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with zf.open(name) as f:
                content = f.read()
                for enc in ("utf-8-sig", "latin-1", "cp1252"):
                    try:
                        df = pd.read_csv(
                            io.BytesIO(content),
                            sep=";",
                            encoding=enc,
                            dtype=str,
                            on_bad_lines="warn",
                        )
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    continue
            df.columns = [col_map.get(c.strip(), c.strip().lower()) for c in df.columns]
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_candidatos(uf: str, year: int) -> pd.DataFrame:
    """Baixa dados cadastrais de candidatos (consulta_cand)."""
    url = _ENDPOINTS["candidatos"].format(base=_CDN_BASE, year=year)
    raw = _download_zip(url)
    if raw is None:
        logger.warning("TSE candidatos não disponível: UF=%s ano=%d", uf, year)
        return pd.DataFrame()
    df = _read_zip_csvs(raw, _COL_MAP_CAND)
    if "sg_uf" in df.columns:
        df = df[df["sg_uf"].str.upper() == uf.upper()].copy()
    df["ano"] = year
    logger.info("TSE candidatos: %d linhas UF=%s ano=%d", len(df), uf, year)
    return df


def fetch_bens_candidatos(uf: str, year: int) -> pd.DataFrame:
    """Baixa bens declarados por candidato. Agrega vr_bem_total por sq_candidato."""
    url = _ENDPOINTS["bens"].format(base=_CDN_BASE, year=year)
    raw = _download_zip(url)
    if raw is None:
        logger.warning("TSE bens não disponível: UF=%s ano=%d", uf, year)
        return pd.DataFrame()
    df = _read_zip_csvs(raw, _COL_MAP_BENS)
    if "vr_bem" in df.columns:
        df["vr_bem"] = pd.to_numeric(df["vr_bem"].str.replace(",", "."), errors="coerce")
        df = df.groupby("sq_candidato", as_index=False).agg(
            vr_bem_total=("vr_bem", "sum"),
            qt_bens=("vr_bem", "count"),
        )
    df["ano"] = year
    logger.info("TSE bens: %d candidatos UF=%s ano=%d", len(df), uf, year)
    return df


def fetch_receitas_candidatos(uf: str, year: int) -> pd.DataFrame:
    """Baixa receitas (financiamento) por candidato. Agrega por origem."""
    url = _ENDPOINTS["receitas"].format(base=_CDN_BASE, year=year)
    raw = _download_zip(url)
    if raw is None:
        logger.warning("TSE receitas não disponível: UF=%s ano=%d", uf, year)
        return pd.DataFrame()
    df = _read_zip_csvs(raw, _COL_MAP_RECEITAS)
    if "vr_receita" in df.columns:
        df["vr_receita"] = pd.to_numeric(df["vr_receita"].str.replace(",", "."), errors="coerce")
    df["ano"] = year
    logger.info("TSE receitas: %d linhas UF=%s ano=%d", len(df), uf, year)
    return df


def build_candidaturas_dataframe(uf: str, year: int) -> pd.DataFrame:
    """Join candidatos + bens + receitas em um único DataFrame enriquecido."""
    df_cand = fetch_candidatos(uf, year)
    if df_cand.empty:
        return pd.DataFrame()

    df_bens = fetch_bens_candidatos(uf, year)
    df_rec = fetch_receitas_candidatos(uf, year)

    if "sq_candidato" in df_cand.columns:
        if not df_bens.empty and "sq_candidato" in df_bens.columns:
            df_cand = df_cand.merge(df_bens, on="sq_candidato", how="left", suffixes=("", "_bens"))
        if not df_rec.empty and "sq_candidato" in df_rec.columns:
            qt_doadores_agg = (
                ("nr_cpf_cnpj_doador", "nunique")
                if "nr_cpf_cnpj_doador" in df_rec.columns
                else ("vr_receita", "count")
            )
            rec_agg = df_rec.groupby("sq_candidato", as_index=False).agg(
                vr_receita_total=("vr_receita", "sum"),
                qt_doadores=qt_doadores_agg,
            )
            df_cand = df_cand.merge(rec_agg, on="sq_candidato", how="left")

    # Mask CPF — LGPD: candidato é figura pública, mas nr_cpf é dado sensível
    for col in ("nr_cpf", "nr_titulo_eleitoral", "nr_cpf_cnpj_doador"):
        if col in df_cand.columns:
            df_cand.drop(columns=[col], inplace=True)

    df_cand["ingested_at"] = pd.Timestamp.utcnow()
    return df_cand
