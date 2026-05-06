"""Source reliability registry for SPEPE social/news pipeline.

Each entry scores a data source on:
  score_confiabilidade  float [1-10]  editorial standards + track record
  tipo_fonte            str           imprensa | social | oficial | agregador | portal
  vies_politico         str           esquerda | centro-esquerda | centro | centro-direita | direita | variado | neutro
  alcance               str           nacional | regional | global | nicho

Methodology:
  - Imprensa: Reuters Institute Digital News Report BR + NewsGuard equivalences
  - Social: intrinsically lower (no editorial filter), boosted by platform reach
  - Oficial: highest factual reliability, may have institutional bias
  - Aggregador (GDELT, Google News): no original content, scores source diversity
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceMeta:
    score_confiabilidade: float
    tipo_fonte: str
    vies_politico: str
    alcance: str
    descricao: str


SOURCE_REGISTRY: dict[str, SourceMeta] = {
    # ── Agregadores / Monitoramento ──────────────────────────────────────────
    "gdelt": SourceMeta(
        score_confiabilidade=8.0,
        tipo_fonte="agregador",
        vies_politico="neutro",
        alcance="global",
        descricao="GDELT Project — monitora toda imprensa mundial; sentimento pré-calculado (AvgTone/GoldsteinScale)",
    ),
    # ── Imprensa Nacional — Alta confiabilidade ──────────────────────────────
    "agencia_brasil": SourceMeta(
        score_confiabilidade=9.0,
        tipo_fonte="oficial",
        vies_politico="neutro",
        alcance="nacional",
        descricao="Agência Brasil (EBC) — agência pública de notícias, referência factual",
    ),
    "poder360": SourceMeta(
        score_confiabilidade=8.5,
        tipo_fonte="imprensa",
        vies_politico="centro",
        alcance="nacional",
        descricao="Poder360 — jornalismo político especializado, fact-checking rigoroso",
    ),
    "g1_globo": SourceMeta(
        score_confiabilidade=8.0,
        tipo_fonte="imprensa",
        vies_politico="centro",
        alcance="nacional",
        descricao="G1 / Globo — maior portal de notícias do Brasil",
    ),
    "folha": SourceMeta(
        score_confiabilidade=8.0,
        tipo_fonte="imprensa",
        vies_politico="centro-esquerda",
        alcance="nacional",
        descricao="Folha de S.Paulo — jornal de referência, maior circulação paga",
    ),
    "o_globo": SourceMeta(
        score_confiabilidade=8.0,
        tipo_fonte="imprensa",
        vies_politico="centro",
        alcance="nacional",
        descricao="O Globo — jornal de referência Rio/nacional",
    ),
    "estadao": SourceMeta(
        score_confiabilidade=8.0,
        tipo_fonte="imprensa",
        vies_politico="centro-direita",
        alcance="nacional",
        descricao="O Estado de S.Paulo — jornal de referência com forte cobertura econômica",
    ),
    "cnn_brasil": SourceMeta(
        score_confiabilidade=7.5,
        tipo_fonte="imprensa",
        vies_politico="centro",
        alcance="nacional",
        descricao="CNN Brasil — TV e portal 24h notícias",
    ),
    "veja": SourceMeta(
        score_confiabilidade=7.0,
        tipo_fonte="imprensa",
        vies_politico="centro-direita",
        alcance="nacional",
        descricao="Veja — maior revista semanal do Brasil",
    ),
    "exame": SourceMeta(
        score_confiabilidade=7.0,
        tipo_fonte="imprensa",
        vies_politico="centro-direita",
        alcance="nacional",
        descricao="Exame — foco economia/negócios",
    ),
    # ── Portais — Média confiabilidade ───────────────────────────────────────
    "uol": SourceMeta(
        score_confiabilidade=7.0,
        tipo_fonte="portal",
        vies_politico="centro",
        alcance="nacional",
        descricao="UOL Notícias — maior portal do Brasil, agrega várias fontes",
    ),
    "r7": SourceMeta(
        score_confiabilidade=6.5,
        tipo_fonte="imprensa",
        vies_politico="centro-direita",
        alcance="nacional",
        descricao="R7 / Record — portal do grupo Record",
    ),
    "metropoles": SourceMeta(
        score_confiabilidade=6.5,
        tipo_fonte="portal",
        vies_politico="centro",
        alcance="nacional",
        descricao="Metrópoles — portal Brasília/nacional",
    ),
    "agencia_publica": SourceMeta(
        score_confiabilidade=7.0,
        tipo_fonte="imprensa",
        vies_politico="esquerda",
        alcance="nicho",
        descricao="Agência Pública — jornalismo investigativo independente",
    ),
    # ── Redes Sociais — Menor confiabilidade editorial ────────────────────────
    "youtube": SourceMeta(
        score_confiabilidade=5.0,
        tipo_fonte="social",
        vies_politico="variado",
        alcance="nacional",
        descricao="YouTube — vídeos políticos; sentimento via título/engajamento",
    ),
    "bluesky": SourceMeta(
        score_confiabilidade=4.5,
        tipo_fonte="social",
        vies_politico="variado",
        alcance="nicho",
        descricao="Bluesky (AT Protocol) — crescendo no BR entre jornalistas/políticos",
    ),
    "reddit": SourceMeta(
        score_confiabilidade=4.5,
        tipo_fonte="social",
        vies_politico="variado",
        alcance="nicho",
        descricao="Reddit r/brasil r/politica — discussão com upvotes (sinal de qualidade parcial)",
    ),
    "twitter_x": SourceMeta(
        score_confiabilidade=4.0,
        tipo_fonte="social",
        vies_politico="variado",
        alcance="nacional",
        descricao="Twitter/X — alta velocidade, baixa curadoria; sinal de narrativa emergente",
    ),
    "facebook": SourceMeta(
        score_confiabilidade=4.0,
        tipo_fonte="social",
        vies_politico="variado",
        alcance="nacional",
        descricao="Facebook — pages públicas de candidatos e partidos",
    ),
    # ── Tendências / Intenção de busca ────────────────────────────────────────
    "google_trends": SourceMeta(
        score_confiabilidade=7.0,
        tipo_fonte="agregador",
        vies_politico="neutro",
        alcance="nacional",
        descricao="Google Trends — interesse de busca por candidato/tema; proxy de relevância",
    ),
    # ── Fallback ─────────────────────────────────────────────────────────────
    "desconhecido": SourceMeta(
        score_confiabilidade=3.0,
        tipo_fonte="desconhecido",
        vies_politico="variado",
        alcance="desconhecido",
        descricao="Fonte não classificada",
    ),
}


def get_source_meta(fonte: str) -> SourceMeta:
    """Return SourceMeta for a given source key, defaulting to 'desconhecido'."""
    return SOURCE_REGISTRY.get(fonte, SOURCE_REGISTRY["desconhecido"])


def enrich_with_source_meta(records: list[dict], fonte_field: str = "fonte") -> list[dict]:
    """Add score_confiabilidade, tipo_fonte, vies_politico, alcance_fonte to each record."""
    for rec in records:
        meta = get_source_meta(rec.get(fonte_field, "desconhecido"))
        rec["score_confiabilidade"] = meta.score_confiabilidade
        rec["tipo_fonte"] = meta.tipo_fonte
        rec["vies_politico"] = meta.vies_politico
        rec["alcance_fonte"] = meta.alcance
    return records


def list_sources_by_score() -> list[tuple[str, float]]:
    """Return all sources sorted by score descending."""
    return sorted(
        [(name, meta.score_confiabilidade) for name, meta in SOURCE_REGISTRY.items()],
        key=lambda x: x[1],
        reverse=True,
    )
