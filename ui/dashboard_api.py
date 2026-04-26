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

from fastapi import Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

# Monta sobre o app Chainlit existente
from chainlit.server import app as _fastapi_app

from agents.supervisor import Supervisor
from config.session_state import SessionState
from config.settings import settings
from dataops.clients.digital_client import fetch_meta_ads, fetch_trends
from security.output_validators import validate_input_injection

logger = logging.getLogger("spepe.dashboard_api")


@_fastapi_app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


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
            return JSONResponse(
                {
                    "uid": decoded["uid"],
                    "email": decoded.get("email", ""),
                    "name": decoded.get("name", ""),
                    "plan": "pro",
                }
            )
        except Exception:
            raise HTTPException(status_code=401, detail="Token inválido")

    # Dev/local: stub sem auth
    return JSONResponse({"uid": "demo-user", "email": "", "name": "Demo", "plan": "pro"})


# ── Candidatos por cargo / UF / ano ───────────────────────────────────────


_MOCK_CANDIDATOS: dict[str, list[dict]] = {
    "Presidente": [
        {
            "nm": "Lula",
            "partido": "PT",
            "pct_t1": 43.8,
            "pct_t2": 50.9,
            "votos": "14.2M",
        },
        {
            "nm": "Bolsonaro",
            "partido": "PL",
            "pct_t1": 43.0,
            "pct_t2": 49.1,
            "votos": "13.9M",
        },
        {
            "nm": "Tebet",
            "partido": "MDB",
            "pct_t1": 7.4,
            "pct_t2": None,
            "votos": "2.4M",
        },
        {
            "nm": "Ciro Gomes",
            "partido": "PDT",
            "pct_t1": 3.7,
            "pct_t2": None,
            "votos": "1.2M",
        },
    ],
    "Governador": [
        {
            "nm": "Tarcísio",
            "partido": "Rep",
            "pct_t1": 42.3,
            "pct_t2": 56.1,
            "votos": "13.7M",
        },
        {
            "nm": "Haddad",
            "partido": "PT",
            "pct_t1": 35.7,
            "pct_t2": 43.9,
            "votos": "11.6M",
        },
        {
            "nm": "Rodrigo Garcia",
            "partido": "PSDB",
            "pct_t1": 18.4,
            "pct_t2": None,
            "votos": "5.9M",
        },
    ],
    "Senador": [
        {
            "nm": "Marcos Pontes",
            "partido": "PL",
            "pct_t1": 36.5,
            "pct_t2": None,
            "votos": "11.8M",
        },
        {
            "nm": "Marta Suplicy",
            "partido": "PT",
            "pct_t1": 22.1,
            "pct_t2": None,
            "votos": "7.1M",
        },
        {
            "nm": "José Aníbal",
            "partido": "PSDB",
            "pct_t1": 15.3,
            "pct_t2": None,
            "votos": "4.9M",
        },
    ],
    "Dep. Federal": [
        {
            "nm": "Eduardo Bolsonaro",
            "partido": "PL",
            "pct_t1": 3.8,
            "pct_t2": None,
            "votos": "1.243M",
        },
        {
            "nm": "Guilherme Boulos",
            "partido": "PSB",
            "pct_t1": 1.1,
            "pct_t2": None,
            "votos": "349k",
        },
        {
            "nm": "Tabata Amaral",
            "partido": "PSB",
            "pct_t1": 0.9,
            "pct_t2": None,
            "votos": "288k",
        },
        {
            "nm": "Paulo Teixeira",
            "partido": "PT",
            "pct_t1": 0.8,
            "pct_t2": None,
            "votos": "254k",
        },
        {
            "nm": "Kim Kataguiri",
            "partido": "MDB",
            "pct_t1": 0.7,
            "pct_t2": None,
            "votos": "213k",
        },
    ],
    "Dep. Estadual": [
        {
            "nm": "Douglas Garcia",
            "partido": "PL",
            "pct_t1": 1.8,
            "pct_t2": None,
            "votos": "581k",
        },
        {
            "nm": "Analice Fernandes",
            "partido": "PSDB",
            "pct_t1": 1.4,
            "pct_t2": None,
            "votos": "451k",
        },
        {
            "nm": "Donato",
            "partido": "PT",
            "pct_t1": 1.1,
            "pct_t2": None,
            "votos": "356k",
        },
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
        "Presidente": 1,
        "Governador": 3,
        "Senador": 5,
        "Dep. Federal": 6,
        "Dep. Estadual": 7,
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
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
        ]
    )
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
    return JSONResponse(
        {
            "vencedor": c0["nm"],
            "vencedor_partido": c0["partido"],
            "vencedor_pct": c0["pct_t1"],
            "segundo": c1["nm"],
            "segundo_pct": c1["pct_t1"],
            "margem_pp": round(abs(c0["pct_t1"] - c1["pct_t1"]), 1),
            "total_votos": "32,5M" if uf == "SP" else "—",
            "municipios": 645 if uf == "SP" else 0,
            "dq_score": 98.5,
        }
    )


# ── Municípios ────────────────────────────────────────────────────────────


_MOCK_MUNICIPIOS = [
    {"nm": "São Paulo", "votos": "6.842k", "c1": 44.1, "c2": 43.2, "c3": 7.9},
    {"nm": "Guarulhos", "votos": "984k", "c1": 52.3, "c2": 37.4, "c3": 6.1},
    {"nm": "Campinas", "votos": "733k", "c1": 40.8, "c2": 47.2, "c3": 8.2},
    {"nm": "S. Bernardo", "votos": "541k", "c1": 55.1, "c2": 34.6, "c3": 6.5},
    {"nm": "Santo André", "votos": "482k", "c1": 53.8, "c2": 35.9, "c3": 7.0},
    {"nm": "Ribeirão Pr.", "votos": "422k", "c1": 38.2, "c2": 49.8, "c3": 8.1},
    {"nm": "Sorocaba", "votos": "389k", "c1": 41.5, "c2": 45.9, "c3": 8.4},
    {"nm": "Osasco", "votos": "375k", "c1": 58.2, "c2": 30.8, "c3": 6.7},
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
            result = {kw: df[kw].tolist() if kw in df.columns else [] for kw in keywords}
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
    results = [{"candidato": c["nm"], "gasto_r": mock[i]} for i, c in enumerate(cands[:4])]
    return JSONResponse({"candidatos": results})


# ── Mapa eleitoral — dados por nível geográfico ───────────────────────────


class NivelGeo(str, Enum):
    nacional = "nacional"
    regiao = "regiao"
    uf = "uf"
    municipio = "municipio"
    zona = "zona"
    secao = "secao"


_CARGO_CD = {
    "Presidente": 1,
    "Governador": 3,
    "Senador": 5,
    "Dep. Federal": 6,
    "Dep. Estadual": 7,
}

_UF_IBGE = {
    "AC": "12",
    "AL": "27",
    "AP": "16",
    "AM": "13",
    "BA": "29",
    "CE": "23",
    "DF": "53",
    "ES": "32",
    "GO": "52",
    "MA": "21",
    "MT": "51",
    "MS": "50",
    "MG": "31",
    "PA": "15",
    "PB": "25",
    "PR": "41",
    "PE": "26",
    "PI": "22",
    "RJ": "33",
    "RN": "24",
    "RS": "43",
    "RO": "11",
    "RR": "14",
    "SC": "42",
    "SP": "35",
    "SE": "28",
    "TO": "17",
}

_UF_REGIAO = {
    "AC": "Norte",
    "AM": "Norte",
    "AP": "Norte",
    "PA": "Norte",
    "RO": "Norte",
    "RR": "Norte",
    "TO": "Norte",
    "AL": "Nordeste",
    "BA": "Nordeste",
    "CE": "Nordeste",
    "MA": "Nordeste",
    "PB": "Nordeste",
    "PE": "Nordeste",
    "PI": "Nordeste",
    "RN": "Nordeste",
    "SE": "Nordeste",
    "DF": "Centro-Oeste",
    "GO": "Centro-Oeste",
    "MS": "Centro-Oeste",
    "MT": "Centro-Oeste",
    "ES": "Sudeste",
    "MG": "Sudeste",
    "RJ": "Sudeste",
    "SP": "Sudeste",
    "PR": "Sul",
    "RS": "Sul",
    "SC": "Sul",
}

# Cores por partido
_PARTIDO_COR = {
    "PT": "#ef4444",
    "PL": "#1565c0",
    "MDB": "#f59e0b",
    "PSDB": "#00bcd4",
    "Rep": "#7c3aed",
    "PDT": "#f97316",
    "PP": "#06b6d4",
    "PSD": "#84cc16",
    "União": "#8b5cf6",
    "PSB": "#ec4899",
    "PSOL": "#ff6b35",
    "PCdoB": "#dc2626",
}

# Mock data de resultado por UF (Presidente 2022, 1º turno)
_MOCK_MAPA_UF = {
    "SP": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 47.7,
        "segundo": "Lula",
        "pct2": 40.9,
        "total_votos": 32400000,
        "turnout": 0.812,
    },
    "RJ": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 54.1,
        "segundo": "Lula",
        "pct2": 37.2,
        "total_votos": 8100000,
        "turnout": 0.791,
    },
    "MG": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 48.3,
        "segundo": "Bolsonaro",
        "pct2": 44.2,
        "total_votos": 12100000,
        "turnout": 0.801,
    },
    "BA": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 73.1,
        "segundo": "Bolsonaro",
        "pct2": 19.8,
        "total_votos": 8400000,
        "turnout": 0.818,
    },
    "RS": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 57.7,
        "segundo": "Lula",
        "pct2": 32.3,
        "total_votos": 6400000,
        "turnout": 0.831,
    },
    "SC": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 65.5,
        "segundo": "Lula",
        "pct2": 24.1,
        "total_votos": 3800000,
        "turnout": 0.848,
    },
    "PR": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 58.2,
        "segundo": "Lula",
        "pct2": 30.5,
        "total_votos": 5900000,
        "turnout": 0.838,
    },
    "GO": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 63.2,
        "segundo": "Lula",
        "pct2": 26.4,
    },
    "MT": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 69.3,
        "segundo": "Lula",
        "pct2": 21.2,
    },
    "MS": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 59.8,
        "segundo": "Lula",
        "pct2": 30.3,
    },
    "CE": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 76.2,
        "segundo": "Bolsonaro",
        "pct2": 17.1,
    },
    "PE": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 72.4,
        "segundo": "Bolsonaro",
        "pct2": 20.4,
    },
    "MA": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 75.8,
        "segundo": "Bolsonaro",
        "pct2": 16.5,
    },
    "PI": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 74.6,
        "segundo": "Bolsonaro",
        "pct2": 17.3,
    },
    "PB": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 70.1,
        "segundo": "Bolsonaro",
        "pct2": 22.1,
    },
    "RN": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 68.4,
        "segundo": "Bolsonaro",
        "pct2": 23.5,
    },
    "AL": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 71.2,
        "segundo": "Bolsonaro",
        "pct2": 21.3,
    },
    "SE": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 64.3,
        "segundo": "Bolsonaro",
        "pct2": 27.8,
    },
    "PA": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 60.1,
        "segundo": "Bolsonaro",
        "pct2": 31.2,
    },
    "AM": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 52.7,
        "segundo": "Lula",
        "pct2": 38.4,
    },
    "AC": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 59.1,
        "segundo": "Lula",
        "pct2": 33.4,
    },
    "RO": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 68.4,
        "segundo": "Lula",
        "pct2": 23.8,
    },
    "RR": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 65.2,
        "segundo": "Lula",
        "pct2": 26.3,
    },
    "AP": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 51.2,
        "segundo": "Bolsonaro",
        "pct2": 41.3,
    },
    "TO": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 56.4,
        "segundo": "Lula",
        "pct2": 34.8,
    },
    "ES": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 55.8,
        "segundo": "Lula",
        "pct2": 35.7,
    },
    "DF": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 52.1,
        "segundo": "Lula",
        "pct2": 37.9,
    },
}

_MOCK_MAPA_REGIAO = {
    "Norte": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 55.2,
        "segundo": "Lula",
        "pct2": 34.1,
        "total_votos": 7100000,
        "turnout": 0.768,
    },
    "Nordeste": {
        "lider": "Lula",
        "partido": "PT",
        "pct": 71.8,
        "segundo": "Bolsonaro",
        "pct2": 20.7,
        "total_votos": 26800000,
        "turnout": 0.792,
    },
    "Centro-Oeste": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 62.4,
        "segundo": "Lula",
        "pct2": 27.6,
        "total_votos": 7800000,
        "turnout": 0.801,
    },
    "Sudeste": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 49.1,
        "segundo": "Lula",
        "pct2": 42.3,
        "total_votos": 48200000,
        "turnout": 0.814,
    },
    "Sul": {
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 60.2,
        "segundo": "Lula",
        "pct2": 29.8,
        "total_votos": 16600000,
        "turnout": 0.822,
    },
}

_MOCK_MAPA_MUN_BY_UF: dict[str, list[dict]] = {
    "SP": [
        {
            "ibge_code": "3550308",
            "label": "São Paulo",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 44.1,
            "pct2": 43.2,
            "total_votos": 6842000,
            "turnout": 0.815,
        },
        {
            "ibge_code": "3518800",
            "label": "Guarulhos",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 52.3,
            "pct2": 37.4,
            "total_votos": 984000,
            "turnout": 0.802,
        },
        {
            "ibge_code": "3509502",
            "label": "Campinas",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 47.2,
            "pct2": 40.8,
            "total_votos": 733000,
            "turnout": 0.811,
        },
        {
            "ibge_code": "3548708",
            "label": "S. Bernardo do Campo",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 55.1,
            "pct2": 34.6,
            "total_votos": 541000,
            "turnout": 0.818,
        },
        {
            "ibge_code": "3547809",
            "label": "Santo André",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 53.8,
            "pct2": 35.9,
            "total_votos": 482000,
            "turnout": 0.821,
        },
        {
            "ibge_code": "3543402",
            "label": "Ribeirão Preto",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 49.8,
            "pct2": 38.2,
            "total_votos": 422000,
            "turnout": 0.809,
        },
        {
            "ibge_code": "3534401",
            "label": "Osasco",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 58.2,
            "pct2": 30.8,
            "total_votos": 375000,
            "turnout": 0.807,
        },
        {
            "ibge_code": "3552205",
            "label": "Sorocaba",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 45.9,
            "pct2": 41.5,
            "total_votos": 389000,
            "turnout": 0.813,
        },
    ],
    "MG": [
        {
            "ibge_code": "3106200",
            "label": "Belo Horizonte",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 48.2,
            "pct2": 39.8,
            "total_votos": 1580000,
            "turnout": 0.824,
        },
        {
            "ibge_code": "3170206",
            "label": "Uberlândia",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 50.1,
            "pct2": 38.2,
            "total_votos": 482000,
            "turnout": 0.816,
        },
        {
            "ibge_code": "3118601",
            "label": "Contagem",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 55.4,
            "pct2": 33.8,
            "total_votos": 425000,
            "turnout": 0.808,
        },
        {
            "ibge_code": "3136702",
            "label": "Juiz de Fora",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 44.8,
            "pct2": 42.1,
            "total_votos": 318000,
            "turnout": 0.819,
        },
        {
            "ibge_code": "3106705",
            "label": "Betim",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 58.9,
            "pct2": 30.4,
            "total_votos": 282000,
            "turnout": 0.801,
        },
        {
            "ibge_code": "3143302",
            "label": "Montes Claros",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 59.4,
            "pct2": 29.8,
            "total_votos": 218000,
            "turnout": 0.788,
        },
        {
            "ibge_code": "3156700",
            "label": "Uberaba",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 50.8,
            "pct2": 37.4,
            "total_votos": 192000,
            "turnout": 0.812,
        },
        {
            "ibge_code": "3110004",
            "label": "Caetanópolis",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 52.1,
            "pct2": 36.8,
            "total_votos": 45000,
            "turnout": 0.793,
        },
    ],
    "RJ": [
        {
            "ibge_code": "3304557",
            "label": "Rio de Janeiro",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 52.1,
            "pct2": 39.4,
            "total_votos": 3580000,
            "turnout": 0.798,
        },
        {
            "ibge_code": "3303500",
            "label": "São Gonçalo",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 50.4,
            "pct2": 40.8,
            "total_votos": 512000,
            "turnout": 0.784,
        },
        {
            "ibge_code": "3301702",
            "label": "Duque de Caxias",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 60.2,
            "pct2": 31.4,
            "total_votos": 478000,
            "turnout": 0.779,
        },
        {
            "ibge_code": "3303302",
            "label": "Nova Iguaçu",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 62.1,
            "pct2": 29.8,
            "total_votos": 432000,
            "turnout": 0.773,
        },
        {
            "ibge_code": "3303103",
            "label": "Niterói",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 52.8,
            "pct2": 38.2,
            "total_votos": 298000,
            "turnout": 0.821,
        },
        {
            "ibge_code": "3301009",
            "label": "Belford Roxo",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 68.2,
            "pct2": 24.1,
            "total_votos": 318000,
            "turnout": 0.762,
        },
        {
            "ibge_code": "3306305",
            "label": "Volta Redonda",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 50.9,
            "pct2": 40.1,
            "total_votos": 198000,
            "turnout": 0.803,
        },
        {
            "ibge_code": "3303906",
            "label": "Petrópolis",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 51.2,
            "pct2": 39.8,
            "total_votos": 158000,
            "turnout": 0.812,
        },
    ],
    "BA": [
        {
            "ibge_code": "2927408",
            "label": "Salvador",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 70.2,
            "pct2": 21.8,
            "total_votos": 1350000,
            "turnout": 0.792,
        },
        {
            "ibge_code": "2910800",
            "label": "Feira de Santana",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 65.3,
            "pct2": 26.8,
            "total_votos": 418000,
            "turnout": 0.784,
        },
        {
            "ibge_code": "2933307",
            "label": "Vitória da Conquista",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 67.1,
            "pct2": 25.2,
            "total_votos": 228000,
            "turnout": 0.779,
        },
        {
            "ibge_code": "2914800",
            "label": "Camaçari",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 72.4,
            "pct2": 20.4,
            "total_votos": 168000,
            "turnout": 0.773,
        },
        {
            "ibge_code": "2910727",
            "label": "Ilhéus",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 68.9,
            "pct2": 23.8,
            "total_votos": 152000,
            "turnout": 0.771,
        },
        {
            "ibge_code": "2903201",
            "label": "Barreiras",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 61.2,
            "pct2": 29.8,
            "total_votos": 138000,
            "turnout": 0.768,
        },
        {
            "ibge_code": "2919207",
            "label": "Lauro de Freitas",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 67.8,
            "pct2": 24.4,
            "total_votos": 142000,
            "turnout": 0.781,
        },
        {
            "ibge_code": "2918001",
            "label": "Jequié",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 65.4,
            "pct2": 27.2,
            "total_votos": 112000,
            "turnout": 0.764,
        },
    ],
    "RS": [
        {
            "ibge_code": "4314902",
            "label": "Porto Alegre",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 51.2,
            "pct2": 38.4,
            "total_votos": 802000,
            "turnout": 0.831,
        },
        {
            "ibge_code": "4304606",
            "label": "Caxias do Sul",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 64.2,
            "pct2": 25.8,
            "total_votos": 312000,
            "turnout": 0.842,
        },
        {
            "ibge_code": "4311403",
            "label": "Pelotas",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 50.8,
            "pct2": 39.2,
            "total_votos": 218000,
            "turnout": 0.828,
        },
        {
            "ibge_code": "4313409",
            "label": "Santa Maria",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 50.4,
            "pct2": 38.8,
            "total_votos": 198000,
            "turnout": 0.833,
        },
        {
            "ibge_code": "4307609",
            "label": "Canoas",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 52.1,
            "pct2": 37.4,
            "total_votos": 228000,
            "turnout": 0.819,
        },
        {
            "ibge_code": "4316907",
            "label": "São Leopoldo",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 50.9,
            "pct2": 38.8,
            "total_votos": 148000,
            "turnout": 0.824,
        },
        {
            "ibge_code": "4319901",
            "label": "Viamão",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 58.9,
            "pct2": 31.2,
            "total_votos": 118000,
            "turnout": 0.811,
        },
        {
            "ibge_code": "4309209",
            "label": "Gravataí",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 51.4,
            "pct2": 38.2,
            "total_votos": 132000,
            "turnout": 0.822,
        },
    ],
    "PR": [
        {
            "ibge_code": "4106902",
            "label": "Curitiba",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 52.8,
            "pct2": 35.4,
            "total_votos": 1102000,
            "turnout": 0.828,
        },
        {
            "ibge_code": "4113700",
            "label": "Londrina",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 55.1,
            "pct2": 32.8,
            "total_votos": 378000,
            "turnout": 0.821,
        },
        {
            "ibge_code": "4115200",
            "label": "Maringá",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 60.2,
            "pct2": 28.4,
            "total_votos": 288000,
            "turnout": 0.832,
        },
        {
            "ibge_code": "4104808",
            "label": "Cascavel",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 62.1,
            "pct2": 26.8,
            "total_votos": 198000,
            "turnout": 0.825,
        },
        {
            "ibge_code": "4126272",
            "label": "São José dos Pinhais",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 55.9,
            "pct2": 32.8,
            "total_votos": 248000,
            "turnout": 0.818,
        },
        {
            "ibge_code": "4119905",
            "label": "Ponta Grossa",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 58.4,
            "pct2": 30.4,
            "total_votos": 228000,
            "turnout": 0.822,
        },
        {
            "ibge_code": "4108304",
            "label": "Foz do Iguaçu",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 48.9,
            "pct2": 41.2,
            "total_votos": 188000,
            "turnout": 0.809,
        },
        {
            "ibge_code": "4103800",
            "label": "Campo Largo",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 57.8,
            "pct2": 31.4,
            "total_votos": 78000,
            "turnout": 0.814,
        },
    ],
    "SC": [
        {
            "ibge_code": "4205407",
            "label": "Florianópolis",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 57.2,
            "pct2": 30.8,
            "total_votos": 258000,
            "turnout": 0.851,
        },
        {
            "ibge_code": "4209102",
            "label": "Joinville",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 65.8,
            "pct2": 23.4,
            "total_votos": 348000,
            "turnout": 0.844,
        },
        {
            "ibge_code": "4202404",
            "label": "Blumenau",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 64.2,
            "pct2": 24.8,
            "total_votos": 248000,
            "turnout": 0.848,
        },
        {
            "ibge_code": "4204806",
            "label": "Chapecó",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 66.8,
            "pct2": 22.4,
            "total_votos": 158000,
            "turnout": 0.839,
        },
        {
            "ibge_code": "4216602",
            "label": "São José",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 60.4,
            "pct2": 27.8,
            "total_votos": 128000,
            "turnout": 0.842,
        },
        {
            "ibge_code": "4214805",
            "label": "Palhoça",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 60.2,
            "pct2": 27.8,
            "total_votos": 112000,
            "turnout": 0.838,
        },
        {
            "ibge_code": "4211702",
            "label": "Lages",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 58.9,
            "pct2": 29.8,
            "total_votos": 98000,
            "turnout": 0.831,
        },
        {
            "ibge_code": "4203808",
            "label": "Brusque",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 68.9,
            "pct2": 20.4,
            "total_votos": 78000,
            "turnout": 0.845,
        },
    ],
    "CE": [
        {
            "ibge_code": "2304400",
            "label": "Fortaleza",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 67.8,
            "pct2": 24.8,
            "total_votos": 1282000,
            "turnout": 0.779,
        },
        {
            "ibge_code": "2307304",
            "label": "Caucaia",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 72.4,
            "pct2": 20.4,
            "total_votos": 258000,
            "turnout": 0.768,
        },
        {
            "ibge_code": "2311405",
            "label": "Juazeiro do Norte",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 74.1,
            "pct2": 18.8,
            "total_votos": 198000,
            "turnout": 0.761,
        },
        {
            "ibge_code": "2304459",
            "label": "Maracanaú",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 74.8,
            "pct2": 17.8,
            "total_votos": 148000,
            "turnout": 0.764,
        },
        {
            "ibge_code": "2308401",
            "label": "Sobral",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 70.2,
            "pct2": 22.8,
            "total_votos": 138000,
            "turnout": 0.772,
        },
        {
            "ibge_code": "2308955",
            "label": "Crato",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 68.9,
            "pct2": 23.4,
            "total_votos": 98000,
            "turnout": 0.758,
        },
        {
            "ibge_code": "2318004",
            "label": "Itapipoca",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 71.8,
            "pct2": 21.4,
            "total_votos": 88000,
            "turnout": 0.752,
        },
        {
            "ibge_code": "2307650",
            "label": "Iguatu",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 72.1,
            "pct2": 20.8,
            "total_votos": 78000,
            "turnout": 0.748,
        },
    ],
    "PE": [
        {
            "ibge_code": "2611606",
            "label": "Recife",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 63.8,
            "pct2": 27.8,
            "total_votos": 948000,
            "turnout": 0.784,
        },
        {
            "ibge_code": "2609600",
            "label": "Olinda",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 67.4,
            "pct2": 24.4,
            "total_votos": 248000,
            "turnout": 0.778,
        },
        {
            "ibge_code": "2604106",
            "label": "Caruaru",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 62.1,
            "pct2": 29.8,
            "total_votos": 218000,
            "turnout": 0.771,
        },
        {
            "ibge_code": "2607901",
            "label": "Jaboatão",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 70.8,
            "pct2": 21.4,
            "total_votos": 348000,
            "turnout": 0.769,
        },
        {
            "ibge_code": "2610707",
            "label": "Paulista",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 71.2,
            "pct2": 21.2,
            "total_votos": 238000,
            "turnout": 0.772,
        },
        {
            "ibge_code": "2615904",
            "label": "Petrolina",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 59.8,
            "pct2": 32.4,
            "total_votos": 178000,
            "turnout": 0.768,
        },
        {
            "ibge_code": "2602902",
            "label": "Cabo de Santo Agostinho",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 69.8,
            "pct2": 22.4,
            "total_votos": 158000,
            "turnout": 0.761,
        },
        {
            "ibge_code": "2604154",
            "label": "CARUARU N",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 63.2,
            "pct2": 28.4,
            "total_votos": 48000,
            "turnout": 0.755,
        },
    ],
    "GO": [
        {
            "ibge_code": "5208707",
            "label": "Goiânia",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 52.4,
            "pct2": 36.8,
            "total_votos": 782000,
            "turnout": 0.808,
        },
        {
            "ibge_code": "5201405",
            "label": "Aparecida de Goiânia",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 55.8,
            "pct2": 33.4,
            "total_votos": 428000,
            "turnout": 0.798,
        },
        {
            "ibge_code": "5218805",
            "label": "Anápolis",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 56.8,
            "pct2": 32.4,
            "total_votos": 248000,
            "turnout": 0.803,
        },
        {
            "ibge_code": "5221858",
            "label": "Rio Verde",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 62.4,
            "pct2": 26.8,
            "total_votos": 118000,
            "turnout": 0.812,
        },
        {
            "ibge_code": "5209101",
            "label": "Luziânia",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 54.8,
            "pct2": 34.4,
            "total_votos": 98000,
            "turnout": 0.791,
        },
        {
            "ibge_code": "5219704",
            "label": "Senador Canedo",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 56.8,
            "pct2": 32.4,
            "total_votos": 88000,
            "turnout": 0.797,
        },
        {
            "ibge_code": "5200050",
            "label": "Águas Lindas",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 54.4,
            "pct2": 34.8,
            "total_votos": 78000,
            "turnout": 0.784,
        },
        {
            "ibge_code": "5208004",
            "label": "Formosa",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 58.2,
            "pct2": 30.8,
            "total_votos": 68000,
            "turnout": 0.788,
        },
    ],
    "AC": [
        {
            "ibge_code": "1200401",
            "label": "Rio Branco",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 57.8,
            "pct2": 35.4,
            "total_votos": 178000,
            "turnout": 0.772,
        },
        {
            "ibge_code": "1200054",
            "label": "Cruzeiro do Sul",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 61.2,
            "pct2": 31.8,
            "total_votos": 58000,
            "turnout": 0.759,
        },
        {
            "ibge_code": "1200344",
            "label": "Sena Madureira",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 62.1,
            "pct2": 30.8,
            "total_votos": 28000,
            "turnout": 0.751,
        },
        {
            "ibge_code": "1200179",
            "label": "Feijó",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 60.4,
            "pct2": 32.4,
            "total_votos": 22000,
            "turnout": 0.748,
        },
        {
            "ibge_code": "1200013",
            "label": "Acrelândia",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 55.8,
            "pct2": 36.4,
            "total_votos": 12000,
            "turnout": 0.762,
        },
    ],
    "AL": [
        {
            "ibge_code": "2704302",
            "label": "Maceió",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 68.4,
            "pct2": 24.8,
            "total_votos": 428000,
            "turnout": 0.779,
        },
        {
            "ibge_code": "2702306",
            "label": "Arapiraca",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 65.8,
            "pct2": 27.4,
            "total_votos": 148000,
            "turnout": 0.771,
        },
        {
            "ibge_code": "2707107",
            "label": "Rio Largo",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 70.1,
            "pct2": 22.8,
            "total_votos": 58000,
            "turnout": 0.762,
        },
        {
            "ibge_code": "2704203",
            "label": "Marechal Deodoro",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 69.4,
            "pct2": 23.4,
            "total_votos": 32000,
            "turnout": 0.758,
        },
        {
            "ibge_code": "2704906",
            "label": "Palmeira dos Índios",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 67.2,
            "pct2": 25.8,
            "total_votos": 38000,
            "turnout": 0.764,
        },
    ],
    "AP": [
        {
            "ibge_code": "1600303",
            "label": "Macapá",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 52.8,
            "pct2": 40.4,
            "total_votos": 198000,
            "turnout": 0.771,
        },
        {
            "ibge_code": "1600204",
            "label": "Santana",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 55.4,
            "pct2": 37.8,
            "total_votos": 58000,
            "turnout": 0.762,
        },
        {
            "ibge_code": "1600154",
            "label": "Laranjal do Jari",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 58.1,
            "pct2": 35.4,
            "total_votos": 28000,
            "turnout": 0.748,
        },
        {
            "ibge_code": "1600253",
            "label": "Oiapoque",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 56.8,
            "pct2": 36.8,
            "total_votos": 18000,
            "turnout": 0.744,
        },
    ],
    "AM": [
        {
            "ibge_code": "1302603",
            "label": "Manaus",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 51.4,
            "pct2": 39.8,
            "total_votos": 982000,
            "turnout": 0.778,
        },
        {
            "ibge_code": "1300300",
            "label": "Parintins",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 54.8,
            "pct2": 37.4,
            "total_votos": 78000,
            "turnout": 0.762,
        },
        {
            "ibge_code": "1301902",
            "label": "Itacoatiara",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 52.1,
            "pct2": 39.8,
            "total_votos": 58000,
            "turnout": 0.751,
        },
        {
            "ibge_code": "1302504",
            "label": "Manacapuru",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 53.4,
            "pct2": 38.4,
            "total_votos": 48000,
            "turnout": 0.748,
        },
        {
            "ibge_code": "1303205",
            "label": "Tefé",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 50.8,
            "pct2": 40.8,
            "total_votos": 38000,
            "turnout": 0.742,
        },
    ],
    "DF": [
        {
            "ibge_code": "5300108",
            "label": "Brasília",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 52.1,
            "pct2": 37.9,
            "total_votos": 1352000,
            "turnout": 0.831,
        },
        {
            "ibge_code": "5300108",
            "label": "Ceilândia (RA)",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 55.8,
            "pct2": 35.4,
            "total_votos": 198000,
            "turnout": 0.814,
        },
        {
            "ibge_code": "5300108",
            "label": "Taguatinga (RA)",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 54.2,
            "pct2": 35.8,
            "total_votos": 148000,
            "turnout": 0.822,
        },
        {
            "ibge_code": "5300108",
            "label": "Plano Piloto (RA)",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 62.4,
            "pct2": 28.8,
            "total_votos": 128000,
            "turnout": 0.851,
        },
    ],
    "ES": [
        {
            "ibge_code": "3205309",
            "label": "Vitória",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 52.4,
            "pct2": 37.8,
            "total_votos": 248000,
            "turnout": 0.821,
        },
        {
            "ibge_code": "3201308",
            "label": "Cariacica",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 54.8,
            "pct2": 35.4,
            "total_votos": 198000,
            "turnout": 0.809,
        },
        {
            "ibge_code": "3205200",
            "label": "Vila Velha",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 56.8,
            "pct2": 33.4,
            "total_votos": 228000,
            "turnout": 0.818,
        },
        {
            "ibge_code": "3205010",
            "label": "Serra",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 55.4,
            "pct2": 34.8,
            "total_votos": 248000,
            "turnout": 0.812,
        },
        {
            "ibge_code": "3202405",
            "label": "Linhares",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 58.8,
            "pct2": 31.2,
            "total_votos": 98000,
            "turnout": 0.808,
        },
    ],
    "MA": [
        {
            "ibge_code": "2111300",
            "label": "São Luís",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 72.4,
            "pct2": 20.8,
            "total_votos": 648000,
            "turnout": 0.779,
        },
        {
            "ibge_code": "2103000",
            "label": "Caxias",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 74.8,
            "pct2": 18.4,
            "total_votos": 148000,
            "turnout": 0.762,
        },
        {
            "ibge_code": "2105302",
            "label": "Imperatriz",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 68.4,
            "pct2": 24.8,
            "total_votos": 198000,
            "turnout": 0.771,
        },
        {
            "ibge_code": "2114007",
            "label": "Timon",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 70.1,
            "pct2": 22.8,
            "total_votos": 88000,
            "turnout": 0.758,
        },
        {
            "ibge_code": "2112209",
            "label": "Santa Inês",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 72.8,
            "pct2": 20.4,
            "total_votos": 68000,
            "turnout": 0.752,
        },
    ],
    "MT": [
        {
            "ibge_code": "5103403",
            "label": "Cuiabá",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 65.4,
            "pct2": 25.8,
            "total_votos": 318000,
            "turnout": 0.808,
        },
        {
            "ibge_code": "5107602",
            "label": "Várzea Grande",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 63.8,
            "pct2": 27.4,
            "total_votos": 148000,
            "turnout": 0.798,
        },
        {
            "ibge_code": "5107040",
            "label": "Sinop",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 72.4,
            "pct2": 19.8,
            "total_votos": 108000,
            "turnout": 0.812,
        },
        {
            "ibge_code": "5103502",
            "label": "Tangará da Serra",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 70.8,
            "pct2": 21.4,
            "total_votos": 78000,
            "turnout": 0.804,
        },
        {
            "ibge_code": "5101803",
            "label": "Rondonópolis",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 68.4,
            "pct2": 23.8,
            "total_votos": 128000,
            "turnout": 0.799,
        },
    ],
    "MS": [
        {
            "ibge_code": "5002704",
            "label": "Campo Grande",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 57.4,
            "pct2": 32.8,
            "total_votos": 448000,
            "turnout": 0.811,
        },
        {
            "ibge_code": "5003207",
            "label": "Dourados",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 58.8,
            "pct2": 31.4,
            "total_votos": 148000,
            "turnout": 0.804,
        },
        {
            "ibge_code": "5007802",
            "label": "Três Lagoas",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 60.4,
            "pct2": 29.8,
            "total_votos": 88000,
            "turnout": 0.808,
        },
        {
            "ibge_code": "5006309",
            "label": "Corumbá",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 51.4,
            "pct2": 39.8,
            "total_votos": 78000,
            "turnout": 0.797,
        },
        {
            "ibge_code": "5007208",
            "label": "Ponta Porã",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 62.4,
            "pct2": 28.4,
            "total_votos": 68000,
            "turnout": 0.801,
        },
    ],
    "PA": [
        {
            "ibge_code": "1501402",
            "label": "Belém",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 58.4,
            "pct2": 33.8,
            "total_votos": 748000,
            "turnout": 0.782,
        },
        {
            "ibge_code": "1506807",
            "label": "Ananindeua",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 62.8,
            "pct2": 29.4,
            "total_votos": 298000,
            "turnout": 0.771,
        },
        {
            "ibge_code": "1508100",
            "label": "Santarém",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 54.8,
            "pct2": 37.4,
            "total_votos": 198000,
            "turnout": 0.764,
        },
        {
            "ibge_code": "1500800",
            "label": "Abaetetuba",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 60.4,
            "pct2": 31.8,
            "total_votos": 98000,
            "turnout": 0.752,
        },
        {
            "ibge_code": "1502202",
            "label": "Castanhal",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 57.8,
            "pct2": 34.4,
            "total_votos": 88000,
            "turnout": 0.758,
        },
        {
            "ibge_code": "1505502",
            "label": "Marabá",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 55.4,
            "pct2": 36.8,
            "total_votos": 148000,
            "turnout": 0.761,
        },
    ],
    "PB": [
        {
            "ibge_code": "2507507",
            "label": "João Pessoa",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 65.4,
            "pct2": 27.8,
            "total_votos": 448000,
            "turnout": 0.784,
        },
        {
            "ibge_code": "2504009",
            "label": "Campina Grande",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 61.8,
            "pct2": 30.4,
            "total_votos": 248000,
            "turnout": 0.778,
        },
        {
            "ibge_code": "2510808",
            "label": "Patos",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 63.4,
            "pct2": 28.8,
            "total_votos": 78000,
            "turnout": 0.764,
        },
        {
            "ibge_code": "2513703",
            "label": "Santa Rita",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 67.8,
            "pct2": 24.4,
            "total_votos": 68000,
            "turnout": 0.758,
        },
        {
            "ibge_code": "2507200",
            "label": "Bayeux",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 69.2,
            "pct2": 23.2,
            "total_votos": 58000,
            "turnout": 0.752,
        },
    ],
    "PI": [
        {
            "ibge_code": "2211001",
            "label": "Teresina",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 68.8,
            "pct2": 24.4,
            "total_votos": 448000,
            "turnout": 0.779,
        },
        {
            "ibge_code": "2203909",
            "label": "Parnaíba",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 70.4,
            "pct2": 22.8,
            "total_votos": 98000,
            "turnout": 0.768,
        },
        {
            "ibge_code": "2201101",
            "label": "Picos",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 72.8,
            "pct2": 20.4,
            "total_votos": 78000,
            "turnout": 0.762,
        },
        {
            "ibge_code": "2209070",
            "label": "Floriano",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 71.4,
            "pct2": 21.8,
            "total_votos": 58000,
            "turnout": 0.758,
        },
        {
            "ibge_code": "2207702",
            "label": "Campo Maior",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 73.2,
            "pct2": 19.8,
            "total_votos": 48000,
            "turnout": 0.751,
        },
    ],
    "RN": [
        {
            "ibge_code": "2408102",
            "label": "Natal",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 65.4,
            "pct2": 27.8,
            "total_votos": 448000,
            "turnout": 0.784,
        },
        {
            "ibge_code": "2403251",
            "label": "Mossoró",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 62.8,
            "pct2": 30.4,
            "total_votos": 148000,
            "turnout": 0.778,
        },
        {
            "ibge_code": "2401305",
            "label": "Caicó",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 64.2,
            "pct2": 29.2,
            "total_votos": 48000,
            "turnout": 0.762,
        },
        {
            "ibge_code": "2403103",
            "label": "Macaíba",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 66.4,
            "pct2": 26.8,
            "total_votos": 38000,
            "turnout": 0.755,
        },
        {
            "ibge_code": "2410306",
            "label": "Parnamirim",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 60.4,
            "pct2": 31.8,
            "total_votos": 108000,
            "turnout": 0.781,
        },
    ],
    "RR": [
        {
            "ibge_code": "1400100",
            "label": "Boa Vista",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 62.4,
            "pct2": 30.8,
            "total_votos": 148000,
            "turnout": 0.779,
        },
        {
            "ibge_code": "1400209",
            "label": "Rorainópolis",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 65.8,
            "pct2": 27.4,
            "total_votos": 28000,
            "turnout": 0.762,
        },
        {
            "ibge_code": "1400027",
            "label": "Alto Alegre",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 54.2,
            "pct2": 38.8,
            "total_votos": 12000,
            "turnout": 0.748,
        },
    ],
    "RO": [
        {
            "ibge_code": "1100205",
            "label": "Porto Velho",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 65.8,
            "pct2": 26.4,
            "total_votos": 248000,
            "turnout": 0.779,
        },
        {
            "ibge_code": "1100122",
            "label": "Ji-Paraná",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 70.4,
            "pct2": 22.8,
            "total_votos": 78000,
            "turnout": 0.768,
        },
        {
            "ibge_code": "1100023",
            "label": "Ariquemes",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 68.8,
            "pct2": 24.4,
            "total_votos": 68000,
            "turnout": 0.762,
        },
        {
            "ibge_code": "1101005",
            "label": "Vilhena",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 72.4,
            "pct2": 20.8,
            "total_votos": 58000,
            "turnout": 0.758,
        },
        {
            "ibge_code": "1101401",
            "label": "Cacoal",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 69.8,
            "pct2": 23.2,
            "total_votos": 48000,
            "turnout": 0.752,
        },
    ],
    "SE": [
        {
            "ibge_code": "2800308",
            "label": "Aracaju",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 62.4,
            "pct2": 30.8,
            "total_votos": 298000,
            "turnout": 0.788,
        },
        {
            "ibge_code": "2804805",
            "label": "Lagarto",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 65.8,
            "pct2": 27.4,
            "total_votos": 68000,
            "turnout": 0.776,
        },
        {
            "ibge_code": "2802106",
            "label": "Itabaiana",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 63.2,
            "pct2": 29.8,
            "total_votos": 48000,
            "turnout": 0.769,
        },
        {
            "ibge_code": "2807006",
            "label": "Nossa Senhora do Socorro",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 67.4,
            "pct2": 25.4,
            "total_votos": 78000,
            "turnout": 0.772,
        },
        {
            "ibge_code": "2807204",
            "label": "São Cristóvão",
            "lider": "Lula",
            "segundo": "Bolsonaro",
            "partido": "PT",
            "pct": 64.8,
            "pct2": 28.2,
            "total_votos": 38000,
            "turnout": 0.762,
        },
    ],
    "TO": [
        {
            "ibge_code": "1721000",
            "label": "Palmas",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 52.8,
            "pct2": 39.4,
            "total_votos": 148000,
            "turnout": 0.808,
        },
        {
            "ibge_code": "1713700",
            "label": "Gurupi",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 56.4,
            "pct2": 35.8,
            "total_votos": 48000,
            "turnout": 0.798,
        },
        {
            "ibge_code": "1716505",
            "label": "Paraíso do Tocantins",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 58.8,
            "pct2": 33.4,
            "total_votos": 38000,
            "turnout": 0.792,
        },
        {
            "ibge_code": "1714203",
            "label": "Araguaína",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 54.4,
            "pct2": 37.8,
            "total_votos": 98000,
            "turnout": 0.788,
        },
        {
            "ibge_code": "1718840",
            "label": "Miracema do Tocantins",
            "lider": "Bolsonaro",
            "segundo": "Lula",
            "partido": "PL",
            "pct": 57.2,
            "pct2": 35.2,
            "total_votos": 22000,
            "turnout": 0.781,
        },
    ],
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
    layer: str = Query("electoral"),
) -> JSONResponse:
    """
    Dados eleitorais por nível geográfico para colorir o choropleth.
    nivel: nacional | regiao | uf | municipio | zona | secao
    layer: electoral | archetype | security | health | economic
    Retorna lista de features com: id, label, lider, partido, pct, segundo, pct2, ibge_code,
                                   total_votos, turnout (e indicadores temáticos por layer)
    """
    nivel_str = nivel.value

    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            if nivel_str == "regiao":
                features = await _bq_mapa_regiao(cargo, ano, turno)
                return JSONResponse({"nivel": "regiao", "features": features})
            if nivel_str == "uf":
                features = await _bq_mapa_uf(cargo, ano, turno)
                return JSONResponse({"nivel": "uf", "features": features})
            if nivel_str == "municipio":
                uf_upper = (uf or "SP").upper()
                features = await _bq_mapa_municipio(uf_upper, cargo, ano, turno)
                return JSONResponse({"nivel": "municipio", "uf": uf_upper, "features": features})
            if nivel_str == "zona":
                uf_upper = (uf or "SP").upper()
                mun = cd_municipio or ""
                features = await _bq_mapa_zona(uf_upper, mun, cargo, ano, turno)
                return JSONResponse(
                    {"nivel": "zona", "uf": uf_upper, "cd_municipio": mun, "features": features}
                )
            if nivel_str == "secao":
                uf_upper = (uf or "SP").upper()
                mun = cd_municipio or ""
                zona = nr_zona or "1"
                features = await _bq_mapa_secao(uf_upper, mun, zona, cargo, ano, turno)
                return JSONResponse(
                    {
                        "nivel": "secao",
                        "uf": uf_upper,
                        "cd_municipio": mun,
                        "nr_zona": zona,
                        "features": features,
                    }
                )
        except Exception as exc:
            logger.warning("BigQuery mapa %s falhou, usando mock: %s", nivel_str, exc)

    if nivel_str == "nacional":
        return JSONResponse(
            {
                "nivel": "nacional",
                "layer": layer,
                "features": [
                    {
                        "id": "BR",
                        "label": "Brasil",
                        "lider": "Lula",
                        "partido": "PT",
                        "pct": 50.9,
                        "segundo": "Bolsonaro",
                        "pct2": 49.1,
                        "total_votos": 118228830,
                        "turnout": 0.799,
                        "ibge_code": "BR",
                    }
                ],
            }
        )

    if nivel_str == "regiao":
        _REGIAO_CODE = {
            "Norte": "1",
            "Nordeste": "2",
            "Centro-Oeste": "5",
            "Sudeste": "3",
            "Sul": "4",
        }
        features = [
            {
                "id": reg,
                "label": reg,
                "ibge_code": _REGIAO_CODE.get(reg, ""),
                **data,
            }
            for reg, data in _MOCK_MAPA_REGIAO.items()
        ]
        return JSONResponse({"nivel": "regiao", "layer": layer, "features": features})

    if nivel_str == "uf":
        uf_total_votos = {
            "SP": 26000000,
            "MG": 11200000,
            "RJ": 8400000,
            "BA": 8100000,
            "RS": 6600000,
            "PR": 6200000,
            "CE": 4200000,
            "PE": 4800000,
            "PA": 3480000,
            "SC": 3800000,
            "GO": 3100000,
            "MA": 3000000,
            "MT": 1680000,
            "ES": 1850000,
            "PB": 2020000,
            "PI": 1480000,
            "RN": 1680000,
            "AL": 1460000,
            "SE": 1150000,
            "AM": 1580000,
            "RO": 780000,
            "MS": 1320000,
            "TO": 760000,
            "AC": 390000,
            "AP": 360000,
            "RR": 230000,
            "DF": 1350000,
        }
        uf_turnout = {
            "SP": 0.814,
            "MG": 0.818,
            "RJ": 0.798,
            "BA": 0.792,
            "RS": 0.831,
            "PR": 0.821,
            "CE": 0.779,
            "PE": 0.784,
            "PA": 0.782,
            "SC": 0.844,
            "GO": 0.808,
            "MA": 0.779,
            "MT": 0.808,
            "ES": 0.821,
            "PB": 0.784,
            "PI": 0.779,
            "RN": 0.784,
            "AL": 0.779,
            "SE": 0.788,
            "AM": 0.778,
            "RO": 0.779,
            "MS": 0.811,
            "TO": 0.808,
            "AC": 0.772,
            "AP": 0.771,
            "RR": 0.779,
            "DF": 0.831,
        }
        features = [
            {
                "id": uf_key,
                "label": uf_key,
                "ibge_code": _UF_IBGE.get(uf_key, ""),
                "regiao": _UF_REGIAO.get(uf_key, ""),
                "total_votos": uf_total_votos.get(uf_key, 500000),
                "turnout": uf_turnout.get(uf_key, 0.80),
                **data,
            }
            for uf_key, data in _MOCK_MAPA_UF.items()
        ]
        return JSONResponse({"nivel": "uf", "layer": layer, "features": features})

    if nivel_str == "municipio":
        uf_upper = (uf or "SP").upper()
        muns_raw = _MOCK_MAPA_MUN_BY_UF.get(uf_upper)
        if not muns_raw:
            uf_fallback = next(iter(_MOCK_MAPA_MUN_BY_UF), "SP")
            muns_raw = _MOCK_MAPA_MUN_BY_UF[uf_fallback]
        features = [
            {
                "id": m["ibge_code"],
                "cd_municipio": m["ibge_code"],
                **m,
            }
            for m in muns_raw
        ]
        return JSONResponse(
            {"nivel": "municipio", "uf": uf_upper, "layer": layer, "features": features}
        )

    if nivel_str == "zona":
        import random as _rand

        uf_upper = (uf or "SP").upper()
        mun = cd_municipio or "3550308"
        uf_data = _MOCK_MAPA_UF.get(uf_upper, _MOCK_MAPA_UF["SP"])
        lider_uf = uf_data["lider"]
        segundo_uf = uf_data["segundo"]
        partido_uf = uf_data["partido"]
        base_pct = uf_data["pct"]
        base_pct2 = uf_data["pct2"]
        _rand.seed(hash(mun) % 9999)
        num_zonas = _rand.randint(15, 25)
        features = []
        for z in range(1, num_zonas + 1):
            delta = _rand.uniform(-7.0, 7.0)
            pct = round(min(78.0, max(32.0, base_pct + delta)), 1)
            pct2 = round(min(68.0, max(20.0, base_pct2 - delta * 0.6)), 1)
            total_v = _rand.randint(18000, 95000)
            turnout = round(_rand.uniform(0.74, 0.88), 3)
            lider = lider_uf if pct > pct2 else segundo_uf
            partido = partido_uf if lider == lider_uf else ("PL" if partido_uf == "PT" else "PT")
            segundo = segundo_uf if lider == lider_uf else lider_uf
            features.append(
                {
                    "id": f"z{z:03d}",
                    "label": f"Zona {z:03d}",
                    "nr_zona": z,
                    "lider": lider,
                    "partido": partido,
                    "segundo": segundo,
                    "pct": pct,
                    "pct2": pct2,
                    "total_votos": total_v,
                    "turnout": turnout,
                    "ibge_code": f"{mun}_z{z:03d}",
                }
            )
        return JSONResponse(
            {
                "nivel": "zona",
                "uf": uf_upper,
                "cd_municipio": mun,
                "layer": layer,
                "features": features,
            }
        )

    if nivel_str == "secao":
        import random as _rand

        uf_upper = (uf or "SP").upper()
        mun = cd_municipio or "3550308"
        zona = nr_zona or "1"
        uf_data = _MOCK_MAPA_UF.get(uf_upper, _MOCK_MAPA_UF["SP"])
        lider_uf = uf_data["lider"]
        segundo_uf = uf_data["segundo"]
        partido_uf = uf_data["partido"]
        base_pct = uf_data["pct"]
        base_pct2 = uf_data["pct2"]
        _rand.seed(hash(f"{mun}_{zona}") % 9999)
        num_secoes = _rand.randint(50, 150)
        features = []
        for s in range(1, num_secoes + 1):
            delta = _rand.uniform(-12.0, 12.0)
            pct = round(min(88.0, max(22.0, base_pct + delta)), 1)
            pct2 = round(min(78.0, max(12.0, base_pct2 - delta * 0.5)), 1)
            total_v = _rand.randint(200, 500)
            turnout = round(_rand.uniform(0.70, 0.92), 3)
            lider = lider_uf if pct > pct2 else segundo_uf
            partido = partido_uf if lider == lider_uf else ("PL" if partido_uf == "PT" else "PT")
            segundo = segundo_uf if lider == lider_uf else lider_uf
            features.append(
                {
                    "id": f"s{s:04d}",
                    "label": f"Seção {s:04d}",
                    "nr_secao": s,
                    "lider": lider,
                    "partido": partido,
                    "segundo": segundo,
                    "pct": pct,
                    "pct2": pct2,
                    "total_votos": total_v,
                    "turnout": turnout,
                    "ibge_code": f"{mun}_z{zona}_s{s:04d}",
                }
            )
        return JSONResponse(
            {
                "nivel": "secao",
                "uf": uf_upper,
                "cd_municipio": mun,
                "nr_zona": zona,
                "layer": layer,
                "features": features,
            }
        )

    return JSONResponse({"error": "Nível não implementado"}, status_code=400)


async def _bq_mapa_regiao(cargo: str, ano: int, turno: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _CARGO_CD.get(cargo, 1)
    _REGIAO_CODE = {"Norte": "1", "Nordeste": "2", "Centro-Oeste": "5", "Sudeste": "3", "Sul": "4"}
    _UF_TO_REGIAO = {
        "AC": "Norte",
        "AM": "Norte",
        "AP": "Norte",
        "PA": "Norte",
        "RO": "Norte",
        "RR": "Norte",
        "TO": "Norte",
        "AL": "Nordeste",
        "BA": "Nordeste",
        "CE": "Nordeste",
        "MA": "Nordeste",
        "PB": "Nordeste",
        "PE": "Nordeste",
        "PI": "Nordeste",
        "RN": "Nordeste",
        "SE": "Nordeste",
        "DF": "Centro-Oeste",
        "GO": "Centro-Oeste",
        "MS": "Centro-Oeste",
        "MT": "Centro-Oeste",
        "ES": "Sudeste",
        "MG": "Sudeste",
        "RJ": "Sudeste",
        "SP": "Sudeste",
        "PR": "Sul",
        "RS": "Sul",
        "SC": "Sul",
    }
    case_expr = (
        "CASE sg_uf "
        + " ".join(f"WHEN '{uf}' THEN '{reg}'" for uf, reg in _UF_TO_REGIAO.items())
        + " ELSE 'Outro' END"
    )
    query = f"""
        SELECT
            {case_expr} AS regiao,
            APPROX_TOP_COUNT(nm_candidato, 1)[OFFSET(0)].value AS lider,
            APPROX_TOP_COUNT(sg_partido, 1)[OFFSET(0)].value AS partido,
            ROUND(MAX(CASE WHEN RANK() OVER (PARTITION BY {case_expr} ORDER BY SUM(qt_votos) DESC) = 1 THEN SUM(qt_votos) END) /
                  SUM(SUM(qt_votos)) OVER (PARTITION BY {case_expr}) * 100, 1) AS pct,
            SUM(qt_votos) AS total_votos
        FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_eleicao`
        WHERE ano_eleicao = @ano AND cd_cargo = @cd_cargo AND nr_turno = @turno
        GROUP BY regiao
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("turno", "INT64", turno),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [
        {
            "id": r["regiao"],
            "label": r["regiao"],
            "ibge_code": _REGIAO_CODE.get(r["regiao"], ""),
            "lider": r["lider"],
            "partido": r["partido"],
            "pct": r["pct"],
            "total_votos": r["total_votos"],
            "segundo": "",
            "pct2": 0.0,
        }
        for r in rows
    ]


async def _bq_mapa_uf(cargo: str, ano: int, turno: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _CARGO_CD.get(cargo, 1)
    query = f"""
        WITH ranked AS (
            SELECT sg_uf, nm_candidato, sg_partido,
                   SUM(qt_votos) AS votos,
                   RANK() OVER (PARTITION BY sg_uf ORDER BY SUM(qt_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_eleicao`
            WHERE ano_eleicao = @ano AND cd_cargo = @cd_cargo AND nr_turno = @turno
            GROUP BY sg_uf, nm_candidato, sg_partido
        ),
        totais AS (
            SELECT sg_uf, SUM(votos) AS total_votos FROM ranked GROUP BY sg_uf
        )
        SELECT r1.sg_uf, r1.nm_candidato AS lider, r1.sg_partido AS partido,
               ROUND(r1.votos / t.total_votos * 100, 1) AS pct,
               r2.nm_candidato AS segundo, ROUND(r2.votos / t.total_votos * 100, 1) AS pct2,
               t.total_votos
        FROM ranked r1
        JOIN ranked r2 ON r1.sg_uf = r2.sg_uf AND r2.rnk = 2
        JOIN totais t ON r1.sg_uf = t.sg_uf
        WHERE r1.rnk = 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("turno", "INT64", turno),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [
        {
            "id": r["sg_uf"],
            "label": r["sg_uf"],
            "ibge_code": _UF_IBGE.get(r["sg_uf"], ""),
            "regiao": _UF_REGIAO.get(r["sg_uf"], ""),
            "lider": r["lider"],
            "partido": r["partido"],
            "pct": r["pct"],
            "segundo": r["segundo"],
            "pct2": r["pct2"],
            "total_votos": r["total_votos"],
            "turnout": 0.80,
        }
        for r in rows
    ]


async def _bq_mapa_municipio(uf: str, cargo: str, ano: int, turno: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _CARGO_CD.get(cargo, 1)
    query = f"""
        WITH ranked AS (
            SELECT cd_municipio, nm_municipio, nm_candidato, sg_partido,
                   SUM(qt_votos) AS votos,
                   RANK() OVER (PARTITION BY cd_municipio ORDER BY SUM(qt_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_eleicao`
            WHERE sg_uf = @uf AND ano_eleicao = @ano AND cd_cargo = @cd_cargo AND nr_turno = @turno
            GROUP BY cd_municipio, nm_municipio, nm_candidato, sg_partido
        ),
        totais AS (
            SELECT cd_municipio, SUM(votos) AS total_votos FROM ranked GROUP BY cd_municipio
        )
        SELECT r1.cd_municipio, r1.nm_municipio, r1.nm_candidato AS lider, r1.sg_partido AS partido,
               ROUND(r1.votos / t.total_votos * 100, 1) AS pct,
               r2.nm_candidato AS segundo, ROUND(r2.votos / t.total_votos * 100, 1) AS pct2,
               t.total_votos
        FROM ranked r1
        JOIN ranked r2 ON r1.cd_municipio = r2.cd_municipio AND r2.rnk = 2
        JOIN totais t ON r1.cd_municipio = t.cd_municipio
        WHERE r1.rnk = 1
        ORDER BY t.total_votos DESC
        LIMIT 50
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("uf", "STRING", uf),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("turno", "INT64", turno),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [
        {
            "id": str(r["cd_municipio"]),
            "cd_municipio": str(r["cd_municipio"]),
            "ibge_code": str(r["cd_municipio"]),
            "label": r["nm_municipio"],
            "lider": r["lider"],
            "partido": r["partido"],
            "pct": r["pct"],
            "segundo": r["segundo"],
            "pct2": r["pct2"],
            "total_votos": r["total_votos"],
            "turnout": 0.80,
        }
        for r in rows
    ]


async def _bq_mapa_zona(uf: str, cd_municipio: str, cargo: str, ano: int, turno: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _CARGO_CD.get(cargo, 1)
    query = f"""
        WITH ranked AS (
            SELECT nr_zona, nm_candidato, sg_partido,
                   SUM(qt_votos) AS votos,
                   RANK() OVER (PARTITION BY nr_zona ORDER BY SUM(qt_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.spepe_silver.tse_{uf.lower()}_{ano}`
            WHERE cd_municipio = @cd_municipio AND cd_cargo = @cd_cargo AND nr_turno = @turno
            GROUP BY nr_zona, nm_candidato, sg_partido
        ),
        totais AS (
            SELECT nr_zona, SUM(votos) AS total_votos FROM ranked GROUP BY nr_zona
        )
        SELECT r1.nr_zona, r1.nm_candidato AS lider, r1.sg_partido AS partido,
               ROUND(r1.votos / t.total_votos * 100, 1) AS pct,
               r2.nm_candidato AS segundo, ROUND(r2.votos / t.total_votos * 100, 1) AS pct2,
               t.total_votos
        FROM ranked r1
        JOIN ranked r2 ON r1.nr_zona = r2.nr_zona AND r2.rnk = 2
        JOIN totais t ON r1.nr_zona = t.nr_zona
        WHERE r1.rnk = 1
        ORDER BY r1.nr_zona
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("cd_municipio", "STRING", cd_municipio),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("turno", "INT64", turno),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [
        {
            "id": f"z{r['nr_zona']:03d}",
            "label": f"Zona {r['nr_zona']:03d}",
            "nr_zona": r["nr_zona"],
            "ibge_code": f"{cd_municipio}_z{r['nr_zona']:03d}",
            "lider": r["lider"],
            "partido": r["partido"],
            "pct": r["pct"],
            "segundo": r["segundo"],
            "pct2": r["pct2"],
            "total_votos": r["total_votos"],
            "turnout": 0.80,
        }
        for r in rows
    ]


async def _bq_mapa_secao(
    uf: str, cd_municipio: str, nr_zona: str, cargo: str, ano: int, turno: int
) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _CARGO_CD.get(cargo, 1)
    query = f"""
        WITH ranked AS (
            SELECT nr_secao, nm_candidato, sg_partido,
                   SUM(qt_votos) AS votos,
                   RANK() OVER (PARTITION BY nr_secao ORDER BY SUM(qt_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.spepe_silver.tse_{uf.lower()}_{ano}`
            WHERE cd_municipio = @cd_municipio AND nr_zona = @nr_zona
              AND cd_cargo = @cd_cargo AND nr_turno = @turno
            GROUP BY nr_secao, nm_candidato, sg_partido
        ),
        totais AS (
            SELECT nr_secao, SUM(votos) AS total_votos FROM ranked GROUP BY nr_secao
        )
        SELECT r1.nr_secao, r1.nm_candidato AS lider, r1.sg_partido AS partido,
               ROUND(r1.votos / t.total_votos * 100, 1) AS pct,
               r2.nm_candidato AS segundo, ROUND(r2.votos / t.total_votos * 100, 1) AS pct2,
               t.total_votos
        FROM ranked r1
        JOIN ranked r2 ON r1.nr_secao = r2.nr_secao AND r2.rnk = 2
        JOIN totais t ON r1.nr_secao = t.nr_secao
        WHERE r1.rnk = 1
        ORDER BY r1.nr_secao
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("cd_municipio", "STRING", cd_municipio),
            bigquery.ScalarQueryParameter("nr_zona", "INT64", int(nr_zona)),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("turno", "INT64", turno),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [
        {
            "id": f"s{r['nr_secao']:04d}",
            "label": f"Seção {r['nr_secao']:04d}",
            "nr_secao": r["nr_secao"],
            "ibge_code": f"{cd_municipio}_z{nr_zona}_s{r['nr_secao']:04d}",
            "lider": r["lider"],
            "partido": r["partido"],
            "pct": r["pct"],
            "segundo": r["segundo"],
            "pct2": r["pct2"],
            "total_votos": r["total_votos"],
            "turnout": 0.80,
        }
        for r in rows
    ]


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
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Input bloqueado: {check.reason}",
                    }
                )
                continue

            # Stream do Supervisor
            full_text = ""
            intent_sink: list[dict[str, Any]] = []
            try:
                supervisor = _get_supervisor()
                async for chunk in supervisor.run(user_text, state, _intent_sink=intent_sink):
                    full_text += chunk
                    await websocket.send_json({"type": "chunk", "text": chunk})

            except Exception as exc:
                logger.error("Supervisor WS erro: %s", exc)
                await websocket.send_json({"type": "error", "message": str(exc)})
                continue

            # Prefer structured intent from emit_dashboard_intent tool; fall back to heuristic
            if intent_sink:
                last_intent = intent_sink[-1]
                dashboard_update: dict[str, Any] = {
                    "intent_schema": "v1",
                    "actions": last_intent.get("actions", []),
                    "narration": last_intent.get("narration", ""),
                }
            else:
                dashboard_update = _extract_dashboard_update(full_text, user_text)

            await websocket.send_json(
                {
                    "type": "done",
                    "cost": round(state.total_cost_usd, 5),
                    "budget_remaining": round(2.0 - state.total_cost_usd, 4),
                    "dashboard_update": dashboard_update,
                }
            )

    except WebSocketDisconnect:
        logger.info("WS chat desconectado: %s", state.session_id)


# ── Admin Panel ───────────────────────────────────────────────────────────────


@_fastapi_app.get("/admin")
async def serve_admin() -> FileResponse:
    """Serve the Admin Panel HTML."""
    from pathlib import Path

    html_path = Path(__file__).parent / "static" / "admin.html"
    return FileResponse(str(html_path), media_type="text/html")


_USER_STORE: list[dict] = []  # in-memory stub; replace with Firestore/Cloud SQL in prod


@_fastapi_app.get("/admin/api/users")
async def admin_list_users() -> JSONResponse:
    return JSONResponse({"users": _USER_STORE})


@_fastapi_app.post("/admin/api/users")
async def admin_create_user(request: Request) -> JSONResponse:
    import uuid
    from datetime import date

    body = await request.json()
    user = {**body, "id": str(uuid.uuid4()), "created_at": str(date.today())}
    _USER_STORE.append(user)
    return JSONResponse({"ok": True, "user": user})


@_fastapi_app.put("/admin/api/users/{user_id}")
async def admin_update_user(user_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    for i, u in enumerate(_USER_STORE):
        if u["id"] == user_id:
            _USER_STORE[i] = {**u, **body, "id": user_id}
            return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "not found"}, status_code=404)


@_fastapi_app.delete("/admin/api/users/{user_id}")
async def admin_delete_user(user_id: str) -> JSONResponse:
    global _USER_STORE
    _USER_STORE = [u for u in _USER_STORE if u["id"] != user_id]
    return JSONResponse({"ok": True})


_ACCESS_MATRIX: dict = {}  # profile → {feature_id: bool}


@_fastapi_app.get("/admin/api/access")
async def admin_get_access() -> JSONResponse:
    return JSONResponse({"matrix": _ACCESS_MATRIX})


@_fastapi_app.post("/admin/api/access")
async def admin_save_access(request: Request) -> JSONResponse:
    global _ACCESS_MATRIX
    _ACCESS_MATRIX = await request.json()
    return JSONResponse({"ok": True})


@_fastapi_app.get("/admin/api/jobs")
async def admin_list_jobs() -> JSONResponse:
    """List Cloud Run Jobs with last execution status."""
    jobs_config = [
        {"name": "spepe-tse-ingest", "module": "tse_ingest", "timeout": "3600s"},
        {"name": "spepe-ibge-sync", "module": "ibge_sync", "timeout": "1800s"},
        {"name": "spepe-security-ingest", "module": "security_ingest", "timeout": "1800s"},
        {"name": "spepe-datasus-ingest", "module": "datasus_ingest", "timeout": "1800s"},
        {"name": "spepe-dieese-ingest", "module": "dieese_ingest", "timeout": "900s"},
        {"name": "spepe-cetic-ingest", "module": "cetic_ingest", "timeout": "900s"},
        {"name": "spepe-silver-transform", "module": "silver_transform", "timeout": "1800s"},
        {"name": "spepe-gold-build", "module": "gold_build", "timeout": "1800s"},
        {"name": "spepe-digital-ingest", "module": "digital_ingest", "timeout": "900s"},
    ]
    if settings.gcp_project_id:
        try:
            from google.cloud import run_v2

            client = run_v2.JobsClient()
            parent = f"projects/{settings.gcp_project_id}/locations/southamerica-east1"
            gcp_jobs = {j.name.split("/")[-1]: j for j in client.list_jobs(parent=parent)}
            for jcfg in jobs_config:
                gj = gcp_jobs.get(jcfg["name"])
                if gj:
                    jcfg["last_status"] = (
                        gj.terminal_condition.type_ if gj.terminal_condition else "UNKNOWN"
                    )
                    jcfg["last_run_at"] = str(gj.update_time) if gj.update_time else None
                else:
                    jcfg["last_status"] = "NOT_DEPLOYED"
                    jcfg["last_run_at"] = None
        except Exception as exc:
            logger.warning("Cloud Run jobs list failed: %s", exc)
            for jcfg in jobs_config:
                jcfg.setdefault("last_status", "UNKNOWN")
                jcfg.setdefault("last_run_at", None)
    else:
        for jcfg in jobs_config:
            jcfg["last_status"] = "LOCAL_DEV"
            jcfg["last_run_at"] = None
    return JSONResponse({"jobs": jobs_config})


@_fastapi_app.post("/admin/api/jobs/{job_name}/run")
async def admin_run_job(job_name: str, uf: str = "SP", year: int = 2022) -> JSONResponse:
    """Trigger a Cloud Run Job execution (admin only)."""
    from agents.tools import RunJobArgs, run_dataops_job

    # Map Cloud Run job name → internal job id
    name_map = {
        "spepe-tse-ingest": "tse_ingest",
        "spepe-ibge-sync": "ibge_sync",
        "spepe-security-ingest": "security_ingest",
        "spepe-datasus-ingest": "datasus_ingest",
        "spepe-dieese-ingest": "dieese_ingest",
        "spepe-cetic-ingest": "cetic_ingest",
        "spepe-silver-transform": "silver_transform",
        "spepe-gold-build": "gold_build",
        "spepe-digital-ingest": "digital_ingest",
    }
    job_id = name_map.get(job_name, job_name.replace("spepe-", "").replace("-", "_"))
    result = run_dataops_job(RunJobArgs(job=job_id, uf=uf, year=year))
    return JSONResponse(result)


@_fastapi_app.get("/admin/api/sentinel/status")
async def admin_sentinel_status() -> JSONResponse:
    """Return snapshot of all Sentinel resource statuses."""
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            rows = client.query(
                f"SELECT * FROM `{settings.gcp_project_id}.spepe_mlops.sentinel_state` "
                "ORDER BY category, resource_id"
            ).result()
            resources = [dict(r) for r in rows]
            return JSONResponse({"resources": resources, "source": "bigquery"})
        except Exception as exc:
            logger.warning("Sentinel state BQ query failed: %s", exc)

    # Stub data for dev/demo
    stub = [
        {
            "resource_id": "dataops:fact_municipio_eleicao",
            "category": "dataops",
            "watcher": "DataOpsWatcher",
            "status": "ok",
            "metrics": {"freshness_min": 30, "dq": 0.987, "rows": 2400000},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "dataops:fact_secao_eleicao",
            "category": "dataops",
            "watcher": "DataOpsWatcher",
            "status": "ok",
            "metrics": {"freshness_min": 30, "dq": 0.981, "rows": 580000000},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "dataops:fact_seguranca_municipio",
            "category": "dataops",
            "watcher": "DataOpsWatcher",
            "status": "warn",
            "metrics": {"freshness_min": 1440, "dq": 0.91},
            "alert_message": "DQ score 0.91 abaixo do threshold 0.95",
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "mlops:champion_model",
            "category": "mlops",
            "watcher": "MLOpsWatcher",
            "status": "ok",
            "metrics": {"brier_score": 0.18, "js_divergence": 0.04},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "mlops:bias_monitor",
            "category": "mlops",
            "watcher": "BiasWatcher",
            "status": "ok",
            "metrics": {"max_group_ratio": 1.08},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "infra:cloud_run",
            "category": "infra",
            "watcher": "InfraWatcher",
            "status": "ok",
            "metrics": {"latency_p99_ms": 420, "error_rate": 0.001},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "infra:budget",
            "category": "infra",
            "watcher": "BudgetWatcher",
            "status": "ok",
            "metrics": {"pct_used": 12},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "crews:observadores",
            "category": "crews",
            "watcher": "SentinelOrchestrator",
            "status": "ok",
            "metrics": {"events_1h": 4, "errors_1h": 0},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "crews:analisadores",
            "category": "crews",
            "watcher": "SentinelOrchestrator",
            "status": "ok",
            "metrics": {"queue_depth": 0, "error_rate_5m": 0},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "crews:interpretadores",
            "category": "crews",
            "watcher": "SentinelOrchestrator",
            "status": "ok",
            "metrics": {"latency_last_s": 3.1},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
        {
            "resource_id": "crews:despachantes",
            "category": "crews",
            "watcher": "SentinelOrchestrator",
            "status": "ok",
            "metrics": {"actions_24h": 1},
            "alert_message": None,
            "last_check": "2026-04-25T17:00:00Z",
        },
    ]
    return JSONResponse({"resources": stub, "source": "stub"})


@_fastapi_app.get("/admin/api/catalog")
async def admin_catalog() -> JSONResponse:
    """Return BigQuery table metadata for all SPEPE datasets."""
    datasets = ["spepe_silver", "spepe_gold", "spepe_mlops"]
    catalog = []
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            for ds in datasets:
                for tbl in client.list_tables(f"{settings.gcp_project_id}.{ds}"):
                    t = client.get_table(tbl)
                    catalog.append(
                        {
                            "dataset": ds,
                            "table": t.table_id,
                            "rows": t.num_rows,
                            "size_mb": round(t.num_bytes / 1e6, 1),
                            "last_modified": str(t.modified),
                            "description": t.description or "",
                        }
                    )
            return JSONResponse({"tables": catalog, "source": "bigquery"})
        except Exception as exc:
            logger.warning("Catalog BQ query failed: %s", exc)

    stub_catalog = [
        {
            "dataset": "spepe_gold",
            "table": "fact_municipio_eleicao",
            "rows": 11140,
            "size_mb": 48.2,
            "last_modified": "2026-04-25 14:32:00",
            "description": "~240 features por município × eleição",
        },
        {
            "dataset": "spepe_gold",
            "table": "fact_secao_eleicao",
            "rows": 0,
            "size_mb": 0,
            "last_modified": "-",
            "description": "Granular seção × candidato",
        },
        {
            "dataset": "spepe_gold",
            "table": "fact_seguranca_municipio",
            "rows": 0,
            "size_mb": 0,
            "last_modified": "-",
            "description": "IVS + Atlas + SINESP",
        },
        {
            "dataset": "spepe_gold",
            "table": "fact_saude_municipio",
            "rows": 0,
            "size_mb": 0,
            "last_modified": "-",
            "description": "DataSUS SIM + ANS",
        },
        {
            "dataset": "spepe_gold",
            "table": "fact_economico_municipio",
            "rows": 0,
            "size_mb": 0,
            "last_modified": "-",
            "description": "DIEESE + PIB IBGE",
        },
        {
            "dataset": "spepe_gold",
            "table": "fact_pesquisa",
            "rows": 0,
            "size_mb": 0,
            "last_modified": "-",
            "description": "Pesquisas eleitorais",
        },
        {
            "dataset": "spepe_mlops",
            "table": "fact_predictions",
            "rows": 0,
            "size_mb": 0,
            "last_modified": "-",
            "description": "Predições com IC 95%",
        },
        {
            "dataset": "spepe_mlops",
            "table": "sentinel_state",
            "rows": 11,
            "size_mb": 0.01,
            "last_modified": "2026-04-25 17:00:00",
            "description": "Estado Sentinel",
        },
    ]
    return JSONResponse({"tables": stub_catalog, "source": "stub"})


# ── Sentinel WebSocket ────────────────────────────────────────────────────────


_sentinel_ws_clients: list[WebSocket] = []


@_fastapi_app.websocket("/ws/sentinel")
async def ws_sentinel(websocket: WebSocket) -> None:
    """WebSocket for real-time Sentinel status updates to the admin panel."""
    import asyncio

    await websocket.accept()
    _sentinel_ws_clients.append(websocket)
    try:
        # Send initial snapshot wrapped as typed message
        status_resp = await admin_sentinel_status()
        status_data = status_resp.body.decode()
        import json as _json

        parsed = _json.loads(status_data)
        await websocket.send_json(
            {
                "type": "sentinel_update",
                "data": parsed.get("resources", parsed.get("watchers", [])),
            }
        )
        # Keep connection alive — server pushes updates when Pub/Sub fires
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _sentinel_ws_clients:
            _sentinel_ws_clients.remove(websocket)


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
