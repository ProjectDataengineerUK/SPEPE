"""
SPEPE Dashboard API — FastAPI standalone (uvicorn).

  - REST:      GET /api/candidatos, /api/kpi, /api/municipios, /api/trends, /api/meta
  - WebSocket: /ws/chat  → Supervisor stream em tempo real
  - Static:    GET /dash  → Dashboard HTML  |  GET /admin → Admin Panel
  - Health:    GET /healthz
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Security,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ValidationError

from agents.supervisor import Supervisor
from config.logging_config import setup_logging
from config.session_state import SessionState
from config.settings import settings
from dataops.clients.digital_client import fetch_meta_ads, fetch_trends
from security.output_validators import validate_input_injection


# ── Sentinel SSE — fan-out queues for /admin/api/sentinel/stream ──────────
_sentinel_subscribers: list[asyncio.Queue] = []


def _json_safe_response(data: Any) -> Response:
    """Return a Response with JSON content, safely serializing BQ/numpy types."""
    import datetime as _dt

    def _default(obj: Any) -> Any:
        if isinstance(obj, (_dt.datetime, _dt.date)):
            return obj.isoformat()
        try:
            import numpy as np

            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        return str(obj)

    return Response(
        content=json.dumps(data, default=_default),
        media_type="application/json",
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    setup_logging(log_level=settings.log_level, console_log_level="WARNING")
    poller_tasks: list[asyncio.Task] = [
        asyncio.create_task(_poll_table_freshness(interval=15)),
        asyncio.create_task(_poll_costs(interval=60)),
        asyncio.create_task(_consume_sentinel_pubsub()),
    ]
    try:
        yield
    finally:
        for t in poller_tasks:
            t.cancel()
        for t in poller_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(title="SPEPE", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/static/{filename:path}")
async def serve_static(filename: str) -> FileResponse:
    file_path = _STATIC_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path))


logger = logging.getLogger("spepe.dashboard_api")

_LOCAL_SILVER_DIR = Path(os.environ.get("DATA_DIR", "data")) / "silver"

# ── Dashboard auth middleware (Google ID token) ───────────────────────────

_PUBLIC_API_PATHS = {
    "/api/auth/me",
    "/api/config/maps-key",
    "/api/socioeconomico",
    "/api/seguranca",
    "/api/saude",
    "/api/pesquisas",
    "/api/pesquisas/intencao",
    "/api/resultados",
    "/api/trends",
    "/api/meta",
    "/api/indicadores",
    "/api/perfis",
    "/api/mapa",
    "/api/mapa/choropleth",
    "/api/ufs",
    "/api/mesorregioes",
    "/api/social/sentimento",
    "/api/social/trends",
    "/api/social/plataformas",
    "/api/social/crise",
    "/api/candidatos",
    "/api/municipios",
    "/api/kpi",
    "/api/mapa/locais",
    "/api/model/status",
    "/api/model/shap",
    "/api/resultados/partido",
    "/api/aliancas",
    "/api/adversarios",
    "/api/parcerias",
    "/api/meta_votos",
    "/api/gdelt_eventos",
    "/api/debug/fontes",
    "/api/previsao",
}


@app.middleware("http")
async def dashboard_auth_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if any(path == p or path.startswith(p + "/") for p in _PUBLIC_API_PATHS):
        return await call_next(request)
    if not settings.gcp_project_id or settings.gcp_project_id in ("", "local"):
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"detail": "Authorization required"}, status_code=401)
    try:
        from google.auth.transport import requests as grequests
        from google.oauth2 import id_token as gid

        info = gid.verify_oauth2_token(
            auth[7:],
            grequests.Request(),
            audience=settings.google_client_id or None,
            clock_skew_in_seconds=10,
        )
        await _assert_user_authorized(info.get("email", ""))
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    except Exception:
        return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
    return await call_next(request)


# ── Admin authentication ───────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)

# Cache de emails autorizados para evitar hit no Firestore a cada request
_AUTH_CACHE: dict[str, float] = {}
_AUTH_CACHE_TTL = 300  # 5 minutos


async def _assert_user_authorized(email: str) -> None:
    """Rejeita com 403 se o email não está cadastrado em spepe_users.

    Verifica primeiro a env var SPEPE_ALLOWED_EMAILS (CSV) para bootstrap,
    depois o cache em memória, depois Firestore. Fail-closed: nega acesso
    se Firestore indisponível e email não está no env var.
    """
    import time

    if not email:
        raise HTTPException(status_code=403, detail="Acesso não autorizado.")

    # Bootstrap: lista de emails permitidos via env var (CSV)
    allowed_env = os.environ.get("SPEPE_ALLOWED_EMAILS", "")
    if allowed_env and email in [e.strip() for e in allowed_env.split(",")]:
        return

    # Cache hit
    now = time.monotonic()
    if email in _AUTH_CACHE and now - _AUTH_CACHE[email] < _AUTH_CACHE_TTL:
        return

    # Firestore check
    db = _fs_client()
    if db:
        try:
            docs = db.collection("spepe_users").where("email", "==", email).limit(1).stream()
            async for _doc in docs:
                _AUTH_CACHE[email] = now
                return
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Firestore auth check failed for %s: %s", email, exc)

    raise HTTPException(
        status_code=403,
        detail="Acesso não autorizado. Conta não cadastrada no SPEPE.",
    )


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> dict:
    """Validate Bearer token for admin API routes.

    In local dev (no GCP_PROJECT_ID or set to 'local') auth is skipped.
    In GCP validates Google OAuth2 ID token AND checks email in spepe_users.
    """
    if not settings.gcp_project_id or settings.gcp_project_id in ("", "local"):
        return {"email": "dev@local", "sub": "dev"}
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authorization required")
    try:
        from google.auth.transport import requests as grequests
        from google.oauth2 import id_token

        info = id_token.verify_oauth2_token(
            credentials.credentials,
            grequests.Request(),
            audience=settings.google_client_id or None,
            clock_skew_in_seconds=10,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    await _assert_user_authorized(info.get("email", ""))
    return info


_HELP_TEXT = """\
## SPEPE — Comandos disponíveis

| Comando | Agente | Modelo |
|---------|--------|--------|
| `/coletar SP 2022` | Coletor | Gemini Flash |
| `/perfil São Paulo 2022` | Analista | Gemini Pro |
| `/arquétipos BR` | Perfilador | Gemini Flash |
| `/prever Lula 2026` | Modelista → Explicador → Narrador | Gemini Pro |
| `/explicar` | Explicador | Gemini Pro |
| `/relatorio` | Narrador | Gemini Flash |
| `/monitorar` | Vigilante | Gemini Flash |
| `/help` | — | — |

*Arquitetura: Claude Sonnet 4.6 (roteamento) + Google Gemini (execução) via Vertex AI*
*Budget por sessão: $2.00*
"""


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    from pathlib import Path

    return FileResponse(
        str(Path(__file__).parent / "static" / "index.html"), media_type="text/html"
    )


@app.get("/entrar", include_in_schema=False)
async def entrar() -> RedirectResponse:
    return RedirectResponse(url="/dash")


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


_supervisor: Supervisor | None = None


def _get_supervisor() -> Supervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = Supervisor()
    return _supervisor


# ── Servir o dashboard HTML ────────────────────────────────────────────────


@app.get("/dash")
async def serve_dashboard() -> FileResponse:
    """Serve o protótipo HTML do dashboard."""
    from pathlib import Path

    html_path = Path(__file__).parent / "static" / "spepe-app.html"
    return FileResponse(
        str(html_path),
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/dash2")
async def serve_dashboard2() -> FileResponse:
    """Serve o novo dashboard de inteligência eleitoral (spepe-dash2)."""
    from pathlib import Path

    html_path = Path(__file__).parent / "static" / "spepe-dash2.html"
    return FileResponse(
        str(html_path),
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/prototype")
async def serve_prototype() -> FileResponse:
    """Serve o protótipo visual standalone (sem backend)."""
    from pathlib import Path

    html_path = Path(__file__).parent / "static" / "spepe-prototype.html"
    return FileResponse(
        str(html_path),
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ── Auth (stub — trocar por Firebase Auth / IAP em prod) ──────────────────


@app.get("/api/auth/me")
async def auth_me(authorization: str = Header(default=None)) -> JSONResponse:
    """Valida token Google OAuth2 e verifica se email está cadastrado no SPEPE."""
    if not settings.gcp_project_id or settings.gcp_project_id in ("", "local"):
        return JSONResponse({"uid": "dev", "email": "dev@local", "name": "Dev", "plan": "pro"})

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token não fornecido")
    token = authorization[7:]
    try:
        from google.auth.transport import requests as grequests
        from google.oauth2 import id_token

        info = id_token.verify_oauth2_token(
            token,
            grequests.Request(),
            audience=settings.google_client_id or None,
            clock_skew_in_seconds=10,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido")

    await _assert_user_authorized(info.get("email", ""))
    return JSONResponse(
        {
            "uid": info.get("sub", ""),
            "email": info.get("email", ""),
            "name": info.get("name", ""),
            "plan": "pro",
        }
    )


# ── Demo / static fallback data (shown when BigQuery is not configured) ────

_DEMO_CANDIDATOS: dict[str, list[dict]] = {
    "Presidente": [
        {"nm": "Lula", "partido": "PT", "pct_t1": 48.4, "votos": "57_259_504"},
        {"nm": "Bolsonaro", "partido": "PL", "pct_t1": 43.2, "votos": "51_072_345"},
        {"nm": "Simone Tebet", "partido": "MDB", "pct_t1": 4.2, "votos": "4_991_727"},
        {"nm": "Ciro Gomes", "partido": "PDT", "pct_t1": 3.0, "votos": "3_599_287"},
        {"nm": "Soraya Thronicke", "partido": "União", "pct_t1": 0.9, "votos": '1_070_"'},
    ],
    "Governador": [
        {
            "nm": "Tarcísio de Freitas",
            "partido": "Republicanos",
            "pct_t1": 42.3,
            "votos": "12_203_540",
        },
        {"nm": "Fernando Haddad", "partido": "PT", "pct_t1": 35.7, "votos": "10_291_047"},
        {"nm": "Rodrigo Garcia", "partido": "PSDB", "pct_t1": 18.4, "votos": "5_312_109"},
        {"nm": "Gabriel Chalita", "partido": "MDB", "pct_t1": 2.1, "votos": "605_813"},
    ],
    "Senador": [
        {"nm": "Marcos Pontes", "partido": "PL", "pct_t1": 25.5, "votos": "7_345_123"},
        {"nm": "Mara Gabrilli", "partido": "PSDB", "pct_t1": 20.1, "votos": "5_798_451"},
        {"nm": "Márcio França", "partido": "PSB", "pct_t1": 18.7, "votos": "5_392_017"},
        {"nm": "Wellington Fagundes", "partido": "PL", "pct_t1": 15.2, "votos": "4_381_022"},
    ],
}

_DEMO_KPI: dict[str, dict] = {
    "Presidente": {
        "vencedor": "Lula",
        "vencedor_partido": "PT",
        "vencedor_pct": 48.4,
        "segundo": "Bolsonaro",
        "segundo_partido": "PL",
        "segundo_pct": 43.2,
        "margem_pp": 5.2,
        "total_votos": 118_995_730,
        "municipios": 5_568,
        "dq_score": 98,
        "fonte": "demo_tse_2022",
    },
    "Governador": {
        "vencedor": "Tarcísio de Freitas",
        "vencedor_partido": "Republicanos",
        "vencedor_pct": 42.3,
        "segundo": "Haddad",
        "segundo_partido": "PT",
        "segundo_pct": 35.7,
        "margem_pp": 6.6,
        "total_votos": 28_836_019,
        "municipios": 645,
        "dq_score": 97,
        "fonte": "demo_tse_2022",
    },
}

# 2022 presidential 1st round by UF — ibge_code=2-digit IBGE code
_DEMO_MAP_UF_FEATURES = [
    {
        "ibge_code": "12",
        "id": "AC",
        "label": "Acre",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 57.1,
        "segundo": "Lula",
        "pct2": 40.2,
    },
    {
        "ibge_code": "27",
        "id": "AL",
        "label": "Alagoas",
        "lider": "Lula",
        "partido": "PT",
        "pct": 59.8,
        "segundo": "Bolsonaro",
        "pct2": 35.6,
    },
    {
        "ibge_code": "16",
        "id": "AP",
        "label": "Amapá",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 60.3,
        "segundo": "Lula",
        "pct2": 34.1,
    },
    {
        "ibge_code": "13",
        "id": "AM",
        "label": "Amazonas",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 57.7,
        "segundo": "Lula",
        "pct2": 37.4,
    },
    {
        "ibge_code": "29",
        "id": "BA",
        "label": "Bahia",
        "lider": "Lula",
        "partido": "PT",
        "pct": 73.0,
        "segundo": "Bolsonaro",
        "pct2": 22.4,
    },
    {
        "ibge_code": "23",
        "id": "CE",
        "label": "Ceará",
        "lider": "Lula",
        "partido": "PT",
        "pct": 72.3,
        "segundo": "Bolsonaro",
        "pct2": 22.8,
    },
    {
        "ibge_code": "53",
        "id": "DF",
        "label": "Dist. Federal",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 55.0,
        "segundo": "Lula",
        "pct2": 36.8,
    },
    {
        "ibge_code": "32",
        "id": "ES",
        "label": "Espírito Santo",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 52.4,
        "segundo": "Lula",
        "pct2": 40.1,
    },
    {
        "ibge_code": "52",
        "id": "GO",
        "label": "Goiás",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 58.3,
        "segundo": "Lula",
        "pct2": 34.2,
    },
    {
        "ibge_code": "21",
        "id": "MA",
        "label": "Maranhão",
        "lider": "Lula",
        "partido": "PT",
        "pct": 71.0,
        "segundo": "Bolsonaro",
        "pct2": 24.4,
    },
    {
        "ibge_code": "31",
        "id": "MG",
        "label": "Minas Gerais",
        "lider": "Lula",
        "partido": "PT",
        "pct": 48.3,
        "segundo": "Bolsonaro",
        "pct2": 44.1,
    },
    {
        "ibge_code": "50",
        "id": "MS",
        "label": "Mato Grosso do Sul",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 62.1,
        "segundo": "Lula",
        "pct2": 30.4,
    },
    {
        "ibge_code": "51",
        "id": "MT",
        "label": "Mato Grosso",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 73.5,
        "segundo": "Lula",
        "pct2": 21.3,
    },
    {
        "ibge_code": "15",
        "id": "PA",
        "label": "Pará",
        "lider": "Lula",
        "partido": "PT",
        "pct": 57.4,
        "segundo": "Bolsonaro",
        "pct2": 37.1,
    },
    {
        "ibge_code": "25",
        "id": "PB",
        "label": "Paraíba",
        "lider": "Lula",
        "partido": "PT",
        "pct": 63.5,
        "segundo": "Bolsonaro",
        "pct2": 31.4,
    },
    {
        "ibge_code": "26",
        "id": "PE",
        "label": "Pernambuco",
        "lider": "Lula",
        "partido": "PT",
        "pct": 70.7,
        "segundo": "Bolsonaro",
        "pct2": 24.4,
    },
    {
        "ibge_code": "22",
        "id": "PI",
        "label": "Piauí",
        "lider": "Lula",
        "partido": "PT",
        "pct": 71.0,
        "segundo": "Bolsonaro",
        "pct2": 24.1,
    },
    {
        "ibge_code": "41",
        "id": "PR",
        "label": "Paraná",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 55.2,
        "segundo": "Lula",
        "pct2": 37.3,
    },
    {
        "ibge_code": "33",
        "id": "RJ",
        "label": "Rio de Janeiro",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 51.3,
        "segundo": "Lula",
        "pct2": 40.9,
    },
    {
        "ibge_code": "24",
        "id": "RN",
        "label": "Rio Gr. do Norte",
        "lider": "Lula",
        "partido": "PT",
        "pct": 67.5,
        "segundo": "Bolsonaro",
        "pct2": 27.3,
    },
    {
        "ibge_code": "11",
        "id": "RO",
        "label": "Rondônia",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 75.4,
        "segundo": "Lula",
        "pct2": 19.1,
    },
    {
        "ibge_code": "14",
        "id": "RR",
        "label": "Roraima",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 73.6,
        "segundo": "Lula",
        "pct2": 21.2,
    },
    {
        "ibge_code": "43",
        "id": "RS",
        "label": "Rio Gr. do Sul",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 52.0,
        "segundo": "Lula",
        "pct2": 38.2,
    },
    {
        "ibge_code": "42",
        "id": "SC",
        "label": "Santa Catarina",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 63.2,
        "segundo": "Lula",
        "pct2": 30.1,
    },
    {
        "ibge_code": "28",
        "id": "SE",
        "label": "Sergipe",
        "lider": "Lula",
        "partido": "PT",
        "pct": 59.8,
        "segundo": "Bolsonaro",
        "pct2": 34.6,
    },
    {
        "ibge_code": "35",
        "id": "SP",
        "label": "São Paulo",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 46.2,
        "segundo": "Lula",
        "pct2": 45.9,
    },
    {
        "ibge_code": "17",
        "id": "TO",
        "label": "Tocantins",
        "lider": "Bolsonaro",
        "partido": "PL",
        "pct": 62.1,
        "segundo": "Lula",
        "pct2": 31.4,
    },
]

_DEMO_PESQUISAS = [
    {
        "candidato": "Lula",
        "intencao_ponderada": 38.5,
        "margem_erro": 2.2,
        "instituto": "Datafolha",
        "data": "2026-04-10",
    },
    {
        "candidato": "Tarcísio",
        "intencao_ponderada": 30.2,
        "margem_erro": 2.2,
        "instituto": "Datafolha",
        "data": "2026-04-10",
    },
    {
        "candidato": "Bolsonaro",
        "intencao_ponderada": 25.1,
        "margem_erro": 2.2,
        "instituto": "Datafolha",
        "data": "2026-04-10",
    },
    {
        "candidato": "Tebet",
        "intencao_ponderada": 6.2,
        "margem_erro": 2.2,
        "instituto": "Datafolha",
        "data": "2026-04-10",
    },
]

_DEMO_SOCIAL_SENTIMENTO = [
    {
        "plataforma": "Twitter/X",
        "sg_uf": "BR",
        "score_sentimento": 0.12,
        "volume": 48_300,
        "tendencia": "estavel",
    },
    {
        "plataforma": "BlueSky",
        "sg_uf": "BR",
        "score_sentimento": 0.08,
        "volume": 12_100,
        "tendencia": "alta",
    },
    {
        "plataforma": "Facebook",
        "sg_uf": "BR",
        "score_sentimento": -0.05,
        "volume": 85_400,
        "tendencia": "queda",
    },
    {
        "plataforma": "YouTube",
        "sg_uf": "BR",
        "score_sentimento": 0.21,
        "volume": 9_700,
        "tendencia": "alta",
    },
]

_DEMO_SOCIOECONOMICO = [
    {
        "nm": "São Paulo",
        "sg_uf": "SP",
        "idhm": 0.783,
        "renda_per_capita": 2_150,
        "taxa_alfabetizacao": 95.6,
        "taxa_urbanizacao": 96.3,
        "gini": 0.54,
        "taxa_emprego": 72.1,
    },
    {
        "nm": "Rio de Janeiro",
        "sg_uf": "RJ",
        "idhm": 0.761,
        "renda_per_capita": 1_920,
        "taxa_alfabetizacao": 95.0,
        "taxa_urbanizacao": 96.9,
        "gini": 0.57,
        "taxa_emprego": 68.4,
    },
    {
        "nm": "Minas Gerais",
        "sg_uf": "MG",
        "idhm": 0.731,
        "renda_per_capita": 1_450,
        "taxa_alfabetizacao": 92.3,
        "taxa_urbanizacao": 85.3,
        "gini": 0.52,
        "taxa_emprego": 70.2,
    },
    {
        "nm": "Bahia",
        "sg_uf": "BA",
        "idhm": 0.660,
        "renda_per_capita": 980,
        "taxa_alfabetizacao": 85.1,
        "taxa_urbanizacao": 73.4,
        "gini": 0.56,
        "taxa_emprego": 58.3,
    },
    {
        "nm": "Ceará",
        "sg_uf": "CE",
        "idhm": 0.682,
        "renda_per_capita": 1_010,
        "taxa_alfabetizacao": 84.7,
        "taxa_urbanizacao": 75.1,
        "gini": 0.55,
        "taxa_emprego": 59.1,
    },
]

_DEMO_SAUDE = [
    {
        "sg_uf": "SP",
        "nm_uf": "São Paulo",
        "mortalidade_infantil": 10.8,
        "cobertura_sus": 79.2,
        "leitos_por_mil": 2.4,
        "medicos_por_mil": 2.1,
    },
    {
        "sg_uf": "RJ",
        "nm_uf": "Rio de Janeiro",
        "mortalidade_infantil": 12.1,
        "cobertura_sus": 74.8,
        "leitos_por_mil": 2.7,
        "medicos_por_mil": 2.5,
    },
    {
        "sg_uf": "MG",
        "nm_uf": "Minas Gerais",
        "mortalidade_infantil": 11.4,
        "cobertura_sus": 80.5,
        "leitos_por_mil": 2.2,
        "medicos_por_mil": 1.8,
    },
    {
        "sg_uf": "BA",
        "nm_uf": "Bahia",
        "mortalidade_infantil": 16.3,
        "cobertura_sus": 68.3,
        "leitos_por_mil": 1.8,
        "medicos_por_mil": 1.1,
    },
    {
        "sg_uf": "CE",
        "nm_uf": "Ceará",
        "mortalidade_infantil": 14.9,
        "cobertura_sus": 71.2,
        "leitos_por_mil": 1.9,
        "medicos_por_mil": 1.3,
    },
]

_DEMO_SEGURANCA = [
    {
        "sg_uf": "SP",
        "nm_uf": "São Paulo",
        "homicidios_100k": 8.2,
        "furtos_100k": 1_240,
        "roubos_100k": 890,
        "ano": 2022,
    },
    {
        "sg_uf": "RJ",
        "nm_uf": "Rio de Janeiro",
        "homicidios_100k": 28.4,
        "furtos_100k": 980,
        "roubos_100k": 1_340,
        "ano": 2022,
    },
    {
        "sg_uf": "MG",
        "nm_uf": "Minas Gerais",
        "homicidios_100k": 18.6,
        "furtos_100k": 870,
        "roubos_100k": 650,
        "ano": 2022,
    },
    {
        "sg_uf": "BA",
        "nm_uf": "Bahia",
        "homicidios_100k": 42.1,
        "furtos_100k": 720,
        "roubos_100k": 510,
        "ano": 2022,
    },
    {
        "sg_uf": "CE",
        "nm_uf": "Ceará",
        "homicidios_100k": 35.8,
        "furtos_100k": 680,
        "roubos_100k": 490,
        "ano": 2022,
    },
]

_DEMO_MUNICIPIOS = [
    {
        "nm": "São Paulo",
        "sg_uf": "SP",
        "pct_t1": 46.2,
        "lider": "Bolsonaro",
        "segundo": "Lula",
        "pct2": 45.9,
        "votos": 4_128_422,
        "ibge_code": "3550308",
    },
    {
        "nm": "Campinas",
        "sg_uf": "SP",
        "pct_t1": 48.1,
        "lider": "Bolsonaro",
        "segundo": "Lula",
        "pct2": 44.0,
        "votos": 651_204,
        "ibge_code": "3509502",
    },
    {
        "nm": "Santos",
        "sg_uf": "SP",
        "pct_t1": 51.2,
        "lider": "Bolsonaro",
        "segundo": "Lula",
        "pct2": 42.1,
        "votos": 188_901,
        "ibge_code": "3548500",
    },
    {
        "nm": "Guarulhos",
        "sg_uf": "SP",
        "pct_t1": 44.8,
        "lider": "Lula",
        "segundo": "Bolsonaro",
        "pct2": 47.5,
        "votos": 458_733,
        "ibge_code": "3518800",
    },
    {
        "nm": "Santo André",
        "sg_uf": "SP",
        "pct_t1": 45.9,
        "lider": "Bolsonaro",
        "segundo": "Lula",
        "pct2": 46.8,
        "votos": 301_125,
        "ibge_code": "3547809",
    },
    {
        "nm": "Sorocaba",
        "sg_uf": "SP",
        "pct_t1": 52.3,
        "lider": "Bolsonaro",
        "segundo": "Lula",
        "pct2": 40.2,
        "votos": 302_890,
        "ibge_code": "3552205",
    },
    {
        "nm": "Ribeirão Preto",
        "sg_uf": "SP",
        "pct_t1": 53.1,
        "lider": "Bolsonaro",
        "segundo": "Lula",
        "pct2": 39.8,
        "votos": 328_405,
        "ibge_code": "3543402",
    },
    {
        "nm": "Osasco",
        "sg_uf": "SP",
        "pct_t1": 43.2,
        "lider": "Lula",
        "segundo": "Bolsonaro",
        "pct2": 48.9,
        "votos": 356_981,
        "ibge_code": "3534401",
    },
    {
        "nm": "Salvador",
        "sg_uf": "BA",
        "pct_t1": 76.1,
        "lider": "Lula",
        "segundo": "Bolsonaro",
        "pct2": 19.2,
        "votos": 878_011,
        "ibge_code": "2927408",
    },
    {
        "nm": "Fortaleza",
        "sg_uf": "CE",
        "pct_t1": 74.3,
        "lider": "Lula",
        "segundo": "Bolsonaro",
        "pct2": 20.8,
        "votos": 1_021_340,
        "ibge_code": "2304400",
    },
    {
        "nm": "Belo Horizonte",
        "sg_uf": "MG",
        "pct_t1": 52.1,
        "lider": "Lula",
        "segundo": "Bolsonaro",
        "pct2": 40.3,
        "votos": 1_012_894,
        "ibge_code": "3106200",
    },
    {
        "nm": "Rio de Janeiro",
        "sg_uf": "RJ",
        "pct_t1": 44.8,
        "lider": "Bolsonaro",
        "segundo": "Lula",
        "pct2": 47.1,
        "votos": 2_891_204,
        "ibge_code": "3304557",
    },
]


def _is_demo_mode() -> bool:
    return not (settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true")


# ── Candidatos por cargo / UF / ano ───────────────────────────────────────


@app.get("/api/candidatos")
async def get_candidatos(
    cargo: str = Query("Presidente"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
) -> JSONResponse:
    """Retorna candidatos para o cargo/UF/ano a partir do BigQuery Gold."""
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            raw = await _bq_candidatos(cargo, uf, ano)
            # Extrai data_ultima_pesquisa (polls) e retorna separado do array de candidatos
            data_pesquisa: str | None = None
            candidatos = []
            for row in raw:
                d = dict(row)
                dp = d.pop("data_ultima_pesquisa", None)
                if dp is not None and data_pesquisa is None:
                    data_pesquisa = dp.isoformat() if hasattr(dp, "isoformat") else str(dp)
                candidatos.append(d)
            return JSONResponse(
                {
                    "cargo": cargo,
                    "uf": uf,
                    "ano": ano,
                    "candidatos": candidatos,
                    "data_pesquisa": data_pesquisa,
                }
            )
        except Exception as exc:
            logger.warning("BigQuery candidatos falhou: %s", exc)

    return JSONResponse(
        {"cargo": cargo, "uf": uf, "ano": ano, "candidatos": [], "fonte": "indisponivel"}
    )


async def _bq_candidatos(cargo: str, uf: str, ano: int) -> list[dict]:
    """Retorna candidatos para cargo/UF/ano.

    Para ano >= 2026 (ou Presidente sem dados históricos), usa fact_intencao_voto
    (pesquisas agregadas) como fonte. Presidente sempre filtra por uf='BR' (âmbito nacional).
    Para anos históricos usa fact_municipio_candidato_eleicao (resultado TSE).
    """
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _cargo_to_cd(cargo, 1)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

    # 2026+: fonte é pesquisas (fact_intencao_voto) — TSE não tem resultados ainda
    # Presidente histórico: TSE não inclui eleições nacionais nos arquivos por UF → retorna vazio
    if ano >= 2026:
        uf_filter = "BR" if cd_cargo == 1 else uf.upper()
        query = f"""
            SELECT
                candidato_normalizado                          AS nm,
                NULL                                          AS partido,
                ROUND(AVG(intencao_ponderada), 1)             AS pct_t1,
                CAST(SUM(n_pesquisas) AS STRING)              AS votos,
                MAX(data_referencia)                          AS data_ultima_pesquisa
            FROM `{gold}.fact_intencao_voto`
            WHERE cd_cargo   = @cd_cargo
              AND uf         = @uf_filter
              AND ano_eleitoral = @ano
              AND intencao_ponderada IS NOT NULL
            GROUP BY candidato_normalizado
            ORDER BY pct_t1 DESC
            LIMIT 50
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
                bigquery.ScalarQueryParameter("uf_filter", "STRING", uf_filter),
                bigquery.ScalarQueryParameter("ano", "INT64", ano),
            ]
        )
    elif cd_cargo == 1:
        # Presidente: nacional, sem filtro de UF — agrega fact_municipio_candidato_eleicao
        query = f"""
            SELECT
                nm_candidato                                    AS nm,
                sg_partido                                      AS partido,
                ROUND(SUM(total_votos) / SUM(SUM(total_votos)) OVER () * 100, 1) AS pct_t1,
                CAST(SUM(total_votos) AS STRING)                AS votos
            FROM `{gold}.fact_municipio_candidato_eleicao`
            WHERE cd_cargo = 1
              AND (ano_eleicao = @ano OR ano_eleicao IS NULL)
              AND nr_turno = 1
            GROUP BY nm_candidato, sg_partido
            ORDER BY pct_t1 DESC
            LIMIT 50
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("ano", "INT64", ano),
            ]
        )
    else:
        query = f"""
            SELECT
                nm_candidato   AS nm,
                sg_partido     AS partido,
                ROUND(SUM(total_votos) / SUM(SUM(total_votos)) OVER () * 100, 1) AS pct_t1,
                CAST(SUM(total_votos) AS STRING) AS votos
            FROM `{gold}.fact_municipio_candidato_eleicao`
            WHERE sg_uf = @uf
              AND ano_eleicao = @ano
              AND cd_cargo = @cd_cargo
              AND nr_turno = 1
            GROUP BY nm_candidato, sg_partido
            ORDER BY pct_t1 DESC
            LIMIT 50
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                bigquery.ScalarQueryParameter("ano", "INT64", ano),
                bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            ]
        )

    rows = await asyncio.to_thread(
        lambda: list(client.query(query, job_config=job_config).result())
    )
    return [dict(row) for row in rows]


# ── KPIs ──────────────────────────────────────────────────────────────────


@app.get("/api/kpi")
async def get_kpi(
    cargo: str = Query("Presidente"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
) -> JSONResponse:
    """Métricas agregadas para os KPI cards do dashboard."""
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            return JSONResponse(await _bq_kpi(cargo, uf, ano))
        except Exception as exc:
            logger.warning("BigQuery KPI falhou: %s", exc)

    return JSONResponse(
        {
            "vencedor": "—",
            "vencedor_partido": "—",
            "vencedor_pct": None,
            "segundo": "—",
            "segundo_pct": None,
            "margem_pp": None,
            "total_votos": "—",
            "municipios": 0,
            "dq_score": 0,
            "fonte": "indisponivel",
        }
    )


async def _bq_kpi(cargo: str, uf: str, ano: int) -> dict:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _cargo_to_cd(cargo, 1)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

    # 2026+: sem resultados TSE — usa pesquisas agregadas (fact_intencao_voto)
    if ano >= 2026:
        uf_filter = "BR" if cd_cargo == 1 else uf.upper()
        query_2026 = f"""
            WITH ranked AS (
                SELECT
                    candidato_normalizado                       AS nm_candidato,
                    ROUND(AVG(intencao_ponderada), 1)           AS pct,
                    CAST(SUM(n_pesquisas) AS INT64)             AS n_pesquisas,
                    ROW_NUMBER() OVER (ORDER BY AVG(intencao_ponderada) DESC) AS rn
                FROM `{gold}.fact_intencao_voto`
                WHERE cd_cargo = @cd_cargo
                  AND (uf = @uf OR @uf = 'BR')
                  AND ano_eleitoral = @ano
                GROUP BY candidato_normalizado
            )
            SELECT
                MAX(IF(rn=1, nm_candidato, NULL))  AS vencedor,
                MAX(IF(rn=1, pct, NULL))            AS vencedor_pct,
                MAX(IF(rn=2, nm_candidato, NULL))   AS segundo,
                MAX(IF(rn=2, pct, NULL))             AS segundo_pct,
                SUM(n_pesquisas)                     AS total_pesquisas
            FROM ranked WHERE rn <= 2
        """
        params_2026 = [
            bigquery.ScalarQueryParameter("uf", "STRING", uf_filter),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
        ]
        rows_2026 = await asyncio.to_thread(
            lambda: list(
                client.query(
                    query_2026,
                    job_config=bigquery.QueryJobConfig(query_parameters=params_2026),
                ).result()
            )
        )
        if not rows_2026 or not rows_2026[0].get("vencedor"):
            raise ValueError("Sem dados de pesquisa 2026 para filtro aplicado")
        r2 = dict(rows_2026[0])
        v_pct2 = r2.get("vencedor_pct") or 0.0
        s_pct2 = r2.get("segundo_pct") or 0.0
        total_pesq = r2.get("total_pesquisas") or 0
        return {
            "vencedor": r2.get("vencedor", "—"),
            "vencedor_partido": "—",
            "vencedor_pct": v_pct2,
            "segundo": r2.get("segundo", "—"),
            "segundo_pct": s_pct2,
            "margem_pp": round(abs(v_pct2 - s_pct2), 1),
            "total_votos": f"{total_pesq} pesquisas",
            "municipios": 0,
            "dq_score": 85.0,
            "fonte": "bigquery_pesquisas_2026",
        }

    # Presidente: resultado nacional sem filtro de UF
    if cd_cargo == 1:
        query = f"""
            WITH ranked AS (
                SELECT
                    nm_candidato,
                    sg_partido,
                    SUM(total_votos) AS total_cand,
                    SUM(SUM(total_votos)) OVER () AS total_geral,
                    COUNT(DISTINCT cd_municipio) AS municipios,
                    ROW_NUMBER() OVER (ORDER BY SUM(total_votos) DESC) AS rn
                FROM `{gold}.fact_municipio_candidato_eleicao`
                WHERE cd_cargo = 1
                  AND (ano_eleicao = @ano OR ano_eleicao IS NULL)
                  AND nr_turno = 1
                GROUP BY nm_candidato, sg_partido
            )
            SELECT
                MAX(IF(rn=1, nm_candidato, NULL))                        AS vencedor,
                MAX(IF(rn=1, sg_partido, NULL))                          AS vencedor_partido,
                ROUND(MAX(IF(rn=1, total_cand/total_geral*100, NULL)),1) AS vencedor_pct,
                MAX(IF(rn=2, nm_candidato, NULL))                        AS segundo,
                ROUND(MAX(IF(rn=2, total_cand/total_geral*100, NULL)),1) AS segundo_pct,
                MAX(municipios)                                           AS municipios,
                MAX(total_geral)                                          AS total_votos
            FROM ranked WHERE rn <= 2
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("ano", "INT64", ano),
            ]
        )
    else:
        query = f"""
            WITH ranked AS (
                SELECT
                    nm_candidato,
                    sg_partido,
                    SUM(total_votos) AS total_cand,
                    SUM(SUM(total_votos)) OVER () AS total_geral,
                    COUNT(DISTINCT cd_municipio) AS municipios,
                    ROW_NUMBER() OVER (ORDER BY SUM(total_votos) DESC) AS rn
                FROM `{gold}.fact_municipio_candidato_eleicao`
                WHERE sg_uf = @uf AND ano_eleicao = @ano
                  AND cd_cargo = @cd_cargo AND nr_turno = 1
                GROUP BY nm_candidato, sg_partido
            )
            SELECT
                MAX(IF(rn=1, nm_candidato, NULL))                        AS vencedor,
                MAX(IF(rn=1, sg_partido, NULL))                          AS vencedor_partido,
                ROUND(MAX(IF(rn=1, total_cand/total_geral*100, NULL)),1) AS vencedor_pct,
                MAX(IF(rn=2, nm_candidato, NULL))                        AS segundo,
                ROUND(MAX(IF(rn=2, total_cand/total_geral*100, NULL)),1) AS segundo_pct,
                MAX(municipios)                                           AS municipios,
                MAX(total_geral)                                          AS total_votos
            FROM ranked WHERE rn <= 2
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                bigquery.ScalarQueryParameter("ano", "INT64", ano),
                bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            ]
        )
    rows = await asyncio.to_thread(
        lambda: list(client.query(query, job_config=job_config).result())
    )
    if not rows:
        raise ValueError("Sem dados no Gold para filtro aplicado")
    r = dict(rows[0])
    v_pct = r.get("vencedor_pct") or 0.0
    s_pct = r.get("segundo_pct") or 0.0
    total = r.get("total_votos") or 0
    return {
        "vencedor": r.get("vencedor", "—"),
        "vencedor_partido": r.get("vencedor_partido", "—"),
        "vencedor_pct": v_pct,
        "segundo": r.get("segundo", "—"),
        "segundo_pct": s_pct,
        "margem_pp": round(abs(v_pct - s_pct), 1),
        "total_votos": f"{total / 1_000_000:.1f}M" if total >= 1_000_000 else str(total),
        "municipios": r.get("municipios", 0),
        "dq_score": 99.0,
        "fonte": "bigquery",
    }


# ── Municípios ────────────────────────────────────────────────────────────


@app.get("/api/municipios")
async def get_municipios(
    cargo: str = Query("Presidente"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
    limit: int = Query(20, ge=1, le=200),
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            return JSONResponse({"municipios": await _bq_municipios(cargo, uf, ano, limit)})
        except Exception as exc:
            logger.warning("BigQuery municipios falhou: %s", exc)
    return JSONResponse({"municipios": [], "fonte": "indisponivel"})


async def _bq_municipios(cargo: str, uf: str, ano: int, limit: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _cargo_to_cd(cargo, 1)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

    query = f"""
        WITH pivot AS (
            SELECT
                nm_municipio,
                nm_candidato,
                sg_partido,
                SUM(total_votos) AS votos,
                SUM(SUM(total_votos)) OVER (PARTITION BY nm_municipio) AS total_mun,
                ROW_NUMBER() OVER (PARTITION BY nm_municipio ORDER BY SUM(total_votos) DESC) AS rk
            FROM `{gold}.fact_municipio_candidato_eleicao`
            WHERE sg_uf = @uf AND ano_eleicao = @ano
              AND cd_cargo = @cd_cargo AND nr_turno = 1
            GROUP BY nm_municipio, nm_candidato, sg_partido
        )
        SELECT
            nm_municipio                                        AS nm,
            MAX(total_mun)                                      AS total,
            ROUND(MAX(IF(rk=1, votos/total_mun*100, NULL)),1)  AS c1,
            ROUND(MAX(IF(rk=2, votos/total_mun*100, NULL)),1)  AS c2,
            ROUND(MAX(IF(rk=3, votos/total_mun*100, NULL)),1)  AS c3,
            MAX(IF(rk=1, nm_candidato, NULL))                  AS nm_c1,
            MAX(IF(rk=2, nm_candidato, NULL))                  AS nm_c2,
            MAX(IF(rk=1, sg_partido, NULL))                    AS partido_c1
        FROM pivot
        WHERE rk <= 3
        GROUP BY nm_municipio
        ORDER BY MAX(total_mun) DESC
        LIMIT @lim
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("lim", "INT64", limit),
        ]
    )
    rows = await asyncio.to_thread(
        lambda: list(client.query(query, job_config=job_config).result())
    )
    result = []
    for r in rows:
        total = r.get("total") or 0
        votos_fmt = f"{total / 1000:.0f}k" if total >= 1000 else str(total)
        result.append(
            {
                "nm": r.get("nm", ""),
                "votos": votos_fmt,
                "c1": r.get("c1") or 0.0,
                "c2": r.get("c2") or 0.0,
                "c3": r.get("c3") or 0.0,
                "nm_c1": r.get("nm_c1", ""),
                "nm_c2": r.get("nm_c2", ""),
                "partido_c1": r.get("partido_c1", ""),
            }
        )
    return result


# ── Resultados por cargo / UF / ano / turno ───────────────────────────────


@app.get("/api/resultados/{cargo}")
async def get_resultados(
    cargo: str,
    uf: str = Query("SP"),
    ano: int = Query(2022),
    turno: int = Query(1),
) -> JSONResponse:
    """Resultados eleitorais agregados por candidato para o cargo/UF/ano/turno."""
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            data = await _bq_resultados(cargo, uf, ano, turno)
            return JSONResponse(
                {"cargo": cargo, "uf": uf, "ano": ano, "turno": turno, "candidatos": data}
            )
        except Exception as exc:
            logger.warning("BigQuery resultados falhou: %s", exc)

    data = await asyncio.to_thread(_local_resultados, cargo, uf, ano, turno)
    return JSONResponse({
        "cargo": cargo, "uf": uf, "ano": ano, "turno": turno,
        "candidatos": data,
        "fonte": "local" if data else "indisponivel",
    })


def _local_resultados(cargo: str, uf: str, ano: int, turno: int) -> list[dict]:
    import pandas as pd

    cd_cargo = _cargo_to_cd(cargo, 1)
    frames: list[pd.DataFrame] = []
    for f in sorted(_LOCAL_SILVER_DIR.glob("tse_*.parquet")):
        try:
            frames.append(pd.read_parquet(f))
        except Exception:
            continue
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"qt_votos_nominais": "total_votos", "ds_sit_cand_tot": "ds_situacao"})
    needed = {"sg_uf", "cd_cargo", "nr_turno", "nm_candidato", "ano_eleicao", "total_votos"}
    if not needed.issubset(df.columns):
        return []
    mask = (
        (df["sg_uf"].str.upper() == uf.upper())
        & (df["cd_cargo"].astype(str) == str(cd_cargo))
        & (df["nr_turno"].astype(str) == str(turno))
        & (df["ano_eleicao"].astype(str) == str(ano))
        & df["nm_candidato"].notna()
        & (~df["nm_candidato"].str.upper().str.strip().isin(_INVALIDOS_NOMES))
        & (~df["nm_candidato"].str.startswith("#", na=False))
    )
    df = df[mask].copy()
    if df.empty:
        return []
    df["total_votos"] = pd.to_numeric(df["total_votos"], errors="coerce").fillna(0)
    if "sg_partido" not in df.columns:
        df["sg_partido"] = ""
    grp = (
        df.groupby(["nm_candidato", "sg_partido"])
        .agg(total_votos=("total_votos", "sum"))
        .reset_index()
    )
    total_all = grp["total_votos"].sum() or 1
    grp["pct"] = (grp["total_votos"] / total_all * 100).round(2)
    grp = grp.sort_values("total_votos", ascending=False).reset_index(drop=True)
    return [
        {
            "candidato": r["nm_candidato"],
            "nm_candidato": r["nm_candidato"],
            "partido": r.get("sg_partido") or "",
            "total_votos": int(r["total_votos"]),
            "pct_votos_validos": float(r["pct"]),
            "pct": float(r["pct"]),
        }
        for _, r in grp.iterrows()
    ]


async def _bq_resultados(cargo: str, uf: str, ano: int, turno: int) -> list[dict]:
    """Query ao BigQuery Gold — fact_municipio_candidato_eleicao agregado por candidato."""
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _cargo_to_cd(cargo, 1)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

    query = f"""
        SELECT
            nm_candidato                                                        AS candidato,
            sg_partido                                                          AS partido,
            SUM(total_votos)                                                    AS total_votos,
            ROUND(
                SUM(total_votos) / NULLIF(SUM(SUM(total_votos)) OVER (), 0) * 100,
                2
            )                                                                   AS pct_votos_validos
        FROM `{gold}.fact_municipio_candidato_eleicao`
        WHERE sg_uf       = @uf
          AND ano_eleicao = @ano
          AND cd_cargo    = @cd_cargo
          AND nr_turno    = @turno
        GROUP BY nm_candidato, sg_partido
        ORDER BY pct_votos_validos DESC
        LIMIT 50
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("turno", "INT64", turno),
        ]
    )
    rows = await asyncio.to_thread(
        lambda: list(client.query(query, job_config=job_config).result())
    )
    return [
        {
            "candidato": r.get("candidato", ""),
            "nm_candidato": r.get("candidato", ""),
            "partido": r.get("partido", ""),
            "total_votos": r.get("total_votos") or 0,
            "pct_votos_validos": float(r.get("pct_votos_validos") or 0.0),
            "pct": float(r.get("pct_votos_validos") or 0.0),
        }
        for r in rows
    ]


# ── Google Trends ─────────────────────────────────────────────────────────


@app.get("/api/trends")
async def get_trends(
    cargo: str = Query("Presidente"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
) -> JSONResponse:
    """
    Busca Google Trends para os candidatos do cargo.
    Usa pytrends real se disponível, senão retorna mock.
    """
    # Get candidate keywords from BQ; if unavailable return empty
    keywords: list[str] = []
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery as _bq_mod

            _bq_client = _bq_mod.Client(project=settings.gcp_project_id)
            _cd_cargo = _cargo_to_cd(cargo, 1)
            _q = (
                f"SELECT nm_candidato FROM "
                f"`{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_candidato_eleicao`"
                f" WHERE cd_cargo = @cd_cargo AND ano_eleicao = @ano"
                f" ORDER BY total_votos DESC LIMIT 3"
            )
            _job_cfg = _bq_mod.QueryJobConfig(
                query_parameters=[
                    _bq_mod.ScalarQueryParameter("cd_cargo", "INT64", _cd_cargo),
                    _bq_mod.ScalarQueryParameter("ano", "INT64", ano),
                ]
            )
            keywords = [
                r["nm_candidato"] for r in _bq_client.query(_q, job_config=_job_cfg).result()
            ]
        except Exception:
            pass
    if not keywords:
        return JSONResponse({"labels": [], "series": {}, "status": "indisponivel"})
    timeframe = f"{ano}-06-01 {ano}-10-30"

    try:
        df = fetch_trends(keywords, timeframe=timeframe, geo="BR")
        if not df.empty:
            result = {kw: df[kw].tolist() if kw in df.columns else [] for kw in keywords}
            return JSONResponse({"labels": df.index.astype(str).tolist(), "series": result})
    except Exception as exc:
        logger.warning("Google Trends falhou: %s", exc)

    return JSONResponse({"labels": [], "series": {}, "status": "indisponivel"})


# ── Meta Ads ──────────────────────────────────────────────────────────────


@app.get("/api/meta")
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
    if not token:
        return JSONResponse({"candidatos": [], "status": "indisponivel"})

    candidatos_nm: list[str] = []
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery as _bq_mod

            _bq_client = _bq_mod.Client(project=settings.gcp_project_id)
            _cd_cargo = _cargo_to_cd(cargo, 1)
            _q = (
                f"SELECT nm_candidato FROM "
                f"`{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_candidato_eleicao`"
                f" WHERE cd_cargo = @cd_cargo AND ano_eleicao = @ano"
                f" ORDER BY total_votos DESC LIMIT 4"
            )
            _job_cfg = _bq_mod.QueryJobConfig(
                query_parameters=[
                    _bq_mod.ScalarQueryParameter("cd_cargo", "INT64", _cd_cargo),
                    _bq_mod.ScalarQueryParameter("ano", "INT64", ano),
                ]
            )
            candidatos_nm = [
                r["nm_candidato"] for r in _bq_client.query(_q, job_config=_job_cfg).result()
            ]
        except Exception:
            pass
    if not candidatos_nm:
        return JSONResponse({"candidatos": [], "status": "indisponivel"})

    results = []
    for nm in candidatos_nm:
        try:
            ads_df, _, _ = fetch_meta_ads([nm], access_token=token, country="BR")
            spend = ads_df["vl_gasto_max"].sum() if not ads_df.empty else 0.0
            results.append({"candidato": nm, "gasto_r": spend})
        except Exception as exc:
            logger.warning("Meta Ads %s falhou: %s", nm, exc)
            results.append({"candidato": nm, "gasto_r": 0.0})
    return JSONResponse({"candidatos": results})


# ── Socioeconômico ─────────────────────────────────────────────────────────


@app.get("/api/socioeconomico")
async def get_socioeconomico(
    uf: str = Query("SP"),
    ano: int = Query(2022),
    limit: int = Query(20, ge=1, le=200),
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            result = await _bq_socioeconomico(uf, ano, limit)
            if result:
                return JSONResponse({"municipios": result})
            logger.warning("BigQuery socioeconomico: Gold vazio para UF=%s ano=%s", uf, ano)
            # Silver TSE fallback — IBGE cols are wide in Silver TSE tables
            try:
                from google.cloud import bigquery as _bq

                silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"
                query_s = f"""
                    SELECT
                        SAFE_CAST(cd_municipio_ibge AS INT64) AS cd_municipio_ibge,
                        sg_uf,
                        ANY_VALUE(nm_municipio) AS nm_municipio,
                        MAX(SAFE_CAST(populacao_total AS FLOAT64)) AS populacao_total,
                        MAX(SAFE_CAST(renda_per_capita AS FLOAT64)) AS renda_per_capita,
                        MAX(SAFE_CAST(taxa_alfabetizacao AS FLOAT64)) AS taxa_alfabetizacao,
                        MAX(SAFE_CAST(taxa_analfabetismo AS FLOAT64)) AS taxa_analfabetismo
                    FROM `{silver}.tse_*`
                    WHERE REGEXP_CONTAINS(_TABLE_SUFFIX, r'^[a-z]{{2}}_2022$')
                      AND sg_uf = @uf AND cd_municipio_ibge IS NOT NULL
                    GROUP BY cd_municipio_ibge, sg_uf
                    ORDER BY populacao_total DESC NULLS LAST
                    LIMIT @lim
                """
                _client = _bq.Client(project=settings.gcp_project_id)
                _jc = _bq.QueryJobConfig(
                    query_parameters=[
                        _bq.ScalarQueryParameter("uf", "STRING", uf.upper()),
                        _bq.ScalarQueryParameter("lim", "INT64", limit),
                    ]
                )
                _rows = list(_client.query(query_s, job_config=_jc).result())
                if _rows:
                    _result = [
                        {
                            "nm": r.get("nm_municipio") or str(r.get("cd_municipio_ibge", "")),
                            "populacao": int(r.get("populacao_total") or 0),
                            "taxa_alfabetizacao": round(float(r.get("taxa_alfabetizacao") or 0), 1),
                            "taxa_analfabetismo": round(float(r.get("taxa_analfabetismo") or 0), 1),
                            "pct_urbano": 0.0,
                            "pct_0_14": 0.0,
                            "pct_60_mais": 0.0,
                            "idhm": 0.0,
                            "renda_per_capita": round(float(r.get("renda_per_capita") or 0), 0),
                            "gini": 0.0,
                        }
                        for r in _rows
                    ]
                    return JSONResponse({"municipios": _result, "fonte": "silver_tse"})
            except Exception as _exc_s:
                logger.warning("Silver TSE socioeconomico falhou: %s", _exc_s)
            return JSONResponse(
                {
                    "municipios": [],
                    "fonte": "gold_empty",
                    "hint": "Execute spepe-gold-build para popular fact_ibge_municipio",
                }
            )
        except Exception as exc:
            logger.warning("BigQuery socioeconomico falhou: %s", exc)
            return JSONResponse({"municipios": [], "fonte": "bq_error", "error": str(exc)})
    data = _local_socioeconomico(uf, ano, limit)
    return JSONResponse({"municipios": data, "fonte": "local" if data else "indisponivel"})


async def _bq_socioeconomico(uf: str, ano: int, limit: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
    query = f"""
        SELECT nm_municipio,
               populacao_total, taxa_alfabetizacao, taxa_analfabetismo,
               pct_urbano, pct_0_14, pct_60_mais,
               idhm, renda_per_capita, gini, pct_extrema_pobreza
        FROM `{gold}.fact_ibge_municipio`
        WHERE sg_uf = @uf AND ano = @ano
        ORDER BY populacao_total DESC NULLS LAST
        LIMIT @lim
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("lim", "INT64", limit),
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())
    return [
        {
            "nm": r.get("nm_municipio", ""),
            "populacao": r.get("populacao_total") or 0,
            "taxa_alfabetizacao": round((r.get("taxa_alfabetizacao") or 0), 1),
            "taxa_analfabetismo": round((r.get("taxa_analfabetismo") or 0), 1),
            "pct_urbano": round((r.get("pct_urbano") or 0), 1),
            "pct_0_14": round((r.get("pct_0_14") or 0), 1),
            "pct_60_mais": round((r.get("pct_60_mais") or 0), 1),
            "idhm": round(r.get("idhm") or 0, 3),
            "renda_per_capita": round(r.get("renda_per_capita") or 0, 0),
            "gini": round(r.get("gini") or 0, 3),
        }
        for r in rows
    ]


def _local_socioeconomico(uf: str, ano: int, limit: int) -> list[dict]:
    import pandas as pd

    files = sorted(_LOCAL_SILVER_DIR.glob("ibge_*.parquet"))
    if not files:
        return []
    dfs = []
    for f in files:
        try:
            dfs.append(pd.read_parquet(f))
        except Exception:
            pass
    if not dfs:
        return []
    df = pd.concat(dfs, ignore_index=True)
    if "sg_uf" in df.columns:
        df = df[df["sg_uf"].str.upper() == uf.upper()]
    if "ano" in df.columns:
        df = df[df["ano"] == ano]
    if "idhm" in df.columns:
        df = df.sort_values("idhm", ascending=False)
    df = df.head(limit)
    result = []
    for _, r in df.iterrows():
        pct_pobreza = float(r.get("pct_extrema_pobreza") or 0)
        taxa_analf = float(r.get("taxa_analfabetismo") or 0)
        pct_urb = float(r.get("pct_urbano") or 0)
        result.append(
            {
                "nm": str(r.get("nm_municipio", "")),
                "idhm": round(float(r.get("idhm") or 0), 3),
                "renda_per_capita": round(float(r.get("renda_per_capita") or 0), 0),
                "gini": round(float(r.get("gini") or 0), 3),
                "pct_extrema_pobreza": round(
                    pct_pobreza * 100 if pct_pobreza <= 1 else pct_pobreza, 1
                ),
                "taxa_analfabetismo": round(taxa_analf * 100 if taxa_analf <= 1 else taxa_analf, 1),
                "pct_urbano": round(pct_urb * 100 if pct_urb <= 1 else pct_urb, 1),
                "populacao": int(r.get("populacao_total") or 0),
            }
        )
    return result


# ── Segurança Pública ──────────────────────────────────────────────────────


@app.get("/api/seguranca")
async def get_seguranca(
    uf: str = Query("SP"),
    ano: int = Query(2022),
    limit: int = Query(15, ge=1, le=100),
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            result = await _bq_seguranca(uf, ano, limit)
            if result:
                return JSONResponse({"municipios": result})
            return JSONResponse(
                {
                    "municipios": [],
                    "fonte": "gold_empty",
                    "hint": "fact_seguranca_municipio vazia — execute spepe-gold-build",
                }
            )
        except Exception as exc:
            logger.warning("BigQuery seguranca falhou: %s", exc)
            # Silver fallback for seguranca
            try:
                from google.cloud import bigquery as _bq

                silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"
                query_seg = f"""
                    SELECT
                        CAST(cd_municipio_ibge AS INT64) AS cd_municipio_ibge,
                        sg_uf,
                        CAST(NULL AS STRING) AS nm_municipio,
                        COALESCE(SAFE_CAST(ano AS INT64), @ano) AS ano,
                        SAFE_CAST(ivs_total AS FLOAT64) AS ivs_total,
                        SAFE_CAST(ivs_infraestrutura AS FLOAT64) AS ivs_infraestrutura,
                        SAFE_CAST(ivs_capital_humano AS FLOAT64) AS ivs_capital_humano,
                        SAFE_CAST(ivs_renda_trabalho AS FLOAT64) AS ivs_renda_trabalho,
                        SAFE_CAST(taxa_homicidio AS FLOAT64) AS taxa_homicidio_100k,
                        CAST(NULL AS FLOAT64) AS taxa_roubo_100k,
                        CAST(NULL AS INT64) AS qt_feminicidio
                    FROM `{silver}.seguranca_municipal`
                    WHERE sg_uf = @uf AND COALESCE(SAFE_CAST(ano AS INT64), 2022) = @ano
                    ORDER BY ivs_total DESC NULLS LAST
                    LIMIT @lim
                """
                _client = _bq.Client(project=settings.gcp_project_id)
                _jc = _bq.QueryJobConfig(
                    query_parameters=[
                        _bq.ScalarQueryParameter("uf", "STRING", uf.upper()),
                        _bq.ScalarQueryParameter("ano", "INT64", ano),
                        _bq.ScalarQueryParameter("lim", "INT64", limit),
                    ]
                )
                _rows_seg = list(_client.query(query_seg, job_config=_jc).result())
                if _rows_seg:
                    return JSONResponse(
                        {
                            "municipios": [
                                {
                                    "nm": r.get("nm_municipio")
                                    or str(r.get("cd_municipio_ibge", "")),
                                    "taxa_homicidio": round(
                                        float(r.get("taxa_homicidio_100k") or 0), 1
                                    ),
                                    "ivs_total": round(float(r.get("ivs_total") or 0), 3),
                                    "ivs_infra": round(float(r.get("ivs_infraestrutura") or 0), 3),
                                    "ivs_capital_humano": round(
                                        float(r.get("ivs_capital_humano") or 0), 3
                                    ),
                                    "ivs_renda": round(float(r.get("ivs_renda_trabalho") or 0), 3),
                                    "taxa_roubo": 0.0,
                                    "qt_feminicidio": 0,
                                }
                                for r in _rows_seg
                            ],
                            "fonte": "silver_seguranca",
                        }
                    )
            except Exception as _exc_seg:
                logger.warning("Silver seguranca falhou: %s", _exc_seg)
            return JSONResponse({"municipios": [], "fonte": "bq_error", "error": str(exc)})
    data = _local_seguranca(uf, ano, limit)
    return JSONResponse({"municipios": data, "fonte": "local" if data else "indisponivel"})


async def _bq_seguranca(uf: str, ano: int, limit: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
    query = f"""
        SELECT s.cd_municipio_ibge,
               i.nm_municipio,
               s.taxa_homicidio_100k,
               s.ivs_total, s.ivs_infraestrutura,
               s.ivs_capital_humano, s.ivs_renda_trabalho,
               s.taxa_roubo_100k, s.qt_feminicidio
        FROM `{gold}.fact_seguranca_municipio` s
        LEFT JOIN (
            SELECT DISTINCT cd_municipio_ibge, nm_municipio
            FROM `{gold}.fact_ibge_municipio`
        ) i USING (cd_municipio_ibge)
        WHERE s.sg_uf = @uf AND s.ano = @ano
        ORDER BY s.ivs_total DESC NULLS LAST
        LIMIT @lim
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("lim", "INT64", limit),
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())
    return [
        {
            "nm": r.get("nm_municipio") or str(r.get("cd_municipio_ibge", "")),
            "taxa_homicidio": round(r.get("taxa_homicidio_100k") or 0, 1),
            "ivs_total": round(r.get("ivs_total") or 0, 3),
            "ivs_infra": round(r.get("ivs_infraestrutura") or 0, 3),
            "ivs_capital_humano": round(r.get("ivs_capital_humano") or 0, 3),
            "ivs_renda": round(r.get("ivs_renda_trabalho") or 0, 3),
            "taxa_roubo": round(r.get("taxa_roubo_100k") or 0, 1),
            "qt_feminicidio": r.get("qt_feminicidio") or 0,
        }
        for r in rows
    ]


def _local_seguranca(uf: str, ano: int, limit: int) -> list[dict]:
    import pandas as pd

    files = sorted(_LOCAL_SILVER_DIR.glob("seguranca_municipal_*.parquet"))
    if not files:
        return []
    dfs = []
    for f in files:
        try:
            dfs.append(pd.read_parquet(f))
        except Exception:
            pass
    if not dfs:
        return []
    df = pd.concat(dfs, ignore_index=True)
    if "sg_uf" in df.columns:
        df = df[df["sg_uf"].str.upper() == uf.upper()]
    if "ano" in df.columns:
        df = df[df["ano"] == ano]
    sort_col = next((c for c in ("taxa_homicidio_100k", "taxa_homicidio") if c in df.columns), None)
    if sort_col:
        df = df.sort_values(sort_col, ascending=False)
    df = df.head(limit)
    result = []
    for _, r in df.iterrows():
        result.append(
            {
                "nm": str(r.get("nm_municipio") or r.get("cd_municipio_ibge", "")),
                "taxa_homicidio": round(
                    float(r.get("taxa_homicidio_100k") or r.get("taxa_homicidio") or 0), 1
                ),
                "ivs_total": round(float(r.get("ivs_total") or r.get("ivs_valor") or 0), 3),
                "ivs_infra": round(float(r.get("ivs_infraestrutura") or 0), 3),
                "ivs_capital_humano": round(float(r.get("ivs_capital_humano") or 0), 3),
                "ivs_renda": round(float(r.get("ivs_renda_trabalho") or 0), 3),
                "taxa_roubo": round(float(r.get("taxa_roubo_100k") or 0), 1),
                "qt_feminicidio": int(r.get("qt_feminicidio") or 0),
            }
        )
    return result


# ── Saúde Pública ──────────────────────────────────────────────────────────


@app.get("/api/saude")
async def get_saude(
    uf: str = Query("SP"),
    ano: int = Query(2022),
    limit: int = Query(15, ge=1, le=100),
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            result = await _bq_saude(uf, ano, limit)
            if result:
                return JSONResponse({"municipios": result})
            return JSONResponse(
                {
                    "municipios": [],
                    "fonte": "gold_empty",
                    "hint": "fact_saude_municipio vazia — DataSUS Bronze não exportou colunas esperadas",
                }
            )
        except Exception as exc:
            logger.warning("BigQuery saude falhou: %s", exc)
            # Silver fallback for saude
            try:
                from google.cloud import bigquery as _bq

                silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"
                query_sau = f"""
                    SELECT
                        CAST(cd_municipio_ibge AS INT64) AS cd_municipio_ibge,
                        sg_uf,
                        CAST(NULL AS STRING) AS nm_municipio,
                        COALESCE(SAFE_CAST(ano AS INT64), @ano) AS ano,
                        SAFE_CAST(taxa_mortalidade_infantil_1000 AS FLOAT64) AS taxa_mortalidade_infantil_1000,
                        CAST(NULL AS FLOAT64) AS taxa_mortalidade_materna_100k,
                        SAFE_CAST(pct_cobertura_plano_saude AS FLOAT64) AS pct_cobertura_plano_saude,
                        CAST(NULL AS FLOAT64) AS idsus_score
                    FROM `{silver}.saude_municipal`
                    WHERE sg_uf = @uf AND COALESCE(SAFE_CAST(ano AS INT64), 2022) = @ano
                    ORDER BY taxa_mortalidade_infantil_1000 ASC NULLS LAST
                    LIMIT @lim
                """
                _client = _bq.Client(project=settings.gcp_project_id)
                _jc = _bq.QueryJobConfig(
                    query_parameters=[
                        _bq.ScalarQueryParameter("uf", "STRING", uf.upper()),
                        _bq.ScalarQueryParameter("ano", "INT64", ano),
                        _bq.ScalarQueryParameter("lim", "INT64", limit),
                    ]
                )
                _rows_sau = list(_client.query(query_sau, job_config=_jc).result())
                if _rows_sau:
                    return JSONResponse(
                        {
                            "municipios": [
                                {
                                    "nm": r.get("nm_municipio")
                                    or str(r.get("cd_municipio_ibge", "")),
                                    "tx_mortalidade_infantil": round(
                                        float(r.get("taxa_mortalidade_infantil_1000") or 0), 1
                                    ),
                                    "tx_mortalidade_materna": 0.0,
                                    "pct_cobertura_plano": round(
                                        float(r.get("pct_cobertura_plano_saude") or 0) * 100, 1
                                    ),
                                    "idsus": 0.0,
                                }
                                for r in _rows_sau
                            ],
                            "fonte": "silver_saude",
                        }
                    )
            except Exception as _exc_sau:
                logger.warning("Silver saude falhou: %s", _exc_sau)
            return JSONResponse({"municipios": [], "fonte": "bq_error", "error": str(exc)})
    data = _local_saude(uf, ano, limit)
    return JSONResponse({"municipios": data, "fonte": "local" if data else "indisponivel"})


async def _bq_saude(uf: str, ano: int, limit: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
    query = f"""
        SELECT s.cd_municipio_ibge,
               i.nm_municipio,
               s.taxa_mortalidade_infantil_1000,
               s.taxa_mortalidade_materna_100k,
               s.pct_cobertura_plano_saude,
               s.idsus_score
        FROM `{gold}.fact_saude_municipio` s
        LEFT JOIN (
            SELECT DISTINCT cd_municipio_ibge, nm_municipio
            FROM `{gold}.fact_ibge_municipio`
        ) i USING (cd_municipio_ibge)
        WHERE s.sg_uf = @uf AND s.ano = @ano
        ORDER BY s.taxa_mortalidade_infantil_1000 ASC NULLS LAST
        LIMIT @lim
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("lim", "INT64", limit),
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())
    return [
        {
            "nm": r.get("nm_municipio") or str(r.get("cd_municipio_ibge", "")),
            "tx_mortalidade_infantil": round(r.get("taxa_mortalidade_infantil_1000") or 0, 1),
            "tx_mortalidade_materna": round(r.get("taxa_mortalidade_materna_100k") or 0, 1),
            "pct_cobertura_plano": round((r.get("pct_cobertura_plano_saude") or 0) * 100, 1),
            "idsus": round(r.get("idsus_score") or 0, 3),
        }
        for r in rows
    ]


def _local_saude(uf: str, ano: int, limit: int) -> list[dict]:
    import pandas as pd

    files = sorted(_LOCAL_SILVER_DIR.glob("saude_municipal_*.parquet"))
    if not files:
        return []
    dfs = []
    for f in files:
        try:
            dfs.append(pd.read_parquet(f))
        except Exception:
            pass
    if not dfs:
        return []
    df = pd.concat(dfs, ignore_index=True)
    if "sg_uf" in df.columns:
        df = df[df["sg_uf"].str.upper() == uf.upper()]
    if "ano" in df.columns:
        df = df[df["ano"] == ano]
    sort_col = next(
        (
            c
            for c in ("taxa_mortalidade_infantil_1000", "tx_mortalidade_infantil")
            if c in df.columns
        ),
        None,
    )
    if sort_col:
        df = df.sort_values(sort_col, ascending=True)
    df = df.head(limit)
    result = []
    for _, r in df.iterrows():
        cobertura = float(r.get("pct_cobertura_plano_saude") or r.get("cobertura_esf_pct") or 0)
        result.append(
            {
                "nm": str(r.get("nm_municipio") or r.get("cd_municipio_ibge", "")),
                "tx_mortalidade_infantil": round(
                    float(
                        r.get("taxa_mortalidade_infantil_1000")
                        or r.get("tx_mortalidade_infantil")
                        or 0
                    ),
                    1,
                ),
                "tx_mortalidade_materna": round(
                    float(
                        r.get("taxa_mortalidade_materna_100k")
                        or r.get("tx_mortalidade_materna")
                        or 0
                    ),
                    1,
                ),
                "pct_cobertura_plano": round(cobertura * 100 if cobertura <= 1 else cobertura, 1),
                "idsus": round(float(r.get("idsus_score") or 0), 3),
            }
        )
    return result


# ── Pesquisas Eleitorais ───────────────────────────────────────────────────


@app.get("/api/pesquisas")
async def get_pesquisas(
    cargo: str = Query("Presidente"),
    sg_uf: str = Query("BR"),
    ano: int = Query(2026),
    tipo: str = Query("corrente"),  # "corrente" | "historica" | "all"
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            return JSONResponse(await _bq_pesquisas(cargo, sg_uf, ano, tipo))
        except Exception as exc:
            logger.warning("BigQuery pesquisas falhou: %s", exc)
    data = _local_pesquisas(cargo, sg_uf, ano, tipo)
    return JSONResponse({**data, "fonte": "local" if data["series"] else "indisponivel"})


async def _bq_pesquisas(cargo: str, sg_uf: str, ano: int, tipo: str = "corrente") -> dict:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
    cd_cargo = _cargo_to_cd(cargo, 1)

    # Try detailed view first; if it fails (table not yet built), fall back to fact_intencao_voto
    try:
        tipo_filter = ""
        params = [
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("sg_uf", "STRING", sg_uf.upper()),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
        ]
        if tipo in ("corrente", "historica"):
            tipo_filter = "AND tipo_pesquisa = @tipo_pesquisa"
            params.append(bigquery.ScalarQueryParameter("tipo_pesquisa", "STRING", tipo))
        query = f"""
            SELECT data_pesquisa_inicio, instituto, candidato, tipo_pesquisa,
                   intencao_pct, intencao_ajustada, house_effect, margem_erro
            FROM `{gold}.vw_pesquisa_intencao_detalhada`
            WHERE cd_cargo = @cd_cargo
              AND (uf = @sg_uf OR @sg_uf = 'BR')
              AND ano_eleitoral = @ano
              {tipo_filter}
            ORDER BY candidato, data_pesquisa_inicio
        """
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(client.query(query, job_config=job_config).result())
        if rows:
            by_candidato: dict[str, list] = {}
            institutes: dict[str, float] = {}
            for r in rows:
                cand = r.get("candidato", "")
                by_candidato.setdefault(cand, []).append(
                    {
                        "data": str(r.get("data_pesquisa_inicio", ""))[:7],
                        "instituto": r.get("instituto", ""),
                        "intencao": round(r.get("intencao_pct") or 0, 1),
                        "ajustada": round(
                            r.get("intencao_ajustada") or r.get("intencao_pct") or 0, 1
                        ),
                        "tipo": r.get("tipo_pesquisa", ""),
                    }
                )
                inst = r.get("instituto", "")
                if inst and inst not in institutes:
                    institutes[inst] = round(r.get("house_effect") or 0, 2)
            series = [{"candidato": c, "pontos": pts} for c, pts in by_candidato.items()]
            house_effects = [{"instituto": k, "house_effect": v} for k, v in institutes.items()]
            return {
                "series": series,
                "house_effects": house_effects,
                "tipo": tipo,
                "fonte": "vw_detalhada",
            }
    except Exception as exc:
        logger.debug(
            "vw_pesquisa_intencao_detalhada indisponível (%s) — usando fact_intencao_voto", exc
        )

    # Fallback: fact_intencao_voto (aggregated polls, always available after gold-build)
    params_fb = [
        bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
        bigquery.ScalarQueryParameter("sg_uf", "STRING", sg_uf.upper()),
        bigquery.ScalarQueryParameter("ano", "INT64", ano),
    ]
    query_fb = f"""
        SELECT FORMAT_DATE('%Y-%m', data_referencia)    AS mes,
               candidato_normalizado                     AS candidato,
               ROUND(AVG(intencao_ponderada), 1)         AS intencao,
               STRING_AGG(institutos, ' | '
                   ORDER BY institutos LIMIT 3)          AS institutos_raw,
               SUM(n_pesquisas)                          AS n_pesquisas
        FROM `{gold}.fact_intencao_voto`
        WHERE cd_cargo = @cd_cargo
          AND (uf = @sg_uf OR @sg_uf = 'BR')
          AND ano_eleitoral = @ano
        GROUP BY mes, candidato_normalizado
        ORDER BY candidato_normalizado, mes
    """
    job_config_fb = bigquery.QueryJobConfig(query_parameters=params_fb)
    rows_fb = list(client.query(query_fb, job_config=job_config_fb).result())
    by_cand: dict[str, list] = {}
    all_institutes: set[str] = set()
    for r in rows_fb:
        cand = r.get("candidato", "")
        inst_raw = r.get("institutos_raw", "") or ""
        # institutos_raw is stringified JSON arrays joined with ' | '
        try:
            parsed = []
            for chunk in inst_raw.split(" | "):
                chunk = chunk.strip()
                if chunk.startswith("["):
                    parsed.extend(json.loads(chunk))
                elif chunk:
                    parsed.append(chunk)
            inst_label = ", ".join(sorted(set(i for i in parsed if i)))
        except Exception:
            inst_label = inst_raw or "Agregado"
        by_cand.setdefault(cand, []).append(
            {
                "data": str(r.get("mes", "")),
                "instituto": inst_label or "Agregado",
                "intencao": float(r.get("intencao") or 0),
                "ajustada": float(r.get("intencao") or 0),
                "tipo": "corrente",
            }
        )
        for inst in (i.strip() for i in inst_label.split(",") if i.strip()):
            all_institutes.add(inst)
    series = [{"candidato": c, "pontos": pts} for c, pts in by_cand.items()]
    house_effects = [{"instituto": inst, "house_effect": 0.0} for inst in sorted(all_institutes)]
    return {
        "series": series,
        "house_effects": house_effects,
        "tipo": tipo,
        "fonte": "fact_intencao_voto",
    }


def _local_pesquisas(cargo: str, sg_uf: str, ano: int, tipo: str) -> dict:
    import pandas as pd

    cd_cargo = _cargo_to_cd(cargo, 1)
    files = sorted(_LOCAL_SILVER_DIR.glob("fact_pesquisa_*.parquet"))
    if not files:
        return {"series": [], "house_effects": [], "tipo": tipo}
    dfs = []
    for f in files:
        try:
            dfs.append(pd.read_parquet(f))
        except Exception:
            pass
    if not dfs:
        return {"series": [], "house_effects": [], "tipo": tipo}
    df = pd.concat(dfs, ignore_index=True)
    if "cd_cargo" in df.columns:
        df = df[df["cd_cargo"] == cd_cargo]
    if "uf" in df.columns and sg_uf.upper() != "BR":
        df = df[df["uf"].str.upper() == sg_uf.upper()]
    if "data_pesquisa_inicio" in df.columns:
        df = df[pd.to_datetime(df["data_pesquisa_inicio"], errors="coerce").dt.year == ano]
    if tipo in ("corrente", "historica") and "tipo_pesquisa" in df.columns:
        df = df[df["tipo_pesquisa"] == tipo]
    by_candidato: dict[str, list] = {}
    institutes: dict[str, float] = {}
    for _, r in df.iterrows():
        cand = str(r.get("candidato", ""))
        by_candidato.setdefault(cand, []).append(
            {
                "data": str(r.get("data_pesquisa_inicio", ""))[:7],
                "instituto": str(r.get("instituto", "")),
                "intencao": round(float(r.get("intencao_pct") or 0), 1),
                "ajustada": round(
                    float(r.get("intencao_ajustada") or r.get("intencao_pct") or 0), 1
                ),
                "tipo": str(r.get("tipo_pesquisa", "")),
            }
        )
        inst = str(r.get("instituto", ""))
        if inst and inst not in institutes:
            institutes[inst] = round(float(r.get("house_effect") or 0), 2)
    series = [{"candidato": c, "pontos": pts} for c, pts in by_candidato.items()]
    house_effects = [{"instituto": k, "house_effect": v} for k, v in institutes.items()]
    return {"series": series, "house_effects": house_effects, "tipo": tipo}


# ── Pesquisas: Intenção de Voto (fact_pesquisa_intencao Silver) ───────────


@app.get("/api/pesquisas/intencao")
async def get_pesquisas_intencao(
    candidato: str | None = Query(None, description="Filtro parcial por nome normalizado"),
    uf: str = Query("BR"),
    cargo: str = Query("presidente", description="presidente | governador | senador"),
    ano: int = Query(2026),
    instituto: str | None = Query(None, description="Filtro exato por instituto"),
    formato: str = Query("serie", description="serie | tabela"),
    janela_dias: int = Query(30, ge=1, le=365),
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            return JSONResponse(
                await _bq_pesquisas_intencao(
                    candidato, uf, cargo, ano, instituto, formato, janela_dias
                )
            )
        except Exception as exc:
            logger.warning("BigQuery pesquisas/intencao falhou: %s", exc)
    return JSONResponse({"status": "no_data", "message": "Pesquisas requerem BigQuery", "rows": []})


async def _bq_pesquisas_intencao(
    candidato: str | None,
    uf: str,
    cargo: str,
    ano: int,
    instituto: str | None,
    formato: str,
    janela_dias: int,
) -> dict:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"

    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("ano", "INT64", ano),
        bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
        bigquery.ScalarQueryParameter("cargo", "STRING", cargo.lower()),
        bigquery.ScalarQueryParameter("janela_dias", "INT64", janela_dias),
    ]
    candidato_filter = ""
    if candidato:
        candidato_filter = (
            "AND LOWER(candidato_normalizado) LIKE CONCAT('%', LOWER(@candidato), '%')"
        )
        params.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))

    instituto_filter = ""
    if instituto:
        instituto_filter = "AND LOWER(instituto) = LOWER(@instituto)"
        params.append(bigquery.ScalarQueryParameter("instituto", "STRING", instituto))

    query = f"""
        SELECT
            CAST(data_pesquisa_fim AS DATE)                              AS data_referencia,
            candidato_normalizado,
            AVG(intencao_pct)                                            AS intencao_media,
            SAFE_DIVIDE(
                SUM(intencao_pct * COALESCE(SAFE_CAST(n_entrevistados AS FLOAT64), 1)),
                NULLIF(SUM(COALESCE(SAFE_CAST(n_entrevistados AS FLOAT64), 1)), 0)
            )                                                            AS intencao_ponderada,
            AVG(intencao_ajustada)                                       AS intencao_ajustada_media,
            COUNT(*)                                                     AS n_pesquisas,
            TO_JSON_STRING(ARRAY_AGG(DISTINCT instituto IGNORE NULLS))   AS institutos
        FROM `{silver}.fact_pesquisa_intencao`
        WHERE ano = @ano
          AND LOWER(COALESCE(uf, 'BR')) = LOWER(@uf)
          AND LOWER(COALESCE(cd_cargo, 'presidente')) = LOWER(@cargo)
          AND data_pesquisa_fim BETWEEN
              DATE_SUB(CURRENT_DATE(), INTERVAL @janela_dias DAY) AND CURRENT_DATE()
          {candidato_filter}
          {instituto_filter}
        GROUP BY CAST(data_pesquisa_fim AS DATE), candidato_normalizado
        ORDER BY data_referencia DESC
        LIMIT 500
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = [dict(r) for r in client.query(query, job_config=job_config).result()]

    if formato == "tabela":
        return {"rows": rows, "total": len(rows), "formato": "tabela"}

    by_candidato: dict[str, list] = {}
    for r in rows:
        cand = r.get("candidato_normalizado", "")
        by_candidato.setdefault(cand, []).append(
            {
                "data": str(r.get("data_referencia", "")),
                "intencao_media": round(r.get("intencao_media") or 0, 2),
                "intencao_ponderada": round(r.get("intencao_ponderada") or 0, 2),
                "intencao_ajustada": round(r.get("intencao_ajustada_media") or 0, 2),
                "n_pesquisas": r.get("n_pesquisas", 0),
                "institutos": r.get("institutos", "[]"),
            }
        )
    series = [{"candidato": c, "pontos": pts} for c, pts in by_candidato.items()]
    return {"series": series, "total": len(rows), "formato": "serie"}


# ── Indicadores temáticos para camadas do mapa ────────────────────────────

_INDICADORES_IBGE_METRICS = frozenset(
    {
        "idhm",
        "renda_per_capita",
        "taxa_analfabetismo",
        "populacao_total",
    }
)
_INDICADORES_ALLOWED: dict[str, frozenset[str]] = {
    "ibge": _INDICADORES_IBGE_METRICS,
    "sentimento": frozenset({"sentiment_score"}),
    "previsao": frozenset({"pct_previsto", "ic_low_95", "ic_high_95"}),
}


@app.get("/api/indicadores/{tipo}")
async def get_indicadores(
    tipo: str,
    metrica: str = Query("idhm"),
    uf: str | None = Query(None),
    candidato: str | None = Query(None),
    data_ref: str | None = Query(None, description="YYYY-MM-DD; default: hoje"),
) -> JSONResponse:
    """Dados temáticos para camadas do mapa (ibge | sentimento | previsao)."""
    if tipo not in _INDICADORES_ALLOWED:
        raise HTTPException(
            status_code=400, detail=f"tipo deve ser: {', '.join(_INDICADORES_ALLOWED)}"
        )

    allowed_metrics = _INDICADORES_ALLOWED[tipo]
    if metrica not in allowed_metrics:
        raise HTTPException(
            status_code=400,
            detail=f"metrica '{metrica}' inválida para tipo '{tipo}'. Permitidas: {', '.join(allowed_metrics)}",
        )

    if not (settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"):
        return JSONResponse({"tipo": tipo, "metrica": metrica, "data": [], "status": "no_data"})

    try:
        result = await _bq_indicadores(tipo, metrica, uf, candidato, data_ref)
        return JSONResponse({"tipo": tipo, "metrica": metrica, "data": result})
    except Exception as exc:
        logger.warning("BigQuery indicadores/%s falhou: %s", tipo, exc)
        return JSONResponse({"tipo": tipo, "metrica": metrica, "data": [], "status": "error"})


async def _bq_indicadores(
    tipo: str,
    metrica: str,
    uf: str | None,
    candidato: str | None,
    data_ref: str | None,
) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
    mlops = f"{settings.gcp_project_id}.spepe_mlops"

    if tipo == "ibge":
        query = f"""
            SELECT cd_municipio_ibge, sg_uf, {metrica} AS valor
            FROM `{gold}.fact_ibge_municipio`
            WHERE (@uf IS NULL OR sg_uf = @uf)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("uf", "STRING", uf.upper() if uf else None),
            ]
        )

    elif tipo == "sentimento":
        resolved_date = data_ref or __import__("datetime").date.today().isoformat()
        query = f"""
            SELECT NULL AS cd_municipio_ibge, sg_uf,
                   ROUND(AVG(score_liquido_sentimento), 4) AS valor
            FROM `{gold}.fact_social_municipio`
            WHERE data_referencia = @data_ref
              AND (@candidato IS NULL OR candidato = @candidato)
              AND (@uf IS NULL OR sg_uf = @uf)
            GROUP BY sg_uf
            LIMIT 5600
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("data_ref", "DATE", resolved_date),
                bigquery.ScalarQueryParameter("candidato", "STRING", candidato),
                bigquery.ScalarQueryParameter("uf", "STRING", uf.upper() if uf else None),
            ]
        )

    else:  # previsao
        query = f"""
            SELECT cd_municipio_ibge, candidato, cargo,
                   p_mean AS pct_previsto, p_lower AS ic_low_95, p_upper AS ic_high_95
            FROM `{mlops}.fact_predictions`
            WHERE prediction_date >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
              AND (@candidato IS NULL OR LOWER(candidato) LIKE CONCAT('%', LOWER(@candidato), '%'))
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("candidato", "STRING", candidato),
            ]
        )

    rows = await asyncio.to_thread(
        lambda: list(client.query(query, job_config=job_config).result())
    )
    return [dict(r) for r in rows]


# ── Perfis Eleitorais ──────────────────────────────────────────────────────


@app.get("/api/perfis")
async def get_perfis(
    uf: str = Query("SP"),
    ano: int = Query(2022),
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            return JSONResponse(await _bq_perfis(uf, ano))
        except Exception as exc:
            logger.warning("BigQuery perfis falhou: %s", exc)
            return JSONResponse(
                {
                    "genero": [],
                    "faixa_etaria": [],
                    "escolaridade": [],
                    "fonte": "bq_error",
                    "error": str(exc),
                }
            )
    result = await asyncio.to_thread(_local_perfis, uf, ano)
    return JSONResponse(result)


def _local_perfis(uf: str, ano: int) -> dict:
    import pandas as pd

    _empty = {"genero": [], "faixa_etaria": [], "escolaridade": [], "fonte": "local_vazio"}
    frames: list[pd.DataFrame] = []
    for pattern in [f"tse_perfil_{uf.lower()}*.parquet", "tse_perfil_*.parquet", f"perfil_{uf.lower()}*.parquet"]:
        for f in sorted(_LOCAL_SILVER_DIR.glob(pattern)):
            try:
                frames.append(pd.read_parquet(f))
            except Exception:
                continue
        if frames:
            break
    if not frames:
        return _empty
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.lower() for c in df.columns]
    if "sg_uf" in df.columns:
        df = df[df["sg_uf"].str.upper() == uf.upper()]
    if "ano" in df.columns:
        df = df[df["ano"].astype(str) == str(ano)]
    if df.empty:
        return _empty
    col_genero = next((c for c in df.columns if "genero" in c), None)
    col_faixa  = next((c for c in df.columns if "faixa" in c or "idade" in c), None)
    col_esc    = next((c for c in df.columns if "escolar" in c or "instruc" in c), None)
    col_qt     = next((c for c in df.columns if "eleitor" in c or ("qt" in c and "voto" not in c)), None) or "qt_eleitores"
    if col_qt not in df.columns:
        df[col_qt] = 1
    df[col_qt] = pd.to_numeric(df[col_qt], errors="coerce").fillna(1)
    genero, faixa, esc = {}, {}, {}
    if col_genero:
        for g, qt in df.groupby(col_genero)[col_qt].sum().items():
            genero[str(g)] = int(qt)
    if col_faixa:
        for fv, qt in df.groupby(col_faixa)[col_qt].sum().items():
            faixa[str(fv)] = int(qt)
    if col_esc:
        for ev, qt in df.groupby(col_esc)[col_qt].sum().items():
            esc[str(ev)] = int(qt)
    return {
        "genero":      [{"label": k, "qt_eleitores": v} for k, v in genero.items()],
        "faixa_etaria": sorted([{"label": k, "qt_eleitores": v} for k, v in faixa.items()], key=lambda x: x["label"]),
        "escolaridade": [{"label": k, "qt_eleitores": v} for k, v in esc.items()],
        "fonte": "local",
    }


async def _bq_perfis(uf: str, ano: int) -> dict:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
    query = f"""
        SELECT ds_genero, ds_faixa_etaria, ds_grau_escolaridade,
               SUM(qt_eleitores) AS qt_eleitores
        FROM `{gold}.fact_perfil_eleitorado`
        WHERE sg_uf = @uf AND ano = @ano
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())
    genero: dict[str, int] = {}
    faixa: dict[str, int] = {}
    escolaridade: dict[str, int] = {}
    for r in rows:
        g = r.get("ds_genero", "Não informado") or "Não informado"
        f = r.get("ds_faixa_etaria", "Não informado") or "Não informado"
        e = r.get("ds_grau_escolaridade", "Não informado") or "Não informado"
        qt = r.get("qt_eleitores") or 0
        genero[g] = genero.get(g, 0) + qt
        faixa[f] = faixa.get(f, 0) + qt
        escolaridade[e] = escolaridade.get(e, 0) + qt
    return {
        "genero": [{"label": k, "qt_eleitores": v} for k, v in genero.items()],
        "faixa_etaria": sorted(
            [{"label": k, "qt_eleitores": v} for k, v in faixa.items()],
            key=lambda x: x["label"],
        ),
        "escolaridade": [{"label": k, "qt_eleitores": v} for k, v in escolaridade.items()],
    }


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

# Normalise cargo string → cd_cargo regardless of case / spacing from frontend
_CARGO_CD_NORM: dict[str, int] = {
    "presidente": 1,
    "governador": 3,
    "senador": 5,
    "dep federal": 6,
    "dep. federal": 6,
    "deputado federal": 6,
    "dep estadual": 7,
    "dep. estadual": 7,
    "deputado estadual": 7,
}


def _cargo_to_cd(cargo: str, default: int = 1) -> int:
    """Case-insensitive lookup of cargo → cd_cargo code."""
    key = cargo.strip().lower()
    return _CARGO_CD_NORM.get(key) or _CARGO_CD.get(cargo, default)


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


@app.get("/api/mapa/{nivel}")
async def get_mapa(
    nivel: NivelGeo,
    cargo: str = Query("Presidente"),
    ano: int = Query(2022),
    uf: str = Query(None),
    cd_municipio: str = Query(None),
    nr_zona: str = Query(None),
    turno: int = Query(1),
    layer: str = Query("electoral"),
    candidato: str = Query(""),
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
            if nivel_str == "nacional":
                features = await _bq_mapa_nacional(cargo, ano, turno, candidato)
                return JSONResponse({"nivel": "nacional", "features": features})
            if nivel_str == "regiao":
                features = await _bq_mapa_regiao(cargo, ano, turno, candidato)
                return JSONResponse({"nivel": "regiao", "features": features})
            if nivel_str == "uf":
                features = await _bq_mapa_uf(cargo, ano, turno, candidato)
                return JSONResponse({"nivel": "uf", "features": features})
            if nivel_str == "municipio":
                uf_upper = (uf or "SP").upper()
                features = await _bq_mapa_municipio(uf_upper, cargo, ano, turno, candidato)
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
            logger.warning("BigQuery mapa %s falhou: %s", nivel_str, exc)

    return JSONResponse({"nivel": nivel_str, "features": [], "fonte": "indisponivel"})


# ── Choropleth temático — endpoint unificado por camada ──────────────────────

_CHOROPLETH_LAYERS = frozenset(
    {"eleitoral", "ibge", "socio", "saude", "seguranca", "pesquisas", "sentimento", "predicao"}
)


@app.get("/api/mapa/choropleth")
async def get_mapa_choropleth(
    layer: str = Query(
        "ibge", description="eleitoral|ibge|socio|saude|seguranca|pesquisas|sentimento|predicao"
    ),
    cargo: str = Query("Governador"),
    candidato: str = Query(""),
    ano: int = Query(2022),
) -> JSONResponse:
    """Retorna {sg_uf → value} para colorir choropleth no nível UF.

    Cada camada agrega sua métrica-chave por estado:
      eleitoral  → pct_votos máximo do líder por UF
      ibge/socio → IDHM médio por UF
      saude      → taxa mortalidade infantil média por UF
      seguranca  → taxa homicídio média por UF
      pesquisas  → intenção de voto média do candidato por UF
      sentimento → score sentimento médio por UF
      predicao   → p_mean do modelo M1 por UF
    """
    if layer not in _CHOROPLETH_LAYERS:
        raise HTTPException(
            400, detail=f"layer deve ser um de: {', '.join(sorted(_CHOROPLETH_LAYERS))}"
        )

    if not (settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"):
        return JSONResponse({"layer": layer, "data": [], "status": "no_bq"})

    try:
        rows = await _bq_choropleth(layer, cargo, candidato, ano)
        return JSONResponse({"layer": layer, "data": rows})
    except Exception as exc:
        logger.warning("choropleth/%s falhou: %s", layer, exc)
        return JSONResponse({"layer": layer, "data": [], "status": "error"})


async def _bq_choropleth(layer: str, cargo: str, candidato: str, ano: int) -> list[dict]:
    import asyncio
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
    mlops = f"{settings.gcp_project_id}.spepe_mlops"
    cd_cargo = _cargo_to_cd(cargo, 3)

    if layer == "eleitoral":
        query = f"""
            SELECT sg_uf, ROUND(MAX(pct_votos_municipio), 4) AS value
            FROM `{gold}.fact_municipio_candidato_eleicao`
            WHERE cd_cargo = @cd_cargo AND ano_eleicao = @ano AND nr_turno = 1
              AND (@candidato = '' OR LOWER(nm_candidato) LIKE CONCAT('%', LOWER(@candidato), '%'))
            GROUP BY sg_uf
        """
        params = [
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("candidato", "STRING", candidato),
        ]

    elif layer in ("ibge", "socio"):
        query = f"""
            SELECT sg_uf, ROUND(AVG(idhm), 4) AS value
            FROM `{gold}.fact_ibge_municipio`
            GROUP BY sg_uf
        """
        params = []

    elif layer == "saude":
        query = f"""
            SELECT sg_uf, ROUND(AVG(tx_mortalidade_infantil_1000), 4) AS value
            FROM `{gold}.fact_saude_municipio`
            WHERE ano = @ano
            GROUP BY sg_uf
        """
        params = [bigquery.ScalarQueryParameter("ano", "INT64", ano)]

    elif layer == "seguranca":
        query = f"""
            SELECT sg_uf, ROUND(AVG(taxa_homicidio_100k), 4) AS value
            FROM `{gold}.fact_seguranca_municipio`
            WHERE ano = @ano
            GROUP BY sg_uf
        """
        params = [bigquery.ScalarQueryParameter("ano", "INT64", ano)]

    elif layer == "pesquisas":
        query = f"""
            SELECT uf AS sg_uf, ROUND(AVG(intencao_ponderada), 4) AS value
            FROM `{gold}.fact_intencao_voto`
            WHERE cd_cargo = @cd_cargo AND ano_eleitoral = @ano
              AND (@candidato = '' OR LOWER(candidato_normalizado) LIKE CONCAT('%', LOWER(@candidato), '%'))
            GROUP BY uf
        """
        params = [
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("candidato", "STRING", candidato),
        ]

    elif layer == "sentimento":
        query = f"""
            SELECT sg_uf, ROUND(AVG(score_liquido_sentimento), 4) AS value
            FROM `{gold}.fact_social_municipio`
            WHERE data_referencia >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
              AND (@candidato = '' OR LOWER(candidato) LIKE CONCAT('%', LOWER(@candidato), '%'))
            GROUP BY sg_uf
        """
        params = [bigquery.ScalarQueryParameter("candidato", "STRING", candidato)]

    else:  # predicao
        query = f"""
            SELECT sg_uf, ROUND(AVG(p_mean), 4) AS value
            FROM `{mlops}.fact_predictions`
            WHERE DATE(prediction_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
              AND (@candidato = '' OR LOWER(candidato) LIKE CONCAT('%', LOWER(@candidato), '%'))
            GROUP BY sg_uf
        """
        params = [bigquery.ScalarQueryParameter("candidato", "STRING", candidato)]

    job_config = (
        bigquery.QueryJobConfig(query_parameters=params) if params else bigquery.QueryJobConfig()
    )
    rows = await asyncio.to_thread(
        lambda: list(client.query(query, job_config=job_config).result())
    )
    return [{"sg_uf": r["sg_uf"], "value": float(r["value"] or 0)} for r in rows]


@app.get("/api/mapa/locais")
async def get_mapa_locais(
    uf: str = Query(..., description="Sigla da UF, ex: SP"),
    cd_municipio: str | None = Query(None),
    nr_zona: str | None = Query(None),
    only_with_coords: bool = Query(True),
) -> Response:
    """GeoJSON FeatureCollection com pontos de locais de votação da UF."""
    if not (settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"):
        return _json_safe_response(
            {"type": "FeatureCollection", "features": [], "fonte": "bigquery_indisponivel"}
        )
    try:
        features = await _bq_locais_votacao(uf.upper(), cd_municipio, nr_zona, only_with_coords)
        return _json_safe_response({"type": "FeatureCollection", "features": features})
    except Exception as exc:
        logger.warning("BQ locais_votacao falhou: %s", exc)
        return _json_safe_response({"type": "FeatureCollection", "features": [], "erro": str(exc)})


async def _bq_locais_votacao(
    uf: str,
    cd_municipio: str | None,
    nr_zona: str | None,
    only_with_coords: bool,
) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

    filters = ["sg_uf = @uf"]
    params: list = [bigquery.ScalarQueryParameter("uf", "STRING", uf)]

    if cd_municipio:
        filters.append("cd_municipio = @cd_municipio")
        params.append(bigquery.ScalarQueryParameter("cd_municipio", "INT64", int(cd_municipio)))
    if nr_zona:
        filters.append("nr_zona = @nr_zona")
        params.append(bigquery.ScalarQueryParameter("nr_zona", "INT64", int(nr_zona)))
    if only_with_coords:
        filters.append("has_coordinates = TRUE")

    query = f"""
        SELECT sg_uf, cd_municipio, nm_municipio, nr_zona, nr_local_votacao,
               nm_local_votacao, ds_endereco, nm_bairro, nr_cep,
               nr_latitude, nr_longitude, qt_secoes
        FROM `{gold}.fact_locais_votacao`
        WHERE {" AND ".join(filters)}
        ORDER BY nr_zona, nr_local_votacao
        LIMIT 10000
    """
    rows = await asyncio.to_thread(
        lambda: list(
            client.query(
                query,
                job_config=bigquery.QueryJobConfig(query_parameters=params),
            ).result()
        )
    )
    return [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(r["nr_longitude"]), float(r["nr_latitude"])],
            },
            "properties": {
                "sg_uf": r["sg_uf"],
                "cd_municipio": r["cd_municipio"],
                "nm_municipio": r["nm_municipio"],
                "nr_zona": r["nr_zona"],
                "nr_local_votacao": r["nr_local_votacao"],
                "nm_local_votacao": r["nm_local_votacao"],
                "ds_endereco": r["ds_endereco"],
                "nm_bairro": r["nm_bairro"],
                "nr_cep": r["nr_cep"],
                "qt_secoes": r["qt_secoes"],
            },
        }
        for r in rows
        if r["nr_latitude"] is not None and r["nr_longitude"] is not None
    ]


async def _bq_mapa_nacional(cargo: str, ano: int, turno: int, candidato: str = "") -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _cargo_to_cd(cargo, 1)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

    # 2026+: usa pesquisas nacionais (uf='BR')
    if ano >= 2026:
        cand_filter_2026 = "AND candidato_normalizado = @candidato" if candidato else ""
        query_2026 = f"""
            WITH ranked AS (
                SELECT candidato_normalizado,
                       ROUND(AVG(intencao_ponderada), 1) AS pct,
                       SUM(n_pesquisas)                   AS n_pesq,
                       ROW_NUMBER() OVER (ORDER BY AVG(intencao_ponderada) DESC) AS rn
                FROM `{gold}.fact_intencao_voto`
                WHERE cd_cargo = @cd_cargo AND ano_eleitoral = @ano
                  AND uf = 'BR'
                  {cand_filter_2026}
                GROUP BY candidato_normalizado
            )
            SELECT MAX(IF(rn=1, candidato_normalizado, NULL)) AS lider,
                   MAX(IF(rn=1, pct, NULL))                   AS pct,
                   MAX(IF(rn=2, candidato_normalizado, NULL)) AS segundo,
                   MAX(IF(rn=2, pct, NULL))                   AS pct2,
                   SUM(n_pesq)                                AS total_votos
            FROM ranked WHERE rn <= 2
        """
        params_2026 = [
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
        ]
        if candidato:
            params_2026.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))
        rows_2026 = await asyncio.to_thread(
            lambda: list(
                client.query(
                    query_2026,
                    job_config=bigquery.QueryJobConfig(query_parameters=params_2026),
                ).result()
            )
        )
        if rows_2026 and rows_2026[0].get("lider"):
            r2 = rows_2026[0]
            return [
                {
                    "id": "BR",
                    "label": "Brasil",
                    "ibge_code": "BR",
                    "lider": r2.get("lider", "—"),
                    "partido": "—",
                    "pct": r2.get("pct") or 0.0,
                    "segundo": r2.get("segundo", "—"),
                    "pct2": r2.get("pct2") or 0.0,
                    "total_votos": r2.get("total_votos") or 0,
                    "turnout": 0.0,
                    "fonte": "pesquisas_2026",
                }
            ]
        return []

    cand_filter = "AND nm_candidato = @candidato" if candidato else ""
    query = f"""
        WITH ranked AS (
            SELECT nm_candidato, sg_partido,
                   SUM(total_votos) AS votos,
                   RANK() OVER (ORDER BY SUM(total_votos) DESC) AS rnk
            FROM `{gold}.fact_municipio_candidato_eleicao`
            WHERE ano_eleicao = @ano AND cd_cargo = @cd_cargo AND nr_turno = @turno
            {cand_filter}
            GROUP BY nm_candidato, sg_partido
        ),
        totais AS (SELECT SUM(votos) AS total_votos FROM ranked)
        SELECT r1.nm_candidato AS lider, r1.sg_partido AS partido,
               ROUND(r1.votos / t.total_votos * 100, 1) AS pct,
               r2.nm_candidato AS segundo, ROUND(r2.votos / t.total_votos * 100, 1) AS pct2,
               t.total_votos
        FROM ranked r1 CROSS JOIN totais t
        LEFT JOIN ranked r2 ON r2.rnk = 2
        WHERE r1.rnk = 1
    """
    params = [
        bigquery.ScalarQueryParameter("ano", "INT64", ano),
        bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
        bigquery.ScalarQueryParameter("turno", "INT64", turno),
    ]
    if candidato:
        params.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = await asyncio.to_thread(
        lambda: list(client.query(query, job_config=job_config).result())
    )
    if not rows:
        return []
    r = rows[0]
    return [
        {
            "id": "BR",
            "label": "Brasil",
            "ibge_code": "BR",
            "lider": r.get("lider", "—"),
            "partido": r.get("partido", "—"),
            "pct": r.get("pct") or 0.0,
            "segundo": r.get("segundo", "—"),
            "pct2": r.get("pct2") or 0.0,
            "total_votos": r.get("total_votos") or 0,
            "turnout": 0.80,
        }
    ]


async def _bq_mapa_regiao(cargo: str, ano: int, turno: int, candidato: str = "") -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _cargo_to_cd(cargo, 1)
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
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

    # 2026+: agrega pesquisas por UF → mapeia para região
    if ano >= 2026:
        cand_filter_2026 = "AND candidato_normalizado = @candidato" if candidato else ""
        # Use CASE expression to map UF → Region inside BQ
        uf_regiao_cases = " ".join(f"WHEN '{uf}' THEN '{reg}'" for uf, reg in _UF_TO_REGIAO.items())
        query_2026 = f"""
            WITH uf_agg AS (
                SELECT uf,
                       CASE uf {uf_regiao_cases} ELSE 'Outro' END AS regiao,
                       candidato_normalizado,
                       AVG(intencao_ponderada) AS pct_cand
                FROM `{gold}.fact_intencao_voto`
                WHERE cd_cargo = @cd_cargo AND ano_eleitoral = @ano
                  AND uf != 'BR'
                  {cand_filter_2026}
                GROUP BY uf, regiao, candidato_normalizado
            ),
            reg_agg AS (
                SELECT regiao, candidato_normalizado,
                       ROUND(AVG(pct_cand), 1) AS pct
                FROM uf_agg GROUP BY regiao, candidato_normalizado
            ),
            ranked AS (
                SELECT regiao, candidato_normalizado AS lider, pct,
                       ROW_NUMBER() OVER (PARTITION BY regiao ORDER BY pct DESC) AS rn
                FROM reg_agg
            )
            SELECT r1.regiao, r1.lider, r1.pct, r2.lider AS segundo, r2.pct AS pct2, 0 AS total_votos
            FROM ranked r1
            LEFT JOIN ranked r2 ON r1.regiao = r2.regiao AND r2.rn = 2
            WHERE r1.rn = 1
        """
        params_2026 = [
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
        ]
        if candidato:
            params_2026.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))
        rows_2026 = await asyncio.to_thread(
            lambda: list(
                client.query(
                    query_2026,
                    job_config=bigquery.QueryJobConfig(query_parameters=params_2026),
                ).result()
            )
        )
        return [
            {
                "id": r["regiao"],
                "label": r["regiao"],
                "ibge_code": _REGIAO_CODE.get(r["regiao"], ""),
                "lider": r.get("lider", "—"),
                "partido": "—",
                "pct": r.get("pct") or 0.0,
                "segundo": r.get("segundo", "") or "",
                "pct2": r.get("pct2") or 0.0,
                "total_votos": 0,
                "fonte": "pesquisas_2026",
            }
            for r in rows_2026
        ]

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
    base_table = f"`{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_candidato_eleicao`"
    base_where = "ano_eleicao = @ano AND cd_cargo = @cd_cargo AND nr_turno = @turno"
    if candidato:
        query = f"""
            WITH base AS (
                SELECT {case_expr} AS regiao, nm_candidato, sg_partido, SUM(total_votos) AS votos
                FROM {base_table}
                WHERE {base_where}
                GROUP BY regiao, nm_candidato, sg_partido
            ),
            totais AS (SELECT regiao, SUM(votos) AS total_votos FROM base GROUP BY regiao)
            SELECT b.regiao AS regiao, b.nm_candidato AS lider, b.sg_partido AS partido,
                   ROUND(b.votos / t.total_votos * 100, 1) AS pct,
                   t.total_votos
            FROM base b JOIN totais t USING (regiao)
            WHERE b.nm_candidato = @candidato
        """
    else:
        query = f"""
            WITH candidatos AS (
                SELECT {case_expr} AS regiao, nm_candidato, sg_partido, SUM(total_votos) AS votos
                FROM {base_table}
                WHERE {base_where}
                GROUP BY regiao, nm_candidato, sg_partido
            ),
            totais AS (SELECT regiao, SUM(votos) AS total_votos FROM candidatos GROUP BY regiao),
            ranked AS (
                SELECT c.regiao, c.nm_candidato, c.sg_partido, c.votos, t.total_votos,
                       ROW_NUMBER() OVER (PARTITION BY c.regiao ORDER BY c.votos DESC) AS rn
                FROM candidatos c JOIN totais t USING (regiao)
            )
            SELECT regiao, nm_candidato AS lider, sg_partido AS partido,
                   ROUND(votos / total_votos * 100, 1) AS pct, total_votos
            FROM ranked WHERE rn = 1
        """
    params = [
        bigquery.ScalarQueryParameter("ano", "INT64", ano),
        bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
        bigquery.ScalarQueryParameter("turno", "INT64", turno),
    ]
    if candidato:
        params.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
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


async def _bq_mapa_uf(cargo: str, ano: int, turno: int, candidato: str = "") -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _cargo_to_cd(cargo, 1)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

    # 2026+: pesquisas por UF (fact_intencao_voto)
    if ano >= 2026:
        cand_filter_2026 = "AND candidato_normalizado = @candidato" if candidato else ""
        uf_scope = "uf != 'BR'" if cd_cargo != 1 else "(uf != 'BR' OR uf = 'BR')"
        query_2026 = f"""
            WITH agg AS (
                SELECT uf,
                       candidato_normalizado,
                       ROUND(AVG(intencao_ponderada), 1) AS pct
                FROM `{gold}.fact_intencao_voto`
                WHERE cd_cargo = @cd_cargo AND ano_eleitoral = @ano
                  AND {uf_scope}
                  {cand_filter_2026}
                GROUP BY uf, candidato_normalizado
            ),
            ranked AS (
                SELECT uf AS sg_uf, candidato_normalizado AS lider, pct,
                       ROW_NUMBER() OVER (PARTITION BY uf ORDER BY pct DESC) AS rn
                FROM agg
            )
            SELECT r1.sg_uf, r1.lider, CAST(NULL AS STRING) AS partido, r1.pct,
                   r2.lider AS segundo, r2.pct AS pct2, 0 AS total_votos
            FROM ranked r1
            LEFT JOIN ranked r2 ON r1.sg_uf = r2.sg_uf AND r2.rn = 2
            WHERE r1.rn = 1 AND r1.sg_uf != 'BR'
        """
        params_2026 = [
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
        ]
        if candidato:
            params_2026.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))
        rows_2026 = await asyncio.to_thread(
            lambda: list(
                client.query(
                    query_2026,
                    job_config=bigquery.QueryJobConfig(query_parameters=params_2026),
                ).result()
            )
        )
        return [
            {
                "id": r["sg_uf"],
                "label": r["sg_uf"],
                "ibge_code": _UF_IBGE.get(r["sg_uf"], ""),
                "regiao": _UF_REGIAO.get(r["sg_uf"], ""),
                "lider": r.get("lider", "—"),
                "partido": "—",
                "pct": r.get("pct") or 0.0,
                "segundo": r.get("segundo") or "—",
                "pct2": r.get("pct2") or 0.0,
                "total_votos": 0,
                "turnout": 0.0,
                "fonte": "pesquisas_2026",
            }
            for r in rows_2026
        ]

    base_table = f"`{gold}.fact_municipio_candidato_eleicao`"
    base_where = "ano_eleicao = @ano AND cd_cargo = @cd_cargo AND nr_turno = @turno"
    if candidato:
        query = f"""
            WITH base AS (
                SELECT sg_uf, nm_candidato, sg_partido, SUM(total_votos) AS votos
                FROM {base_table}
                WHERE {base_where}
                GROUP BY sg_uf, nm_candidato, sg_partido
            ),
            totais AS (SELECT sg_uf, SUM(votos) AS total_votos FROM base GROUP BY sg_uf)
            SELECT b.sg_uf, b.nm_candidato AS lider, b.sg_partido AS partido,
                   ROUND(b.votos / t.total_votos * 100, 1) AS pct,
                   NULL AS segundo, NULL AS pct2, t.total_votos
            FROM base b JOIN totais t USING (sg_uf)
            WHERE b.nm_candidato = @candidato
        """
    else:
        query = f"""
            WITH ranked AS (
                SELECT sg_uf, nm_candidato, sg_partido,
                       SUM(total_votos) AS votos,
                       RANK() OVER (PARTITION BY sg_uf ORDER BY SUM(total_votos) DESC) AS rnk
                FROM {base_table}
                WHERE {base_where}
                GROUP BY sg_uf, nm_candidato, sg_partido
            ),
            totais AS (SELECT sg_uf, SUM(votos) AS total_votos FROM ranked GROUP BY sg_uf)
            SELECT r1.sg_uf, r1.nm_candidato AS lider, r1.sg_partido AS partido,
                   ROUND(r1.votos / t.total_votos * 100, 1) AS pct,
                   r2.nm_candidato AS segundo, ROUND(r2.votos / t.total_votos * 100, 1) AS pct2,
                   t.total_votos
            FROM ranked r1
            LEFT JOIN ranked r2 ON r1.sg_uf = r2.sg_uf AND r2.rnk = 2
            JOIN totais t ON r1.sg_uf = t.sg_uf
            WHERE r1.rnk = 1
        """
    params = [
        bigquery.ScalarQueryParameter("ano", "INT64", ano),
        bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
        bigquery.ScalarQueryParameter("turno", "INT64", turno),
    ]
    if candidato:
        params.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = await asyncio.to_thread(
        lambda: list(client.query(query, job_config=job_config).result())
    )
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


async def _bq_mapa_municipio(
    uf: str, cargo: str, ano: int, turno: int, candidato: str = ""
) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _cargo_to_cd(cargo, 1)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

    # 2026+: pesquisas não têm granularidade municipal — retorna municípios da UF com dados por UF
    if ano >= 2026:
        cand_filter_2026 = "AND candidato_normalizado = @candidato" if candidato else ""
        query_2026 = f"""
            WITH uf_polls AS (
                SELECT candidato_normalizado AS lider,
                       ROUND(AVG(intencao_ponderada), 1) AS pct,
                       ROW_NUMBER() OVER (ORDER BY AVG(intencao_ponderada) DESC) AS rn
                FROM `{gold}.fact_intencao_voto`
                WHERE cd_cargo = @cd_cargo AND ano_eleitoral = @ano
                  AND (uf = @uf OR uf = 'BR')
                  {cand_filter_2026}
                GROUP BY candidato_normalizado
            ),
            poll_leaders AS (
                SELECT MAX(IF(rn=1, lider, NULL)) AS lider, MAX(IF(rn=1, pct, NULL)) AS pct,
                       MAX(IF(rn=2, lider, NULL)) AS segundo, MAX(IF(rn=2, pct, NULL)) AS pct2
                FROM uf_polls WHERE rn <= 2
            ),
            munic AS (
                SELECT DISTINCT cd_municipio, cd_municipio_ibge, nm_municipio
                FROM `{gold}.fact_municipio_candidato_eleicao`
                WHERE sg_uf = @uf
                LIMIT 500
            )
            SELECT m.cd_municipio, m.cd_municipio_ibge, m.nm_municipio,
                   p.lider, p.pct, p.segundo, p.pct2
            FROM munic m CROSS JOIN poll_leaders p
        """
        params_2026 = [
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
        ]
        if candidato:
            params_2026.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))
        rows_2026 = await asyncio.to_thread(
            lambda: list(
                client.query(
                    query_2026,
                    job_config=bigquery.QueryJobConfig(query_parameters=params_2026),
                ).result()
            )
        )
        return [
            {
                "id": str(r["cd_municipio"]),
                "cd_municipio": str(r["cd_municipio"]),
                "ibge_code": str(r["cd_municipio_ibge"]),
                "label": r["nm_municipio"],
                "lider": r.get("lider", "—"),
                "partido": "—",
                "pct": r.get("pct") or 0.0,
                "segundo": r.get("segundo") or "—",
                "pct2": r.get("pct2") or 0.0,
                "total_votos": 0,
                "turnout": 0.0,
                "fonte": "pesquisas_2026_uf",
            }
            for r in rows_2026
        ]

    base_table = f"`{gold}.fact_municipio_candidato_eleicao`"
    base_where = "sg_uf = @uf AND ano_eleicao = @ano AND cd_cargo = @cd_cargo AND nr_turno = @turno"
    if candidato:
        query = f"""
            WITH base AS (
                SELECT cd_municipio, cd_municipio_ibge, nm_municipio, nm_candidato, sg_partido,
                       SUM(total_votos) AS votos
                FROM {base_table}
                WHERE {base_where}
                GROUP BY cd_municipio, cd_municipio_ibge, nm_municipio, nm_candidato, sg_partido
            ),
            totais AS (
                SELECT cd_municipio, SUM(votos) AS total_votos FROM base GROUP BY cd_municipio
            )
            SELECT b.cd_municipio, b.cd_municipio_ibge, b.nm_municipio,
                   b.nm_candidato AS lider, b.sg_partido AS partido,
                   ROUND(b.votos / t.total_votos * 100, 1) AS pct,
                   NULL AS segundo, NULL AS pct2,
                   t.total_votos
            FROM base b
            JOIN totais t ON b.cd_municipio = t.cd_municipio
            WHERE b.nm_candidato = @candidato
            ORDER BY b.votos DESC
        """
    else:
        query = f"""
            WITH ranked AS (
                SELECT cd_municipio, cd_municipio_ibge, nm_municipio, nm_candidato, sg_partido,
                       SUM(total_votos) AS votos,
                       RANK() OVER (PARTITION BY cd_municipio ORDER BY SUM(total_votos) DESC) AS rnk
                FROM {base_table}
                WHERE {base_where}
                GROUP BY cd_municipio, cd_municipio_ibge, nm_municipio, nm_candidato, sg_partido
            ),
            totais AS (
                SELECT cd_municipio, SUM(votos) AS total_votos FROM ranked GROUP BY cd_municipio
            )
            SELECT r1.cd_municipio, r1.cd_municipio_ibge, r1.nm_municipio,
                   r1.nm_candidato AS lider, r1.sg_partido AS partido,
                   ROUND(r1.votos / t.total_votos * 100, 1) AS pct,
                   r2.nm_candidato AS segundo, ROUND(r2.votos / t.total_votos * 100, 1) AS pct2,
                   t.total_votos
            FROM ranked r1
            LEFT JOIN ranked r2 ON r1.cd_municipio = r2.cd_municipio AND r2.rnk = 2
            JOIN totais t ON r1.cd_municipio = t.cd_municipio
            WHERE r1.rnk = 1
            ORDER BY t.total_votos DESC
        """
    params = [
        bigquery.ScalarQueryParameter("uf", "STRING", uf),
        bigquery.ScalarQueryParameter("ano", "INT64", ano),
        bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
        bigquery.ScalarQueryParameter("turno", "INT64", turno),
    ]
    if candidato:
        params.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = client.query(query, job_config=job_config).result()
    return [
        {
            "id": str(r["cd_municipio"]),
            "cd_municipio": str(r["cd_municipio"]),
            "ibge_code": str(r["cd_municipio_ibge"]),
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
    cd_cargo = _cargo_to_cd(cargo, 1)
    query = f"""
        WITH ranked AS (
            SELECT nr_zona, nm_candidato, CAST(NULL AS STRING) AS sg_partido,
                   SUM(qt_votos) AS votos,
                   RANK() OVER (PARTITION BY nr_zona ORDER BY SUM(qt_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.spepe_silver.tse_{uf.lower()}_{ano}`
            WHERE cd_municipio = @cd_municipio AND cd_cargo = @cd_cargo AND nr_turno = @turno
            GROUP BY nr_zona, nm_candidato
        ),
        totais AS (
            SELECT nr_zona, SUM(votos) AS total_votos FROM ranked GROUP BY nr_zona
        )
        SELECT r1.nr_zona, r1.nm_candidato AS lider, r1.sg_partido AS partido,
               ROUND(r1.votos / t.total_votos * 100, 1) AS pct,
               r2.nm_candidato AS segundo, ROUND(r2.votos / t.total_votos * 100, 1) AS pct2,
               t.total_votos
        FROM ranked r1
        LEFT JOIN ranked r2 ON r1.nr_zona = r2.nr_zona AND r2.rnk = 2
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
    cd_cargo = _cargo_to_cd(cargo, 1)
    query = f"""
        WITH ranked AS (
            SELECT nr_secao, nm_candidato, CAST(NULL AS STRING) AS sg_partido,
                   SUM(qt_votos) AS votos,
                   RANK() OVER (PARTITION BY nr_secao ORDER BY SUM(qt_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.spepe_silver.tse_{uf.lower()}_{ano}`
            WHERE cd_municipio = @cd_municipio AND nr_zona = @nr_zona
              AND cd_cargo = @cd_cargo AND nr_turno = @turno
            GROUP BY nr_secao, nm_candidato
        ),
        totais AS (
            SELECT nr_secao, SUM(votos) AS total_votos FROM ranked GROUP BY nr_secao
        )
        SELECT r1.nr_secao, r1.nm_candidato AS lider, r1.sg_partido AS partido,
               ROUND(r1.votos / t.total_votos * 100, 1) AS pct,
               r2.nm_candidato AS segundo, ROUND(r2.votos / t.total_votos * 100, 1) AS pct2,
               t.total_votos
        FROM ranked r1
        LEFT JOIN ranked r2 ON r1.nr_secao = r2.nr_secao AND r2.rnk = 2
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


# ── UFs ───────────────────────────────────────────────────────────────────────

_UFSALL = [
    "AC",
    "AL",
    "AM",
    "AP",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MG",
    "MS",
    "MT",
    "PA",
    "PB",
    "PE",
    "PI",
    "PR",
    "RJ",
    "RN",
    "RO",
    "RR",
    "RS",
    "SC",
    "SE",
    "SP",
    "TO",
]


@app.get("/api/ufs")
async def get_ufs() -> JSONResponse:
    return JSONResponse({"ufs": _UFSALL})


# ── Mesorregiões ──────────────────────────────────────────────────────────────


_UF_TO_IBGE_ID = {
    "AC": "12",
    "AL": "27",
    "AM": "13",
    "AP": "16",
    "BA": "29",
    "CE": "23",
    "DF": "53",
    "ES": "32",
    "GO": "52",
    "MA": "21",
    "MG": "31",
    "MS": "50",
    "MT": "51",
    "PA": "15",
    "PB": "25",
    "PE": "26",
    "PI": "22",
    "PR": "41",
    "RJ": "33",
    "RN": "24",
    "RO": "11",
    "RR": "14",
    "RS": "43",
    "SC": "42",
    "SE": "28",
    "SP": "35",
    "TO": "17",
}


@app.get("/api/mesorregioes")
async def get_mesorregioes(uf: str = "SP") -> JSONResponse:
    import httpx

    ibge_id = _UF_TO_IBGE_ID.get(uf.upper(), "35")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{ibge_id}/mesorregioes",
                timeout=10,
            )
            data = resp.json() if resp.status_code == 200 else []
    except Exception as exc:
        logger.warning("IBGE mesorregioes falhou: %s", exc)
        data = []
    return JSONResponse({"mesorregioes": [{"id": m["id"], "nome": m["nome"]} for m in data]})


@app.get("/api/resultados/partido")
async def get_historico_partido(
    partido: str = Query("PT"),
    cargo: str = Query("Presidente"),
    uf: str = Query("BR"),
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            params: list = [
                bigquery.ScalarQueryParameter("partido", "STRING", partido.upper()),
                bigquery.ScalarQueryParameter("cargo", "STRING", cargo),
            ]
            where = "sg_partido = @partido AND ds_cargo_normalizado = @cargo"
            if uf.upper() != "BR":
                where += " AND sg_uf = @uf"
                params.append(bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()))
            query = f"""
                SELECT
                    ano_eleicao                                       AS ano,
                    COUNT(DISTINCT nm_candidato)                      AS candidaturas,
                    COUNTIF(ds_sit_tot_turno IN
                        ('ELEITO','ELEITO POR QP','ELEITO POR MÉDIA')) AS eleitos,
                    SUM(qt_votos_nominais)                            AS votos
                FROM `{gold}.fact_municipio_candidato_eleicao`
                WHERE {where}
                GROUP BY ano_eleicao
                ORDER BY ano_eleicao
                LIMIT 10
            """
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse(
                {"data": [dict(r) for r in rows], "partido": partido, "cargo": cargo}
            )
        except Exception as exc:
            logger.warning("BigQuery historico_partido falhou: %s", exc)
    return JSONResponse({"data": [], "partido": partido, "cargo": cargo, "status": "no_bq"})


# ── Social: Sentimento ────────────────────────────────────────────────────────


@app.get("/api/social/sentimento")
async def get_social_sentimento(
    cargo: str = "",
    uf: str = "",
    ano: int = 2026,
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"
            params: list = []
            where_clauses = ["data_referencia >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 WEEK)"]
            # sg_uf is often empty in social data — only filter when it has real values
            if uf and uf.upper() != "BR":
                where_clauses.append("(sg_uf = @uf OR sg_uf IS NULL OR sg_uf = '')")
                params.append(bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()))
            where_sql = " AND ".join(where_clauses)
            query = f"""
                SELECT
                    candidato,
                    FORMAT_DATE('%Y-%m-%d', data_referencia) AS semana,
                    ROUND(AVG(sentimento_score), 3)          AS sentimento_score_medio,
                    COUNT(*)                                 AS total_mencoes,
                    SUM(COALESCE(like_count, 0) + COALESCE(view_count, 0) + COALESCE(comment_count, 0))
                                                             AS engajamento_total
                FROM `{silver}.social_mencoes_br`
                WHERE {where_sql}
                  AND candidato IS NOT NULL
                GROUP BY candidato, data_referencia
                ORDER BY data_referencia DESC, total_mencoes DESC
                LIMIT 200
            """
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery social sentimento falhou: %s", exc)
    return JSONResponse(
        {
            "data": [],
            "status": "sem_tokens",
            "hint": "Configure TWITTER_BEARER_TOKEN e YOUTUBE_API_KEY para ativar monitoramento social.",
        }
    )


# ── Social: Google Trends por UF ─────────────────────────────────────────────


@app.get("/api/social/trends")
async def get_social_trends(uf: str = "SP", ano: int = 2026) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            query = f"""
                SELECT candidato, ano, interesse_busca_medio AS interesse
                FROM `{gold}.fact_google_trends_uf`
                WHERE sg_uf = @uf AND ano = @ano
                ORDER BY candidato
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                    bigquery.ScalarQueryParameter("ano", "INT64", ano),
                ]
            )
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery social trends falhou: %s", exc)
    return JSONResponse(
        {
            "data": [],
            "status": "sem_tokens",
            "hint": "Configure TWITTER_BEARER_TOKEN e YOUTUBE_API_KEY para ativar monitoramento social.",
        }
    )


# ── Social: Plataformas ───────────────────────────────────────────────────────


@app.get("/api/social/plataformas")
async def get_social_plataformas(uf: str = "SP", ano: int = 2026) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            try:
                query = f"""
                    SELECT fonte, total_posts, total_likes, sentimento_medio
                    FROM `{gold}.vw_social_plataforma_uf`
                    WHERE sg_uf = @uf AND EXTRACT(YEAR FROM data_ref) = @ano
                    ORDER BY total_posts DESC
                """
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                        bigquery.ScalarQueryParameter("ano", "INT64", ano),
                    ]
                )
                rows = list(client.query(query, job_config=job_config).result())
            except Exception:
                silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"
                query = f"""
                    SELECT fonte,
                           COUNT(*) AS total_posts,
                           SUM(qt_likes) AS total_likes,
                           AVG(sentimento_score) AS sentimento_medio
                    FROM `{silver}.social_mencoes_br`
                    WHERE sg_uf = @uf AND EXTRACT(YEAR FROM data_referencia) = @ano
                    GROUP BY fonte
                    ORDER BY total_posts DESC
                """
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                        bigquery.ScalarQueryParameter("ano", "INT64", ano),
                    ]
                )
                rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery social plataformas falhou: %s", exc)
    return JSONResponse(
        {
            "data": [],
            "status": "sem_tokens",
            "hint": "Configure TWITTER_BEARER_TOKEN e YOUTUBE_API_KEY para ativar monitoramento social.",
        }
    )


# ── Social: Detector de crise ─────────────────────────────────────────────────


@app.get("/api/social/crise")
async def get_social_crise(uf: str = "SP") -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            query = f"""
                SELECT candidato, sg_uf, fonte, data_ref, ratio_vs_baseline
                FROM `{gold}.vw_social_crise_detector`
                WHERE crise_detectada = TRUE AND sg_uf = @uf
                ORDER BY ratio_vs_baseline DESC
                LIMIT 20
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                ]
            )
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery social crise falhou: %s", exc)
    return JSONResponse(
        {
            "data": [],
            "status": "sem_tokens",
            "hint": "Configure TWITTER_BEARER_TOKEN e YOUTUBE_API_KEY para ativar monitoramento social.",
        }
    )


# ── Social: Temas por UF ──────────────────────────────────────────────────────


@app.get("/api/social/temas")
async def get_social_temas(uf: str = "SP", ano: int = 2026) -> JSONResponse:
    """Retorna distribuição de temas/tipos de conteúdo por UF.

    fact_social_municipio não possui coluna `tema`; usa `tipo_fonte` como proxy
    de categoria para o gráfico de rosca no dashboard. Retorna array vazio com
    estrutura correta quando o BQ não está disponível, evitando erro 404.
    """
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            query = f"""
                SELECT tipo_fonte AS tema, SUM(qt_posts) AS mencoes
                FROM `{gold}.fact_social_municipio`
                WHERE sg_uf = @uf AND ano = @ano
                GROUP BY tipo_fonte
                ORDER BY mencoes DESC
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                    bigquery.ScalarQueryParameter("ano", "INT64", ano),
                ]
            )
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery social temas falhou: %s", exc)
    return JSONResponse(
        {
            "data": [],
            "status": "sem_tokens",
            "hint": "Configure TWITTER_BEARER_TOKEN e YOUTUBE_API_KEY para ativar monitoramento social.",
        }
    )


# ── Digital: Meta Ads por UF ──────────────────────────────────────────────────


@app.get("/api/digital/meta")
async def get_digital_meta(uf: str = "SP", ano: int = 2026) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            query = f"""
                SELECT candidato, vl_gasto_total_uf, total_impressions
                FROM `{gold}.fact_meta_ads_uf`
                WHERE sg_uf = @uf AND ano = @ano
                ORDER BY vl_gasto_total_uf DESC
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                    bigquery.ScalarQueryParameter("ano", "INT64", ano),
                ]
            )
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery digital meta falhou: %s", exc)
    return JSONResponse({"data": []})


# ── Previsão ──────────────────────────────────────────────────────────────────


@app.get("/api/previsao")
async def get_previsao(
    cargo: str = "Governador",
    uf: str = "SP",
    ano: int = 2026,
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            mlops = f"{settings.gcp_project_id}.{settings.bigquery_dataset_mlops}"
            cd_cargo = _cargo_to_cd(cargo, 3)
            try:
                # fact_predictions schema: candidato, sg_uf, prediction_date, p_mean, p_lower,
                # p_upper, model_version — table has require_partition_filter on prediction_date
                query = f"""
                    SELECT candidato,
                           ROUND(p_mean, 4)  AS prob_vitoria,
                           ROUND(p_lower, 4) AS intervalo_inferior,
                           ROUND(p_upper, 4) AS intervalo_superior
                    FROM `{mlops}.fact_predictions`
                    WHERE sg_uf = @uf
                      AND DATE(prediction_date) >= DATE_SUB(CURRENT_DATE(), INTERVAL 2 YEAR)
                    ORDER BY p_mean DESC
                    LIMIT 10
                """
                job_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                    ]
                )
                rows = list(client.query(query, job_config=job_config).result())
                if rows:
                    return JSONResponse({"data": [dict(r) for r in rows]})
            except Exception:
                pass
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            query = f"""
                SELECT candidato_normalizado                          AS candidato,
                       ROUND(AVG(intencao_ponderada) / 100.0, 4)     AS prob_vitoria,
                       ROUND((AVG(intencao_ponderada) - STDDEV(intencao_ponderada)) / 100.0, 4) AS intervalo_inferior,
                       ROUND((AVG(intencao_ponderada) + STDDEV(intencao_ponderada)) / 100.0, 4) AS intervalo_superior
                FROM `{gold}.fact_intencao_voto`
                WHERE (uf = @uf OR @uf = 'BR' OR uf = 'BR')
                  AND cd_cargo = @cd_cargo
                  AND ano_eleitoral = @ano
                GROUP BY candidato_normalizado
                ORDER BY AVG(intencao_ponderada) DESC
                LIMIT 10
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                    bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
                    bigquery.ScalarQueryParameter("ano", "INT64", ano),
                ]
            )
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery previsao falhou: %s", exc)
    return JSONResponse({"data": []})


@app.get("/api/multifonte")
async def get_multifonte(
    cargo: str = Query("Governador"),
    uf: str = Query("SP"),
    ano: int = Query(2022),
) -> JSONResponse:
    """Comparação de dados entre fontes: TSE histórico, polls, sentimento social, socioeco."""
    results: dict = {"tse": None, "polls": None, "social": None, "ibge": None}

    if not (settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"):
        return JSONResponse({"sources": results, "uf": uf, "ano": ano, "cargo": cargo})

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=settings.gcp_project_id)
        gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
        cd_cargo = _cargo_to_cd(cargo, 3)

        async def _run_q(key: str, q: str, params: list) -> None:
            try:
                rows = await asyncio.to_thread(
                    lambda: list(
                        client.query(
                            q,
                            job_config=bigquery.QueryJobConfig(query_parameters=params),
                        ).result()
                    )
                )
                results[key] = [dict(r) for r in rows]
            except Exception as exc:
                logger.debug("multifonte[%s] falhou: %s", key, exc)

        tse_q = f"""
            SELECT nm_candidato AS candidato,
                   ROUND(SUM(total_votos)/SUM(SUM(total_votos)) OVER()*100,1) AS valor,
                   'pct_votos_tse' AS metrica
            FROM `{gold}.fact_municipio_candidato_eleicao`
            WHERE sg_uf=@uf AND ano_eleicao=@ano AND cd_cargo=@cd AND nr_turno=1
            GROUP BY nm_candidato ORDER BY valor DESC LIMIT 6
        """
        polls_q = f"""
            SELECT candidato_normalizado AS candidato,
                   ROUND(AVG(intencao_ponderada),1) AS valor,
                   'pct_pesquisa' AS metrica
            FROM `{gold}.fact_intencao_voto`
            WHERE uf=@uf AND ano_eleitoral=@ano AND cd_cargo=@cd
            GROUP BY candidato_normalizado ORDER BY valor DESC LIMIT 6
        """
        social_q = f"""
            SELECT nm_candidato AS candidato,
                   ROUND(AVG(sentimento_score_medio)*100,1) AS valor,
                   'sentimento_pct' AS metrica
            FROM `{gold}.vw_sentimento_municipio`
            WHERE sg_uf=@uf AND EXTRACT(YEAR FROM data_semana)=@ano
            GROUP BY nm_candidato ORDER BY valor DESC LIMIT 6
        """
        base_params = [
            bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("cd", "INT64", cd_cargo),
        ]
        await asyncio.gather(
            _run_q("tse", tse_q, base_params),
            _run_q("polls", polls_q, base_params),
            _run_q("social", social_q, base_params[:2]),
        )
    except Exception as exc:
        logger.warning("multifonte geral falhou: %s", exc)

    return JSONResponse({"sources": results, "uf": uf, "ano": ano, "cargo": cargo})


# ── Endividamento familiar BACEN ──────────────────────────────────────────────


@app.get("/api/endividamento")
async def get_endividamento(
    ano_start: int = Query(2025),
    ano_end: int = Query(2026),
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            query = f"""
                SELECT data_referencia, endividamento_familias_pct,
                       comprometimento_renda_pct, inadimplencia_pf_pct,
                       inadimplencia_pf_credito, fontes
                FROM `{gold}.fact_endividamento_nacional`
                WHERE ano BETWEEN @ano_start AND @ano_end
                ORDER BY data_referencia
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("ano_start", "INT64", ano_start),
                    bigquery.ScalarQueryParameter("ano_end", "INT64", ano_end),
                ]
            )
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery endividamento falhou: %s", exc)
            # Fallback: Silver endividamento_nacional
            try:
                silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"
                query3 = f"""
                    SELECT SAFE_CAST(ano AS INT64) AS ano,
                           SAFE_CAST(mes AS INT64) AS mes,
                           CAST(data_referencia AS DATE) AS data_referencia,
                           SAFE_CAST(endividamento_familias_pct AS FLOAT64) AS endividamento_familias_pct,
                           SAFE_CAST(comprometimento_renda_pct AS FLOAT64) AS comprometimento_renda_pct,
                           SAFE_CAST(inadimplencia_pf_pct AS FLOAT64) AS inadimplencia_pf_pct,
                           fontes
                    FROM `{silver}.endividamento_nacional`
                    WHERE SAFE_CAST(ano AS INT64) BETWEEN @ano_start AND @ano_end
                    ORDER BY data_referencia
                """
                job_config3 = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("ano_start", "INT64", ano_start),
                        bigquery.ScalarQueryParameter("ano_end", "INT64", ano_end),
                    ]
                )
                rows3 = list(client.query(query3, job_config=job_config3).result())
                if rows3:
                    return JSONResponse({"data": [dict(r) for r in rows3], "fonte": "silver"})
            except Exception as exc3:
                logger.warning("BigQuery endividamento Silver falhou: %s", exc3)
    # Last resort: call BACEN SGS API directly (public, no auth)
    try:
        import httpx

        _series = {
            "endividamento_familias_pct": 29037,
            "comprometimento_renda_pct": 29038,
            "inadimplencia_pf_pct": 21084,
        }
        _d_ini = f"01/01/{ano_start}"
        _d_fim = f"31/12/{ano_end}"
        _frames: dict = {}
        async with httpx.AsyncClient(timeout=20) as _hc:
            for _col, _codigo in _series.items():
                try:
                    _r = await _hc.get(
                        f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{_codigo}/dados",
                        params={"formato": "json", "dataInicial": _d_ini, "dataFinal": _d_fim},
                    )
                    _r.raise_for_status()
                    _frames[_col] = {
                        item["data"]: float(item["valor"].replace(",", "."))
                        for item in _r.json()
                        if item.get("valor") not in (None, "", "-")
                    }
                except Exception:
                    pass
        _all_dates = sorted(set().union(*[set(f.keys()) for f in _frames.values()]))
        if _all_dates:
            _rows_live = [
                {
                    "data_referencia": _d,
                    "endividamento_familias_pct": _frames.get("endividamento_familias_pct", {}).get(
                        _d
                    ),
                    "comprometimento_renda_pct": _frames.get("comprometimento_renda_pct", {}).get(
                        _d
                    ),
                    "inadimplencia_pf_pct": _frames.get("inadimplencia_pf_pct", {}).get(_d),
                    "inadimplencia_pf_credito": None,
                    "fontes": "BACEN-SGS-live",
                }
                for _d in _all_dates
            ]
            return JSONResponse({"data": _rows_live, "fonte": "bacen_live"})
    except Exception as _exc:
        logger.warning("BACEN SGS live falhou: %s", _exc)
    return JSONResponse({"data": []})


# ── Votações parlamentares (Câmara + Senado) ──────────────────────────────────


@app.get("/api/parlamentares")
async def get_parlamentares(
    uf: str = Query("BR"),
    year: int = Query(2025),
    casa: str = Query(""),
) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            params: list = [bigquery.ScalarQueryParameter("year", "INT64", year)]
            uf_filter = "" if uf.upper() == "BR" else "AND sg_uf = @uf"
            casa_filter = ""
            if uf.upper() != "BR":
                params.append(bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()))
            if casa:
                casa_filter = "AND casa = @casa"
                params.append(bigquery.ScalarQueryParameter("casa", "STRING", casa))
            query = f"""
                SELECT sg_uf, sg_partido, casa, tema,
                       SUM(qt_parlamentares)                              AS qt_parlamentares,
                       SUM(qt_votacoes)                                   AS qt_votacoes,
                       SUM(qt_sim)                                        AS qt_sim,
                       SUM(qt_nao)                                        AS qt_nao,
                       SUM(qt_abstencao)                                  AS qt_abstencao,
                       ROUND(SUM(qt_sim) / NULLIF(SUM(qt_votacoes), 0) * 100, 1) AS pct_favoravel
                FROM `{gold}.fact_votacoes_parlamentar`
                WHERE ano = @year {uf_filter} {casa_filter}
                GROUP BY sg_uf, sg_partido, casa, tema
                ORDER BY qt_parlamentares DESC NULLS LAST
                LIMIT 200
            """
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery parlamentares falhou (Gold): %s", exc)
            # Fallback: Silver parlamentares_federais (directory, no voting data)
            try:
                silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"
                params2: list = [bigquery.ScalarQueryParameter("year", "INT64", year)]
                uf_filter2 = "" if uf.upper() == "BR" else "AND sg_uf = @uf"
                if uf.upper() != "BR":
                    params2.append(bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()))
                query2 = f"""
                    SELECT COALESCE(sg_uf, 'BR') AS sg_uf,
                           COALESCE(sg_partido, 'N/A') AS sg_partido,
                           COALESCE(casa, 'Câmara') AS casa,
                           'Geral' AS tema,
                           COUNT(*) AS qt_parlamentares,
                           CAST(NULL AS INT64) AS qt_votacoes,
                           CAST(NULL AS INT64) AS qt_sim,
                           CAST(NULL AS INT64) AS qt_nao,
                           CAST(NULL AS INT64) AS qt_abstencao,
                           CAST(NULL AS FLOAT64) AS pct_favoravel
                    FROM `{silver}.parlamentares_federais`
                    WHERE SAFE_CAST(ano_ref AS INT64) = @year {uf_filter2}
                    GROUP BY sg_uf, sg_partido, casa
                    ORDER BY qt_parlamentares DESC
                    LIMIT 200
                """
                rows2 = list(
                    client.query(
                        query2,
                        job_config=bigquery.QueryJobConfig(query_parameters=params2),
                    ).result()
                )
                if rows2:
                    return JSONResponse({"data": [dict(r) for r in rows2], "fonte": "silver"})
            except Exception as exc2:
                logger.warning("BigQuery parlamentares Silver falhou: %s", exc2)
    return JSONResponse({"data": []})


# ── Diagnóstico de dados — quais tabelas Gold/Silver têm dados ───────────────


@app.get("/api/debug/tables", dependencies=[Depends(require_auth)])
async def debug_tables() -> JSONResponse:
    """Retorna row_count de todas as tabelas Gold e Silver. Útil para diagnosticar abas vazias."""
    if not (settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"):
        return JSONResponse({"error": "USE_BIGQUERY não habilitado", "tables": []})
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=settings.gcp_project_id)
        gold = settings.bigquery_dataset_gold
        silver = settings.bigquery_dataset_silver
        mlops = settings.bigquery_dataset_mlops
        results = []
        for dataset in [gold, silver, mlops]:
            try:
                query = f"""
                    SELECT table_id, row_count, size_bytes,
                           TIMESTAMP_MILLIS(last_modified_time) AS last_modified
                    FROM `{settings.gcp_project_id}.{dataset}.__TABLES__`
                    ORDER BY table_id
                """
                rows = list(client.query(query).result())
                for r in rows:
                    results.append(
                        {
                            "dataset": dataset,
                            "table": r.get("table_id", ""),
                            "rows": r.get("row_count", 0),
                            "size_mb": round((r.get("size_bytes") or 0) / 1_048_576, 2),
                            "last_modified": str(r.get("last_modified", "")),
                            "status": "ok" if (r.get("row_count") or 0) > 0 else "empty",
                        }
                    )
            except Exception as exc:
                results.append({"dataset": dataset, "error": str(exc)})
        return JSONResponse({"tables": results, "total": len(results)})
    except Exception as exc:
        return JSONResponse({"error": str(exc), "tables": []}, status_code=500)


# ── Debug: Validação de Fontes ────────────────────────────────────────────────

_FONTES_SPEC: list[dict] = [
    {
        "id": "eleitoral",
        "icon": "🏆",
        "label": "Eleitoral TSE",
        "bq_table": "fact_municipio_candidato_eleicao",
        "bq_dataset": "gold",
        "bq_date_col": "ano_eleicao",
        "bq_sample": "sg_uf, nm_municipio, nm_candidato, ROUND(pct_votos_municipio,3) AS pct_votos, ano_eleicao",
        "local_glob": "tse_*_*.parquet",
        "local_date_col": "ano_eleicao",
    },
    {
        "id": "ibge",
        "icon": "📊",
        "label": "IBGE / IDH",
        "bq_table": "fact_ibge_municipio",
        "bq_dataset": "gold",
        "bq_date_col": "ano",
        "bq_sample": "sg_uf, nm_municipio, ano, idhm, renda_per_capita, gini",
        "local_glob": "ibge_*.parquet",
        "local_date_col": "ano",
    },
    {
        "id": "saude",
        "icon": "🏥",
        "label": "Saúde DATASUS",
        "bq_table": "fact_saude_municipio",
        "bq_dataset": "gold",
        "bq_date_col": "ano",
        "bq_sample": "cd_municipio_ibge, sg_uf, ano, taxa_mortalidade_infantil_1000, idsus_score",
        "local_glob": "saude_municipal_*.parquet",
        "local_date_col": "ano",
    },
    {
        "id": "seguranca",
        "icon": "🛡",
        "label": "Segurança SINESP",
        "bq_table": "fact_seguranca_municipio",
        "bq_dataset": "gold",
        "bq_date_col": "ano",
        "bq_sample": "cd_municipio_ibge, sg_uf, ano, taxa_homicidio_100k, ivs_total",
        "local_glob": "seguranca_municipal_*.parquet",
        "local_date_col": "ano",
    },
    {
        "id": "pesquisas",
        "icon": "📋",
        "label": "Pesquisas Polls",
        "bq_table": "fact_intencao_voto",
        "bq_dataset": "gold",
        "bq_date_col": "ano_eleitoral",
        "bq_sample": "uf, candidato_normalizado, cd_cargo, ano_eleitoral, ROUND(intencao_ponderada,3) AS intencao",
        "local_glob": "fact_pesquisa_intencao_*.parquet",
        "local_date_col": "ano_eleitoral",
    },
    {
        "id": "sentimento",
        "icon": "📱",
        "label": "Sentimento Social",
        "bq_table": "fact_social_municipio",
        "bq_dataset": "gold",
        "bq_date_col": "data_referencia",
        "bq_sample": "sg_uf, candidato, fonte, ano, qt_posts, ROUND(score_liquido_sentimento,2) AS score",
        "local_glob": "social_mencoes_br_*.parquet",
        "local_date_col": "ano",
    },
    {
        "id": "trends",
        "icon": "🔥",
        "label": "Google Trends",
        "bq_table": "fact_google_trends_uf",
        "bq_dataset": "gold",
        "bq_date_col": "ano",
        "bq_sample": "sg_uf, candidato, ano, interesse_busca_medio, qt_semanas",
        "local_glob": "google_trends_uf_*.parquet",
        "local_date_col": "ano",
    },
    {
        "id": "bolsa",
        "icon": "💰",
        "label": "Bolsa Família",
        "bq_table": "fact_transferencias_sociais",
        "bq_dataset": "gold",
        "bq_date_col": "ano",
        "bq_sample": "sg_uf, nm_municipio, ano, qtd_beneficiarios_bolsa_familia, valor_total_bolsa_familia_reais",
        "local_glob": "transferencias_sociais_*.parquet",
        "local_date_col": "ano",
    },
    {
        "id": "meta",
        "icon": "📢",
        "label": "Meta Ads",
        "bq_table": "fact_meta_ads_uf",
        "bq_dataset": "gold",
        "bq_date_col": "ano",
        "bq_sample": "sg_uf, candidato, ano, qt_anuncios, vl_gasto_total_uf, qt_impressoes_total_uf",
        "local_glob": "meta_ads_regioes_*.parquet",
        "local_date_col": "ano",
    },
    {
        "id": "predicao",
        "icon": "🔮",
        "label": "Predição PyMC",
        "bq_table": "fact_predictions",
        "bq_dataset": "mlops",
        "bq_date_col": "prediction_date",
        "bq_sample": "sg_uf, candidato, ROUND(p_mean,3) AS p_mean, ROUND(p_lower,3) AS p_lower, ROUND(p_upper,3) AS p_upper, prediction_date",
        "bq_where": "prediction_date >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 365 DAY)",
        "local_glob": None,
        "local_date_col": None,
    },
]


async def _validate_fonte_bq(spec: dict, base: dict) -> dict:
    import asyncio
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    dataset_name = settings.bigquery_dataset_gold if spec["bq_dataset"] == "gold" else "spepe_mlops"
    full = f"`{settings.gcp_project_id}.{dataset_name}.{spec['bq_table']}`"
    dc = spec["bq_date_col"]

    bq_where = spec.get("bq_where")
    where_clause = f" WHERE {bq_where}" if bq_where else ""
    count_q = f"SELECT COUNT(*) AS n, CAST(MIN({dc}) AS STRING) AS d_min, CAST(MAX({dc}) AS STRING) AS d_max FROM {full}{where_clause}"
    rows = await asyncio.to_thread(lambda: list(client.query(count_q).result()))
    n = int(rows[0]["n"] or 0)
    base.update(
        rows=n,
        date_min=str(rows[0]["d_min"] or ""),
        date_max=str(rows[0]["d_max"] or ""),
        status="ok" if n > 0 else "empty",
        source_type="bigquery",
    )
    if n > 0:
        sample_rows = await asyncio.to_thread(
            lambda: list(
                client.query(
                    f"SELECT {spec['bq_sample']} FROM {full}{where_clause} LIMIT 5"
                ).result()
            )
        )
        base["sample"] = [
            {k: (str(v) if v is not None else None) for k, v in dict(r).items()}
            for r in sample_rows
        ]
    return base


def _validate_fonte_local(spec: dict, base: dict) -> dict:
    import pandas as pd

    glob_pat = spec.get("local_glob")
    if not glob_pat:
        base.update(status="error", error="sem fallback local")
        return base

    files = sorted(_LOCAL_SILVER_DIR.glob(glob_pat))
    if not files:
        gold_dir = Path(os.environ.get("DATA_DIR", "data")) / "gold"
        files = sorted(gold_dir.glob(glob_pat)) if gold_dir.exists() else []

    if not files:
        base["status"] = "empty"
        return base

    total, sample_df, dates = 0, None, []
    dc = spec.get("local_date_col")
    for f in files:
        try:
            df = pd.read_parquet(f)
            total += len(df)
            if sample_df is None:
                sample_df = df
            if dc and dc in df.columns:
                dates.extend(df[dc].dropna().tolist())
        except Exception:
            pass

    base.update(rows=total, status="ok" if total > 0 else "empty", source_type="local_parquet")
    if dates:
        base["date_min"] = str(min(dates))
        base["date_max"] = str(max(dates))
    if sample_df is not None and total > 0:
        cols = sample_df.columns.tolist()[:6]
        sample = sample_df[cols].head(5).fillna("").astype(str)
        base["sample"] = sample.to_dict(orient="records")
    return base


@app.get("/api/debug/fontes")
async def debug_fontes() -> JSONResponse:
    """Valida cada tabela Gold — row count, date range, amostra de 5 linhas.
    Tenta BigQuery; fallback para parquet local se USE_BIGQUERY não ativo.
    """
    use_bq = bool(settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true")
    results = []
    for spec in _FONTES_SPEC:
        base: dict = {
            "id": spec["id"],
            "icon": spec["icon"],
            "label": spec["label"],
            "table": spec["bq_table"],
            "rows": 0,
            "date_min": None,
            "date_max": None,
            "sample": [],
            "status": "empty",
            "source_type": "local",
        }
        try:
            if use_bq:
                base = await _validate_fonte_bq(spec, base)
            else:
                base = _validate_fonte_local(spec, base)
        except Exception as exc:
            logger.warning("debug_fontes %s: %s", spec["id"], exc)
            base.update(status="error", error=str(exc))
        results.append(base)

    ok = sum(1 for r in results if r["status"] == "ok")
    empty = sum(1 for r in results if r["status"] == "empty")
    err = sum(1 for r in results if r["status"] == "error")
    return JSONResponse(
        {
            "sources": results,
            "ok": ok,
            "empty": empty,
            "error": err,
            "source_type": "bigquery" if use_bq else "local_parquet",
        }
    )


# ── Config: Google Maps Key ───────────────────────────────────────────────────


@app.get("/api/config/maps-key")
async def get_maps_key() -> JSONResponse:
    from security.secret_manager import get_secret

    key = get_secret("GOOGLE_MAPS_API_KEY") or settings.google_maps_api_key
    return JSONResponse({"key": key})


# ── DashboardCommand v1 — structured intent payload sent to frontend ──────


class DashboardAction(BaseModel):
    type: str
    layer: str | None = None
    uf: str | None = None
    cargo: str | None = None
    candidato: str | None = None
    municipio: int | str | None = None
    municipios: list[int | str] | None = None
    metric: str | None = None
    period_from: str | None = Field(default=None, alias="from")
    period_to: str | None = Field(default=None, alias="to")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class DashboardUpdate(BaseModel):
    intent_schema: str = "v1"
    actions: list[DashboardAction] = Field(default_factory=list)
    narration: str = ""

    model_config = {"extra": "ignore"}


_ALLOWED_ACTION_TYPES = {
    "set_layer",
    "set_filter",
    "zoom_to",
    "highlight",
    "set_metric",
    "set_period",
}


def _validate_dashboard_update(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Validate v1 DashboardCommand. Returns serializable dict (alias preserved) or None."""
    if not isinstance(raw, dict):
        return None
    try:
        update = DashboardUpdate(**raw)
    except ValidationError as exc:
        logger.warning("DashboardUpdate validation failed: %s", exc)
        return None
    actions: list[dict[str, Any]] = []
    for action in update.actions:
        if action.type not in _ALLOWED_ACTION_TYPES:
            continue
        actions.append(action.model_dump(by_alias=True, exclude_none=True))
    if not actions:
        return None
    return {
        "intent_schema": "v1",
        "actions": actions,
        "narration": update.narration,
    }


# ── WebSocket Chat → Supervisor ────────────────────────────────────────────


@app.websocket("/ws/chat")
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

    await websocket.send_json(
        {
            "type": "welcome",
            "text": (
                f"## SPEPE — Sistema de Perfilamento do Eleitorado\n\n"
                f"Digite `/help` para ver os comandos disponíveis.\n\n"
                f"*Sessão `{state.session_id[:8]}` | Budget: $2.00*"
            ),
        }
    )

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

            if user_text.lower() in ("/help", "help", "ajuda"):
                await websocket.send_json({"type": "chunk", "text": _HELP_TEXT})
                await websocket.send_json(
                    {
                        "type": "done",
                        "cost": 0,
                        "budget_remaining": round(2.0 - state.total_cost_usd, 4),
                        "dashboard_update": {},
                    }
                )
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
                logger.error("Supervisor WS erro: %s", exc, exc_info=True)
                await websocket.send_json({"type": "error", "message": str(exc)})
                continue

            # Prefer structured intent from emit_dashboard_intent tool / Gemini extraction
            dashboard_update: dict[str, Any] = {}
            if intent_sink:
                last_intent = intent_sink[-1]
                actions = last_intent.get("actions") or []
                is_v1 = any(isinstance(a, dict) and "type" in a for a in actions)
                if is_v1:
                    validated = _validate_dashboard_update(
                        {
                            "intent_schema": "v1",
                            "actions": actions,
                            "narration": last_intent.get("narration", ""),
                        }
                    )
                    if validated:
                        dashboard_update = validated
                else:
                    dashboard_update = {
                        "intent_schema": "v1",
                        "actions": actions,
                        "narration": last_intent.get("narration", ""),
                    }

            if not dashboard_update:
                dashboard_update = _extract_dashboard_update(full_text, user_text)

            payload: dict[str, Any] = {
                "type": "done",
                "cost": round(state.total_cost_usd, 5),
                "budget_remaining": round(2.0 - state.total_cost_usd, 4),
            }
            if dashboard_update:
                payload["dashboard_update"] = dashboard_update

            await websocket.send_json(payload)

    except WebSocketDisconnect:
        logger.info("WS chat desconectado: %s", state.session_id)


# ── Admin Panel ───────────────────────────────────────────────────────────────


@app.get("/admin")
async def serve_admin() -> FileResponse:
    """Serve the Admin Panel HTML."""
    from pathlib import Path

    html_path = Path(__file__).parent / "static" / "admin.html"
    return FileResponse(str(html_path), media_type="text/html")


_USER_STORE: list[dict] = []  # populated by Firestore spepe_users collection
_ACCESS_MATRIX: dict = {
    "jornalista": {"tab_mapa": 1, "tab_socioeconomico": 1, "chat_ai": 1, "export_data": 1},
    "consultor": {
        "tab_mapa": 1,
        "tab_socioeconomico": 1,
        "tab_seguranca": 1,
        "tab_saude": 1,
        "tab_pesquisas": 1,
        "tab_comparar": 1,
        "chat_ai": 1,
        "export_data": 1,
        "tab_predicao": 1,
    },
    "cientista": {
        "tab_mapa": 1,
        "tab_socioeconomico": 1,
        "tab_seguranca": 1,
        "tab_saude": 1,
        "tab_pesquisas": 1,
        "tab_comparar": 1,
        "tab_predicao": 1,
        "chat_ai": 1,
        "export_data": 1,
        "shap_detail": 1,
        "bias_metrics": 1,
        "model_confidence": 1,
        "raw_api": 1,
    },
    "pesquisador": {
        "tab_mapa": 1,
        "tab_socioeconomico": 1,
        "tab_seguranca": 1,
        "tab_saude": 1,
        "tab_pesquisas": 1,
        "tab_comparar": 1,
        "tab_predicao": 1,
        "chat_ai": 1,
        "export_data": 1,
        "shap_detail": 1,
        "bias_metrics": 1,
        "model_confidence": 1,
        "raw_api": 1,
    },
}  # fallback when Firestore unavailable — matches frontend ACCESS_DEFAULTS
_FIRESTORE_PROJECT = os.environ.get("GCP_PROJECT_ID", "")


def _fs_client():
    """Return Firestore client or None if unavailable."""
    try:
        from google.cloud import firestore

        return firestore.AsyncClient(project=_FIRESTORE_PROJECT) if _FIRESTORE_PROJECT else None
    except Exception:
        return None


@app.get("/admin/api/users", dependencies=[Depends(require_auth)])
async def admin_list_users() -> JSONResponse:
    """List users from Firestore spepe_users collection. Returns empty list when unavailable."""
    db = _fs_client()
    if db:
        try:
            docs = db.collection("spepe_users").stream()
            users = [doc.to_dict() async for doc in docs]
            return JSONResponse({"users": users, "source": "firestore"})
        except Exception as exc:
            logger.warning("Firestore users query failed: %s", exc)
            return JSONResponse(
                {"users": [], "source": "unavailable", "error": "Firestore indisponível"},
                status_code=503,
            )
    return JSONResponse(
        {
            "users": [],
            "source": "unavailable",
            "error": "Firestore não configurado (GCP_PROJECT_ID ausente)",
        },
        status_code=503,
    )


@app.post("/admin/api/users", dependencies=[Depends(require_auth)])
async def admin_create_user(request: Request) -> JSONResponse:
    import uuid
    from datetime import date

    body = await request.json()
    user = {**body, "id": str(uuid.uuid4()), "created_at": str(date.today())}
    db = _fs_client()
    if db:
        try:
            await db.collection("spepe_users").document(user["id"]).set(user)
            return JSONResponse({"ok": True, "user": user})
        except Exception as exc:
            logger.warning("Firestore create user failed: %s", exc)
            return JSONResponse(
                {"ok": False, "error": "Firestore indisponível — usuário não persistido"},
                status_code=503,
            )
    return JSONResponse(
        {"ok": False, "error": "Firestore não configurado (GCP_PROJECT_ID ausente)"},
        status_code=503,
    )


@app.put("/admin/api/users/{user_id}", dependencies=[Depends(require_auth)])
async def admin_update_user(user_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    db = _fs_client()
    if db:
        try:
            ref = db.collection("spepe_users").document(user_id)
            doc = await ref.get()
            if not doc.exists:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            await ref.update({**body, "id": user_id})
            return JSONResponse({"ok": True})
        except Exception as exc:
            logger.warning("Firestore update user %s failed: %s", user_id, exc)
            return JSONResponse(
                {"ok": False, "error": "Firestore indisponível — alteração não persistida"},
                status_code=503,
            )
    return JSONResponse(
        {"ok": False, "error": "Firestore não configurado (GCP_PROJECT_ID ausente)"},
        status_code=503,
    )


@app.delete("/admin/api/users/{user_id}", dependencies=[Depends(require_auth)])
async def admin_delete_user(user_id: str) -> JSONResponse:
    db = _fs_client()
    if db:
        try:
            await db.collection("spepe_users").document(user_id).delete()
            return JSONResponse({"ok": True})
        except Exception as exc:
            logger.warning("Firestore delete user %s failed: %s", user_id, exc)
            return JSONResponse(
                {"ok": False, "error": "Firestore indisponível — exclusão não persistida"},
                status_code=503,
            )
    return JSONResponse(
        {"ok": False, "error": "Firestore não configurado (GCP_PROJECT_ID ausente)"},
        status_code=503,
    )


@app.get("/admin/api/access", dependencies=[Depends(require_auth)])
async def admin_get_access() -> JSONResponse:
    """Get access matrix from Firestore spepe_admin/access_matrix with fallback."""
    db = _fs_client()
    if db:
        try:
            doc = await db.collection("spepe_admin").document("access_matrix").get()
            if doc.exists:
                raw = doc.to_dict() or {}
                # Handle both {"matrix": {...}} and flat {"jornalista": {...}} stored formats
                matrix = raw.get("matrix", raw)
                if matrix and isinstance(next(iter(matrix.values()), None), dict):
                    return JSONResponse({"matrix": matrix, "source": "firestore"})
        except Exception as exc:
            logger.debug("Firestore access_matrix query failed: %s", exc)
    return JSONResponse({"matrix": _ACCESS_MATRIX, "source": "local"})


@app.post("/admin/api/access", dependencies=[Depends(require_auth)])
async def admin_save_access(request: Request) -> JSONResponse:
    global _ACCESS_MATRIX
    data = await request.json()
    db = _fs_client()
    if db:
        try:
            # Store flat (not nested under "matrix") to avoid double-nesting on read
            await (
                db.collection("spepe_admin")
                .document("access_matrix")
                .set({"matrix": data, "updated_at": str(__import__("datetime").datetime.utcnow())})
            )
            _ACCESS_MATRIX = data
            return JSONResponse({"ok": True, "source": "firestore"})
        except Exception as exc:
            logger.debug("Firestore access_matrix save failed: %s", exc)
    _ACCESS_MATRIX = data
    return JSONResponse({"ok": True, "source": "local"})


@app.get("/admin/api/auth/me", dependencies=[Depends(require_auth)])
async def admin_auth_me(user_info: dict = Depends(require_auth)) -> JSONResponse:
    """Return current authenticated user info and their SPEPE profile."""
    email = user_info.get("email", "")
    name = user_info.get("name", email)
    # Look up profile in Firestore — user already verified by require_auth
    profile = "viewer"
    db = _fs_client()
    if db and email:
        try:
            docs = db.collection("spepe_users").where("email", "==", email).limit(1).stream()
            async for doc in docs:
                d = doc.to_dict() or {}
                profile = d.get("profile") or d.get("role") or "viewer"
                name = d.get("name") or name
                break
        except Exception:
            pass
    return JSONResponse({"email": email, "name": name, "profile": profile})


@app.get("/admin/api/jobs", dependencies=[Depends(require_auth)])
async def admin_list_jobs() -> JSONResponse:
    """List Cloud Run Jobs with last execution status."""
    jobs_config = [
        {"name": "spepe-tse-ingest", "module": "tse_ingest", "timeout": "3600s"},
        {
            "name": "spepe-tse-candidaturas-ingest",
            "module": "tse_candidaturas_ingest",
            "timeout": "3600s",
        },
        {"name": "spepe-tse-perfil-ingest", "module": "tse_perfil_ingest", "timeout": "1800s"},
        {"name": "spepe-ibge-sync", "module": "ibge_sync", "timeout": "1800s"},
        {"name": "spepe-security-ingest", "module": "security_ingest", "timeout": "1800s"},
        {"name": "spepe-datasus-ingest", "module": "datasus_ingest", "timeout": "1800s"},
        {"name": "spepe-dieese-ingest", "module": "dieese_ingest", "timeout": "900s"},
        {"name": "spepe-cetic-ingest", "module": "cetic_ingest", "timeout": "900s"},
        {"name": "spepe-social-ingest", "module": "social_ingest", "timeout": "1800s"},
        {"name": "spepe-pesquisas-ingest", "module": "pesquisas_ingest", "timeout": "1800s"},
        {"name": "spepe-digital-ingest", "module": "digital_ingest", "timeout": "900s"},
        {
            "name": "spepe-camara-senado-ingest",
            "module": "camara_senado_ingest",
            "timeout": "3600s",
        },
        {"name": "spepe-endividamento-ingest", "module": "endividamento_ingest", "timeout": "900s"},
        {"name": "spepe-cadunico-ingest", "module": "cadunico_ingest", "timeout": "3600s"},
        {"name": "spepe-emendas-ingest", "module": "emendas_ingest", "timeout": "1800s"},
        # Sanções: mantém em Gold para modelo, removido de dashboard (2026-05-12)
        # {"name": "spepe-sancoes-ingest", "module": "sancoes_ingest", "timeout": "1800s"},
        {"name": "spepe-reddit-ingest", "module": "reddit_ingest", "timeout": "900s"},
        {"name": "spepe-silver-transform", "module": "silver_transform", "timeout": "1800s"},
        {"name": "spepe-gold-build", "module": "gold_build", "timeout": "1800s"},
    ]
    source = "local"
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
                    jcfg["last_run_at"] = str(gj.update_time) if gj.update_time else ""
                    source = "cloud_run"
                else:
                    jcfg["last_status"] = "NOT_DEPLOYED"
                    jcfg["last_run_at"] = ""
        except Exception as exc:
            logger.warning("Cloud Run jobs list failed: %s", exc)
            for jcfg in jobs_config:
                jcfg.setdefault("last_status", "UNKNOWN")
                jcfg.setdefault("last_run_at", "")
    else:
        for jcfg in jobs_config:
            jcfg["last_status"] = "LOCAL_DEV"
            jcfg["last_run_at"] = ""
    return JSONResponse({"jobs": jobs_config, "source": source})


@app.post("/admin/api/jobs/{job_name}/run", dependencies=[Depends(require_auth)])
async def admin_run_job(job_name: str, uf: str = "SP", year: int = 2022) -> JSONResponse:
    """Trigger a Cloud Run Job execution (admin only)."""
    from agents.tools import RunJobArgs, run_dataops_job

    job_id = job_name.replace("spepe-", "").replace("-", "_")
    result = run_dataops_job(RunJobArgs(job=job_id, uf=uf, year=year))
    return JSONResponse(result)


@app.post("/jobs/retrain-trigger")
async def retrain_trigger(request: Request) -> Response:
    """Eventarc (Pub/Sub drift-detected) → submit Vertex AI Pipeline for retraining."""
    from dataops.jobs.retrain_trigger_job import handle_pubsub_event

    envelope = await request.json()
    message = envelope.get("message", {})
    exit_code = handle_pubsub_event(message)
    return Response(status_code=204 if exit_code == 0 else 500)


_SENTINEL_VIEWS = [
    "vw_sentimento_municipio",
    "vw_vulnerabilidade_municipio",
    "vw_perfil_municipio",
    "vw_intencao_voto_uf",
    "vw_pesquisa_vs_social",
    "vw_narrativa_por_tema_uf",
    "vw_cenario_2018_2022_2026",
    "vw_transferencias_municipio",
    "vw_transferencias_vs_eleicao",
    "vw_emendas_municipio",
    "vw_emendas_vs_eleicao",
    # "vw_sancoes_uf",  # Removido de dashboard (mantém em Gold para modelo)
    "vw_score_municipal_integrado",
    "vw_mapa_prioridade_campanha",
    "vw_social_candidato_sentimento",
    "vw_social_temas_uf",
    "vw_social_plataforma_uf",
    "vw_social_crise_detector",
    "vw_social_credibilidade",
    "vw_candidato_360",
    "vw_transferencias_candidato",
    "vw_emendas_candidato_uf",
]
_SENTINEL_JOBS = [
    "spepe-tse-ingest",
    "spepe-ibge-sync",
    "spepe-security-ingest",
    "spepe-datasus-ingest",
    "spepe-dieese-ingest",
    "spepe-cetic-ingest",
    "spepe-silver-transform",
    "spepe-gold-build",
    "spepe-digital-ingest",
    "spepe-social-ingest",
    "spepe-pesquisas-ingest",
    "spepe-tse-perfil-ingest",
    "spepe-tse-candidaturas-ingest",
    "spepe-reddit-ingest",
    "spepe-camara-senado-ingest",
    "spepe-endividamento-ingest",
    "spepe-cadunico-ingest",
    "spepe-emendas-ingest",
    # "spepe-sancoes-ingest",  # Removido de dashboard (mantém em Gold para modelo)
    "spepe-candidatos-discovery",
]
_SENTINEL_AGENTS = [
    "coletor",
    "analista-eleitoral",
    "perfilador",
    "modelista-bayesiano",
    "explicador",
    "narrador",
    "vigilante",
    "sentinela_social",
    "analista_seguranca",
    "contextualizador_saude",
]


@app.get("/admin/api/sentinel/status", dependencies=[Depends(require_auth)])
async def admin_sentinel_status() -> Response:
    """Real-time Sentinel snapshot — uses sentinel_queries for all data sources."""
    from datetime import datetime, timezone

    from ui.sentinel_queries import (
        AGENT_NAMES,
        JOB_NAMES,
        compute_maturity_score,
        query_agents_telemetry,
        query_cloud_run_services,
        query_gold_storage,
        query_jobs_executions,
        query_mlops_metrics,
        query_silver_storage,
        query_views_existence,
    )

    ts = datetime.now(timezone.utc).isoformat()
    use_bq = bool(settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true")

    _stub_jobs = [{"job": j, "status": "warn", "last_status": "unknown"} for j in JOB_NAMES]
    _stub_agents = [
        {"agent": a, "status": "ok", "calls_24h": 0, "p99_latency_s": 0.0, "cost_24h_usd": 0.0}
        for a in AGENT_NAMES
    ]
    _stub_mlops: dict = {
        "brier_score": None,
        "js_divergence": None,
        "eval_score": None,
        "bias_metrics": [],
    }

    if not use_bq:
        return _json_safe_response(
            {
                "source": "stub",
                "ts": ts,
                "dataops": {"gold": [], "silver": [], "views": []},
                "jobs": _stub_jobs,
                "services": [],
                "llmops": _stub_agents,
                "maturity": {"dataops": 0, "mlops": 0, "llmops": 0},
                "mlops": _stub_mlops,
            }
        )

    # Run all blocking queries concurrently in the thread pool
    results = await asyncio.gather(
        asyncio.to_thread(query_gold_storage),
        asyncio.to_thread(query_silver_storage),
        asyncio.to_thread(query_views_existence),
        asyncio.to_thread(query_jobs_executions),
        asyncio.to_thread(query_agents_telemetry),
        asyncio.to_thread(query_mlops_metrics),
        asyncio.to_thread(query_cloud_run_services),
        return_exceptions=True,
    )

    def _safe(r: Any, fallback: Any) -> Any:
        if isinstance(r, Exception):
            logger.warning("sentinel query failed: %s", r)
            return fallback
        return r

    gold_tables = _safe(results[0], [])
    silver_tables = _safe(results[1], [])
    views = _safe(results[2], [])
    jobs = _safe(results[3], _stub_jobs)
    agents = _safe(results[4], _stub_agents)
    mlops_metrics = _safe(results[5], _stub_mlops)
    services = _safe(results[6], [])

    logger.info(
        "Sentinel: %d gold, %d silver, %d views, %d jobs, %d services",
        len(gold_tables),
        len(silver_tables),
        len(views),
        len(jobs),
        len(services),
    )

    maturity = compute_maturity_score(jobs, gold_tables, silver_tables, mlops_metrics, agents)

    return _json_safe_response(
        {
            "source": "live",
            "ts": ts,
            "dataops": {"gold": gold_tables, "silver": silver_tables, "views": views},
            "jobs": jobs,
            "services": services,
            "llmops": agents,
            "maturity": maturity,
            "mlops": mlops_metrics,
        }
    )


@app.get("/admin/api/catalog", dependencies=[Depends(require_auth)])
async def admin_catalog() -> JSONResponse:
    """Return BigQuery table metadata for all SPEPE datasets."""
    datasets = ["spepe_silver", "spepe_gold", "spepe_mlops"]
    catalog = []
    source = "stub"
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            for ds in datasets:
                try:
                    for tbl in client.list_tables(ds):
                        t = client.get_table(tbl)
                        catalog.append(
                            {
                                "dataset_id": ds,
                                "table_id": t.table_id,
                                "num_rows": t.num_rows,
                                "size_mb": round(t.num_bytes / 1e6, 1) if t.num_bytes else 0,
                                "last_modified": str(t.modified) if t.modified else None,
                                "description": t.description or "",
                                "partitioning": str(t.time_partitioning.type_)
                                if t.time_partitioning
                                else None,
                                "clustering": list(t.clustering_fields)
                                if t.clustering_fields
                                else [],
                            }
                        )
                except Exception as exc:
                    logger.debug("Failed to read dataset %s: %s", ds, exc)
            if catalog:
                source = "bigquery"
                return _json_safe_response({"tables": catalog, "source": source})
        except Exception as exc:
            logger.warning("Catalog BQ query failed: %s", exc)

    return JSONResponse(
        {
            "tables": [],
            "source": "unavailable",
            "note": "BigQuery indisponível ou desabilitado (USE_BIGQUERY=false)",
        }
    )


# ── Admin Fase 2 — Arquitetura, KPIs, Explorer, Modelo ───────────────────────

_PIPELINE_STAGES = [
    {
        "stage": "Bronze",
        "layer": "gcs",
        "color": "#b45309",
        "description": "Raw parquet imutável no GCS",
    },
    {
        "stage": "Silver",
        "layer": "bigquery",
        "color": "#1d4ed8",
        "description": "Limpo + joined TSE+IBGE no BQ",
    },
    {
        "stage": "Gold",
        "layer": "bigquery",
        "color": "#15803d",
        "description": "Fatos agregados, particionado por ano/UF",
    },
    {
        "stage": "MLOps",
        "layer": "vertex",
        "color": "#7c3aed",
        "description": "Treinamento PyMC + predições IC 95%",
    },
]

_ARCH_JOBS = {
    "ingestion": [
        "spepe-tse-ingest",
        "spepe-tse-candidaturas-ingest",
        "spepe-tse-perfil-ingest",
        "spepe-ibge-sync",
        "spepe-security-ingest",
        "spepe-datasus-ingest",
        "spepe-dieese-ingest",
        "spepe-cetic-ingest",
        "spepe-social-ingest",
        "spepe-pesquisas-ingest",
        "spepe-digital-ingest",
        "spepe-camara-senado-ingest",
        "spepe-cadunico-ingest",
        "spepe-emendas-ingest",
    ],
    "transform": ["spepe-silver-transform"],
    "aggregation": ["spepe-gold-build"],
    "mlops": ["spepe-pymc-train"],
}


@app.get("/admin/api/architecture", dependencies=[Depends(require_auth)])
async def admin_architecture() -> Response:
    """Return pipeline DAG + job statuses + service health for the Architecture tab."""
    use_bq = settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"
    jobs: list[dict] = []
    services: list[dict] = []
    gold_summary: list[dict] = []
    if use_bq:
        from ui.sentinel_queries import (
            query_cloud_run_services,
            query_gold_storage,
            query_jobs_executions,
        )

        results = await asyncio.gather(
            asyncio.to_thread(query_jobs_executions),
            asyncio.to_thread(query_cloud_run_services),
            asyncio.to_thread(query_gold_storage),
            return_exceptions=True,
        )
        jobs = results[0] if not isinstance(results[0], BaseException) else []  # type: ignore[assignment]
        services = results[1] if not isinstance(results[1], BaseException) else []  # type: ignore[assignment]
        gold_summary = results[2] if not isinstance(results[2], BaseException) else []  # type: ignore[assignment]
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning("architecture query[%d] failed: %s", i, r)

    job_map = {j["job"]: j for j in jobs}

    def _enrich(name: str) -> dict:
        info = job_map.get(name, {})
        return {
            "name": name,
            "deployed": info.get("deployed", False),
            "status": info.get("status", "unknown"),
            "last_status": info.get("last_status", "NEVER_RUN"),
            "last_run_at": info.get("last_run_at"),
            "alert_message": info.get("alert_message"),
        }

    # Gold table summary per stage for the visual diagram
    gold_ok = sum(1 for t in gold_summary if t.get("status") == "ok")
    gold_total = len(gold_summary)

    return _json_safe_response(
        {
            "stages": _PIPELINE_STAGES,
            "jobs": {
                group: [_enrich(j) for j in job_list] for group, job_list in _ARCH_JOBS.items()
            },
            "services": services,
            "gold_summary": {"ok": gold_ok, "total": gold_total},
            "source": "cloud_run" if jobs else "stub",
        }
    )


@app.get("/admin/api/kpis", dependencies=[Depends(require_auth)])
async def admin_kpis() -> JSONResponse:
    """Return DataOps/MLOps/LLMOps/FinOps KPI scores."""
    use_bq = settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"
    maturity = {"dataops": 0, "mlops": 0, "llmops": 0}
    costs: dict = {"bq_total_30d_usd": 0.0, "llm_total_30d_usd": 0.0, "bq_daily": []}
    mlops: dict = {
        "brier_score": None,
        "js_divergence": None,
        "eval_score": None,
        "bias_metrics": [],
    }
    agents: list = []
    gold: list = []
    jobs: list = []
    source = "stub"

    if use_bq:
        try:
            from ui.sentinel_queries import (
                compute_maturity_score,
                query_agents_telemetry,
                query_costs,
                query_gold_storage,
                query_jobs_executions,
                query_mlops_metrics,
                query_silver_storage,
            )

            # Run all queries in parallel — sequential approach was causing timeouts
            kpi_results = await asyncio.gather(
                asyncio.to_thread(query_gold_storage),
                asyncio.to_thread(query_silver_storage),
                asyncio.to_thread(query_jobs_executions),
                asyncio.to_thread(query_mlops_metrics),
                asyncio.to_thread(query_costs),
                asyncio.to_thread(query_agents_telemetry),
                return_exceptions=True,
            )

            def _kpi_safe(r: Any, default: Any) -> Any:
                if isinstance(r, Exception):
                    logger.warning("kpis query failed: %s", r)
                    return default
                return r or default

            gold = _kpi_safe(kpi_results[0], [])
            silver = _kpi_safe(kpi_results[1], [])
            jobs = _kpi_safe(kpi_results[2], [])
            mlops = _kpi_safe(kpi_results[3], {})
            costs = _kpi_safe(kpi_results[4], {})
            agents = _kpi_safe(kpi_results[5], [])
            maturity = compute_maturity_score(jobs, gold, silver, mlops, agents)
            source = "bigquery" if (gold or jobs) else "partial"
        except Exception as exc:
            logger.warning("admin_kpis failed: %s", exc)

    n_jobs_ok = sum(1 for j in jobs if j.get("status") == "ok")
    n_tables_ok = sum(1 for t in gold if t.get("status") == "ok")
    n_agents_calls = sum(int(a.get("calls_24h", 0) or 0) for a in agents)

    maturity_report: dict = {"dataops": {}, "mlops": {}, "llmops": {}, "opportunities": []}
    try:
        from ui.sentinel_queries import compute_maturity_report

        maturity_report = compute_maturity_report(jobs, gold, silver, mlops or {}, agents)
    except Exception as exc:
        logger.warning("compute_maturity_report failed: %s", exc)

    return _json_safe_response(
        {
            "source": source,
            "maturity": maturity,
            "maturity_report": maturity_report,
            "dataops": {
                "score": maturity.get("dataops", 0),
                "score_5": maturity_report.get("dataops", {}).get("score", 0),
                "label": maturity_report.get("dataops", {}).get("label", "—"),
                "jobs_ok": n_jobs_ok,
                "jobs_total": len(jobs),
                "tables_ok": n_tables_ok,
                "tables_total": len(gold),
                "items": maturity_report.get("dataops", {}).get("items", []),
            },
            "mlops": {
                "score": maturity.get("mlops", 0),
                "score_5": maturity_report.get("mlops", {}).get("score", 0),
                "label": maturity_report.get("mlops", {}).get("label", "—"),
                "brier_score": mlops.get("brier_score") if mlops else None,
                "js_divergence": mlops.get("js_divergence") if mlops else None,
                "eval_score": mlops.get("eval_score") if mlops else None,
                "model_version": mlops.get("model_version") if mlops else None,
                "items": maturity_report.get("mlops", {}).get("items", []),
            },
            "llmops": {
                "score": maturity.get("llmops", 0),
                "score_5": maturity_report.get("llmops", {}).get("score", 0),
                "label": maturity_report.get("llmops", {}).get("label", "—"),
                "calls_24h": n_agents_calls,
                "agents": len(agents),
                "agents_ok": sum(1 for a in agents if a.get("status") == "ok"),
                "items": maturity_report.get("llmops", {}).get("items", []),
            },
            "finops": {
                "bq_total_30d_usd": costs.get("bq_total_30d_usd", 0.0),
                "llm_total_30d_usd": costs.get("llm_total_30d_usd", 0.0),
                "total_30d_usd": round(
                    float(costs.get("bq_total_30d_usd") or 0.0)
                    + float(costs.get("llm_total_30d_usd") or 0.0),
                    2,
                ),
                "bq_daily": costs.get("bq_daily", []),
            },
            "opportunities": maturity_report.get("opportunities", []),
        }
    )


@app.get("/admin/api/explorer/tables", dependencies=[Depends(require_auth)])
async def admin_explorer_tables() -> JSONResponse:
    """List Gold/Silver/MLOps tables for the explorer."""
    use_bq = settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"
    if use_bq:
        try:
            from ui.sentinel_queries import query_gold_storage, query_silver_storage

            gold = await asyncio.to_thread(query_gold_storage)
            silver = await asyncio.to_thread(query_silver_storage)
            tables = [{"layer": "gold", **t} for t in gold] + [
                {"layer": "silver", **t} for t in silver
            ]
            return JSONResponse({"tables": tables, "source": "bigquery"})
        except Exception as exc:
            logger.warning("explorer/tables failed: %s", exc)
    return JSONResponse({"tables": [], "source": "stub"})


@app.get("/admin/api/explorer/data", dependencies=[Depends(require_auth)])
async def admin_explorer_data(
    table: str = Query(..., min_length=1, max_length=80, pattern=r"^[\w]+$"),
    layer: str = Query(default="gold", pattern=r"^(gold|silver|mlops)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    """Query table rows for the data explorer (read-only, parameterized)."""
    use_bq = settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"
    if not use_bq:
        return JSONResponse({"rows": [], "columns": [], "total": 0, "source": "stub"})
    try:
        from google.cloud import bigquery as _bq_mod

        layer_map = {
            "gold": settings.bigquery_dataset_gold,
            "silver": settings.bigquery_dataset_silver,
            "mlops": settings.bigquery_dataset_mlops,
        }
        dataset = layer_map.get(layer, settings.bigquery_dataset_gold)
        client = _bq_mod.Client(project=settings.gcp_project_id)

        def _run() -> dict:
            count_sql = f"SELECT COUNT(*) as n FROM `{settings.gcp_project_id}.{dataset}.{table}`"
            total = next(iter(client.query(count_sql).result()))["n"]
            sql = (
                f"SELECT * FROM `{settings.gcp_project_id}.{dataset}.{table}`"
                f" LIMIT {limit} OFFSET {offset}"
            )
            rows_result = list(client.query(sql).result())
            if not rows_result:
                return {"rows": [], "columns": [], "total": int(total)}
            columns = list(rows_result[0].keys())
            rows = [dict(zip(columns, row.values())) for row in rows_result]
            return {"rows": rows, "columns": columns, "total": int(total)}

        data = await asyncio.to_thread(_run)
        return JSONResponse({**data, "source": "bigquery"})
    except Exception as exc:
        logger.warning("explorer/data %s.%s failed: %s", layer, table, exc)
        return JSONResponse(
            {"rows": [], "columns": [], "total": 0, "source": "error", "error": str(exc)}
        )


@app.get("/admin/api/model/overview", dependencies=[Depends(require_auth)])
async def admin_model_overview() -> JSONResponse:
    """Return model validation overview (MLOps metrics + feature importance)."""
    use_bq = settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"
    mlops: dict = {
        "brier_score": None,
        "js_divergence": None,
        "eval_score": None,
        "bias_metrics": [],
    }
    features: list = []
    source = "stub"

    if use_bq:
        try:
            from ui.sentinel_queries import query_mlops_metrics

            mlops = await asyncio.to_thread(query_mlops_metrics)
            source = "bigquery"
        except Exception as exc:
            logger.warning("model/overview mlops failed: %s", exc)
        try:
            from google.cloud import bigquery as _bq_mod

            client = _bq_mod.Client(project=settings.gcp_project_id)

            def _features() -> list:
                sql = f"""
                SELECT feature_name, importance_mean, importance_std, rank
                FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_mlops}.feature_importance`
                ORDER BY rank ASC
                LIMIT 20
                """
                return [dict(r) for r in client.query(sql).result()]

            features = await asyncio.to_thread(_features)
        except Exception as exc:
            logger.debug("feature_importance table not found or empty: %s", exc)

    trained = mlops.get("model_version") is not None or mlops.get("brier_score") is not None
    return _json_safe_response(
        {
            "trained": trained,
            "source": source,
            "metrics": {
                "model_version": mlops.get("model_version"),
                "brier_score": mlops.get("brier_score"),
                "js_divergence": mlops.get("js_divergence"),
                "eval_score": mlops.get("eval_score"),
                "computed_at": mlops.get("computed_at"),
            },
            "feature_importance": features,
            "bias_metrics": mlops.get("bias_metrics", []),
        }
    )


# ── Sentinel WebSocket ────────────────────────────────────────────────────────


_sentinel_ws_clients: list[WebSocket] = []


@app.websocket("/ws/sentinel")
async def ws_sentinel(websocket: WebSocket) -> None:
    """WebSocket for real-time Sentinel status updates to the admin panel."""
    import asyncio

    await websocket.accept()
    _sentinel_ws_clients.append(websocket)
    try:
        # Send initial snapshot wrapped as typed message
        status_resp = await admin_sentinel_status()
        status_data = bytes(status_resp.body).decode()
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


# ─────────────────────────────────────────────────────────────────────────────
# Sentinel SSE — Server-Sent Events real-time stream for /admin
# ─────────────────────────────────────────────────────────────────────────────


def _format_sse(event_type: str, data: dict) -> str:
    """Format a dict as an SSE message frame."""
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


async def _sentinel_broadcast(event_type: str, payload: dict) -> None:
    """Fan-out a single event to every connected SSE subscriber."""
    msg = (event_type, payload)
    dead: list[asyncio.Queue] = []
    for q in _sentinel_subscribers:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            _sentinel_subscribers.remove(q)
        except ValueError:
            pass


async def _build_full_snapshot() -> dict:
    """Collect a full snapshot for the initial SSE event.

    Each sub-query is wrapped individually — one API failure (e.g. Cloud Run
    Jobs permission) does not erase BQ data that is already available.
    """
    use_bq = settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"
    if not use_bq:
        return {
            "dataops": {"gold": [], "silver": [], "views": []},
            "jobs": [],
            "mlops": {},
            "llmops": [],
            "costs": {},
            "maturity": {"dataops": 0, "mlops": 0, "llmops": 0},
            "source": "stub",
            "ts": time.time(),
        }
    from ui.sentinel_queries import (
        compute_maturity_score,
        query_agents_telemetry,
        query_costs,
        query_gold_storage,
        query_jobs_executions,
        query_mlops_metrics,
        query_silver_storage,
        query_views_existence,
    )

    async def _safe(fn, default=None):
        if default is None:
            default = []
        try:
            return await asyncio.to_thread(fn)
        except Exception as exc:
            logger.warning("%s failed: %s", fn.__name__, exc)
            return default

    gold = await _safe(query_gold_storage)
    silver = await _safe(query_silver_storage)
    views = await _safe(query_views_existence)
    jobs = await _safe(query_jobs_executions)
    mlops = await _safe(query_mlops_metrics, default={})
    costs = await _safe(query_costs, default={})
    agents = await _safe(query_agents_telemetry)
    maturity = compute_maturity_score(jobs, gold, silver, mlops, agents)
    source = "bigquery" if (gold or jobs) else "partial"
    return {
        "dataops": {"gold": gold, "silver": silver, "views": views},
        "jobs": jobs,
        "mlops": mlops,
        "llmops": agents,
        "costs": costs,
        "maturity": maturity,
        "source": source,
        "ts": time.time(),
    }


async def _poll_table_freshness(interval: int) -> None:
    """Background task: poll BQ INFORMATION_SCHEMA every `interval` seconds."""
    use_bq = settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"
    if not use_bq:
        return
    from ui.sentinel_queries import (
        query_gold_storage,
        query_silver_storage,
        query_views_existence,
    )

    while True:
        try:
            gold = await asyncio.to_thread(query_gold_storage)
            silver = await asyncio.to_thread(query_silver_storage)
            views = await asyncio.to_thread(query_views_existence)
            await _sentinel_broadcast(
                "table_freshness_updated",
                {"gold": gold, "silver": silver, "views": views},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("table freshness poll failed: %s", exc)
        await asyncio.sleep(interval)


async def _poll_costs(interval: int) -> None:
    """Background task: poll BQ JOBS_BY_PROJECT every `interval` seconds."""
    use_bq = settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"
    if not use_bq:
        return
    from ui.sentinel_queries import query_costs

    while True:
        try:
            costs = await asyncio.to_thread(query_costs)
            await _sentinel_broadcast("cost_updated", costs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("cost poll failed: %s", exc)
        await asyncio.sleep(interval)


async def _consume_sentinel_pubsub() -> None:
    """Background task: pull spepe-sentinel-events and re-broadcast as SSE."""
    try:
        from ui.sentinel_pubsub import consume_sentinel_pubsub

        await consume_sentinel_pubsub(_sentinel_broadcast)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("sentinel pubsub consumer crashed: %s", exc)


@app.get("/admin/api/sentinel/stream")
async def admin_sentinel_stream(
    token: str = Query(default=""),
) -> StreamingResponse:
    """SSE endpoint streaming sentinel events to the admin panel.

    Auth via ?token=<google_id_token> query param (EventSource can't send headers).
    In local dev (no GCP_PROJECT_ID) auth is skipped.
    """
    if settings.gcp_project_id and settings.gcp_project_id not in ("", "local"):
        if not token:
            raise HTTPException(status_code=401, detail="token required")
        try:
            from google.auth.transport import requests as grequests
            from google.oauth2 import id_token as gid

            gid.verify_oauth2_token(
                token,
                grequests.Request(),
                audience=settings.google_client_id or None,
                clock_skew_in_seconds=10,
            )
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token")
    """SSE endpoint streaming sentinel events to the admin panel."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=128)
    _sentinel_subscribers.append(queue)

    async def event_generator():
        try:
            snapshot = await _build_full_snapshot()
            yield _format_sse("snapshot", snapshot)
            while True:
                try:
                    event_type, payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield _format_sse(event_type, payload)
                except asyncio.TimeoutError:
                    yield _format_sse("heartbeat", {"ts": time.time()})
        except asyncio.CancelledError:
            pass
        finally:
            try:
                _sentinel_subscribers.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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


# ── Model status ───────────────────────────────────────────────────────────


@app.get("/api/model/status")
async def get_model_status() -> Response:
    """Status dos modelos M1 (demográfico) e M2 (eleitoral) do SPEPE."""
    if not (settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"):
        return _json_safe_response(
            {
                "m1": {"active": False, "status": "pending", "version": None, "brier_score": None},
                "m2": {"active": False, "status": "pending", "version": None, "brier_score": None},
            }
        )
    try:
        result = await _bq_model_status()
        return _json_safe_response(result)
    except Exception as exc:
        logger.warning("model_status BQ falhou: %s", exc)
        return _json_safe_response(
            {
                "m1": {"active": False, "status": "pending", "version": None, "brier_score": None},
                "m2": {"active": False, "status": "pending", "version": None, "brier_score": None},
            }
        )


async def _bq_model_status() -> dict:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    mlops = f"{settings.gcp_project_id}.spepe_mlops"

    query = f"""
        SELECT
          model_type,
          MAX(model_version)  AS version,
          MAX(brier_score)    AS brier_score,
          MAX(created_at)     AS last_run,
          COUNT(*)            AS n_evals
        FROM `{mlops}.model_evaluations`
        GROUP BY model_type
    """
    rows = await asyncio.to_thread(lambda: list(client.query(query).result()))

    m1 = {"active": False, "status": "pending", "version": None, "brier_score": None}
    m2 = {"active": False, "status": "pending", "version": None, "brier_score": None}

    for r in rows:
        mtype = str(r.get("model_type") or "").lower()
        info = {
            "active": True,
            "status": "ready",
            "version": r.get("version"),
            "brier_score": float(r["brier_score"]) if r.get("brier_score") is not None else None,
            "last_run": r["last_run"].isoformat() if r.get("last_run") else None,
            "n_evals": int(r.get("n_evals") or 0),
        }
        if "demographic" in mtype or mtype == "m1":
            m1 = info
        elif "electoral" in mtype or mtype == "m2":
            m2 = info

    # Also check fact_predictions for any M1 data
    if not m1["active"]:
        pred_q = f"SELECT COUNT(*) AS n FROM `{mlops}.fact_predictions` LIMIT 1"
        try:
            pred_rows = await asyncio.to_thread(lambda: list(client.query(pred_q).result()))
            if pred_rows and int(pred_rows[0]["n"] or 0) > 0:
                m1["active"] = True
                m1["status"] = "ready"
        except Exception:
            pass

    return {"m1": m1, "m2": m2}


# ── SHAP public endpoint ──────────────────────────────────────────────────────

_SHAP_FALLBACK = [
    {"feature": "renda_per_capita", "shap": 0.18, "tipo": "ibge"},
    {"feature": "idhm", "shap": 0.15, "tipo": "ibge"},
    {"feature": "pct_bolsa_familia", "shap": -0.12, "tipo": "social"},
    {"feature": "sentimento_score", "shap": 0.10, "tipo": "social"},
    {"feature": "historico_partido", "shap": 0.09, "tipo": "tse"},
    {"feature": "taxa_desemprego", "shap": -0.08, "tipo": "ibge"},
    {"feature": "urbanizacao", "shap": 0.07, "tipo": "ibge"},
    {"feature": "escolaridade_media", "shap": 0.06, "tipo": "ibge"},
    {"feature": "google_trends", "shap": 0.05, "tipo": "digital"},
    {"feature": "populacao", "shap": -0.04, "tipo": "ibge"},
]


@app.get("/api/model/shap")
async def get_model_shap(
    cargo: str = Query("Presidente"),
    uf: str = Query("BR"),
) -> JSONResponse:
    """Top-10 SHAP feature importances for the selected cargo/UF."""
    if not (settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"):
        return JSONResponse(
            {"features": _SHAP_FALLBACK, "cargo": cargo, "uf": uf, "status": "fallback"}
        )
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=settings.gcp_project_id)
        mlops = f"{settings.gcp_project_id}.spepe_mlops"
        query = f"""
            SELECT
              feature_name  AS feature,
              shap_value    AS shap,
              feature_type  AS tipo
            FROM `{mlops}.model_evaluations`
            WHERE LOWER(cargo) = LOWER(@cargo)
              AND (UPPER(sg_uf) = UPPER(@uf) OR @uf = 'BR')
              AND feature_name IS NOT NULL
            ORDER BY ABS(shap_value) DESC
            LIMIT 10
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("cargo", "STRING", cargo),
                bigquery.ScalarQueryParameter("uf", "STRING", uf),
            ]
        )
        rows = await asyncio.to_thread(
            lambda: list(client.query(query, job_config=job_config).result())
        )
        if not rows:
            return JSONResponse(
                {"features": _SHAP_FALLBACK, "cargo": cargo, "uf": uf, "status": "fallback"}
            )
        features = [
            {
                "feature": str(r["feature"] or ""),
                "shap": float(r["shap"] or 0.0),
                "tipo": str(r["tipo"] or ""),
            }
            for r in rows
        ]
        return JSONResponse({"features": features, "cargo": cargo, "uf": uf, "status": "ok"})
    except Exception as exc:
        logger.warning("SHAP BQ query failed: %s — returning fallback", exc)
        return JSONResponse(
            {"features": _SHAP_FALLBACK, "cargo": cargo, "uf": uf, "status": "fallback"}
        )


# ── Alianças Históricas ───────────────────────────────────────────────────────

_ALIANCAS_FALLBACK = [
    {"partido": "MDB", "coligacoes": 18, "ufs": 14, "tendencia": "aliado"},
    {"partido": "PSD", "coligacoes": 15, "ufs": 12, "tendencia": "aliado"},
    {"partido": "PP", "coligacoes": 12, "ufs": 10, "tendencia": "aliado"},
    {"partido": "UNIÃO", "coligacoes": 10, "ufs": 9, "tendencia": "neutro"},
    {"partido": "PL", "coligacoes": 3, "ufs": 3, "tendencia": "adversario"},
]


@app.get("/api/aliancas")
async def get_aliancas(
    cargo: str = Query("Presidente"),
    uf: str = Query("BR"),
) -> JSONResponse:
    """Alianças históricas por partido/cargo — fallback estático."""
    return JSONResponse(
        {"aliancos": _ALIANCAS_FALLBACK, "cargo": cargo, "uf": uf, "status": "fallback"}
    )


# ── Mapeamento de Adversários ─────────────────────────────────────────────────

_ADVERSARIOS_FALLBACK = [
    {"candidato": "Jair Bolsonaro", "partido": "PL", "pct": 0.433, "status": "principal"},
    {"candidato": "Ciro Gomes", "partido": "PDT", "pct": 0.12, "status": "terciário"},
    {"candidato": "Simone Tebet", "partido": "MDB", "pct": 0.048, "status": "terceiro"},
]


@app.get("/api/adversarios")
async def get_adversarios(
    uf: str = Query("SP"),
    cargo: str = Query("Presidente"),
    ano: int = Query(2022),
) -> JSONResponse:
    """Top adversários por cargo/UF com análise de vulnerabilidade."""
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.spepe_gold"
            query = f"""
                SELECT
                  nm_candidato,
                  sg_partido,
                  SUM(qt_votos_nominais) AS votos,
                  ROUND(
                    SUM(qt_votos_nominais)
                    / SUM(SUM(qt_votos_nominais)) OVER (),
                    3
                  ) AS pct
                FROM `{gold}.fact_municipio_candidato_eleicao`
                WHERE sg_uf = @uf
                  AND ds_cargo_normalizado = @cargo
                  AND ano_eleicao = @ano
                GROUP BY nm_candidato, sg_partido
                ORDER BY votos DESC
                LIMIT 8
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("uf", "STRING", uf),
                    bigquery.ScalarQueryParameter("cargo", "STRING", cargo),
                    bigquery.ScalarQueryParameter("ano", "INT64", ano),
                ]
            )
            rows = await asyncio.to_thread(
                lambda: list(client.query(query, job_config=job_config).result())
            )
            if rows:
                adversarios = [
                    {
                        "candidato": str(r["nm_candidato"] or ""),
                        "partido": str(r["sg_partido"] or ""),
                        "pct": float(r["pct"] or 0.0),
                        "status": "principal" if i == 0 else "secundário",
                    }
                    for i, r in enumerate(rows)
                ]
                return JSONResponse(
                    {
                        "adversarios": adversarios,
                        "municipios_risco": None,
                        "municipios_seguros": None,
                        "margem_media": None,
                        "uf": uf,
                        "cargo": cargo,
                        "status": "ok",
                    }
                )
        except Exception as exc:
            logger.warning("adversarios BQ query failed: %s — returning fallback", exc)
    return JSONResponse(
        {
            "adversarios": _ADVERSARIOS_FALLBACK,
            "municipios_risco": 1247,
            "municipios_seguros": 3305,
            "margem_media": "8.4%",
            "uf": uf,
            "cargo": cargo,
            "status": "fallback",
        }
    )


# ── Oportunidades de Parcerias ────────────────────────────────────────────────

_PARCERIAS_FALLBACK_OPORTUNIDADES = [
    {"municipio": "Campinas", "cd_municipio": "3509502", "soma_pct": 0.58, "score": "alto"},
    {"municipio": "Santo André", "cd_municipio": "3547809", "soma_pct": 0.54, "score": "alto"},
    {"municipio": "São Bernardo", "cd_municipio": "3548708", "soma_pct": 0.51, "score": "médio"},
]

_PARCERIAS_FALLBACK_TOP_UFS = [
    {"uf": "SP", "score": 0.72},
    {"uf": "MG", "score": 0.65},
    {"uf": "RS", "score": 0.61},
    {"uf": "PR", "score": 0.58},
    {"uf": "SC", "score": 0.55},
]


@app.get("/api/parcerias")
async def get_parcerias(
    uf: str = Query("SP"),
    cargo: str = Query("Presidente"),
) -> JSONResponse:
    """Oportunidades de coalizão por município e score por UF — fallback estático."""
    return JSONResponse(
        {
            "oportunidades": _PARCERIAS_FALLBACK_OPORTUNIDADES,
            "top_ufs": _PARCERIAS_FALLBACK_TOP_UFS,
            "uf": uf,
            "cargo": cargo,
            "status": "fallback",
        }
    )


# ── Meta de Votos por Município ───────────────────────────────────────────────

_CRESCIMENTO_ELEITORADO = 0.042  # estimativa IBGE 2022→2026


@app.get("/api/meta_votos")
async def get_meta_votos(
    uf: str = Query("SP"),
    cargo: str = Query("Governador"),
    candidato: str = Query(""),
) -> JSONResponse:
    """Projeção de votos necessários para vitória com base em 2022 + crescimento estimado."""
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            params = [
                bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                bigquery.ScalarQueryParameter("cargo", "STRING", cargo),
                bigquery.ScalarQueryParameter("ano", "INT64", 2022),
            ]
            where = "sg_uf = @uf AND ds_cargo_normalizado = @cargo AND ano_eleicao = @ano"
            if candidato:
                where += " AND nm_candidato = @candidato"
                params.append(bigquery.ScalarQueryParameter("candidato", "STRING", candidato))
            query = f"""
                SELECT
                    sg_uf,
                    nm_municipio,
                    SUM(qt_votos_nominais) AS votos_2022,
                    ROUND(SUM(qt_votos_nominais) * {1 + _CRESCIMENTO_ELEITORADO}, 0) AS meta_2026
                FROM `{gold}.fact_municipio_candidato_eleicao`
                WHERE {where}
                GROUP BY sg_uf, nm_municipio
                ORDER BY votos_2022 DESC
                LIMIT 20
            """
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            rows = list(client.query(query, job_config=job_config).result())
            data = [dict(r) for r in rows]
            total_2022 = sum(r["votos_2022"] for r in data)
            return JSONResponse(
                {
                    "data": data,
                    "total_votos_2022": total_2022,
                    "meta_2026": round(total_2022 * (1 + _CRESCIMENTO_ELEITORADO)),
                    "crescimento_pct": _CRESCIMENTO_ELEITORADO,
                }
            )
        except Exception as exc:
            logger.warning("BQ meta_votos falhou: %s", exc)
    return JSONResponse(
        {
            "data": [],
            "total_votos_2022": 0,
            "meta_2026": 0,
            "crescimento_pct": _CRESCIMENTO_ELEITORADO,
            "status": "no_bq",
        }
    )


# ── GDELT Clipping ────────────────────────────────────────────────────────────


@app.get("/api/gdelt_eventos")
async def get_gdelt_eventos(
    uf: str = Query("BR"),
    candidato: str = Query(""),
) -> JSONResponse:
    """Eventos GDELT — fallback com eventos representativos estáticos."""
    return JSONResponse(
        {
            "eventos": [
                {
                    "data": "2024-03-15",
                    "titulo": "Discurso no Congresso Nacional",
                    "tom": 0.3,
                    "intensidade": 42,
                    "fonte": "Reuters",
                },
                {
                    "data": "2024-02-28",
                    "titulo": "Declaração sobre política econômica",
                    "tom": -0.1,
                    "intensidade": 28,
                    "fonte": "AP",
                },
                {
                    "data": "2024-02-10",
                    "titulo": "Encontro bilateral com líderes sul-americanos",
                    "tom": 0.5,
                    "intensidade": 18,
                    "fonte": "AFP",
                },
            ],
            "semanas": [
                {"semana": "S1 fev", "eventos": 12},
                {"semana": "S2 fev", "eventos": 18},
                {"semana": "S3 fev", "eventos": 28},
                {"semana": "S4 fev", "eventos": 15},
                {"semana": "S1 mar", "eventos": 42},
                {"semana": "S2 mar", "eventos": 35},
            ],
            "status": "fallback",
        }
    )


# ── Comparativo 2018 × 2022 ─────────────────────────────────────────────────


_VOTOS_INVALIDOS = frozenset(
    [
        "VOTO BRANCO",
        "VOTO NULO",
        "VOTO EM BRANCO",
        "#NULO#",
        "#NULO",
        "NULO",
        "BRANCO",
    ]
)
_FILTER_INVALIDOS = (
    "nm_candidato IS NOT NULL"
    " AND UPPER(TRIM(nm_candidato)) NOT IN"
    " ('VOTO BRANCO','VOTO NULO','VOTO EM BRANCO','#NULO#','#NULO','NULO','BRANCO')"
    " AND nm_candidato NOT LIKE '#%'"
)


def _is_eleito(ds: str | None) -> bool:
    if not ds:
        return False
    ds_up = ds.upper()
    return "ELEITO" in ds_up and "NÃO" not in ds_up and "NAO" not in ds_up


_INVALIDOS_NOMES = {
    "VOTO BRANCO", "VOTO NULO", "VOTO EM BRANCO", "#NULO#", "#NULO", "NULO", "BRANCO"
}


def _comparativo_from_silver_local(
    uf: str,
    cd_cargo: int,
    turno: int,
    situacao: str,
    limit: int,
) -> tuple[list[dict], bool]:
    """
    Monta comparativo 2018×2022 a partir de arquivos Silver locais (parquet).
    Retorna (candidatos, situacao_disponivel).
    """
    import pandas as pd

    frames: list[pd.DataFrame] = []
    for f in sorted(_LOCAL_SILVER_DIR.glob("tse_*.parquet")):
        try:
            df = pd.read_parquet(f)
            frames.append(df)
        except Exception:
            continue
    if not frames:
        return [], False

    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.lower() for c in df.columns]

    # normalise column names
    col_map = {"sg_uf": "sg_uf", "cd_cargo": "cd_cargo", "nr_turno": "nr_turno",
               "ano_eleicao": "ano_eleicao", "nm_candidato": "nm_candidato",
               "sg_partido": "sg_partido", "total_votos": "total_votos",
               "qt_votos_nominais": "total_votos", "ds_situacao": "ds_situacao",
               "ds_sit_cand_tot": "ds_situacao"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    needed = {"sg_uf", "cd_cargo", "nr_turno", "ano_eleicao", "nm_candidato", "total_votos"}
    if not needed.issubset(df.columns):
        return [], False

    # filter
    mask = (
        (df["sg_uf"].str.upper() == uf.upper())
        & (df["cd_cargo"].astype(str) == str(cd_cargo))
        & (df["nr_turno"].astype(str) == str(turno))
        & (df["ano_eleicao"].isin([2018, 2022]))
        & (df["nm_candidato"].notna())
        & (~df["nm_candidato"].str.upper().str.strip().isin(_INVALIDOS_NOMES))
        & (~df["nm_candidato"].str.startswith("#", na=False))
    )
    df = df[mask].copy()
    if df.empty:
        return [], False

    sit_disponivel = "ds_situacao" in df.columns
    if "ds_situacao" not in df.columns:
        df["ds_situacao"] = ""
    if "sg_partido" not in df.columns:
        df["sg_partido"] = ""

    df["total_votos"] = pd.to_numeric(df["total_votos"], errors="coerce").fillna(0)
    df["ano_eleicao"] = pd.to_numeric(df["ano_eleicao"], errors="coerce").astype(int)

    grp = (
        df.groupby(["nm_candidato", "sg_partido", "ano_eleicao", "ds_situacao"])
        .agg(votos=("total_votos", "sum"))
        .reset_index()
    )

    # pivot
    pivot = grp.groupby("nm_candidato").agg(
        sg_partido=("sg_partido", "last"),
        votos_2018=("votos", lambda s: s[grp.loc[s.index, "ano_eleicao"] == 2018].sum()),
        votos_2022=("votos", lambda s: s[grp.loc[s.index, "ano_eleicao"] == 2022].sum()),
        ds_sit_2018=("ds_situacao", lambda s: next(
            (v for v, a in zip(s, grp.loc[s.index, "ano_eleicao"]) if a == 2018 and v), "")),
        ds_sit_2022=("ds_situacao", lambda s: next(
            (v for v, a in zip(s, grp.loc[s.index, "ano_eleicao"]) if a == 2022 and v), "")),
    ).reset_index()

    tot18 = pivot["votos_2018"].sum() or 1
    tot22 = pivot["votos_2022"].sum() or 1
    pivot["pct_2018"] = (pivot["votos_2018"] / tot18 * 100).round(2)
    pivot["pct_2022"] = (pivot["votos_2022"] / tot22 * 100).round(2)
    pivot["delta_votos"] = pivot["votos_2022"] - pivot["votos_2018"]
    pivot = pivot.sort_values("votos_2022", ascending=False).reset_index(drop=True)
    pivot["rank_2022"] = (pivot["votos_2022"] > 0).cumsum().where(pivot["votos_2022"] > 0)
    pivot18 = pivot.sort_values("votos_2018", ascending=False).reset_index(drop=True)
    pivot["rank_2018"] = None
    for i, nm in enumerate(pivot18["nm_candidato"]):
        if pivot18.iloc[i]["votos_2018"] > 0:
            pivot.loc[pivot["nm_candidato"] == nm, "rank_2018"] = i + 1

    pivot["delta_rank"] = pivot.apply(
        lambda r: (int(r["rank_2018"]) - int(r["rank_2022"]))
        if pd.notna(r["rank_2018"]) and pd.notna(r["rank_2022"]) else None,
        axis=1,
    )

    candidatos = []
    for _, r in pivot.iterrows():
        ds18 = r.get("ds_sit_2018") or ""
        ds22 = r.get("ds_sit_2022") or ""
        if sit_disponivel:
            if situacao == "eleito" and not (_is_eleito(ds18) or _is_eleito(ds22)):
                continue
            if situacao == "nao_eleito" and (_is_eleito(ds18) or _is_eleito(ds22)):
                continue
        candidatos.append({
            "nm_candidato": r["nm_candidato"],
            "sg_partido": r.get("sg_partido") or "",
            "votos_2018": int(r["votos_2018"]),
            "votos_2022": int(r["votos_2022"]),
            "pct_2018": float(r["pct_2018"]),
            "pct_2022": float(r["pct_2022"]),
            "rank_2018": int(r["rank_2018"]) if pd.notna(r.get("rank_2018")) else None,
            "rank_2022": int(r["rank_2022"]) if pd.notna(r.get("rank_2022")) else None,
            "delta_votos": int(r["delta_votos"]),
            "delta_rank": int(r["delta_rank"]) if pd.notna(r.get("delta_rank")) else None,
            "ds_situacao_2018": ds18,
            "ds_situacao_2022": ds22,
            "eleito_2018": _is_eleito(ds18),
            "eleito_2022": _is_eleito(ds22),
        })
        if len(candidatos) >= limit:
            break

    return candidatos, sit_disponivel


@app.get("/api/comparativo/candidatos")
async def get_comparativo_candidatos(
    uf: str = Query("SP"),
    cargo: str = Query("Deputado Federal"),
    turno: int = Query(1),
    limit: int = Query(200),
    situacao: str = Query("todos"),  # todos | eleito | nao_eleito
) -> JSONResponse:
    """
    Ranking comparativo 2018 × 2022 por candidato/UF/cargo.
    situacao: todos | eleito | nao_eleito — depende de ds_situacao no Silver.
    Exclui votos em branco e nulos automaticamente.
    Fallback: Silver local (parquet) quando BigQuery indisponível.
    """
    cd_cargo = _cargo_to_cd(cargo, 6)

    # ── BigQuery path ─────────────────────────────────────────────────────────
    use_bq = bool(settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true")
    if use_bq:
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"
            params = [
                bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
                bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
                bigquery.ScalarQueryParameter("turno", "INT64", turno),
            ]

            gold_query = f"""
                WITH base AS (
                    SELECT nm_candidato, sg_partido, ano_eleicao,
                           SUM(total_votos) AS votos
                    FROM `{gold}.fact_municipio_candidato_eleicao`
                    WHERE sg_uf    = @uf
                      AND cd_cargo = @cd_cargo
                      AND nr_turno = @turno
                      AND ano_eleicao IN (2018, 2022)
                      AND {_FILTER_INVALIDOS}
                    GROUP BY nm_candidato, sg_partido, ano_eleicao
                ),
                ranked AS (
                    SELECT nm_candidato, sg_partido, ano_eleicao, votos,
                        ROUND(votos / NULLIF(SUM(votos) OVER (PARTITION BY ano_eleicao), 0) * 100, 2) AS pct,
                        RANK() OVER (PARTITION BY ano_eleicao ORDER BY votos DESC) AS ranking
                    FROM base
                ),
                pivoted AS (
                    SELECT nm_candidato,
                        MAX(sg_partido)                           AS sg_partido,
                        MAX(IF(ano_eleicao=2018, votos,   NULL)) AS votos_2018,
                        MAX(IF(ano_eleicao=2022, votos,   NULL)) AS votos_2022,
                        MAX(IF(ano_eleicao=2018, pct,     NULL)) AS pct_2018,
                        MAX(IF(ano_eleicao=2022, pct,     NULL)) AS pct_2022,
                        MAX(IF(ano_eleicao=2018, ranking, NULL)) AS rank_2018,
                        MAX(IF(ano_eleicao=2022, ranking, NULL)) AS rank_2022
                    FROM ranked GROUP BY nm_candidato
                )
                SELECT nm_candidato, sg_partido,
                    COALESCE(votos_2018, 0)                          AS votos_2018,
                    COALESCE(votos_2022, 0)                          AS votos_2022,
                    COALESCE(pct_2018,   0.0)                        AS pct_2018,
                    COALESCE(pct_2022,   0.0)                        AS pct_2022,
                    rank_2018, rank_2022,
                    COALESCE(votos_2022,0) - COALESCE(votos_2018,0) AS delta_votos,
                    IF(rank_2022 IS NOT NULL AND rank_2018 IS NOT NULL,
                       rank_2018 - rank_2022, NULL)                  AS delta_rank
                FROM pivoted
                ORDER BY COALESCE(votos_2022, votos_2018) DESC
                LIMIT {min(limit, 500)}
            """

            silver_query = f"""
                SELECT nm_candidato, ano_eleicao,
                       ANY_VALUE(ds_situacao) AS ds_situacao
                FROM `{silver}.tse_*`
                WHERE sg_uf    = @uf
                  AND cd_cargo = @cd_cargo
                  AND nr_turno = @turno
                  AND ano_eleicao IN (2018, 2022)
                  AND nm_candidato IS NOT NULL
                  AND UPPER(TRIM(nm_candidato)) NOT IN
                      ('VOTO BRANCO','VOTO NULO','VOTO EM BRANCO','#NULO#','#NULO','NULO','BRANCO')
                GROUP BY nm_candidato, ano_eleicao
            """

            cfg_gold = bigquery.QueryJobConfig(query_parameters=params)
            cfg_silver = bigquery.QueryJobConfig(query_parameters=params)

            gold_rows = await asyncio.to_thread(
                lambda: list(client.query(gold_query, job_config=cfg_gold).result())
            )

            sit_map: dict[tuple, str] = {}
            silver_ok = True
            try:
                silver_rows = await asyncio.to_thread(
                    lambda: list(client.query(silver_query, job_config=cfg_silver).result())
                )
                for r in silver_rows:
                    sit_map[(r["nm_candidato"], int(r["ano_eleicao"]))] = r["ds_situacao"] or ""
            except Exception as exc:
                logger.warning("comparativo silver sit_map indisponivel: %s", exc)
                silver_ok = False

            candidatos = []
            for r in gold_rows:
                nm = r["nm_candidato"] or ""
                ds18 = sit_map.get((nm, 2018), "")
                ds22 = sit_map.get((nm, 2022), "")
                if situacao == "eleito" and not (_is_eleito(ds18) or _is_eleito(ds22)):
                    continue
                if situacao == "nao_eleito" and (_is_eleito(ds18) or _is_eleito(ds22)):
                    continue
                candidatos.append({
                    "nm_candidato": nm,
                    "sg_partido": r["sg_partido"] or "",
                    "votos_2018": int(r["votos_2018"] or 0),
                    "votos_2022": int(r["votos_2022"] or 0),
                    "pct_2018": float(r["pct_2018"] or 0.0),
                    "pct_2022": float(r["pct_2022"] or 0.0),
                    "rank_2018": int(r["rank_2018"]) if r["rank_2018"] else None,
                    "rank_2022": int(r["rank_2022"]) if r["rank_2022"] else None,
                    "delta_votos": int(r["delta_votos"] or 0),
                    "delta_rank": int(r["delta_rank"]) if r["delta_rank"] else None,
                    "ds_situacao_2018": ds18,
                    "ds_situacao_2022": ds22,
                    "eleito_2018": _is_eleito(ds18),
                    "eleito_2022": _is_eleito(ds22),
                })

            return JSONResponse({
                "status": "ok",
                "uf": uf.upper(), "cargo": cargo, "turno": turno,
                "anos": [2018, 2022], "total": len(candidatos),
                "candidatos": candidatos,
                "situacao_disponivel": silver_ok,
                "fonte": "bigquery",
            })

        except Exception as exc:
            logger.warning("comparativo BQ falhou, tentando Silver local: %s", exc)
            # fall through to local path

    # ── Local Silver parquet fallback ────────────────────────────────────────
    candidatos, sit_disponivel = await asyncio.to_thread(
        _comparativo_from_silver_local, uf, cd_cargo, turno, situacao, min(limit, 500)
    )
    if not candidatos and situacao != "todos":
        # retry sem filtro para confirmar se há dados no Silver local
        all_cands, _ = await asyncio.to_thread(
            _comparativo_from_silver_local, uf, cd_cargo, turno, "todos", 1
        )
        if not all_cands:
            return JSONResponse({
                "status": "sem_dados",
                "candidatos": [], "anos": [2018, 2022],
                "situacao_disponivel": False,
                "fonte": "local",
                "msg": "Sem dados Silver locais para esta UF/cargo — execute o job tse_ingest primeiro",
            })

    return JSONResponse({
        "status": "ok",
        "uf": uf.upper(), "cargo": cargo, "turno": turno,
        "anos": [2018, 2022], "total": len(candidatos),
        "candidatos": candidatos,
        "situacao_disponivel": sit_disponivel,
        "fonte": "local",
    })


@app.get("/api/comparativo/mapa")
async def get_comparativo_mapa(
    uf: str = Query("SP"),
    cargo: str = Query("Deputado Federal"),
    turno: int = Query(1),
) -> JSONResponse:
    """
    Vencedor por município para comparativo 2018 × 2022.
    Retorna lista de municípios com candidato/partido líder em cada ano.
    Chave de join com geojson: nm_municipio (normalizado uppercase).
    """
    import pandas as pd
    import unicodedata

    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFD", s.upper())
        return "".join(c for c in s if unicodedata.category(c) != "Mn")

    cd_cargo = _cargo_to_cd(cargo, 6)

    frames: list[pd.DataFrame] = []
    for f in sorted(_LOCAL_SILVER_DIR.glob("tse_*.parquet")):
        try:
            df = pd.read_parquet(f)
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return JSONResponse({"status": "sem_dados", "municipios": [], "fonte": "local"})

    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.lower() for c in df.columns]

    col_map = {
        "qt_votos_nominais": "total_votos",
        "sg_uf": "sg_uf", "cd_cargo": "cd_cargo", "nr_turno": "nr_turno",
        "nm_candidato": "nm_candidato", "sg_partido": "sg_partido",
        "nm_municipio": "nm_municipio", "ano_eleicao": "ano_eleicao",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "total_votos" not in df.columns and "qt_votos_nominais" not in df.columns:
        votos_col = next((c for c in df.columns if "voto" in c), None)
        if votos_col:
            df = df.rename(columns={votos_col: "total_votos"})

    needed = {"sg_uf", "cd_cargo", "nr_turno", "nm_candidato", "nm_municipio", "total_votos", "ano_eleicao"}
    if not needed.issubset(df.columns):
        return JSONResponse({"status": "sem_dados", "municipios": [], "fonte": "local",
                             "msg": f"Colunas insuficientes: {list(df.columns[:10])}"})

    df = df[
        (df["sg_uf"].str.upper() == uf.upper())
        & (df["cd_cargo"].astype(str) == str(cd_cargo))
        & (df["nr_turno"].astype(str) == str(turno))
        & (df["ano_eleicao"].isin([2018, 2022]))
        & df["nm_candidato"].notna()
        & (~df["nm_candidato"].str.upper().str.strip().isin(_INVALIDOS_NOMES))
        & (~df["nm_candidato"].str.startswith("#", na=False))
    ].copy()

    if df.empty:
        return JSONResponse({"status": "sem_dados", "municipios": [], "fonte": "local"})

    df["total_votos"] = pd.to_numeric(df["total_votos"], errors="coerce").fillna(0)
    if "sg_partido" not in df.columns:
        df["sg_partido"] = ""

    # vencedor por município × ano
    grp = (
        df.groupby(["nm_municipio", "nm_candidato", "sg_partido", "ano_eleicao"])
        ["total_votos"].sum().reset_index()
    )
    grp["rank"] = grp.groupby(["nm_municipio", "ano_eleicao"])["total_votos"].rank(
        ascending=False, method="first"
    )
    liders = grp[grp["rank"] == 1].copy()

    tot = df.groupby(["nm_municipio", "ano_eleicao"])["total_votos"].sum().reset_index(name="total_mun")
    liders = liders.merge(tot, on=["nm_municipio", "ano_eleicao"], how="left")
    liders["pct"] = (liders["total_votos"] / liders["total_mun"].replace(0, 1) * 100).round(2)
    liders["nm_mun_norm"] = liders["nm_municipio"].apply(_norm)

    l22 = liders[liders["ano_eleicao"] == 2022][["nm_mun_norm", "nm_candidato", "sg_partido", "pct", "total_votos"]].rename(
        columns={"nm_candidato": "nm_vencedor_2022", "sg_partido": "sg_partido_2022",
                 "pct": "pct_2022", "total_votos": "votos_2022"})
    l18 = liders[liders["ano_eleicao"] == 2018][["nm_mun_norm", "nm_candidato", "sg_partido", "pct", "total_votos"]].rename(
        columns={"nm_candidato": "nm_vencedor_2018", "sg_partido": "sg_partido_2018",
                 "pct": "pct_2018", "total_votos": "votos_2018"})

    merged = l22.merge(l18, on="nm_mun_norm", how="outer")
    merged["mudou_lider"] = merged["nm_vencedor_2022"] != merged["nm_vencedor_2018"]
    merged["mudou_partido"] = merged["sg_partido_2022"] != merged["sg_partido_2018"]

    # nome original do município (para o join com geojson)
    nm_map = liders.drop_duplicates("nm_mun_norm").set_index("nm_mun_norm")["nm_municipio"].to_dict()
    merged["nm_municipio"] = merged["nm_mun_norm"].map(nm_map)

    municipios = merged.fillna("").to_dict(orient="records")
    for m in municipios:
        for k in ("pct_2022", "pct_2018"):
            try: m[k] = float(m[k])
            except (ValueError, TypeError): m[k] = 0.0
        for k in ("votos_2022", "votos_2018"):
            try: m[k] = int(m[k])
            except (ValueError, TypeError): m[k] = 0

    return JSONResponse({
        "status": "ok", "uf": uf.upper(), "cargo": cargo,
        "municipios": municipios, "total": len(municipios), "fonte": "local",
    })


# Federações eleitorais 2022 (TSE não expõe via resultados — lookup estático)
_FEDERACOES_2022: dict[str, str] = {
    "PT": "Fed. Brasil da Esperança",
    "PCDOB": "Fed. Brasil da Esperança",
    "PCdoB": "Fed. Brasil da Esperança",
    "PV": "Fed. Brasil da Esperança",
    "PSDB": "Fed. PSDB/Cidadania",
    "CIDADANIA": "Fed. PSDB/Cidadania",
    "SOLIDARIEDADE": "Fed. SD/Avante",
    "AVANTE": "Fed. SD/Avante",
    "PROS": "Fed. PROS/DC",
    "DC": "Fed. PROS/DC",
    "PMN": "Fed. AGIR/PMN",
    "AGIR": "Fed. AGIR/PMN",
    "PSOL": "Fed. PSOL/Rede",
    "REDE": "Fed. PSOL/Rede",
}


@app.get("/api/comparativo/partidos")
async def get_comparativo_partidos(
    uf: str = Query("SP"),
    cargo: str = Query("Deputado Federal"),
    turno: int = Query(1),
    agrupar_federacao: bool = Query(False),
) -> JSONResponse:
    """
    Votos por partido 2018×2022. Exclui brancos/nulos.
    Inclui nm_partido (nome completo) e nm_federacao_2022 (se em federação).
    agrupar_federacao=true: agrega votos pela federação em 2022.
    """
    if not (settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true"):
        return JSONResponse({"status": "bigquery_disabled", "partidos": [], "anos": [2018, 2022]})

    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _cargo_to_cd(cargo, 6)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
    silver = f"{settings.gcp_project_id}.{settings.bigquery_dataset_silver}"
    params = [
        bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()),
        bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
        bigquery.ScalarQueryParameter("turno", "INT64", turno),
    ]

    gold_query = f"""
        WITH base AS (
            SELECT
                sg_partido,
                ano_eleicao,
                SUM(total_votos)             AS votos,
                COUNT(DISTINCT nm_candidato) AS num_candidatos
            FROM `{gold}.fact_municipio_candidato_eleicao`
            WHERE sg_uf    = @uf
              AND cd_cargo = @cd_cargo
              AND nr_turno = @turno
              AND ano_eleicao IN (2018, 2022)
              AND {_FILTER_INVALIDOS}
            GROUP BY sg_partido, ano_eleicao
        ),
        ranked AS (
            SELECT
                sg_partido, ano_eleicao, votos, num_candidatos,
                ROUND(votos / NULLIF(SUM(votos) OVER (PARTITION BY ano_eleicao), 0)*100, 2) AS pct,
                RANK() OVER (PARTITION BY ano_eleicao ORDER BY votos DESC) AS ranking
            FROM base
        )
        SELECT
            sg_partido,
            MAX(IF(ano_eleicao=2018, votos,          NULL)) AS votos_2018,
            MAX(IF(ano_eleicao=2022, votos,          NULL)) AS votos_2022,
            MAX(IF(ano_eleicao=2018, pct,            NULL)) AS pct_2018,
            MAX(IF(ano_eleicao=2022, pct,            NULL)) AS pct_2022,
            MAX(IF(ano_eleicao=2018, ranking,        NULL)) AS rank_2018,
            MAX(IF(ano_eleicao=2022, ranking,        NULL)) AS rank_2022,
            MAX(IF(ano_eleicao=2018, num_candidatos, NULL)) AS cands_2018,
            MAX(IF(ano_eleicao=2022, num_candidatos, NULL)) AS cands_2022,
            COALESCE(MAX(IF(ano_eleicao=2022,votos,NULL)),0)
                - COALESCE(MAX(IF(ano_eleicao=2018,votos,NULL)),0) AS delta_votos
        FROM ranked
        GROUP BY sg_partido
        ORDER BY COALESCE(MAX(IF(ano_eleicao=2022,votos,NULL)),
                          MAX(IF(ano_eleicao=2018,votos,NULL))) DESC
        LIMIT 100
    """

    # Silver: get nm_partido (full party name) per sg_partido
    nm_query = f"""
        SELECT sg_partido, ANY_VALUE(nm_partido) AS nm_partido
        FROM `{silver}.tse_*`
        WHERE sg_uf = @uf AND cd_cargo = @cd_cargo AND nm_partido IS NOT NULL
        GROUP BY sg_partido
    """

    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        gold_task = asyncio.to_thread(
            lambda: list(client.query(gold_query, job_config=cfg).result())
        )
        nm_task = asyncio.to_thread(lambda: list(client.query(nm_query, job_config=cfg).result()))
        gold_rows, nm_rows = await asyncio.gather(gold_task, nm_task)
    except Exception as exc:
        logger.error("comparativo partidos BQ erro: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "error": str(exc), "partidos": []}, status_code=500)

    nm_map = {r["sg_partido"]: r["nm_partido"] for r in nm_rows if r["sg_partido"]}

    partidos_raw = [
        {
            "sg_partido": r["sg_partido"] or "",
            "nm_partido": nm_map.get(r["sg_partido"] or "", ""),
            "nm_federacao_2022": _FEDERACOES_2022.get(r["sg_partido"] or "", ""),
            "votos_2018": int(r["votos_2018"] or 0),
            "votos_2022": int(r["votos_2022"] or 0),
            "pct_2018": float(r["pct_2018"] or 0.0),
            "pct_2022": float(r["pct_2022"] or 0.0),
            "rank_2018": int(r["rank_2018"]) if r["rank_2018"] else None,
            "rank_2022": int(r["rank_2022"]) if r["rank_2022"] else None,
            "cands_2018": int(r["cands_2018"] or 0),
            "cands_2022": int(r["cands_2022"] or 0),
            "delta_votos": int(r["delta_votos"] or 0),
        }
        for r in gold_rows
        if r["sg_partido"]
    ]

    # Se agrupar_federacao: agrega votos 2022 pela federação
    if agrupar_federacao:
        fed_agg: dict[str, dict] = {}
        sem_fed = []
        for p in partidos_raw:
            fed = p["nm_federacao_2022"]
            if fed:
                if fed not in fed_agg:
                    fed_agg[fed] = {
                        "sg_partido": fed,
                        "nm_partido": fed,
                        "nm_federacao_2022": fed,
                        "votos_2018": 0,
                        "votos_2022": 0,
                        "pct_2018": 0.0,
                        "pct_2022": 0.0,
                        "rank_2018": None,
                        "rank_2022": None,
                        "cands_2018": 0,
                        "cands_2022": 0,
                        "delta_votos": 0,
                        "partidos_membros": [],
                    }
                g = fed_agg[fed]
                g["votos_2018"] += p["votos_2018"]
                g["votos_2022"] += p["votos_2022"]
                g["cands_2018"] += p["cands_2018"]
                g["cands_2022"] += p["cands_2022"]
                g["delta_votos"] += p["delta_votos"]
                g["partidos_membros"].append(p["sg_partido"])
            else:
                sem_fed.append(p)
        partidos_final = sorted(
            list(fed_agg.values()) + sem_fed,
            key=lambda x: x["votos_2022"],
            reverse=True,
        )
    else:
        partidos_final = partidos_raw

    return JSONResponse(
        {
            "status": "ok",
            "uf": uf.upper(),
            "cargo": cargo,
            "anos": [2018, 2022],
            "total": len(partidos_final),
            "agrupar_federacao": agrupar_federacao,
            "partidos": partidos_final,
        }
    )
