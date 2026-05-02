"""Portal Câmara e Senado clients — votações, presença, gastos de gabinete.

Câmara: API REST v2 — dados.camara.leg.br/api/v2
Senado: API LegislaBrasil — legis.senado.leg.br/dadosabertos/v1
Ambas são públicas (sem autenticação) e retornam JSON.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("spepe.clients.camara_senado")

_CAMARA_BASE = "https://dadosabertos.camara.leg.br/api/v2"
_SENADO_BASE = "https://legis.senado.leg.br/dadosabertos"

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json", "User-Agent": "SPEPE-DataOps/1.0"})


# ── helpers ──────────────────────────────────────────────────────────────────


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _get(url: str, params: dict[str, Any] | None = None) -> dict:
    resp = _SESSION.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _paginate(url: str, params: dict, page_key: str = "dados", max_pages: int = 20) -> list[dict]:
    """Follow Câmara API pagination (Link header next)."""
    results: list[dict] = []
    current_url: str | None = url
    page = 0

    while current_url and page < max_pages:
        try:
            data = _get(current_url, params if page == 0 else None)
        except Exception as exc:
            logger.warning("Erro paginação %s p%d: %s", url, page, exc)
            break

        items = data.get(page_key, [])
        if not items:
            break
        results.extend(items if isinstance(items, list) else [items])

        links = data.get("links", [])
        next_link = next((lk["href"] for lk in links if lk.get("rel") == "next"), None)
        current_url = next_link
        page += 1
        time.sleep(0.3)

    return results


# ── Câmara dos Deputados ─────────────────────────────────────────────────────


def fetch_deputados(legislature: int = 57) -> pd.DataFrame:
    """Busca lista de deputados federais de uma legislatura.

    legislature: 56 = 2019-2023, 57 = 2023-2027
    Returns: id, nome, sg_partido, sg_uf, url_foto, uri
    """
    url = f"{_CAMARA_BASE}/deputados"
    items = _paginate(url, {"idLegislatura": legislature, "itens": 200})
    if not items:
        return pd.DataFrame()

    rows = []
    for dep in items:
        rows.append({
            "id_deputado": dep.get("id"),
            "nome": dep.get("nome", ""),
            "sg_partido": dep.get("siglaPartido", ""),
            "sg_uf": dep.get("siglaUf", ""),
            "legislatura": legislature,
            "uri": dep.get("uri", ""),
        })
    df = pd.DataFrame(rows)
    logger.info("Câmara deputados: %d encontrados (leg=%d)", len(df), legislature)
    return df


def fetch_votacoes_camara(year: int, cargo_sigla: str = "dep") -> pd.DataFrame:
    """Busca votações nominais da Câmara para um ano.

    Returns: id_votacao, data, titulo, proposicao, placar_sim, placar_nao, placar_abstencao.
    """
    url = f"{_CAMARA_BASE}/votacoes"
    params = {"dataInicio": f"{year}-01-01", "dataFim": f"{year}-12-31", "itens": 200}
    items = _paginate(url, params)
    if not items:
        return pd.DataFrame()

    rows = []
    for v in items:
        rows.append({
            "id_votacao": v.get("id"),
            "data": v.get("data", "")[:10],
            "titulo": v.get("titulo", "")[:200],
            "descricao": v.get("descricao", "")[:500],
            "placar_sim": v.get("aprovacao", 0),
            "placar_nao": v.get("reprovacao", 0),
            "placar_abstencao": v.get("abstencao", 0),
            "ano": year,
            "casa": "camara",
        })
    df = pd.DataFrame(rows)
    if "data" in df.columns:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
    logger.info("Câmara votações: %d em %d", len(df), year)
    return df


def fetch_votos_deputado(id_votacao: str) -> pd.DataFrame:
    """Busca como cada deputado votou em uma votação específica."""
    url = f"{_CAMARA_BASE}/votacoes/{id_votacao}/votos"
    try:
        data = _get(url)
        items = data.get("dados", [])
    except Exception as exc:
        logger.warning("Votos votação %s: %s", id_votacao, exc)
        return pd.DataFrame()

    rows = []
    for voto in items:
        dep = voto.get("deputado_", {})
        rows.append({
            "id_votacao": id_votacao,
            "id_deputado": dep.get("id"),
            "nome": dep.get("nome", ""),
            "sg_partido": dep.get("siglaPartido", ""),
            "sg_uf": dep.get("siglaUf", ""),
            "voto": voto.get("tipoVoto", ""),
        })
    return pd.DataFrame(rows)


def fetch_gastos_gabinete_camara(year: int, id_deputado: int | None = None) -> pd.DataFrame:
    """Busca gastos de gabinete (CEAP — Cota para Exercício da Atividade Parlamentar).

    Se id_deputado é None, busca todos os deputados (pode ser lento — use com parcimônia).
    """
    url = f"{_CAMARA_BASE}/deputados"
    if id_deputado:
        url = f"{_CAMARA_BASE}/deputados/{id_deputado}/despesas"
        params = {"ano": year, "itens": 200}
        items = _paginate(url, params)
    else:
        deps = fetch_deputados()
        if deps.empty:
            return pd.DataFrame()
        items = []
        for dep_id in deps["id_deputado"].dropna().astype(int).tolist()[:20]:
            dep_url = f"{_CAMARA_BASE}/deputados/{dep_id}/despesas"
            batch = _paginate(dep_url, {"ano": year, "itens": 200}, max_pages=5)
            for b in batch:
                b["id_deputado"] = dep_id
            items.extend(batch)
            time.sleep(0.5)

    if not items:
        return pd.DataFrame()

    rows = []
    for d in items:
        rows.append({
            "id_deputado": d.get("id_deputado") or id_deputado,
            "mes": d.get("mes"),
            "tipo_despesa": d.get("tipoDespesa", ""),
            "fornecedor": d.get("nomeFornecedor", ""),
            "cnpj_cpf_fornecedor": d.get("cnpjCpfFornecedor", ""),
            "vr_documento": float(d.get("valorDocumento", 0) or 0),
            "vr_liquido": float(d.get("valorLiquido", 0) or 0),
            "ano": year,
            "casa": "camara",
        })
    df = pd.DataFrame(rows)
    logger.info("Câmara CEAP: %d despesas ano=%d", len(df), year)
    return df


# ── Senado Federal ───────────────────────────────────────────────────────────


def fetch_senadores(legislature: int = 57) -> pd.DataFrame:
    """Busca senadores de uma legislatura via LegislaBrasil."""
    url = f"{_SENADO_BASE}/senado/lista/atual"
    try:
        data = _get(url)
        senadores = (
            data.get("ListaParlamentarEmExercicio", {})
                .get("Parlamentares", {})
                .get("Parlamentar", [])
        )
    except Exception as exc:
        logger.warning("Senado lista: %s", exc)
        return pd.DataFrame()

    rows = []
    for s in senadores:
        p = s.get("IdentificacaoParlamentar", {})
        rows.append({
            "codigo_senador": p.get("CodigoParlamentar"),
            "nome": p.get("NomeParlamentar", ""),
            "sg_partido": p.get("SiglaPartidoParlamentar", ""),
            "sg_uf": p.get("UfParlamentar", ""),
            "sexo": p.get("SexoParlamentar", ""),
            "foto_url": p.get("UrlFotoParlamentar", ""),
        })
    df = pd.DataFrame(rows)
    logger.info("Senado: %d senadores", len(df))
    return df


def fetch_votacoes_senado(year: int) -> pd.DataFrame:
    """Busca votações nominais do Senado para um ano."""
    url = f"{_SENADO_BASE}/plenario/lista/votacao/{year}"
    try:
        data = _get(url)
        items = (
            data.get("ListaVotacoes", {})
                .get("Votacoes", {})
                .get("Votacao", [])
        )
    except Exception as exc:
        logger.warning("Senado votações %d: %s", year, exc)
        return pd.DataFrame()

    if isinstance(items, dict):
        items = [items]

    rows = []
    for v in items:
        rows.append({
            "codigo_sessao": v.get("CodigoSessao"),
            "data": str(v.get("DataSessao", ""))[:10],
            "tipo_votacao": v.get("DescricaoVotacao", "")[:200],
            "resultado": v.get("DescricaoResultado", ""),
            "ano": year,
            "casa": "senado",
        })
    df = pd.DataFrame(rows)
    if "data" in df.columns:
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
    logger.info("Senado votações: %d em %d", len(df), year)
    return df


def build_parlamentares_dataframe(legislature: int = 57, year: int = 2024) -> pd.DataFrame:
    """Consolida deputados + senadores em um único DataFrame de parlamentares."""
    df_dep = fetch_deputados(legislature)
    df_dep["casa"] = "camara"

    df_sen = fetch_senadores(legislature)
    df_sen["casa"] = "senado"

    frames = [f for f in [df_dep, df_sen] if not f.empty]
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["legislatura"] = legislature
    df["ano_ref"] = year
    df["ingested_at"] = pd.Timestamp.utcnow()
    logger.info("Parlamentares consolidados: %d total", len(df))
    return df
