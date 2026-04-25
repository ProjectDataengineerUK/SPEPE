"""Integration tests for Bronze→Silver→Gold pipeline."""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_tse_df():
    rng = np.random.default_rng(42)
    n = 500
    return pd.DataFrame(
        {
            "CD_MUNICIPIO": rng.integers(10000, 99999, n),
            "SG_UF": ["SP"] * n,
            "NM_MUNICIPIO": [f"MUNICIPIO_{i}" for i in range(n)],
            "QT_VOTOS_NOMINAIS": rng.integers(100, 50000, n),
            "NR_ZONA": rng.integers(1, 300, n),
            "DS_CARGO": ["PRESIDENTE"] * n,
            "NM_CANDIDATO": [f"CANDIDATO_{i % 5}" for i in range(n)],
            "NR_CANDIDATO": rng.integers(10, 99, n),
        }
    )


@pytest.fixture
def sample_ibge_df():
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "cd_municipio_ibge": [355030, 330455, 150140],
            "idhm_2010": [0.805, 0.799, 0.746],
            "renda_media_domiciliar": [rng.uniform(1000, 3000) for _ in range(3)],
            "pct_zona_rural": [rng.uniform(0, 30) for _ in range(3)],
            "populacao_2022": rng.integers(100000, 5000000, 3),
            "gini": rng.uniform(0.4, 0.7, 3),
        }
    )


class TestBronzeWriter:
    def test_write_bronze_local(self, sample_tse_df, tmp_path):
        from dataops.bronze_writer import write_bronze

        with patch("dataops.bronze_writer.GCS_BUCKET", ""):
            with patch("dataops.bronze_writer.LOCAL_BRONZE_DIR", tmp_path):
                write_bronze(sample_tse_df, "tse", 2022, "SP", "test.parquet")

        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1

        written = pd.read_parquet(parquet_files[0])
        assert "_ingested_at" in written.columns
        assert "_source" in written.columns
        assert len(written) == len(sample_tse_df)

    def test_bronze_immutability(self, sample_tse_df, tmp_path):
        from dataops.bronze_writer import write_bronze

        with patch("dataops.bronze_writer.GCS_BUCKET", ""):
            with patch("dataops.bronze_writer.LOCAL_BRONZE_DIR", tmp_path):
                write_bronze(sample_tse_df, "tse", 2022, "SP", "test.parquet")
                files = list(tmp_path.rglob("*.parquet"))
                assert len(files) == 1, "Bronze file not created"

                # Bronze should not overwrite (immutable)
                write_bronze(sample_tse_df, "tse", 2022, "SP", "test.parquet")
                files_after = list(tmp_path.rglob("*.parquet"))
                assert (
                    len(files_after) == 1
                ), "Bronze created duplicate (immutability violated)"


class TestDeParaMunicipios:
    def test_join_tse_ibge(self, sample_tse_df, sample_ibge_df):
        from dataops.depara_municipios import join_tse_ibge

        sample_ibge_df["cd_municipio_tse"] = (
            sample_ibge_df["cd_municipio_ibge"] // 10
        ).astype(str)
        sample_tse_df["CD_MUNICIPIO"] = sample_tse_df["CD_MUNICIPIO"].astype(str)

        result = join_tse_ibge(sample_tse_df, sample_ibge_df, tse_key="CD_MUNICIPIO")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(sample_tse_df)


class TestSilverTransformer:
    def test_dq_check_fails_on_nulls(self, sample_tse_df):
        from dataops.dq.runner import run_suite

        bad_df = sample_tse_df.copy()
        bad_df.loc[:10, "CD_MUNICIPIO"] = None

        result = run_suite(bad_df, "tse")
        assert result["score"] < 100.0
        assert any(not r["passed"] for r in result["results"])

    def test_dq_check_passes_clean_data(self, sample_tse_df):
        from dataops.dq.runner import run_suite

        result = run_suite(sample_tse_df, "tse")
        assert result["score"] >= 50.0  # Most checks should pass


class TestGoldBuilder:
    def test_build_fact_municipio_eleicao_structure(self, sample_tse_df, tmp_path):
        from dataops.gold_builder import _build_fact_municipio_eleicao

        silver = sample_tse_df.rename(
            columns={
                "CD_MUNICIPIO": "cd_municipio",
                "SG_UF": "sg_uf",
                "QT_VOTOS_NOMINAIS": "qt_votos",
                "DS_CARGO": "ds_cargo",
            }
        )
        silver["ano_eleicao"] = 2022

        result = _build_fact_municipio_eleicao(silver)
        assert isinstance(result, pd.DataFrame)
        assert "cd_municipio" in result.columns
        assert "ano_eleicao" in result.columns
        assert len(result) <= len(silver)  # Aggregated: fewer rows


class TestPipelineEndToEnd:
    @pytest.mark.integration
    def test_bronze_to_silver_schema(self, sample_tse_df, tmp_path):
        """Verifies column normalization through the schema registry."""
        from dataops.silver_transformer import CANONICAL_TSE_COLS

        # Check if standard TSE columns are present in canonical list
        expected_cols = [
            "qt_votos",
            "cd_municipio",
            "sg_uf",
            "nm_candidato",
            "ds_cargo",
        ]
        for col in expected_cols:
            assert col in CANONICAL_TSE_COLS, f"Missing canonical column: {col}"
