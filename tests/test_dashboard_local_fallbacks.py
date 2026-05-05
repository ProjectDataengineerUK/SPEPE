"""Tests for dashboard_api local parquet fallback functions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def silver_dir(tmp_path: Path) -> Path:
    return tmp_path / "silver"


@pytest.fixture()
def ibge_parquet(silver_dir: Path) -> Path:
    silver_dir.mkdir()
    df = pd.DataFrame(
        [
            {
                "nm_municipio": "São Paulo",
                "sg_uf": "SP",
                "ano": 2022,
                "idhm": 0.805,
                "renda_per_capita": 1800.0,
                "gini": 0.56,
                "pct_extrema_pobreza": 0.03,
                "taxa_analfabetismo": 0.04,
                "pct_urbano": 0.98,
                "populacao_total": 12_396_372,
                "cd_municipio_ibge": 3550308,
            },
            {
                "nm_municipio": "Campinas",
                "sg_uf": "SP",
                "ano": 2022,
                "idhm": 0.805,
                "renda_per_capita": 1600.0,
                "gini": 0.53,
                "pct_extrema_pobreza": 0.02,
                "taxa_analfabetismo": 0.03,
                "pct_urbano": 0.99,
                "populacao_total": 1_213_792,
                "cd_municipio_ibge": 3509502,
            },
        ]
    )
    path = silver_dir / "ibge_SP_2022.parquet"
    df.to_parquet(path, index=False)
    return silver_dir


@pytest.fixture()
def seguranca_parquet(silver_dir: Path) -> Path:
    silver_dir.mkdir(exist_ok=True)
    df = pd.DataFrame(
        [
            {
                "sg_uf": "SP",
                "ano": 2022,
                "cd_municipio_ibge": 3550308,
                "nm_municipio": "São Paulo",
                "taxa_homicidio": 8.2,
                "ivs_valor": 0.312,
                "n_ocorrencias": 1200,
            }
        ]
    )
    path = silver_dir / "seguranca_municipal_sp_2022.parquet"
    df.to_parquet(path, index=False)
    return silver_dir


@pytest.fixture()
def saude_parquet(silver_dir: Path) -> Path:
    silver_dir.mkdir(exist_ok=True)
    df = pd.DataFrame(
        [
            {
                "sg_uf": "SP",
                "ano": 2022,
                "cd_municipio_ibge": 3550308,
                "nm_municipio": "São Paulo",
                "tx_mortalidade_infantil": 9.4,
                "cobertura_esf_pct": 0.62,
            }
        ]
    )
    path = silver_dir / "saude_municipal_sp_2022.parquet"
    df.to_parquet(path, index=False)
    return silver_dir


@pytest.fixture()
def pesquisa_parquet(silver_dir: Path) -> Path:
    silver_dir.mkdir(exist_ok=True)
    df = pd.DataFrame(
        [
            {
                "uf": "BR",
                "candidato": "LULA",
                "instituto": "Datafolha",
                "cd_cargo": 1,
                "data_pesquisa_inicio": pd.Timestamp("2026-03-10"),
                "intencao_pct": 45.0,
                "intencao_ajustada": 44.0,
                "house_effect": -1.0,
                "margem_erro": 2.0,
                "tipo_pesquisa": "corrente",
                "record_confidence_score": 1.0,
                "poll_id": "DF-001",
                "ano": 2026,
            },
            {
                "uf": "BR",
                "candidato": "BOLSONARO",
                "instituto": "Datafolha",
                "cd_cargo": 1,
                "data_pesquisa_inicio": pd.Timestamp("2026-03-10"),
                "intencao_pct": 38.0,
                "intencao_ajustada": 39.0,
                "house_effect": 1.0,
                "margem_erro": 2.0,
                "tipo_pesquisa": "corrente",
                "record_confidence_score": 1.0,
                "poll_id": "DF-002",
                "ano": 2026,
            },
        ]
    )
    path = silver_dir / "fact_pesquisa_2026.parquet"
    df.to_parquet(path, index=False)
    return silver_dir


# ── Import helpers ─────────────────────────────────────────────────────────────


def _import_locals():
    from ui.dashboard_api import (
        _local_pesquisas,
        _local_saude,
        _local_seguranca,
        _local_socioeconomico,
    )

    return _local_socioeconomico, _local_seguranca, _local_saude, _local_pesquisas


# ── _local_socioeconomico ─────────────────────────────────────────────────────


def test_local_socioeconomico_returns_list(ibge_parquet):
    _local_socioeconomico, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", ibge_parquet):
        result = _local_socioeconomico("SP", 2022, 20)
    assert isinstance(result, list)
    assert len(result) == 2


def test_local_socioeconomico_schema(ibge_parquet):
    _local_socioeconomico, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", ibge_parquet):
        result = _local_socioeconomico("SP", 2022, 20)
    row = result[0]
    for key in (
        "nm",
        "idhm",
        "renda_per_capita",
        "gini",
        "pct_extrema_pobreza",
        "taxa_analfabetismo",
        "pct_urbano",
        "populacao",
    ):
        assert key in row, f"missing key: {key}"


def test_local_socioeconomico_filters_uf(ibge_parquet):
    _local_socioeconomico, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", ibge_parquet):
        result = _local_socioeconomico("RJ", 2022, 20)
    assert result == []


def test_local_socioeconomico_filters_ano(ibge_parquet):
    _local_socioeconomico, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", ibge_parquet):
        result = _local_socioeconomico("SP", 2020, 20)
    assert result == []


def test_local_socioeconomico_limit(ibge_parquet):
    _local_socioeconomico, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", ibge_parquet):
        result = _local_socioeconomico("SP", 2022, 1)
    assert len(result) == 1


def test_local_socioeconomico_fraction_conversion(ibge_parquet):
    _local_socioeconomico, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", ibge_parquet):
        result = _local_socioeconomico("SP", 2022, 20)
    # pct_extrema_pobreza=0.03 → should be 3.0 after *100
    assert result[0]["pct_extrema_pobreza"] == pytest.approx(3.0, abs=0.1)


def test_local_socioeconomico_empty_dir(tmp_path):
    _local_socioeconomico, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", tmp_path / "silver"):
        result = _local_socioeconomico("SP", 2022, 20)
    assert result == []


# ── _local_seguranca ──────────────────────────────────────────────────────────


def test_local_seguranca_returns_list(seguranca_parquet):
    _, _local_seguranca, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", seguranca_parquet):
        result = _local_seguranca("SP", 2022, 15)
    assert isinstance(result, list)
    assert len(result) == 1


def test_local_seguranca_schema(seguranca_parquet):
    _, _local_seguranca, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", seguranca_parquet):
        result = _local_seguranca("SP", 2022, 15)
    row = result[0]
    for key in (
        "nm",
        "taxa_homicidio",
        "ivs_total",
        "ivs_infra",
        "ivs_capital_humano",
        "ivs_renda",
        "taxa_roubo",
        "qt_feminicidio",
    ):
        assert key in row, f"missing key: {key}"


def test_local_seguranca_maps_bronze_columns(seguranca_parquet):
    """taxa_homicidio (Bronze) maps to taxa_homicidio field without _100k suffix."""
    _, _local_seguranca, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", seguranca_parquet):
        result = _local_seguranca("SP", 2022, 15)
    assert result[0]["taxa_homicidio"] == pytest.approx(8.2, abs=0.1)


def test_local_seguranca_empty(tmp_path):
    _, _local_seguranca, *_ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", tmp_path / "silver"):
        result = _local_seguranca("SP", 2022, 15)
    assert result == []


# ── _local_saude ──────────────────────────────────────────────────────────────


def test_local_saude_returns_list(saude_parquet):
    _, _, _local_saude, _ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", saude_parquet):
        result = _local_saude("SP", 2022, 15)
    assert isinstance(result, list)
    assert len(result) == 1


def test_local_saude_schema(saude_parquet):
    _, _, _local_saude, _ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", saude_parquet):
        result = _local_saude("SP", 2022, 15)
    row = result[0]
    for key in (
        "nm",
        "tx_mortalidade_infantil",
        "tx_mortalidade_materna",
        "pct_cobertura_plano",
        "idsus",
    ):
        assert key in row, f"missing key: {key}"


def test_local_saude_maps_bronze_columns(saude_parquet):
    _, _, _local_saude, _ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", saude_parquet):
        result = _local_saude("SP", 2022, 15)
    assert result[0]["tx_mortalidade_infantil"] == pytest.approx(9.4, abs=0.1)
    # cobertura_esf_pct=0.62 → 62.0%
    assert result[0]["pct_cobertura_plano"] == pytest.approx(62.0, abs=0.5)


def test_local_saude_empty(tmp_path):
    _, _, _local_saude, _ = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", tmp_path / "silver"):
        result = _local_saude("SP", 2022, 15)
    assert result == []


# ── _local_pesquisas ──────────────────────────────────────────────────────────


def test_local_pesquisas_returns_dict(pesquisa_parquet):
    *_, _local_pesquisas = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", pesquisa_parquet):
        result = _local_pesquisas("Presidente", "BR", 2026, "corrente")
    assert "series" in result
    assert "house_effects" in result
    assert "tipo" in result


def test_local_pesquisas_candidatos(pesquisa_parquet):
    *_, _local_pesquisas = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", pesquisa_parquet):
        result = _local_pesquisas("Presidente", "BR", 2026, "corrente")
    candidatos = {s["candidato"] for s in result["series"]}
    assert "LULA" in candidatos
    assert "BOLSONARO" in candidatos


def test_local_pesquisas_pontos_schema(pesquisa_parquet):
    *_, _local_pesquisas = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", pesquisa_parquet):
        result = _local_pesquisas("Presidente", "BR", 2026, "corrente")
    for s in result["series"]:
        for pt in s["pontos"]:
            for key in ("data", "instituto", "intencao", "ajustada", "tipo"):
                assert key in pt, f"missing key: {key}"


def test_local_pesquisas_house_effects(pesquisa_parquet):
    *_, _local_pesquisas = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", pesquisa_parquet):
        result = _local_pesquisas("Presidente", "BR", 2026, "corrente")
    assert len(result["house_effects"]) >= 1
    assert "instituto" in result["house_effects"][0]
    assert "house_effect" in result["house_effects"][0]


def test_local_pesquisas_filters_tipo(pesquisa_parquet):
    *_, _local_pesquisas = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", pesquisa_parquet):
        result = _local_pesquisas("Presidente", "BR", 2026, "historica")
    assert result["series"] == []


def test_local_pesquisas_filters_cd_cargo(pesquisa_parquet):
    *_, _local_pesquisas = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", pesquisa_parquet):
        result = _local_pesquisas("Governador", "BR", 2026, "corrente")
    assert result["series"] == []


def test_local_pesquisas_empty(tmp_path):
    *_, _local_pesquisas = _import_locals()
    with patch("ui.dashboard_api._LOCAL_SILVER_DIR", tmp_path / "silver"):
        result = _local_pesquisas("Presidente", "BR", 2026, "corrente")
    assert result == {"series": [], "house_effects": [], "tipo": "corrente"}
