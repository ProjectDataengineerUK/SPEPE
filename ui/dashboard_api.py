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

from agents.supervisor import Supervisor
from config.logging_config import setup_logging
from config.session_state import SessionState
from config.settings import settings
from dataops.clients.digital_client import fetch_meta_ads, fetch_trends
from security.output_validators import validate_input_injection


# ── Sentinel SSE — fan-out queues for /admin/api/sentinel/stream ──────────
_sentinel_subscribers: list[asyncio.Queue] = []


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

logger = logging.getLogger("spepe.dashboard_api")

_LOCAL_SILVER_DIR = Path(os.environ.get("DATA_DIR", "data")) / "silver"

# ── Dashboard auth middleware (Google ID token) ───────────────────────────

_PUBLIC_API_PATHS = {"/api/auth/me", "/api/config/maps-key"}


@app.middleware("http")
async def dashboard_auth_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in _PUBLIC_API_PATHS:
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
    return FileResponse(str(html_path), media_type="text/html")


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
            data = await _bq_candidatos(cargo, uf, ano)
            return JSONResponse({"cargo": cargo, "uf": uf, "ano": ano, "candidatos": data})
        except Exception as exc:
            logger.warning("BigQuery candidatos falhou: %s", exc)

    return JSONResponse(
        {"cargo": cargo, "uf": uf, "ano": ano, "candidatos": [], "fonte": "indisponivel"}
    )


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
            ROUND(SUM(total_votos) / SUM(SUM(total_votos)) OVER () * 100, 1) AS pct_t1,
            CAST(SUM(total_votos) AS STRING) AS votos
        FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_candidato_eleicao`
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
    rows = list(client.query(query, job_config=job_config).result())
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
    rows = list(client.query(query, job_config=job_config).result())
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
        FROM `{gold}.fact_pesquisa`
        WHERE cd_cargo = @cd_cargo
          AND (uf = @sg_uf OR uf IS NULL OR @sg_uf = 'BR')
          AND EXTRACT(YEAR FROM data_pesquisa_inicio) = @ano
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
    silver = f"{settings.gcp_project_id}.spepe_silver"
    query = f"""
        SELECT ds_genero, ds_faixa_etaria, ds_grau_escolaridade,
               SUM(qt_eleitores) AS qt_eleitores
        FROM `{silver}.perfil_eleitorado_{uf.lower()}_{ano}`
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    """
    job_config = bigquery.QueryJobConfig()
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
                features = await _bq_mapa_nacional(cargo, ano, turno)
                return JSONResponse({"nivel": "nacional", "features": features})
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
            logger.warning("BigQuery mapa %s falhou: %s", nivel_str, exc)

    return JSONResponse({"nivel": nivel_str, "features": [], "fonte": "indisponivel"})


async def _bq_mapa_nacional(cargo: str, ano: int, turno: int) -> list[dict]:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project_id)
    cd_cargo = _CARGO_CD.get(cargo, 1)
    query = f"""
        WITH ranked AS (
            SELECT nm_candidato, sg_partido,
                   SUM(total_votos) AS votos,
                   RANK() OVER (ORDER BY SUM(total_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_candidato_eleicao`
            WHERE ano_eleicao = @ano AND cd_cargo = @cd_cargo AND nr_turno = @turno
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
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ano", "INT64", ano),
            bigquery.ScalarQueryParameter("cd_cargo", "INT64", cd_cargo),
            bigquery.ScalarQueryParameter("turno", "INT64", turno),
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())
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
        WITH candidatos AS (
            SELECT
                {case_expr} AS regiao,
                nm_candidato,
                sg_partido,
                SUM(total_votos) AS votos
            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_candidato_eleicao`
            WHERE ano_eleicao = @ano AND cd_cargo = @cd_cargo AND nr_turno = @turno
            GROUP BY regiao, nm_candidato, sg_partido
        ),
        totais AS (
            SELECT regiao, SUM(votos) AS total_votos
            FROM candidatos GROUP BY regiao
        ),
        ranked AS (
            SELECT c.regiao, c.nm_candidato, c.sg_partido, c.votos, t.total_votos,
                   ROW_NUMBER() OVER (PARTITION BY c.regiao ORDER BY c.votos DESC) AS rn
            FROM candidatos c JOIN totais t USING (regiao)
        )
        SELECT regiao, nm_candidato AS lider, sg_partido AS partido,
               ROUND(votos / total_votos * 100, 1) AS pct,
               total_votos
        FROM ranked WHERE rn = 1
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
                   SUM(total_votos) AS votos,
                   RANK() OVER (PARTITION BY sg_uf ORDER BY SUM(total_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_candidato_eleicao`
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
                   SUM(total_votos) AS votos,
                   RANK() OVER (PARTITION BY cd_municipio ORDER BY SUM(total_votos) DESC) AS rnk
            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset_gold}.fact_municipio_candidato_eleicao`
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
                SELECT candidato, mes, interesse
                FROM `{gold}.fact_google_trends_uf`
                WHERE sg_uf = @uf AND EXTRACT(YEAR FROM mes) = @ano
                ORDER BY mes, candidato
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


# ── Digital: Meta Ads por UF ──────────────────────────────────────────────────


@app.get("/api/digital/meta")
async def get_digital_meta(uf: str = "SP", ano: int = 2026) -> JSONResponse:
    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=settings.gcp_project_id)
            gold = f"{settings.gcp_project_id}.{settings.bigquery_dataset_gold}"
            query = f"""
                SELECT candidato, total_spend_r, total_impressions
                FROM `{gold}.fact_meta_ads_uf`
                WHERE sg_uf = @uf AND ano = @ano
                ORDER BY total_spend_r DESC
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
                SELECT candidato,
                       AVG(intencao_ajustada) / 100.0 AS prob_vitoria,
                       NULL AS intervalo_inferior,
                       NULL AS intervalo_superior
                FROM `{gold}.vw_intencao_voto_uf`
                WHERE sg_uf = @uf AND cd_cargo = @cd_cargo
                  AND EXTRACT(YEAR FROM data_pesquisa_inicio) = @ano
                GROUP BY candidato
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


# ── Config: Google Maps Key ───────────────────────────────────────────────────


@app.get("/api/config/maps-key")
async def get_maps_key() -> JSONResponse:
    from security.secret_manager import get_secret

    key = get_secret("GOOGLE_MAPS_API_KEY") or settings.google_maps_api_key
    return JSONResponse({"key": key})


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


@app.get("/admin")
async def serve_admin() -> FileResponse:
    """Serve the Admin Panel HTML."""
    from pathlib import Path

    html_path = Path(__file__).parent / "static" / "admin.html"
    return FileResponse(str(html_path), media_type="text/html")


_USER_STORE: list[dict] = []  # fallback when Firestore unavailable
_ACCESS_MATRIX: dict = {}  # fallback when Firestore unavailable
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
    db = _fs_client()
    if db:
        try:
            docs = db.collection("spepe_users").stream()
            users = [doc.to_dict() async for doc in docs]
            return JSONResponse({"users": users})
        except Exception:
            pass
    return JSONResponse({"users": _USER_STORE})


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
    db = _fs_client()
    if db:
        try:
            doc = await db.collection("spepe_admin").document("access_matrix").get()
            if doc.exists:
                return JSONResponse({"matrix": doc.to_dict().get("matrix", {})})
        except Exception:
            pass
    return JSONResponse({"matrix": _ACCESS_MATRIX})


@app.post("/admin/api/access", dependencies=[Depends(require_auth)])
async def admin_save_access(request: Request) -> JSONResponse:
    global _ACCESS_MATRIX
    data = await request.json()
    db = _fs_client()
    if db:
        try:
            await db.collection("spepe_admin").document("access_matrix").set({"matrix": data})
            return JSONResponse({"ok": True})
        except Exception:
            pass
    _ACCESS_MATRIX = data
    return JSONResponse({"ok": True})


@app.get("/admin/api/jobs", dependencies=[Depends(require_auth)])
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
                    jcfg["last_run_at"] = str(gj.update_time) if gj.update_time else ""
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
    return JSONResponse({"jobs": jobs_config})


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
    "vw_sancoes_uf",
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
    "spepe-sancoes-ingest",
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
async def admin_sentinel_status() -> JSONResponse:
    """Snapshot no formato esperado por applySnapshot() no admin panel."""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()
    gold_tables: list[dict] = []
    silver_tables: list[dict] = []
    jobs: list[dict] = []

    if settings.gcp_project_id and os.environ.get("USE_BIGQUERY", "").lower() == "true":
        try:
            from google.cloud import bigquery

            bq = bigquery.Client(project=settings.gcp_project_id)
            now_ms = datetime.now(timezone.utc).timestamp() * 1000

            for layer, dataset in [
                ("gold", settings.bigquery_dataset_gold),
                ("silver", settings.bigquery_dataset_silver),
            ]:
                try:
                    rows = list(
                        bq.query(
                            f"SELECT table_id, row_count, last_modified_time "
                            f"FROM `{settings.gcp_project_id}.{dataset}.__TABLES__`"
                            f" WHERE table_id NOT LIKE 'vw_%'"
                        ).result()
                    )
                    for r in rows:
                        fh = round((now_ms - (r.last_modified_time or now_ms)) / 3_600_000, 1)
                        rc = r.row_count or 0
                        st = "ok" if rc > 0 and fh < 48 else ("warn" if rc > 0 else "error")
                        entry = {
                            "table": r.table_id,
                            "layer": layer,
                            "row_count": rc,
                            "freshness_hours": fh,
                            "dq_score": None,
                            "status": st,
                        }
                        (gold_tables if layer == "gold" else silver_tables).append(entry)
                except Exception as e:
                    logger.warning("__TABLES__ %s failed: %s", dataset, e)

            try:
                from google.cloud import run_v2

                region = os.environ.get("GCP_REGION", "southamerica-east1")
                exec_client = run_v2.ExecutionsClient()
                for jname in _SENTINEL_JOBS:
                    try:
                        parent = (
                            f"projects/{settings.gcp_project_id}/locations/{region}/jobs/{jname}"
                        )
                        execs = list(exec_client.list_executions(parent=parent, page_size=1))
                        if execs:
                            cond = next(
                                (c for c in execs[0].conditions if c.type_ == "Completed"), None
                            )
                            ok = cond and cond.status == "True"
                            last, st = (
                                ("Completed", "ok")
                                if ok
                                else (("Failed", "error") if cond else ("Running", "warn"))
                            )
                        else:
                            last, st = "never", "warn"
                    except Exception:
                        last, st = "unknown", "warn"
                    jobs.append({"job": jname, "status": st, "last_status": last})
            except Exception as e:
                logger.warning("Cloud Run executions query failed: %s", e)

        except Exception as exc:
            logger.warning("Admin sentinel status failed: %s", exc)

    views = [{"view": v, "status": "ok"} for v in _SENTINEL_VIEWS]
    llmops = [
        {"agent": a, "status": "ok", "calls_24h": 0, "p99_latency_s": 0.0, "cost_24h_usd": 0.0}
        for a in _SENTINEL_AGENTS
    ]

    return JSONResponse(
        {
            "source": "live" if (gold_tables or jobs) else "stub",
            "ts": ts,
            "dataops": {"gold": gold_tables, "silver": silver_tables, "views": views},
            "jobs": jobs
            if jobs
            else [{"job": j, "status": "warn", "last_status": "unknown"} for j in _SENTINEL_JOBS],
            "llmops": llmops,
            "costs": {"bq_total_30d_usd": 0.0, "llm_total_30d_usd": 0.0, "bq_daily": []},
            "maturity": {"dataops": 75, "mlops": 35, "llmops": 60},
            "mlops": {
                "brier_score": None,
                "js_divergence": None,
                "eval_score": 0.995,
                "bias_metrics": [],
            },
        }
    )


@app.get("/admin/api/catalog", dependencies=[Depends(require_auth)])
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
    """Collect a full snapshot for the initial SSE event."""
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
    try:
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

        gold = await asyncio.to_thread(query_gold_storage)
        silver = await asyncio.to_thread(query_silver_storage)
        views = await asyncio.to_thread(query_views_existence)
        jobs = await asyncio.to_thread(query_jobs_executions)
        mlops = await asyncio.to_thread(query_mlops_metrics)
        costs = await asyncio.to_thread(query_costs)
        agents = await asyncio.to_thread(query_agents_telemetry)
        maturity = compute_maturity_score(jobs, gold, silver, mlops, agents)
        return {
            "dataops": {"gold": gold, "silver": silver, "views": views},
            "jobs": jobs,
            "mlops": mlops,
            "llmops": agents,
            "costs": costs,
            "maturity": maturity,
            "source": "bigquery",
            "ts": time.time(),
        }
    except Exception as exc:
        logger.warning("snapshot build failed: %s", exc)
        return {
            "dataops": {"gold": [], "silver": [], "views": []},
            "jobs": [],
            "mlops": {},
            "llmops": [],
            "costs": {},
            "maturity": {"dataops": 0, "mlops": 0, "llmops": 0},
            "source": "stub",
            "error": str(exc),
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


@app.get("/admin/api/sentinel/stream", dependencies=[Depends(require_auth)])
async def admin_sentinel_stream() -> StreamingResponse:
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
