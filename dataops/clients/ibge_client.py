"""IBGE client — Localidades API + SIDRA API."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.ibge")

_LOCALIDADES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
_SIDRA_URL = (
    "https://servicodados.ibge.gov.br/api/v3/agregados"
    "/{tabela}/periodos/{periodo}/variaveis/{variavel}"
    "?localidades=N6[all]"
)

# SIDRA: tabela, periodo, variavel, col_name
# Sources: Censo 2022 (9514, 9543, 9662, 9714) + PNAD Contínua 2023 (9532, 9605)
#          Censo 2010 para religião (2094) — Censo 2022 tabelas ainda em consolidação
_SIDRA_TABLES: dict[str, tuple[str, str, str]] = {
    # Core
    "populacao":              ("9514", "2022", "9324"),
    "pct_analfabetos":        ("9543", "2022", "4104"),
    "renda_media":            ("9532", "2023", "9531"),
    "taxa_desemprego":        ("9605", "2023", "4099"),
    # Faixas etárias — Censo 2022 tabela 9662
    "pct_0_14":               ("9662", "2022", "9325"),
    "pct_15_29":              ("9662", "2022", "9326"),
    "pct_30_59":              ("9662", "2022", "9327"),
    "pct_60_mais":            ("9662", "2022", "9328"),
    # Gênero — Censo 2022
    "pct_mulheres":           ("9514", "2022", "9329"),
    # Escolaridade — Censo 2022
    "pct_ensino_medio":       ("9543", "2022", "4103"),
    "pct_superior_completo":  ("9543", "2022", "4102"),
    # Religião — Censo 2010 (2022 ainda consolidando)
    "pct_catolico":           ("2094", "2010", "4150"),
    "pct_sem_religiao":       ("2094", "2010", "4154"),
    # Urbanização — Censo 2022 tabela 9714
    "pct_urbano":             ("9714", "2022", "9325"),
    "densidade_demografica":  ("9514", "2022", "616"),
    # Pobreza — PNAD 2022
    "pct_extrema_pobreza":    ("9532", "2022", "9535"),
    # Acesso digital — PNAD TIC 2023
    "pct_internet_domiciliar": ("9606", "2023", "9607"),
}

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
        rows.append(
            {
                "cd_municipio_ibge": int(m["id"]),
                "cd_municipio_tse": int(m["id"]) // 10,
                "nm_municipio": m["nome"],
                "sg_uf": m["microrregiao"]["mesorregiao"]["UF"]["sigla"],
                "nm_uf": m["microrregiao"]["mesorregiao"]["UF"]["nome"],
                "cd_mesorregiao": m["microrregiao"]["mesorregiao"]["id"],
                "nm_mesorregiao": m["microrregiao"]["mesorregiao"]["nome"],
                "cd_microrregiao": m["microrregiao"]["id"],
                "nm_microrregiao": m["microrregiao"]["nome"],
            }
        )

    df = pd.DataFrame(rows)
    logger.info("Municípios IBGE carregados: %d para UF=%s", len(df), uf)
    return df


def _fetch_sidra(tabela: str, periodo: str, variavel: str) -> dict[str, float]:
    """Return {ibge_municipio_code: value} from a SIDRA table."""
    url = _SIDRA_URL.format(tabela=tabela, periodo=periodo, variavel=variavel)
    try:
        data = _get(url)
    except Exception as exc:
        logger.warning("SIDRA %s/%s/%s falhou: %s", tabela, periodo, variavel, exc)
        return {}

    result: dict[str, float] = {}
    for item in data:
        for res in item.get("resultados", []):
            for loc in res.get("series", []):
                loc_id = loc.get("localidade", {}).get("id", "")
                val_str = list(loc.get("serie", {}).values())
                if loc_id and val_str:
                    try:
                        result[loc_id] = float(str(val_str[0]).replace(",", "."))
                    except (ValueError, TypeError):
                        pass
    return result


def fetch_sidra_indicators(
    uf: str,
    indicators: list[str],
    cache_dir: Path,
    uf_code: str = "35",
) -> list[dict]:
    """Fetch SIDRA indicators for all municipalities in a UF.

    Returns a list of dicts, one per municipality × indicator.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    df_mun = load_municipios(uf)
    ibge_codes = set(df_mun["cd_municipio_ibge"].astype(str).tolist())

    rows: list[dict] = []
    for indicator in indicators:
        if indicator not in _SIDRA_TABLES:
            logger.debug("Indicador não mapeado no SIDRA: %s — pulando", indicator)
            continue

        tabela, periodo, variavel = _SIDRA_TABLES[indicator]
        logger.info("SIDRA %s (tabela=%s, periodo=%s)", indicator, tabela, periodo)
        values = _fetch_sidra(tabela, periodo, variavel)

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

    logger.info("SIDRA retornou %d linhas para UF=%s", len(rows), uf)
    return rows
