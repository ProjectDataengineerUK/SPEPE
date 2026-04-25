"""
SPEPE Dashboard API — FastAPI bridge entre o frontend HTML e o backend.

Monta sobre o mesmo processo do Chainlit via _fastapi_app:
  - REST:      GET /api/candidatos, /api/kpi, /api/municipios, /api/trends, /api/meta
  - WebSocket: /ws/chat  → Supervisor stream em tempo real
  - Static:    GET /dash  → Serve o dashboard HTML

Adaptação do protótipo spepe-app.html:
  - Mock data JS → fetch('/api/*')
  - Chat mock → WebSocket /ws/chat
  - Login mock → Firebase Auth token validado em /api/auth/me
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from enum import Enum

from fastapi import Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

# Monta sobre o app Chainlit existente
from chainlit.server import app as _fastapi_app

from agents.supervisor import Supervisor
from config.session_state import SessionState
from config.settings import settings
from dataops.clients.digital_client import fetch_meta_ads, fetch_trends
from security.output_validators import validate_input_injection

logger = logging.getLogger("spepe.dashboard_api")

_supervisor: Supervisor | None = None


def _get_supervisor() -> Supervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = Supervisor()
    return _supervisor


# ── Servir o dashboard HTML ────────────────────────────────────────────────


@_fastapi_app.get("/dash")
async def serve_dashboard() -> FileResponse:
    """Serve o protótipo HTML do dashboard."""
    from pathlib import Path
    html_path = Path(__file__).parent / "static" / "spepe-app.html"
    return FileResponse(str(html_path), media_type="text/html")


# ── Auth (stub — trocar por Firebase Auth / IAP em prod) ──────────────────


@_fastapi_app.get("/api/auth/me")
async def auth_me(authorization: str = Header(default=None)) -> JSONResponse:
    """Retorna perfil do usuário autenticado. Valida Firebase ID token em produção."""
    import os
    if os.environ.get("FIREBASE_PROJECT_ID"):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Token não fornecido")
        token = authorization[7:]
        try:
            from google.auth.transport import requests as grequests
            from google.oauth2 import id_token
            decoded = id_token.verify_firebase_token(token, grequests.Request())
            return JSONResponse({
                "uid": decoded["uid"],
                "email": decoded.get("email", ""),
                "name": decoded.get("name", ""),
                "plan": "pro",
            })
        except Exception:
            raise HTTPException(status_code=401, detail="Token inválido")

    # Dev/local: stub sem auth
    return JSONResponse({"uid": "demo-user", "email": "", "name": "Demo", "plan": "pro"})


# ── Candidatos por cargo / UF / ano ───────────────────────────────────────


_MOCK_CANDIDATOS: dict[str, list[dict]] = {
    "Presidente": [
        {"nm": "Lula",       "partido": "PT",  "pct_t1": 43.8, "pct_t2": 50.9, "votos": "14.2M"},
        {"nm": "Bolsonaro",  "partido": "PL",  "pct_t1": 43.0, "pct_t2": 49.1, "votos": "13.9M"},
        {"nm": "Tebet",      "partido": "MDB", "pct_t1":  7.4, "pct_t2": None,  "votos": "2.4M"},
        {"nm": "Ciro Gomes", "partido": "PDT", "pct_t1":  3.7, "pct_t2": None,  "votos": "1.2M"},
    ],
    "Governador": [
        {"nm": "Tarcísio",       "partido": "Rep",  "pct_t1": 42.3, "pct_t2": 56.1, "votos": "13.7M"},
        {"nm": "Haddad",         "partido": "PT",   "pct_t1": 35.7, "pct_t2": 43.9, "votos": "11.6M"},
        {"nm": "Rodrigo Garcia", "partido": "PSDB", "pct_t1": 18.4, "pct_t2": None,  "votos": "5.9M"},
    ],
    "Senador": [
        {"nm": "Marcos Pontes", "partido": "PL",   "pct_t1": 36.5, "pct_t2": None, "votos": "11.8M"},
        {"nm": "Marta Suplicy", "partido": "PT",   "pct_t1": 22.1, "pct_t2": None, "votos": "7.1M"},
        {"nm": "José Aníbal",   "partido": "PSDB", "pct_t1": 15.3, "pct_t2": None, "votos": "4.9M"},
    ],
    "Dep. Federal": [
        {"nm": "Eduardo Bolsonaro", "partido": "PL",  "pct_t1": 3.8, "pct_t2": None, "votos": "1.243M"},
        {"nm": "Guilherme Boulos",  "partido": "PSB", "pct_t1": 1.1, "pct_t2": None, "votos": "349k"},
        {"nm": "Tabata Amaral",     "partido": "PSB", "pct_t1": 0.9, "pct_t2": None, "votos": "288k"},
        {"nm": "Paulo Teixeira",    "partido": "PT",  "pct_t1": 0.8, "pct_t2": None, "votos": "254k"},
        {"nm": "Kim Kataguiri",     "partido": "MDB", "pct_t1": 0.7, "pct_t2": None, "votos": "213k"},
    ],
    "Dep. Estadual": [
        {"nm": "Douglas Garcia",    "partido": "PL",   "pct_t1": 1.8, "pct_t2": None, "votos": "581k"},
        {"nm": "Analice Fernandes", "partido": "PSDB", "pct_t1": 1.4, "pct_t2": None, "votos": "451k"},
        {"nm": "Donato",            "partido": "PT",   "pct_t1": 1.1, "pct_t2": None, "votos": "356k"},
    ],
}


@_fastapi_app.get("/api/candidatos")
async def get_candidatos(
    cargo: str = Query("Presidente"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
) -> JSONResponse:
    """
    Retorna candidatos para o cargo/UF/ano.
    Fonte real: BigQuery gold.fact_municipio_eleicao (quando disponível).
    Fallback: dados mock para demonstração.
    """
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            data = await _bq_candidatos(cargo, uf, ano)
            return JSONResponse({"cargo": cargo, "uf": uf, "ano": ano, "candidatos": data})
        except Exception as exc:
            logger.warning("BigQuery candidatos falhou, usando mock: %s", exc)

    candidatos = _MOCK_CANDIDATOS.get(cargo, _MOCK_CANDIDATOS["Presidente"])
    return JSONResponse({"cargo": cargo, "uf": uf, "ano": ano, "candidatos": candidatos})


async def _bq_candidatos(cargo: str, uf: str, ano: int) -> list[dict]:
    """Query real ao BigQuery Gold — quando dados estiverem ingeridos."""
    from google.cloud import bigquery
    client = bigquery.Client(project=settings.gcp_project_id)
    cargo_map = {
        "Presidente": 1, "Governador": 3, "Senador": 5,
        "Dep. Federal": 6, "Dep. Estadual": 7,
    }
    cd_cargo = cargo_map.get(cargo, 1)
    query = f"""
        SELECT
            nm_candidato   AS nm,
            sg_partido     AS partido,
            ROUND(SUM(qt_votos) / SUM(SUM(qt_votos)) OVER () * 100, 1) AS pct_t1,
            CAST(SUM(qt_votos) AS STRING) AS votos
        FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_eleicao`
        WHERE sg_uf = @uf
          AND ano_eleicao = @ano
          AND cd_cargo = @cd_cargo
          AND nr_turno = 1
        GROUP BY nm_candidato, sg_partido
        ORDER BY pct_t1 DESC
        LIMIT 10
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
        bigquery.ScalarQueryParameter("ano", "INT64", ano),
        bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
    ])
    rows = client.query(query, job_config=job_config).result()
    return [dict(row) for row in rows]


# ── KPIs ──────────────────────────────────────────────────────────────────


@_fastapi_app.get("/api/kpi")
async def get_kpi(
    cargo: str = Query("Presidente"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
) -> JSONResponse:
    """Métricas agregadas para os KPI cards do dashboard."""
    cands = _MOCK_CANDIDATOS.get(cargo, _MOCK_CANDIDATOS["Presidente"])
    if not cands:
        return JSONResponse({"error": "sem dados"}, status_code=404)

    c0, c1 = cands[0], cands[1] if len(cands) > 1 else cands[0]
    return JSONResponse({
        "vencedor": c0["nm"],
        "vencedor_partido": c0["partido"],
        "vencedor_pct": c0["pct_t1"],
        "segundo": c1["nm"],
        "segundo_pct": c1["pct_t1"],
        "margem_pp": round(abs(c0["pct_t1"] - c1["pct_t1"]), 1),
        "total_votos": "32,5M" if uf == "SP" else "—",
        "municipios": 645 if uf == "SP" else 0,
        "dq_score": 98.5,
    })


# ── Municípios ────────────────────────────────────────────────────────────


_MOCK_MUNICIPIOS = [
    {"nm": "São Paulo",    "votos": "6.842k", "c1": 44.1, "c2": 43.2, "c3": 7.9},
    {"nm": "Guarulhos",    "votos": "984k",   "c1": 52.3, "c2": 37.4, "c3": 6.1},
    {"nm": "Campinas",     "votos": "733k",   "c1": 40.8, "c2": 47.2, "c3": 8.2},
    {"nm": "S. Bernardo",  "votos": "541k",   "c1": 55.1, "c2": 34.6, "c3": 6.5},
    {"nm": "Santo André",  "votos": "482k",   "c1": 53.8, "c2": 35.9, "c3": 7.0},
    {"nm": "Ribeirão Pr.", "votos": "422k",   "c1": 38.2, "c2": 49.8, "c3": 8.1},
    {"nm": "Sorocaba",     "votos": "389k",   "c1": 41.5, "c2": 45.9, "c3": 8.4},
    {"nm": "Osasco",       "votos": "375k",   "c1": 58.2, "c2": 30.8, "c3": 6.7},
]


@_fastapi_app.get("/api/municipios")
async def get_municipios(
    cargo: str = Query("Presidente"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
    limit: int = Query(20, ge=1, le=200),
) -> JSONResponse:
    return JSONResponse({"municipios": _MOCK_MUNICIPIOS[:limit]})


# ── Google Trends ─────────────────────────────────────────────────────────


@_fastapi_app.get("/api/trends")
async def get_trends(
    cargo: str = Query("Presidente"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
) -> JSONResponse:
    """
    Busca Google Trends para os candidatos do cargo.
    Usa pytrends real se disponível, senão retorna mock.
    """
    cands = _MOCK_CANDIDATOS.get(cargo, _MOCK_CANDIDATOS["Presidente"])
    keywords = [c["nm"] for c in cands[:3]]
    timeframe = f"{ano}-06-01 {ano}-10-30"

    try:
        df = fetch_trends(keywords, timeframe=timeframe, geo="BR")
        if not df.empty:
            result = {kw: df[kw].tolist() if kw in df.columns else []
                      for kw in keywords}
            return JSONResponse({"labels": df.index.astype(str).tolist(), "series": result})
    except Exception as exc:
        logger.warning("Google Trends real falhou: %s — usando mock", exc)

    # Mock fallback
    months = ["Jun", "Jul", "Ago", "Set", "Out"]
    mock_series = {
        keywords[0]: [42, 48, 55, 62, 80],
        keywords[1]: [58, 62, 60, 65, 72],
    }
    if len(keywords) > 2:
        mock_series[keywords[2]] = [15, 22, 30, 35, 28]
    return JSONResponse({"labels": months, "series": mock_series})


# ── Meta Ads ──────────────────────────────────────────────────────────────


@_fastapi_app.get("/api/meta")
async def get_meta(
    cargo: str = Query("Presidente"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
) -> JSONResponse:
    """
    Busca Meta Ad Library para os candidatos do cargo.
    Requer META_APP_TOKEN no ambiente.
    """
    token = os.environ.get("META_APP_TOKEN", "")
    cands = _MOCK_CANDIDATOS.get(cargo, _MOCK_CANDIDATOS["Presidente"])

    if token:
        results = []
        for c in cands[:4]:
            try:
                df = fetch_meta_ads(c["nm"], access_token=token, country="BR")
                spend = df["spend_upper"].sum() if not df.empty else 0.0
                results.append({"candidato": c["nm"], "gasto_r": spend})
            except Exception as exc:
                logger.warning("Meta Ads %s falhou: %s", c["nm"], exc)
                results.append({"candidato": c["nm"], "gasto_r": 0.0})
        return JSONResponse({"candidatos": results})

    # Mock fallback
    mock = [14200, 22800, 4100, 1900]
    results = [{"candidato": c["nm"], "gasto_r": mock[i]}
               for i, c in enumerate(cands[:4])]
    return JSONResponse({"candidatos": results})


# ── Mapa eleitoral — dados por nível geográfico ───────────────────────────


class NivelGeo(str, Enum):
    nacional  = "nacional"
    regiao    = "regiao"
    uf        = "uf"
    municipio = "municipio"
    zona      = "zona"
    secao     = "secao"


_CARGO_CD = {"Presidente": 1, "Governador": 3, "Senador": 5, "Dep. Federal": 6, "Dep. Estadual": 7}

_UF_IBGE = {
    "AC": "12", "AL": "27", "AP": "16", "AM": "13", "BA": "29", "CE": "23", "DF": "53", "ES": "32",
    "GO": "52", "MA": "21", "MT": "51", "MS": "50", "MG": "31", "PA": "15", "PB": "25", "PR": "41",
    "PE": "26", "PI": "22", "RJ": "33", "RN": "24", "RS": "43", "RO": "11", "RR": "14", "SC": "42",
    "SP": "35", "SE": "28", "TO": "17",
}

_UF_REGIAO = {
    "AC": "Norte", "AM": "Norte", "AP": "Norte", "PA": "Norte", "RO": "Norte", "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste", "PB": "Nordeste",
    "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MS": "Centro-Oeste", "MT": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}

# Cores por partido
_PARTIDO_COR = {
    "PT": "#ef4444", "PL": "#1565c0", "MDB": "#f59e0b", "PSDB": "#00bcd4",
    "Rep": "#7c3aed", "PDT": "#f97316", "PP": "#06b6d4", "PSD": "#84cc16",
    "União": "#8b5cf6", "PSB": "#ec4899", "PSOL": "#ff6b35", "PCdoB": "#dc2626",
}

# Mock data de resultado por UF (Presidente 2022, 1º turno)
_MOCK_MAPA_UF = {
    "SP": {"lider": "Bolsonaro", "partido": "PL", "pct": 47.7, "segundo": "Lula", "pct2": 40.9},
    "RJ": {"lider": "Bolsonaro", "partido": "PL", "pct": 54.1, "segundo": "Lula", "pct2": 37.2},
    "MG": {"lider": "Lula", "partido": "PT", "pct": 48.3, "segundo": "Bolsonaro", "pct2": 44.2},
    "BA": {"lider": "Lula", "partido": "PT", "pct": 73.1, "segundo": "Bolsonaro", "pct2": 19.8},
    "RS": {"lider": "Bolsonaro", "partido": "PL", "pct": 57.7, "segundo": "Lula", "pct2": 32.3},
    "SC": {"lider": "Bolsonaro", "partido": "PL", "pct": 65.5, "segundo": "Lula", "pct2": 24.1},
    "PR": {"lider": "Bolsonaro", "partido": "PL", "pct": 58.2, "segundo": "Lula", "pct2": 30.5},
    "GO": {"lider": "Bolsonaro", "partido": "PL", "pct": 63.2, "segundo": "Lula", "pct2": 26.4},
    "MT": {"lider": "Bolsonaro", "partido": "PL", "pct": 69.3, "segundo": "Lula", "pct2": 21.2},
    "MS": {"lider": "Bolsonaro", "partido": "PL", "pct": 59.8, "segundo": "Lula", "pct2": 30.3},
    "CE": {"lider": "Lula", "partido": "PT", "pct": 76.2, "segundo": "Bolsonaro", "pct2": 17.1},
    "PE": {"lider": "Lula", "partido": "PT", "pct": 72.4, "segundo": "Bolsonaro", "pct2": 20.4},
    "MA": {"lider": "Lula", "partido": "PT", "pct": 75.8, "segundo": "Bolsonaro", "pct2": 16.5},
    "PI": {"lider": "Lula", "partido": "PT", "pct": 74.6, "segundo": "Bolsonaro", "pct2": 17.3},
    "PB": {"lider": "Lula", "partido": "PT", "pct": 70.1, "segundo": "Bolsonaro", "pct2": 22.1},
    "RN": {"lider": "Lula", "partido": "PT", "pct": 68.4, "segundo": "Bolsonaro", "pct2": 23.5},
    "AL": {"lider": "Lula", "partido": "PT", "pct": 71.2, "segundo": "Bolsonaro", "pct2": 21.3},
    "SE": {"lider": "Lula", "partido": "PT", "pct": 64.3, "segundo": "Bolsonaro", "pct2": 27.8},
    "PA": {"lider": "Lula", "partido": "PT", "pct": 60.1, "segundo": "Bolsonaro", "pct2": 31.2},
    "AM": {"lider": "Bolsonaro", "partido": "PL", "pct": 52.7, "segundo": "Lula", "pct2": 38.4},
    "AC": {"lider": "Bolsonaro", "partido": "PL", "pct": 59.1, "segundo": "Lula", "pct2": 33.4},
    "RO": {"lider": "Bolsonaro", "partido": "PL", "pct": 68.4, "segundo": "Lula", "pct2": 23.8},
    "RR": {"lider": "Bolsonaro", "partido": "PL", "pct": 65.2, "segundo": "Lula", "pct2": 26.3},
    "AP": {"lider": "Lula", "partido": "PT", "pct": 51.2, "segundo": "Bolsonaro", "pct2": 41.3},
    "TO": {"lider": "Bolsonaro", "partido": "PL", "pct": 56.4, "segundo": "Lula", "pct2": 34.8},
    "ES": {"lider": "Bolsonaro", "partido": "PL", "pct": 55.8, "segundo": "Lula", "pct2": 35.7},
    "DF": {"lider": "Bolsonaro", "partido": "PL", "pct": 52.1, "segundo": "Lula", "pct2": 37.9},
}

_MOCK_MAPA_REGIAO = {
    "Norte":        {"lider": "Bolsonaro", "partido": "PL", "pct": 55.2, "segundo": "Lula", "pct2": 34.1},
    "Nordeste":     {"lider": "Lula", "partido": "PT", "pct": 71.8, "segundo": "Bolsonaro", "pct2": 20.7},
    "Centro-Oeste": {"lider": "Bolsonaro", "partido": "PL", "pct": 62.4, "segundo": "Lula", "pct2": 27.6},
    "Sudeste":      {"lider": "Bolsonaro", "partido": "PL", "pct": 49.1, "segundo": "Lula", "pct2": 42.3},
    "Sul":          {"lider": "Bolsonaro", "partido": "PL", "pct": 60.2, "segundo": "Lula", "pct2": 29.8},
}


@_fastapi_app.get("/api/mapa/{nivel}")
async def get_mapa(
    nivel: NivelGeo,
    cargo: str = Query("Presidente"),
    ano: int = Query(2022),
    uf: str = Query(None),
    cd_municipio: str = Query(None),
    nr_zona: str = Query(None),
    turno: int = Query(1),
) -> JSONResponse:
    """
    Dados eleitorais por nível geográfico para colorir o choropleth.
    nivel: nacional | regiao | uf | municipio | zona | secao
    Retorna lista de features com: id, label, lider, partido, pct, segundo, pct2, ibge_code
    """
    nivel = nivel.value

    if nivel == "nacional":
        lula_votos = sum(1 for v in _MOCK_MAPA_UF.values() if v["lider"] == "Lula")
        total = len(_MOCK_MAPA_UF)
        return JSONResponse({
            "nivel": "nacional",
            "features": [{
                "id": "BR",
                "label": "Brasil",
                "lider": "Lula" if lula_votos > total / 2 else "Bolsonaro",
                "partido": "PT" if lula_votos > total / 2 else "PL",
                "pct": 50.9,
                "segundo": "Bolsonaro" if lula_votos > total / 2 else "Lula",
                "pct2": 49.1,
                "total_votos": 118228830,
                "ibge_code": "BR",
            }],
        })

    if nivel == "regiao":
        features = [
            {
                "id": reg,
                "label": reg,
                "ibge_code": {"Norte": "1", "Nordeste": "2", "Centro-Oeste": "5", "Sudeste": "3", "Sul": "4"}.get(reg, ""),
                **data,
            }
            for reg, data in _MOCK_MAPA_REGIAO.items()
        ]
        return JSONResponse({"nivel": "regiao", "features": features})

    if nivel == "uf":
        features = [
            {
                "id": uf_key,
                "label": uf_key,
                "ibge_code": _UF_IBGE.get(uf_key, ""),
                "regiao": _UF_REGIAO.get(uf_key, ""),
                **data,
            }
            for uf_key, data in _MOCK_MAPA_UF.items()
        ]
        return JSONResponse({"nivel": "uf", "features": features})

    if nivel == "municipio":
        uf_upper = (uf or "SP").upper()
        mock_muns = [
            {"id": f"{uf_upper}_71072", "cd_municipio": "71072", "label": "São Paulo",
             "lider": "Bolsonaro", "partido": "PL", "pct": 47.7, "segundo": "Lula", "pct2": 40.9, "ibge_code": "3550308"},
            {"id": f"{uf_upper}_69922", "cd_municipio": "69922", "label": "Guarulhos",
             "lider": "Lula", "partido": "PT", "pct": 52.3, "segundo": "Bolsonaro", "pct2": 37.4, "ibge_code": "3518800"},
            {"id": f"{uf_upper}_72843", "cd_municipio": "72843", "label": "Campinas",
             "lider": "Bolsonaro", "partido": "PL", "pct": 47.2, "segundo": "Lula", "pct2": 40.8, "ibge_code": "3509502"},
            {"id": f"{uf_upper}_62910", "cd_municipio": "62910", "label": "Santos",
             "lider": "Lula", "partido": "PT", "pct": 51.1, "segundo": "Bolsonaro", "pct2": 38.2, "ibge_code": "3548100"},
        ]
        return JSONResponse({"nivel": "municipio", "uf": uf_upper, "features": mock_muns})

    if nivel == "zona":
        uf_upper = (uf or "SP").upper()
        mun = cd_municipio or "71072"
        features = [
            {
                "id": f"z{z}", "label": f"Zona {z:03d}", "nr_zona": z,
                "lider": "Bolsonaro" if z % 2 == 0 else "Lula",
                "partido": "PL" if z % 2 == 0 else "PT",
                "pct": round(45 + (z % 10), 1),
                "segundo": "Lula" if z % 2 == 0 else "Bolsonaro",
                "pct2": round(35 + ((z + 3) % 8), 1),
            }
            for z in range(1, 16)
        ]
        return JSONResponse({"nivel": "zona", "uf": uf_upper, "cd_municipio": mun, "features": features})

    if nivel == "secao":
        uf_upper = (uf or "SP").upper()
        mun = cd_municipio or "71072"
        zona = nr_zona or "1"
        import random
        random.seed(42)
        features = [
            {
                "id": f"s{s}", "label": f"Seção {s:04d}", "nr_secao": s,
                "lider": random.choice(["Lula", "Bolsonaro"]),
                "partido": random.choice(["PT", "PL"]),
                "pct": round(random.uniform(35, 65), 1),
                "segundo": "Lula",
                "pct2": round(random.uniform(25, 55), 1),
            }
            for s in range(1, 51)
        ]
        return JSONResponse({
            "nivel": "secao", "uf": uf_upper, "cd_municipio": mun, "nr_zona": zona, "features": features,
        })

    return JSONResponse({"error": "Nível não implementado"}, status_code=400)


# ── WebSocket Chat → Supervisor ────────────────────────────────────────────


@_fastapi_app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    """
    WebSocket que conecta o dashboard HTML ao Supervisor SPEPE real.

    Protocolo (JSON):
      Client → Server: {"type": "message", "text": "...", "session_id": "..."}
      Server → Client: {"type": "chunk",   "text": "..."}   (streaming)
      Server → Client: {"type": "done",    "cost": 0.002, "dashboard_update": {...}}
      Server → Client: {"type": "error",   "message": "..."}

    O campo dashboard_update instrui o frontend a atualizar charts/KPIs.
    O Supervisor pode incluir JSON estruturado no output (detectado por marcador).
    """
    await websocket.accept()
    state = SessionState(session_id=str(uuid.uuid4()))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "JSON inválido"})
                continue

            if msg.get("type") != "message":
                continue

            user_text = msg.get("text", "").strip()
            if not user_text:
                continue

            # Validação de segurança
            check = validate_input_injection(user_text)
            if not check.ok:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Input bloqueado: {check.reason}",
                })
                continue

            # Stream do Supervisor
            full_text = ""
            dashboard_update: dict[str, Any] = {}
            try:
                supervisor = _get_supervisor()
                async for chunk in supervisor.run(user_text, state):
                    full_text += chunk
                    await websocket.send_json({"type": "chunk", "text": chunk})

                # Extrai dashboard_update se o agente emitiu JSON estruturado
                dashboard_update = _extract_dashboard_update(full_text, user_text)

            except Exception as exc:
                logger.error("Supervisor WS erro: %s", exc)
                await websocket.send_json({"type": "error", "message": str(exc)})
                continue

            await websocket.send_json({
                "type": "done",
                "cost": round(state.total_cost_usd, 5),
                "budget_remaining": round(2.0 - state.total_cost_usd, 4),
                "dashboard_update": dashboard_update,
            })

    except WebSocketDisconnect:
        logger.info("WS chat desconectado: %s", state.session_id)


def _extract_dashboard_update(response_text: str, query: str) -> dict[str, Any]:
    """
    Mapeia a resposta do Supervisor → ações de atualização do dashboard.

    Padrões detectados:
    - Menção a candidatos + percentuais → atualiza card-resultado
    - Menção a P(X)=NN% → atualiza card-previsao
    - Menção a "governador" → sugere switchCargo Governador
    - Menção a "trends"/"busca" → sugere highlight card-trends

    Retorna dict com instruções para o frontend JS executar.
    """
    update: dict[str, Any] = {}
    q_lower = query.lower()
    r_lower = response_text.lower()

    # Detecção de cargo
    cargo_map = {
        "governador": "Governador",
        "senador": "Senador",
        "dep. federal": "Dep. Federal",
        "deputado federal": "Dep. Federal",
        "dep. estadual": "Dep. Estadual",
        "deputado estadual": "Dep. Estadual",
        "presidente": "Presidente",
    }
    for keyword, cargo in cargo_map.items():
        if keyword in q_lower or keyword in r_lower:
            update["switchCargo"] = cargo
            break

    # Detecção de previsão
    if "p(" in r_lower and "%" in r_lower:
        update["highlightCard"] = "card-previsao"

    # Detecção de trends
    if any(w in r_lower for w in ["trends", "busca", "google"]):
        update["highlightCard"] = update.get("highlightCard") or "card-trends"

    # Detecção de municípios
    if any(w in q_lower for w in ["município", "cidade", "municipal"]):
        update["highlightCard"] = "card-table"
        update["scrollTo"] = "card-table"

    # Detecção de comparativo
    if any(w in q_lower for w in ["comparar", "comparativo", "todos os cargos"]):
        update["showCompare"] = True

    if update:
        update["syncPulse"] = True

    return update
