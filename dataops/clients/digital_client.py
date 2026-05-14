"""Digital signals client — Google Trends (pytrends) + Meta Ad Library API.

Meta Ad Library:
  - Anúncios políticos obrigatoriamente públicos por lei (transparência eleitoral)
  - Retorna gasto × impressões × distribuição regional × distribuição demográfica
  - Sem autenticação especial: App Access Token (client_credentials) suficiente
  - API: https://graph.facebook.com/v21.0/ads_archive

Google Trends:
  - Interesse de busca relativo (0-100) por candidato × UF × semana
  - Sem API key — pytrends (scraping oficial do Google Trends)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.digital")

_GRAPH_BASE = "https://graph.facebook.com/v21.0"
_ADS_ARCHIVE_URL = f"{_GRAPH_BASE}/ads_archive"

# ── Mapeamento estado BR → UF ────────────────────────────────────────────────
_BR_STATE_TO_UF: dict[str, str] = {
    "Acre": "AC",
    "Alagoas": "AL",
    "Amapá": "AP",
    "Amazonas": "AM",
    "Bahia": "BA",
    "Ceará": "CE",
    "Espírito Santo": "ES",
    "Goiás": "GO",
    "Maranhão": "MA",
    "Mato Grosso": "MT",
    "Mato Grosso do Sul": "MS",
    "Minas Gerais": "MG",
    "Pará": "PA",
    "Paraíba": "PB",
    "Paraná": "PR",
    "Pernambuco": "PE",
    "Piauí": "PI",
    "Rio de Janeiro": "RJ",
    "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS",
    "Rondônia": "RO",
    "Roraima": "RR",
    "Santa Catarina": "SC",
    "São Paulo": "SP",
    "Sergipe": "SE",
    "Tocantins": "TO",
    "Distrito Federal": "DF",
    # Variantes com acento diferente
    "Amapa": "AP",
    "Para": "PA",
    "Paraiba": "PB",
    "Parana": "PR",
    "Piaui": "PI",
    "Rondonia": "RO",
}


def _state_to_uf(region_name: str) -> str:
    cleaned = region_name.strip()
    if cleaned in _BR_STATE_TO_UF:
        return _BR_STATE_TO_UF[cleaned]
    for k, v in _BR_STATE_TO_UF.items():
        if k.lower() == cleaned.lower():
            return v
    return cleaned[:2].upper()


def _safe_float(val: Any) -> float:
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _midpoint(lower: Any, upper: Any) -> float:
    lo, hi = _safe_float(lower), _safe_float(upper)
    if lo == 0 and hi == 0:
        return 0.0
    return (lo + hi) / 2.0


# ── Meta App Token ────────────────────────────────────────────────────────────


def get_meta_app_token(app_id: str = "", app_secret: str = "") -> str:
    """Exchange App ID + Secret for an App Access Token (client_credentials flow).

    Falls back to META_APP_TOKEN env var if already a token (not an ID+secret pair).
    """
    env_token = os.environ.get("META_APP_TOKEN", "")
    if env_token and "|" in env_token:
        return env_token

    app_id = app_id or os.environ.get("META_APP_ID", "")
    app_secret = app_secret or os.environ.get("META_APP_SECRET", "")

    if app_id and app_secret:
        try:
            resp = requests.get(
                f"{_GRAPH_BASE}/oauth/access_token",
                params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "grant_type": "client_credentials",
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("access_token", env_token)
        except Exception as exc:
            logger.warning("Meta App Token exchange falhou: %s", exc)

    return env_token


# ── Meta Ad Library ───────────────────────────────────────────────────────────

_AD_FIELDS = (
    "id,"
    "ad_creative_bodies,"
    "ad_delivery_start_time,"
    "ad_delivery_stop_time,"
    "currency,"
    "impressions,"
    "spend,"
    "region_distribution,"
    "demographic_distribution,"
    "estimated_audience_size,"
    "page_name,"
    "funding_entity,"
    "languages,"
    "bylines"
)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
def _meta_get(params: dict[str, Any]) -> dict:
    resp = requests.get(_ADS_ARCHIVE_URL, params=params, timeout=45)
    if resp.status_code == 429:
        logger.warning("Meta Ads rate limit — aguardando 60s")
        time.sleep(60)
        resp = requests.get(_ADS_ARCHIVE_URL, params=params, timeout=45)
    resp.raise_for_status()
    return resp.json()


def fetch_meta_ads(
    candidatos: list[str],
    access_token: str = "",
    year: int = 2026,
    country: str = "BR",
    max_per_candidato: int = 1000,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch Meta Ad Library data for a list of Brazilian electoral candidates.

    Returns three DataFrames:
      ads_df     — one row per ad with spend/impressions summary
      regions_df — one row per ad × UF with regional spend distribution
      demo_df    — one row per ad × age_group × gender with demographic split

    Args:
        candidatos: list of candidate names to search
        access_token: Meta App Access Token (falls back to META_APP_TOKEN env)
        year: election year for date filtering
        country: ISO country code
        max_per_candidato: max ads to fetch per candidate (pagination)
    """
    token = access_token or get_meta_app_token()
    if not token:
        logger.warning("META_APP_TOKEN ausente — Meta Ads pulado")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"

    ads_rows: list[dict] = []
    region_rows: list[dict] = []
    demo_rows: list[dict] = []

    for candidato in candidatos:
        collected = 0
        after_cursor: str | None = None

        while collected < max_per_candidato:
            params: dict[str, Any] = {
                "access_token": token,
                "ad_type": "POLITICAL_AND_ISSUE_ADS",
                # Must be JSON-encoded array — plain string "BR" returns 0 results silently
                "ad_reached_countries": json.dumps([country]),
                "search_terms": candidato,
                "ad_delivery_date_min": start_date,
                "ad_delivery_date_max": end_date,
                "fields": _AD_FIELDS,
                "limit": min(max_per_candidato - collected, 500),
            }
            if after_cursor:
                params["after"] = after_cursor

            try:
                data = _meta_get(params)
            except Exception as exc:
                logger.warning("Meta Ads falhou para '%s': %s", candidato, exc)
                break

            ads = data.get("data", [])
            if not ads:
                logger.info(
                    "Meta Ads: 0 anúncios para '%s' (resposta: %s)", candidato, str(data)[:200]
                )
                break

            for ad in ads:
                ad_id = ad.get("id", "")
                spend = ad.get("spend", {})
                impr = ad.get("impressions", {})
                audience = ad.get("estimated_audience_size", {})

                vl_gasto_min = _safe_float(spend.get("lower_bound"))
                vl_gasto_max = _safe_float(spend.get("upper_bound"))
                vl_gasto_mid = _midpoint(vl_gasto_min, vl_gasto_max)
                qt_impr_min = _safe_float(impr.get("lower_bound"))
                qt_impr_max = _safe_float(impr.get("upper_bound"))
                qt_impr_mid = _midpoint(qt_impr_min, qt_impr_max)

                body_text = ""
                bodies = ad.get("ad_creative_bodies")
                if isinstance(bodies, list) and bodies:
                    body_text = bodies[0][:500]

                ads_rows.append(
                    {
                        "ad_id": ad_id,
                        "candidato": candidato,
                        "page_name": ad.get("page_name", ""),
                        "funding_entity": ad.get("funding_entity", ""),
                        "dt_inicio": ad.get("ad_delivery_start_time", ""),
                        "dt_fim": ad.get("ad_delivery_stop_time", ""),
                        "currency": ad.get("currency", "BRL"),
                        "vl_gasto_min": vl_gasto_min,
                        "vl_gasto_max": vl_gasto_max,
                        "vl_gasto_mid": vl_gasto_mid,
                        "qt_impressoes_min": qt_impr_min,
                        "qt_impressoes_max": qt_impr_max,
                        "qt_impressoes_mid": qt_impr_mid,
                        "audience_min": _safe_float(audience.get("lower_bound")),
                        "audience_max": _safe_float(audience.get("upper_bound")),
                        "idiomas": json.dumps(ad.get("languages") or []),
                        "texto_anuncio": body_text,
                        "ano": year,
                        "fonte": "meta_ad_library",
                        "score_confiabilidade": 8.0,  # Ad Library = dado verificado Meta
                    }
                )

                # ── Distribuição regional ─────────────────────────────────
                for region_entry in ad.get("region_distribution") or []:
                    region_name = region_entry.get("region", "")
                    pct = _safe_float(region_entry.get("percentage"))
                    sg_uf = _state_to_uf(region_name)
                    region_rows.append(
                        {
                            "ad_id": ad_id,
                            "candidato": candidato,
                            "sg_uf": sg_uf,
                            "nm_estado": region_name,
                            "pct_regiao": pct,
                            "vl_gasto_estimado_uf": vl_gasto_mid * pct,
                            "qt_impressoes_estimadas_uf": qt_impr_mid * pct,
                            "dt_inicio": ad.get("ad_delivery_start_time", ""),
                            "ano": year,
                            "fonte": "meta_ad_library",
                        }
                    )

                # ── Distribuição demográfica ──────────────────────────────
                for demo_entry in ad.get("demographic_distribution") or []:
                    pct = _safe_float(demo_entry.get("percentage"))
                    demo_rows.append(
                        {
                            "ad_id": ad_id,
                            "candidato": candidato,
                            "faixa_etaria": demo_entry.get("age", ""),
                            "genero": demo_entry.get("gender", ""),
                            "pct_demografico": pct,
                            "vl_gasto_estimado_demo": vl_gasto_mid * pct,
                            "dt_inicio": ad.get("ad_delivery_start_time", ""),
                            "ano": year,
                            "fonte": "meta_ad_library",
                        }
                    )

            collected += len(ads)

            paging = data.get("paging", {})
            cursors = paging.get("cursors", {})
            after_cursor = cursors.get("after")
            if not after_cursor or not paging.get("next"):
                break

            time.sleep(1)

        logger.info("Meta Ads: %d anúncios para '%s' (%d)", collected, candidato, year)

    ads_df = pd.DataFrame(ads_rows) if ads_rows else pd.DataFrame()
    regions_df = pd.DataFrame(region_rows) if region_rows else pd.DataFrame()
    demo_df = pd.DataFrame(demo_rows) if demo_rows else pd.DataFrame()

    logger.info(
        "Meta Ads total: %d anúncios | %d regiões | %d demos",
        len(ads_df),
        len(regions_df),
        len(demo_df),
    )
    return ads_df, regions_df, demo_df


# ── Google Trends ─────────────────────────────────────────────────────────────


def fetch_trends(
    keywords: list[str],
    timeframe: str = "today 3-m",
    geo: str = "BR",
) -> pd.DataFrame:
    """Return Google Trends interest-over-time DataFrame for given keywords.

    Columns: date (index) + one column per keyword (0–100 relative interest).
    Returns empty DataFrame on failure.
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.error("pytrends não instalado — pip install pytrends")
        return pd.DataFrame()

    try:
        pt = TrendReq(hl="pt-BR", tz=-180, timeout=(10, 30), retries=3, backoff_factor=0.5)

        frames: list[pd.DataFrame] = []
        chunks = [keywords[i : i + 5] for i in range(0, len(keywords), 5)]

        for chunk in chunks:
            pt.build_payload(chunk, timeframe=timeframe, geo=geo)
            df = pt.interest_over_time()
            if df.empty:
                logger.warning("Google Trends vazio para: %s", chunk)
                continue
            df = df.drop(columns=["isPartial"], errors="ignore")
            frames.append(df)
            time.sleep(1.5)

        if not frames:
            return pd.DataFrame()

        result = frames[0]
        for df in frames[1:]:
            result = result.join(df, how="outer")

        logger.info("Google Trends: %d semanas, %d candidatos", len(result), len(result.columns))
        return result

    except Exception as exc:
        logger.error("Google Trends falhou: %s", exc)
        return pd.DataFrame()


def fetch_trends_by_uf(
    keywords: list[str],
    timeframe: str = "today 3-m",
) -> pd.DataFrame:
    """Fetch Google Trends interest by Brazilian state (UF) for each keyword.

    Returns DataFrame with columns: keyword, sg_uf, interest_value (0-100).
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.error("pytrends não instalado")
        return pd.DataFrame()

    rows: list[dict] = []
    try:
        pt = TrendReq(hl="pt-BR", tz=-180, timeout=(10, 30), retries=2, backoff_factor=0.5)
        for chunk in [keywords[i : i + 5] for i in range(0, len(keywords), 5)]:
            pt.build_payload(chunk, timeframe=timeframe, geo="BR")
            df_region = pt.interest_by_region(resolution="REGION", inc_low_vol=True)
            df_region = df_region.reset_index()
            for _, row in df_region.iterrows():
                uf = _state_to_uf(str(row.get("geoName", "")))
                for kw in chunk:
                    if kw in row:
                        rows.append(
                            {
                                "keyword": kw,
                                "candidato": kw,
                                "sg_uf": uf,
                                "interesse_busca": int(row[kw]),
                                "fonte": "google_trends",
                            }
                        )
            time.sleep(1.5)
    except Exception as exc:
        logger.error("Google Trends by UF falhou: %s", exc)

    logger.info("Google Trends UF: %d registros", len(rows))
    return pd.DataFrame(rows) if rows else pd.DataFrame()
