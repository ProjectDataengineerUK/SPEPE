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
    "/api/ufs",
    "/api/mesorregioes",
    "/api/social/sentimento",
    "/api/social/trends",
    "/api/social/plataformas",
    "/api/social/crise",
    "/api/candidatos",
    "/api/municipios",
    "/api/kpi",
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

        gid.verify_oauth2_token(
            auth[7:],
            grequests.Request(),
            audience=settings.google_client_id or None,
            clock_skew_in_seconds=10,
        )
    except Exception:
        return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
    return await call_next(request)


# ── Admin authentication ───────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> dict:
    """Validate Bearer token for admin API routes.

    In local dev (no GCP_PROJECT_ID or set to 'local') auth is skipped.
    In GCP the token must be a valid Google OAuth2 ID token.
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
        return info
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


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


# ── Auth (stub — trocar por Firebase Auth / IAP em prod) ──────────────────


@app.get("/api/auth/me")
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
    cargo_map = {
        "Presidente": 1,
        "Governador": 3,
        "Senador": 5,
        "Dep. Federal": 6,
        "Dep. Estadual": 7,
    }
    cd_cargo = cargo_map.get(cargo, 1)
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
        # Presidente: usa fact_presidente_resultado (nacional, sem filtro de UF)
        query = f"""
            SELECT
                nm_candidato                                    AS nm,
                CAST(NULL AS STRING)                            AS partido,
                ROUND(SUM(total_votos) / SUM(SUM(total_votos)) OVER () * 100, 1) AS pct_t1,
                CAST(SUM(total_votos) AS STRING)                AS votos
            FROM `{gold}.fact_presidente_resultado`
            WHERE ano_eleicao = @ano
              AND nr_turno = 1
            GROUP BY nm_candidato
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
    cd_cargo = _CARGO_CD.get(cargo, 1)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"

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
        FROM ranked
        WHERE rn <= 2
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
    cd_cargo = _CARGO_CD.get(cargo, 1)
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

    return JSONResponse(
        {
            "cargo": cargo,
            "uf": uf,
            "ano": ano,
            "turno": turno,
            "candidatos": [],
            "fonte": "indisponivel",
        }
    )


async def _bq_resultados(cargo: str, uf: str, ano: int, turno: int) -> list[dict]:
    """Query ao BigQuery Gold — fact_municipio_candidato_eleicao agregado por candidato."""
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _CARGO_CD.get(cargo, 1)
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
            _cd_cargo = _CARGO_CD.get(cargo, 1)
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
            _cd_cargo = _CARGO_CD.get(cargo, 1)
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
            return JSONResponse({"municipios": await _bq_socioeconomico(uf, ano, limit)})
        except Exception as exc:
            logger.warning("BigQuery socioeconomico falhou: %s", exc)
    data = _local_socioeconomico(uf, ano, limit)
    return JSONResponse({"municipios": data, "fonte": "local" if data else "indisponivel"})


async def _bq_socioeconomico(uf: str, ano: int, limit: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
    query = f"""
        SELECT nm_municipio,
               idhm, renda_per_capita, gini, pct_extrema_pobreza,
               taxa_analfabetismo, pct_urbano, populacao_total
        FROM `{gold}.fact_ibge_municipio`
        WHERE sg_uf = @uf AND ano = @ano
        ORDER BY idhm DESC NULLS LAST
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
            "idhm": round(r.get("idhm") or 0, 3),
            "renda_per_capita": round(r.get("renda_per_capita") or 0, 0),
            "gini": round(r.get("gini") or 0, 3),
            "pct_extrema_pobreza": round((r.get("pct_extrema_pobreza") or 0) * 100, 1),
            "taxa_analfabetismo": round((r.get("taxa_analfabetismo") or 0) * 100, 1),
            "pct_urbano": round((r.get("pct_urbano") or 0) * 100, 1),
            "populacao": r.get("populacao_total") or 0,
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
            return JSONResponse({"municipios": await _bq_seguranca(uf, ano, limit)})
        except Exception as exc:
            logger.warning("BigQuery seguranca falhou: %s", exc)
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
        ORDER BY s.taxa_homicidio_100k DESC NULLS LAST
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
            return JSONResponse({"municipios": await _bq_saude(uf, ano, limit)})
        except Exception as exc:
            logger.warning("BigQuery saude falhou: %s", exc)
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

    cd_cargo = _CARGO_CD.get(cargo, 1)
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
    by_candidato: dict[str, list] = {}
    institutes: dict[str, float] = {}
    for r in rows:
        cand = r.get("candidato", "")
        by_candidato.setdefault(cand, []).append(
            {
                "data": str(r.get("data_pesquisa_inicio", ""))[:7],
                "instituto": r.get("instituto", ""),
                "intencao": round(r.get("intencao_pct") or 0, 1),
                "ajustada": round(r.get("intencao_ajustada") or r.get("intencao_pct") or 0, 1),
                "tipo": r.get("tipo_pesquisa", ""),
            }
        )
        inst = r.get("instituto", "")
        if inst and inst not in institutes:
            institutes[inst] = round(r.get("house_effect") or 0, 2)
    series = [{"candidato": c, "pontos": pts} for c, pts in by_candidato.items()]
    house_effects = [{"instituto": k, "house_effect": v} for k, v in institutes.items()]
    return {"series": series, "house_effects": house_effects, "tipo": tipo}


def _local_pesquisas(cargo: str, sg_uf: str, ano: int, tipo: str) -> dict:
    import pandas as pd

    cd_cargo = _CARGO_CD.get(cargo, 1)
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
          AND LOWER(COALESCE(cargo, 'presidente')) = LOWER(@cargo)
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
            SELECT cd_municipio_ibge, sg_uf, sentimento_score AS valor
            FROM `{gold}.fact_sentiment_municipio`
            WHERE data_referencia = @data_ref
              AND (@candidato IS NULL OR candidato = @candidato)
              AND (@uf IS NULL OR sg_uf = @uf)
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
            WHERE prediction_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
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
        {"genero": [], "faixa_etaria": [], "escolaridade": [], "fonte": "indisponivel"}
    )


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


async def _bq_mapa_nacional(cargo: str, ano: int, turno: int, candidato: str = "") -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _CARGO_CD.get(cargo, 1)
    cand_filter = "AND nm_candidato = @candidato" if candidato else ""
    query = f"""
        WITH ranked AS (
            SELECT nm_candidato, sg_partido,
                   SUM(total_votos) AS votos,
                   RANK() OVER (ORDER BY SUM(total_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_candidato_eleicao`
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
    cd_cargo = _CARGO_CD.get(cargo, 1)
    base_table = f"`{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_candidato_eleicao`"
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
    cd_cargo = _CARGO_CD.get(cargo, 1)
    base_table = f"`{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_candidato_eleicao`"
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
    cd_cargo = _CARGO_CD.get(cargo, 1)
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
    cd_cargo = _CARGO_CD.get(cargo, 1)
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
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            params = []
            where_clauses = ["data_ref >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 WEEK)"]
            if uf:
                where_clauses.append("sg_uf = @uf")
                params.append(bigquery.ScalarQueryParameter("uf", "STRING", uf.upper()))
            where_sql = " AND ".join(where_clauses)
            query = f"""
                SELECT candidato, semana, sentimento_score_medio,
                       total_mencoes, engajamento_total
                FROM `{gold}.vw_social_candidato_sentimento`
                WHERE {where_sql}
                ORDER BY semana DESC, total_mencoes DESC
                LIMIT 200
            """
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery social sentimento falhou: %s", exc)
    return JSONResponse({"data": []})


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
    return JSONResponse({"data": []})


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
                    WHERE sg_uf = @uf AND EXTRACT(YEAR FROM data_ref) = @ano
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
    return JSONResponse({"data": []})


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
    return JSONResponse({"data": []})


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
    return JSONResponse({"data": []})


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
            cd_cargo = _CARGO_CD.get(cargo, 3)
            try:
                query = f"""
                    SELECT candidato, prob_vitoria, intervalo_inferior, intervalo_superior
                    FROM `{mlops}.fact_predictions`
                    WHERE sg_uf = @uf AND cd_cargo = @cd_cargo AND ano_eleicao = @ano
                    ORDER BY prob_vitoria DESC
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
                if rows:
                    return JSONResponse({"data": [dict(r) for r in rows]})
            except Exception:
                pass
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            query = f"""
                SELECT nm_candidato AS candidato,
                       ROUND(pct_uf / 100.0, 4) AS prob_vitoria,
                       NULL AS intervalo_inferior,
                       NULL AS intervalo_superior
                FROM `{gold}.vw_intencao_voto_uf`
                WHERE sg_uf = @uf AND cd_cargo = @cd_cargo AND ano_eleicao = @ano
                ORDER BY prob_vitoria DESC
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
        cd_cargo = _CARGO_CD.get(cargo, 3)

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
                       SUM(qt_votacoes) AS qt_votacoes,
                       SUM(qt_sim)      AS qt_sim,
                       SUM(qt_nao)      AS qt_nao,
                       SUM(qt_abstencao) AS qt_abstencao,
                       ROUND(SUM(qt_sim) / NULLIF(SUM(qt_votacoes), 0) * 100, 1) AS pct_favoravel
                FROM `{gold}.fact_votacoes_parlamentar`
                WHERE ano = @year {uf_filter} {casa_filter}
                GROUP BY sg_uf, sg_partido, casa, tema
                ORDER BY qt_votacoes DESC
                LIMIT 200
            """
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            rows = list(client.query(query, job_config=job_config).result())
            return JSONResponse({"data": [dict(r) for r in rows]})
        except Exception as exc:
            logger.warning("BigQuery parlamentares falhou: %s", exc)
    return JSONResponse({"data": []})


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
                logger.error("Supervisor WS erro: %s", exc)
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


_USER_STORE: list[dict] = [
    {
        "id": "admin-001",
        "name": "Admin SPEPE",
        "email": "admin@***.***",
        "role": "admin",
        "created_at": "2026-05-01",
    },
    {
        "id": "analyst-001",
        "name": "Analista Senior",
        "email": "analyst@***.***",
        "role": "analyst",
        "created_at": "2026-05-01",
    },
]  # fallback when Firestore unavailable
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
    """List users from Firestore spepe_users collection with fallback to local store."""
    db = _fs_client()
    if db:
        try:
            docs = db.collection("spepe_users").stream()
            users = [doc.to_dict() async for doc in docs]
            if users:
                return JSONResponse({"users": users, "source": "firestore"})
        except Exception as exc:
            logger.debug("Firestore users query failed: %s", exc)
    return JSONResponse({"users": _USER_STORE, "source": "local" if _USER_STORE else "empty"})


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
        except Exception:
            pass
    _USER_STORE.append(user)
    return JSONResponse({"ok": True, "user": user})


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
        except Exception:
            pass
    for i, u in enumerate(_USER_STORE):
        if u["id"] == user_id:
            _USER_STORE[i] = {**u, **body, "id": user_id}
            return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "not found"}, status_code=404)


@app.delete("/admin/api/users/{user_id}", dependencies=[Depends(require_auth)])
async def admin_delete_user(user_id: str) -> JSONResponse:
    global _USER_STORE
    db = _fs_client()
    if db:
        try:
            await db.collection("spepe_users").document(user_id).delete()
            return JSONResponse({"ok": True})
        except Exception:
            pass
    _USER_STORE = [u for u in _USER_STORE if u["id"] != user_id]
    return JSONResponse({"ok": True})


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
    # Look up profile in Firestore or fallback to admin
    profile = "admin"
    db = _fs_client()
    if db and email:
        try:
            docs = db.collection("spepe_users").where("email", "==", email).limit(1).stream()
            async for doc in docs:
                d = doc.to_dict() or {}
                profile = d.get("profile") or d.get("role") or "admin"
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

    stub_catalog = [
        {
            "dataset_id": "spepe_gold",
            "table_id": "fact_municipio_eleicao",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": "DAY (ano_eleicao)",
            "clustering": ["sg_uf", "cd_cargo", "nr_turno"],
            "description": "Resultados eleitorais por município × eleição",
        },
        {
            "dataset_id": "spepe_gold",
            "table_id": "fact_secao_eleicao",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": None,
            "clustering": ["sg_uf"],
            "description": "Granular seção × candidato (139M+ rows)",
        },
        {
            "dataset_id": "spepe_gold",
            "table_id": "fact_municipio_candidato_eleicao",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": None,
            "clustering": ["sg_uf", "candidato"],
            "description": "Desempenho candidato × município",
        },
        {
            "dataset_id": "spepe_gold",
            "table_id": "fact_ibge_municipio",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": "RANGE (ano)",
            "clustering": ["sg_uf"],
            "description": "Indicadores socioeconômicos IBGE por município",
        },
        {
            "dataset_id": "spepe_gold",
            "table_id": "fact_seguranca_municipio",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": None,
            "clustering": ["sg_uf"],
            "description": "IVS + Atlas da Violência + SINESP",
        },
        {
            "dataset_id": "spepe_gold",
            "table_id": "fact_saude_municipio",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": None,
            "clustering": ["sg_uf"],
            "description": "DataSUS SIM + ANS cobertura",
        },
        {
            "dataset_id": "spepe_gold",
            "table_id": "fact_pesquisa",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": None,
            "clustering": ["sg_uf"],
            "description": "Pesquisas eleitorais TSE/institutos",
        },
        {
            "dataset_id": "spepe_mlops",
            "table_id": "model_evaluations",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": "DAY (evaluated_at)",
            "clustering": ["model_version"],
            "description": "Backtests e canary evaluation por versão",
        },
        {
            "dataset_id": "spepe_mlops",
            "table_id": "fact_predictions",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": "DAY (prediction_date)",
            "clustering": ["candidato", "sg_uf", "model_version"],
            "description": "Predições com IC 95%",
        },
        {
            "dataset_id": "spepe_mlops",
            "table_id": "bias_metrics",
            "num_rows": None,
            "size_mb": None,
            "last_modified": None,
            "partitioning": "DAY (measured_at)",
            "clustering": ["model_version"],
            "description": "Fairness metrics por sg_uf e quintil de renda",
        },
    ]
    return JSONResponse(
        {"tables": stub_catalog, "source": "stub", "note": "BigQuery unavailable or disabled"}
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
