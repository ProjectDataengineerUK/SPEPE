"""TSE Perfil do Eleitorado client — sexo, faixa etária, escolaridade, estado civil."""

from __future__ import annotations

import io
import logging
import zipfile

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.tse_perfil")

# TSE CDN — national file (no UF suffix); sg_uf column inside filters by UF
_CDN_PERFIL = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/perfil_eleitorado/perfil_eleitorado_{year}.zip"
)

# Canon map: TSE raw column → canonical name
_COL_MAP = {
    "SG_UF": "sg_uf",
    "CD_MUNICIPIO": "cd_municipio",
    "NM_MUNICIPIO": "nm_municipio",
    "NR_ZONA": "nr_zona",
    "DS_GENERO": "ds_genero",
    "DS_FAIXA_ETARIA": "ds_faixa_etaria",
    "DS_GRAU_ESCOLARIDADE": "ds_grau_escolaridade",
    "DS_ESTADO_CIVIL": "ds_estado_civil",
    "QT_ELEITORES_PERFIL": "qt_eleitores",
    "QT_ELEITORES_INC_DEFICIENCIA": "qt_eleitores_deficiencia",
    "QT_ELEITORES_BIOMETRIA": "qt_eleitores_biometria",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _download_zip(url: str) -> bytes:
    resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()
    return resp.content


def fetch_perfil_eleitorado(uf: str, year: int) -> pd.DataFrame:
    """Stream national perfil ZIP, filter by UF in chunks — avoids loading 800MB into RAM.

    Returns DataFrame with columns: sg_uf, cd_municipio, nm_municipio, nr_zona,
    ds_genero, ds_faixa_etaria, ds_grau_escolaridade, ds_estado_civil,
    qt_eleitores, ano.
    """
    url = _CDN_PERFIL.format(year=year)
    logger.info("TSE Perfil Eleitorado: baixando nacional %s", url)

    try:
        raw = _download_zip(url)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logger.warning("Perfil eleitorado não disponível: ano=%d", year)
            return pd.DataFrame()
        raise

    frames: list[pd.DataFrame] = []
    uf_upper = uf.upper()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with zf.open(name) as f:
                enc_used = "latin-1"
                for enc in ("utf-8-sig", "latin-1", "cp1252"):
                    try:
                        pd.read_csv(io.BytesIO(f.read(1024)), sep=";", encoding=enc, nrows=1)
                        enc_used = enc
                        break
                    except (UnicodeDecodeError, Exception):
                        continue
                f.seek(0)
                # Read 50k rows at a time, keep only target UF
                for chunk in pd.read_csv(
                    f, sep=";", encoding=enc_used, dtype=str, on_bad_lines="warn", chunksize=50_000
                ):
                    chunk.columns = [_COL_MAP.get(c.strip(), c.strip().lower()) for c in chunk.columns]
                    if "sg_uf" in chunk.columns:
                        chunk = chunk[chunk["sg_uf"].str.upper() == uf_upper]
                    if not chunk.empty:
                        frames.append(chunk)

    if not frames:
        logger.warning("Nenhum dado perfil para UF=%s ano=%d", uf, year)
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    if "qt_eleitores" in df.columns:
        df["qt_eleitores"] = pd.to_numeric(df["qt_eleitores"], errors="coerce").fillna(0).astype(int)
    df["ano"] = year
    logger.info("TSE Perfil Eleitorado: %d linhas UF=%s ano=%d", len(df), uf, year)
    return df


def build_perfil_municipio(uf: str, year: int) -> pd.DataFrame:
    """Aggregate perfil eleitorado to municipality level.

    Returns pivot with qt_eleitores per (cd_municipio, ds_genero, ds_faixa_etaria,
    ds_grau_escolaridade, ds_estado_civil) — ready for Silver/Gold.
    """
    df = fetch_perfil_eleitorado(uf, year)
    if df.empty:
        return pd.DataFrame()

    group_cols = [
        c
        for c in (
            "cd_municipio",
            "nm_municipio",
            "sg_uf",
            "ds_genero",
            "ds_faixa_etaria",
            "ds_grau_escolaridade",
            "ds_estado_civil",
            "ano",
        )
        if c in df.columns
    ]
    agg = df.groupby(group_cols, as_index=False)["qt_eleitores"].sum()
    agg["ingested_at"] = pd.Timestamp.utcnow()
    return agg
