"""Test: end-to-end pipeline validation with mock data (Bronze → Silver → Gold)."""
import pandas as pd
import pytest
from unittest.mock import patch
import tempfile
import shutil


class TestPipelineE2EMock:
    """AT-004: Validar pipeline end-to-end: Bronze → Silver → Gold com 1 UF mock."""

    @pytest.fixture
    def mock_tse_data(self):
        """Mock TSE data for SP 2022."""
        return pd.DataFrame({
            "SG_UF": ["SP", "SP", "SP"],
            "CD_MUNICIPIO": [35001, 35001, 35002],
            "NM_MUNICIPIO": ["São Paulo", "São Paulo", "Campinas"],
            "CD_CARGO": [1, 1, 3],  # Presidente, Presidente, Governador
            "DS_CARGO": ["Presidente", "Presidente", "Governador"],
            "NR_CANDIDATO": [1, 2, 5],
            "NM_CANDIDATO": ["Candidato A", "Candidato B", "Candidato C"],
            "QT_VOTOS": [100000, 200000, 150000],
            "NR_TURNO": [1, 1, 1],
        })

    @pytest.fixture
    def mock_ibge_data(self):
        """Mock IBGE contextual data (indexed by IBGE municipality code only)."""
        return pd.DataFrame({
            "cd_municipio_ibge": ["3500105", "3500204"],  # 7-digit IBGE code
            "idhm_valor": [0.805, 0.800],
            "renda_media": [2500, 2400],
            "populacao": [11450000, 1223000],
        })

    @pytest.fixture
    def temp_data_dir(self):
        """Create temp directory for test data."""
        tmpdir = tempfile.mkdtemp(prefix="spepe_test_")
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.fixture
    def mock_depara(self):
        """Mock de-para table (TSE ↔ IBGE municipality codes)."""
        return pd.DataFrame({
            "cd_municipio_tse": [35001, 35002],
            "cd_municipio_ibge": ["3500105", "3500204"],
            "nm_municipio": ["São Paulo", "Campinas"],
            "sg_uf": ["SP", "SP"],
        })

    def test_bronze_write_and_read(self, mock_tse_data, temp_data_dir):
        """Step 1: Bronze layer — write and read parquet."""
        from dataops.bronze_writer import write_bronze

        # Write to Bronze
        path = write_bronze(
            df=mock_tse_data,
            source="tse",
            year=2022,
            uf="SP",
            filename="tse_resultados_SP_2022.parquet",
            use_gcs=False,
            base_path=temp_data_dir,
        )

        # Read back
        bronze_df = pd.read_parquet(path)

        # Validate
        assert len(bronze_df) == 3, "Bronze data row count mismatch"
        assert "QT_VOTOS" in bronze_df.columns or "qt_votos" in bronze_df.columns
        assert bronze_df["SG_UF"].unique()[0] in ["SP", "sp"]

    def test_silver_normalization(self, mock_tse_data):
        """Step 2: Silver layer — normalize TSE columns."""
        from dataops.clients.tse_client import normalize_columns

        normalized = normalize_columns(mock_tse_data, year=2022)

        # Validate normalization
        assert "sg_uf" in normalized.columns
        assert "cd_cargo" in normalized.columns
        assert "qt_votos" in normalized.columns
        assert normalized["sg_uf"].iloc[0].upper() == "SP"

    @patch("dataops.depara_municipios.load_depara")
    def test_silver_with_ibge_join(self, mock_load_depara, mock_tse_data, mock_ibge_data, mock_depara):
        """Step 3: Silver layer — join TSE + IBGE."""
        from dataops.clients.tse_client import normalize_columns
        from dataops.depara_municipios import join_tse_ibge

        # Mock the de-para table
        mock_load_depara.return_value = mock_depara

        # Normalize TSE
        tse_norm = normalize_columns(mock_tse_data, year=2022)

        # Join with IBGE
        joined = join_tse_ibge(tse_norm, mock_ibge_data)

        # Validate join
        assert len(joined) > 0, "Join resulted in empty dataframe"
        assert "cod_municipio_ibge" in joined.columns or "cd_municipio_ibge" in joined.columns
        if "cod_municipio_ibge" in joined.columns:
            match_pct = joined["cod_municipio_ibge"].notna().mean() * 100
            assert match_pct >= 50, f"IBGE match too low: {match_pct:.0f}%"

    @patch("dataops.depara_municipios.load_depara")
    def test_gold_aggregation(self, mock_load_depara, mock_tse_data, mock_ibge_data, mock_depara):
        """Step 4: Gold layer — aggregate into facts."""
        from dataops.clients.tse_client import normalize_columns
        from dataops.depara_municipios import join_tse_ibge
        from dataops.gold_builder import _build_fact_municipio_eleicao

        mock_load_depara.return_value = mock_depara

        # Full pipeline: TSE → normalize → join IBGE
        tse_norm = normalize_columns(mock_tse_data, year=2022)
        silver = join_tse_ibge(tse_norm, mock_ibge_data)

        # Build Gold fact table
        fact = _build_fact_municipio_eleicao(silver)

        # Validate Gold
        assert len(fact) > 0, "Gold table is empty"
        assert "sg_uf" in fact.columns
        assert "ano_eleicao" in fact.columns or "ano_eleição" in fact.columns.map(str.lower())

        # Multi-cargo support
        if "cd_cargo" in fact.columns:
            cargos_in_fact = fact["cd_cargo"].nunique()
            assert cargos_in_fact > 0, "cd_cargo column is empty"

    @patch("dataops.depara_municipios.load_depara")
    def test_pipeline_idempotency(self, mock_load_depara, mock_tse_data, mock_ibge_data, mock_depara):
        """Step 5: Pipeline — running twice produces same output."""
        from dataops.clients.tse_client import normalize_columns
        from dataops.depara_municipios import join_tse_ibge
        from dataops.gold_builder import _build_fact_municipio_eleicao

        mock_load_depara.return_value = mock_depara

        tse_norm = normalize_columns(mock_tse_data, year=2022)
        silver = join_tse_ibge(tse_norm, mock_ibge_data)

        # Build twice
        fact1 = _build_fact_municipio_eleicao(silver)
        fact2 = _build_fact_municipio_eleicao(silver)

        # Compare
        assert len(fact1) == len(fact2), "Different row counts on second run"
        assert fact1.shape == fact2.shape, "Different shapes on second run"

    def test_dq_score_calculation(self, mock_tse_data):
        """Step 6: Data Quality — score validation."""
        from dataops.silver_transformer import _run_dq_checks

        # Run DQ checks on mock data
        dq_result = _run_dq_checks(mock_tse_data, "SP", 2022)

        # Validate DQ result
        assert "score" in dq_result
        assert "warnings" in dq_result
        assert 0 <= dq_result["score"] <= 100, f"Invalid DQ score: {dq_result['score']}"
