"""TSE client — HTTP download from TSE CDN + column normalization."""

from __future__ import annotations

import io
import logging
import zipfile

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.tse")

_CDN = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_secao"
    "/votacao_secao_{year}_{uf}.zip"
)

# Raw TSE column → canonical lowercase name
_COL_MAP: dict[str, str] = {
    "SG_UF": "sg_uf",
    "SG_UF_VOTO": "sg_uf",
    "CD_MUNICIPIO": "cd_municipio",
    "NM_MUNICIPIO": "nm_municipio",
    "NR_ZONA": "nr_zona",
    "NR_SECAO": "nr_secao",
    "NR_TURNO": "nr_turno",
    "CD_CARGO": "cd_cargo",
    "DS_CARGO": "ds_cargo",
    "NR_CANDIDATO": "nr_candidato",
    "NR_VOTAVEL": "nr_candidato",
    "NM_CANDIDATO": "nm_candidato",
    "NM_URNA_CANDIDATO": "nm_urna_candidato",
    "NM_VOTAVEL": "nm_candidato",
    "SG_PARTIDO": "sg_partido",
    "NM_PARTIDO": "nm_partido",
    "SQ_CANDIDATO": "sq_candidato",
    "QT_VOTOS_NOMINAIS": "qt_votos",
    "QT_VOTOS_NOMINAIS_VALIDOS": "qt_votos",
    "QT_VOTOS": "qt_votos",
    "CD_SIT_TOT_TURNO": "cd_situacao",
    "DS_SIT_TOT_TURNO": "ds_situacao",
}

_CARGO_NAMES: dict[int, str] = {
    1: "Presidente",
    3: "Governador",
    5: "Senador",
    6: "Deputado Federal",
    7: "Deputado Estadual",
    8: "Deputado Distrital",
    11: "Prefeito",
    13: "Vereador",
}

_NUMERIC_COLS = (
    "cd_municipio",
    "nr_zona",
    "nr_secao",
    "nr_turno",
    "nr_candidato",
    "cd_cargo",
    "qt_votos",
)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def download_tse_resultados(uf: str, year: int) -> pd.DataFrame:
    """Download TSE votacao_secao ZIP and return concatenated DataFrame."""
    url = _CDN.format(year=year, uf=uf.upper())
    logger.info("Baixando TSE: %s", url)

    resp = requests.get(url, timeout=180, stream=True)
    resp.raise_for_status()

    buf = io.BytesIO(resp.content)
    frames: list[pd.DataFrame] = []

    with zipfile.ZipFile(buf) as zf:
        csv_files = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_files:
            raise ValueError(f"ZIP TSE vazio para {uf}/{year}: {zf.namelist()}")

        for name in csv_files:
            with zf.open(name) as f:
                try:
                    df = pd.read_csv(
                        f,
                        sep=";",
                        encoding="latin-1",
                        dtype=str,
                        low_memory=False,
                    )
                    frames.append(df)
                    logger.debug("Lido %s: %d linhas", name, len(df))
                except Exception as exc:
                    logger.warning("Falha ao ler %s: %s", name, exc)

    if not frames:
        raise ValueError(f"Nenhum CSV lido do ZIP TSE {uf}/{year}")

    result = pd.concat(frames, ignore_index=True)
    logger.info("TSE raw: %d linhas, colunas=%s", len(result), list(result.columns[:6]))
    return result


def normalize_columns(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Rename TSE raw columns to canonical lowercase names and coerce types."""
    rename = {c: _COL_MAP[c] for c in df.columns if c in _COL_MAP}
    df = df.rename(columns=rename)

    # If multiple source cols mapped to qt_votos, keep first non-null
    if "qt_votos" not in df.columns:
        for alt in ("qt_votos_validos", "qt_votos_brancos"):
            if alt in df.columns:
                df["qt_votos"] = df[alt]
                break

    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ano_eleicao"] = year

    if "cd_cargo" in df.columns and "ds_cargo" not in df.columns:
        df["ds_cargo"] = df["cd_cargo"].map(_CARGO_NAMES)

    if "sg_uf" in df.columns:
        df["sg_uf"] = df["sg_uf"].str.strip().str.upper()

    if "nm_municipio" in df.columns:
        df["nm_municipio"] = df["nm_municipio"].str.strip().str.title()

    return df
