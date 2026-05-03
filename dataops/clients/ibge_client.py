"""IBGE client — Localidades API + SIDRA API (apisidra.ibge.gov.br)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.ibge")

_LOCALIDADES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"

# apisidra.ibge.gov.br — API estável para consultas SIDRA
# Formato: /values/t/{tabela}/n6/all/v/{variavel}/p/{periodo}
# Resposta: array onde [0] é cabeçalho, [1:] são dados com D1C=cod_municipio, V=valor
_APISIDRA_URL = "https://apisidra.ibge.gov.br/values/t/{tabela}/n6/all/v/{variavel}/p/{periodo}"

# (tabela, periodo, variavel) — confirmados funcionando em apisidra.ibge.gov.br
# Censo 2022: N6 (município) disponível
# PNAD Contínua: apenas N3 (UF) — não disponível em N6
_SIDRA_TABLES: dict[str, tuple[str, str, str]] = {
    # Estimativas populacao — última disponível (2021 confirmado; 2022 sem dado nesta tabela)
    "populacao": ("6579", "last", "9324"),
    # Censo 2022 — município (N6) — CONFIRMADOS
    "taxa_alfabetizacao": ("9543", "2022", "2513"),  # Taxa alfabetização ≥15 anos
    # Censo 2022 — faixas etárias (N6) — a confirmar quando API estabilizar
    "pct_0_14": ("9662", "2022", "9325"),
    "pct_15_29": ("9662", "2022", "9326"),
    "pct_30_59": ("9662", "2022", "9327"),
    "pct_60_mais": ("9662", "2022", "9328"),
    # Censo 2022 — urbanização (N6)
    "pct_urbano": ("9714", "2022", "9325"),
    # Religião — Censo 2010 (Censo 2022 ainda consolidando)
    "pct_catolico": ("2094", "2010", "4150"),
    "pct_sem_religiao": ("2094", "2010", "4154"),
}

# pct_analfabetos = 100 - taxa_alfabetizacao (derivado, não busca direta)
# renda_media e taxa_desemprego: PNAD só disponível em N3 (UF) — implementar v2

_SIDRA_BATCH_SIZE = 50  # municípios por requisição

_IPEADATA_URL = (
    "http://www.ipeadata.gov.br/api/odata4/ValoresSerie"
    "(SERCODIGO='{serie}')?$top=100&$filter=NIVNOME%20eq%20'Estados'"
)

_IPEADATA_SERIES = {
    "gini_renda": "PNAD_GINI",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _get(url: str, **kwargs: Any) -> Any:
    resp = requests.get(url, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def fetch_ipeadata_gini(uf: str) -> float | None:
    """Return Gini coefficient for a UF from IPEADATA (state-level, latest available)."""
    serie = _IPEADATA_SERIES["gini_renda"]
    url = _IPEADATA_URL.format(serie=serie)
    try:
        data = _get(url)
    except Exception as exc:
        logger.warning("IPEADATA Gini fetch failed for UF=%s: %s", uf, exc)
        return None

    uf_upper = uf.upper()
    values = []
    for item in data.get("value", []):
        if item.get("UFDESCRICAO", "").startswith(uf_upper) or item.get("TERNOME", "") == uf_upper:
            try:
                values.append((item.get("VALDATA", ""), float(item["VALVALOR"])))
            except (TypeError, ValueError, KeyError):
                pass

    if not values:
        return None
    values.sort(key=lambda x: x[0], reverse=True)
    return values[0][1]


def load_municipios(uf: str) -> pd.DataFrame:
    """Return all municipalities for a UF with IBGE codes and names."""
    url = _LOCALIDADES_URL.format(uf=uf.upper())
    logger.info("Buscando municípios IBGE: UF=%s", uf)
    data = _get(url)

    rows = []
    for m in data:
        try:
            micro = m.get("microrregiao") or {}
            meso = micro.get("mesorregiao") or {}
            uf_obj = meso.get("UF") or {}
            rows.append(
                {
                    "cd_municipio_ibge": int(m["id"]),
                    "cd_municipio_tse": int(m["id"]) // 10,
                    "nm_municipio": m.get("nome", ""),
                    "sg_uf": uf_obj.get("sigla", ""),
                    "nm_uf": uf_obj.get("nome", ""),
                    "cd_mesorregiao": meso.get("id"),
                    "nm_mesorregiao": meso.get("nome", ""),
                    "cd_microrregiao": micro.get("id"),
                    "nm_microrregiao": micro.get("nome", ""),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue

    df = pd.DataFrame(rows)
    logger.info("Municípios IBGE carregados: %d para UF=%s", len(df), uf)
    return df


def _parse_sidra_rows(data: list[dict]) -> dict[str, float]:
    """Parse apisidra flat-array response into {ibge_code: value}."""
    result: dict[str, float] = {}
    for row in data[1:]:  # row[0] is header
        cod = str(row.get("D1C", "")).strip()
        val_str = str(row.get("V", "")).strip()
        if not cod or val_str in ("", "-", "...", "X"):
            continue
        try:
            result[cod] = float(val_str.replace(",", "."))
        except (ValueError, TypeError):
            pass
    return result


def _fetch_sidra(
    tabela: str, periodo: str, variavel: str, ibge_codes: list[str] | None = None
) -> dict[str, float]:
    """Return {ibge_municipio_code: value} via apisidra.ibge.gov.br.

    If ibge_codes provided, fetches in batches of _SIDRA_BATCH_SIZE (avoids N6[all] timeout).
    """
    if ibge_codes:
        result: dict[str, float] = {}
        for i in range(0, len(ibge_codes), _SIDRA_BATCH_SIZE):
            batch = ibge_codes[i : i + _SIDRA_BATCH_SIZE]
            codes_str = ",".join(batch)
            url = (
                f"https://apisidra.ibge.gov.br/values/t/{tabela}"
                f"/n6/{codes_str}/v/{variavel}/p/{periodo}"
            )
            try:
                data = _get(url)
                result.update(_parse_sidra_rows(data))
            except Exception as exc:
                logger.warning("SIDRA batch %s/%s/%s falhou: %s", tabela, periodo, variavel, exc)
        return result

    # Fallback: N6[all] (pode falhar para tabelas grandes)
    url = _APISIDRA_URL.format(tabela=tabela, periodo=periodo, variavel=variavel)
    try:
        data = _get(url)
    except Exception as exc:
        logger.warning("SIDRA %s/%s/%s falhou: %s", tabela, periodo, variavel, exc)
        return {}

    if not isinstance(data, list) or len(data) < 2:
        logger.warning("SIDRA %s/%s/%s resposta vazia", tabela, periodo, variavel)
        return {}

    return _parse_sidra_rows(data)


def fetch_sidra_indicators(
    uf: str,
    indicators: list[str],
    cache_dir: Path,
    uf_code: str = "35",
) -> list[dict]:
    """Fetch SIDRA indicators for all municipalities in a UF.

    Returns a list of dicts, one per municipality × indicator.
    pct_analfabetos is derived as 100 - taxa_alfabetizacao when requested.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    df_mun = load_municipios(uf)
    ibge_codes_list = df_mun["cd_municipio_ibge"].astype(str).tolist()
    ibge_codes = set(ibge_codes_list)

    # Expand pct_analfabetos → fetch taxa_alfabetizacao and invert
    expanded = list(indicators)
    needs_analfabetos = "pct_analfabetos" in expanded
    if needs_analfabetos and "taxa_alfabetizacao" not in expanded:
        expanded.append("taxa_alfabetizacao")

    rows: list[dict] = []
    fetched: dict[str, dict[str, float]] = {}

    for indicator in expanded:
        if indicator == "pct_analfabetos":
            continue  # derived below
        if indicator not in _SIDRA_TABLES:
            logger.debug("Indicador não mapeado no SIDRA: %s — pulando", indicator)
            continue

        tabela, periodo, variavel = _SIDRA_TABLES[indicator]
        logger.info("SIDRA %s (tabela=%s, periodo=%s)", indicator, tabela, periodo)
        values = _fetch_sidra(tabela, periodo, variavel, ibge_codes=ibge_codes_list)
        fetched[indicator] = values

        for ibge_id, value in values.items():
            if ibge_id in ibge_codes:
                rows.append(
                    {
                        "cd_municipio_ibge": int(ibge_id),
                        "sg_uf": uf.upper(),
                        "indicador": indicator,
                        "valor": value,
                        "periodo": periodo,
                        "fonte": f"IBGE SIDRA tabela {tabela}",
                    }
                )

    # Derive pct_analfabetos = 100 - taxa_alfabetizacao
    if needs_analfabetos and "taxa_alfabetizacao" in fetched:
        _, periodo, _ = _SIDRA_TABLES["taxa_alfabetizacao"]
        for ibge_id, alfa in fetched["taxa_alfabetizacao"].items():
            if ibge_id in ibge_codes:
                rows.append(
                    {
                        "cd_municipio_ibge": int(ibge_id),
                        "sg_uf": uf.upper(),
                        "indicador": "pct_analfabetos",
                        "valor": round(100.0 - alfa, 4),
                        "periodo": periodo,
                        "fonte": "IBGE SIDRA tabela 9543 (derivado)",
                    }
                )

    logger.info("SIDRA retornou %d linhas para UF=%s", len(rows), uf)
    return rows
