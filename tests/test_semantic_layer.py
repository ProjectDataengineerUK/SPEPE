"""Tests for the semantic layer view definitions."""

import re

import pytest

from dataops.semantic_layer import _VIEWS, _MV_ZONA_SQL


# ── Registry ──────────────────────────────────────────────────────────────────

EXPECTED_VIEWS = {
    "vw_sentimento_municipio",
    "vw_vulnerabilidade_municipio",
    "vw_perfil_municipio",
    "vw_intencao_voto_uf",
    "vw_pesquisa_vs_social",
    "vw_narrativa_por_tema_uf",
    "vw_cenario_2018_2022_2026",
    "vw_mapa_prioridade_campanha",
    "vw_transferencias_municipio",
    "vw_transferencias_vs_eleicao",
    "vw_emendas_municipio",
    "vw_emendas_vs_eleicao",
    "vw_sancoes_uf",
    "vw_score_municipal_integrado",
}


def test_all_views_registered():
    assert set(_VIEWS.keys()) == EXPECTED_VIEWS


def test_all_views_have_sql():
    for name, sql in _VIEWS.items():
        assert sql.strip(), f"{name} has empty SQL"


def test_mv_zona_sql_defined():
    assert _MV_ZONA_SQL.strip()
    assert "{project}" in _MV_ZONA_SQL
    assert "{gold}" in _MV_ZONA_SQL


# ── SQL structure per view ─────────────────────────────────────────────────────


@pytest.mark.parametrize("view_name", sorted(EXPECTED_VIEWS))
def test_view_sql_has_select(view_name):
    sql = _VIEWS[view_name]
    assert re.search(r"\bSELECT\b", sql, re.IGNORECASE), f"{view_name}: no SELECT"


@pytest.mark.parametrize("view_name", sorted(EXPECTED_VIEWS))
def test_view_sql_has_from(view_name):
    sql = _VIEWS[view_name]
    assert re.search(r"\bFROM\b", sql, re.IGNORECASE), f"{view_name}: no FROM"


@pytest.mark.parametrize("view_name", sorted(EXPECTED_VIEWS))
def test_view_references_project_placeholder(view_name):
    sql = _VIEWS[view_name]
    # All views must reference a GCP project — placeholder or resolved
    assert (
        "spepe" in sql.lower()
        or "{_project}" in sql.lower()
        or re.search(r"`[^`]+\.[^`]+\.[^`]+`", sql)
    ), f"{view_name}: no project.dataset.table reference"


# ── New views: column presence ─────────────────────────────────────────────────


def test_vw_pesquisa_vs_social_columns():
    sql = _VIEWS["vw_pesquisa_vs_social"]
    # Fase 1: raw polling metadata (no candidate-level intentions yet)
    for col in ("qt_pesquisas", "qt_institutos", "total_mencoes", "total_engajamento"):
        assert col in sql, f"vw_pesquisa_vs_social missing column: {col}"


def test_vw_pesquisa_vs_social_confidence_filter():
    sql = _VIEWS["vw_pesquisa_vs_social"]
    assert "record_confidence_score >= 0.80" in sql


def test_vw_narrativa_por_tema_uf_columns():
    sql = _VIEWS["vw_narrativa_por_tema_uf"]
    for col in ("volume_mencoes", "engajamento_total", "share_plataforma_pct"):
        assert col in sql, f"vw_narrativa_por_tema_uf missing column: {col}"


def test_vw_cenario_union_all():
    sql = _VIEWS["vw_cenario_2018_2022_2026"]
    assert "UNION ALL" in sql.upper()
    assert "2018" in sql and "2022" in sql and "2026" in sql


def test_vw_cenario_tipo_dado_column():
    sql = _VIEWS["vw_cenario_2018_2022_2026"]
    assert "'resultado'" in sql
    assert "'pesquisa'" in sql


def test_vw_cenario_confidence_filter():
    sql = _VIEWS["vw_cenario_2018_2022_2026"]
    assert "record_confidence_score >= 0.80" in sql
    assert "tipo_pesquisa = 'corrente'" in sql


def test_vw_mapa_prioridade_cte_structure():
    sql = _VIEWS["vw_mapa_prioridade_campanha"]
    assert "WITH" in sql.upper()
    # Must have top2 and margem CTEs
    assert "top2" in sql
    assert "margem" in sql


def test_vw_mapa_prioridade_columns():
    sql = _VIEWS["vw_mapa_prioridade_campanha"]
    for col in ("score_competitividade", "classificacao_disputa", "margem_pp", "lider"):
        assert col in sql, f"vw_mapa_prioridade_campanha missing: {col}"


def test_vw_mapa_prioridade_classificacao_values():
    sql = _VIEWS["vw_mapa_prioridade_campanha"]
    for label in ("Disputado", "Competitivo", "Inclinado", "Definido"):
        assert label in sql, f"missing classificacao: {label}"


def test_vw_mapa_prioridade_joins_ibge():
    sql = _VIEWS["vw_mapa_prioridade_campanha"]
    assert "fact_ibge_municipio" in sql


# ── Existing views: non-regression ────────────────────────────────────────────


def test_vw_sentimento_municipio_sources():
    sql = _VIEWS["vw_sentimento_municipio"]
    assert "social_mencoes_br" in sql


def test_vw_vulnerabilidade_joins_seguranca():
    sql = _VIEWS["vw_vulnerabilidade_municipio"]
    assert "fact_seguranca_municipio" in sql
    assert "fact_ibge_municipio" in sql


def test_vw_cenario_uses_fact_pesquisa():
    sql = _VIEWS["vw_cenario_2018_2022_2026"]
    assert "fact_pesquisa" in sql
    assert "fact_candidato_eleicao" in sql
