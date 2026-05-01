"""TSE client — HTTP download from TSE CDN + column normalization."""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.tse")

_CDN = "https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_secao/votacao_secao_{year}_{uf}.zip"

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
def download_tse_resultados(uf: str, year: int) -> str:
    """Download TSE votacao_secao ZIP, normalize per chunk, write to temp parquet.

    Returns path to the parquet file. Never loads the full DataFrame into memory —
    each 500k-row chunk is normalized and written via pyarrow streaming, then freed.
    Caller is responsible for deleting the returned file after use.
    """
    url = _CDN.format(year=year, uf=uf.upper())
    logger.info("Baixando TSE: %s", url)

    tmp_fd, tmp_zip = tempfile.mkstemp(suffix=".zip")
    tmp_parquet = tmp_zip.replace(".zip", ".parquet")
    try:
        with requests.get(url, timeout=300, stream=True) as resp:
            resp.raise_for_status()
            with os.fdopen(tmp_fd, "wb") as fout:
                for http_chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                    fout.write(http_chunk)
        logger.info("ZIP baixado: %s (%.1f MB)", tmp_zip, os.path.getsize(tmp_zip) / 1e6)

        _KEEP_COLS = set(_COL_MAP.keys())
        writer: pq.ParquetWriter | None = None
        total_rows = 0

        with zipfile.ZipFile(tmp_zip) as zf:
            csv_files = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_files:
                raise ValueError(f"ZIP TSE vazio para {uf}/{year}: {zf.namelist()}")

            for name in csv_files:
                with zf.open(name) as f:
                    try:
                        for chunk in pd.read_csv(
                            f,
                            sep=";",
                            encoding="latin-1",
                            dtype=str,
                            chunksize=500_000,
                        ):
                            keep = [c for c in chunk.columns if c in _KEEP_COLS]
                            chunk = chunk[keep]
                            chunk = normalize_columns(chunk, year)
                            table = pa.Table.from_pandas(chunk, preserve_index=False)
                            if writer is None:
                                writer = pq.ParquetWriter(tmp_parquet, table.schema, compression="zstd")
                            writer.write_table(table)
                            total_rows += len(chunk)
                            del chunk, table
                    except Exception as exc:
                        logger.warning("Falha ao ler %s: %s", name, exc)

        if writer:
            writer.close()

        if total_rows == 0:
            raise ValueError(f"Nenhuma linha lida do ZIP TSE {uf}/{year}")

        logger.info("TSE streaming: %d linhas → %s", total_rows, tmp_parquet)
        return tmp_parquet

    finally:
        if os.path.exists(tmp_zip):
            os.unlink(tmp_zip)


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
