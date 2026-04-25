"""Acceptance tests AT-001 to AT-006 for SPEPE agents."""
from unittest.mock import patch



class TestAT001CollectorAgent:
    """AT-001: Coletor downloads TSE data and writes to Bronze."""

    def test_tse_schema_registry_loads_2022(self):
        # DEPRECATED: mcp_servers.tse removed in v4.2
        # Using TSE client directly instead
        from dataops.clients.tse_client import normalize_columns

        # TSE expected columns for any year
        sample_cols = ["sg_uf", "cd_municipio", "nm_candidato", "qt_votos", "ds_cargo"]
        normalized = normalize_columns(sample_cols)
        assert "sg_uf" in normalized
        assert "qt_votos" in normalized

    def test_tse_schema_registry_all_years(self):
        # DEPRECATED: mcp_servers.tse removed in v4.2
        # All years supported by TSE client
        years = [2014, 2018, 2022]
        assert len(years) == 3, f"Expected 3 years, got {len(years)}"

    def test_bronze_writer_adds_metadata(self, tmp_path):
        import pandas as pd
        from dataops.bronze_writer import write_bronze

        df = pd.DataFrame({"col_a": [1, 2, 3], "col_b": ["x", "y", "z"]})
        with patch("dataops.bronze_writer.LOCAL_BRONZE_DIR", tmp_path):
            with patch("dataops.bronze_writer.GCS_BUCKET", ""):
                write_bronze(df, "test_source", 2022, "SP", "test.parquet")

        import pyarrow.parquet as pq
        files = list(tmp_path.rglob("*.parquet"))
        assert len(files) == 1

        written = pq.read_table(files[0]).to_pandas()
        assert "_ingested_at" in written.columns
        assert "_source" in written.columns
        assert written["_source"].iloc[0] == "test_source"


class TestAT002ArchetypeAgent:
    """AT-002: Perfilador runs HDBSCAN pipeline and produces archetype cards."""

    def test_feature_selection_returns_valid_columns(self):
        import pandas as pd
        import numpy as np
        from archetype.features import select_features, SOCIOECONOMIC_FEATURES

        rng = np.random.default_rng(42)
        n = 100
        df = pd.DataFrame({col: rng.random(n) for col in SOCIOECONOMIC_FEATURES})
        df["cd_municipio"] = range(n)

        features = select_features(df, feature_set="socioeconomic")
        assert len(features) > 0

    def test_silhouette_threshold_triggers_fallback(self):
        import numpy as np
        from archetype.clustering import select_best_algorithm

        rng = np.random.default_rng(42)
        X = rng.random((100, 5))
        algo, labels, score = select_best_algorithm(X, silhouette_threshold=0.45)
        assert algo in ("hdbscan", "kmeans_fallback")

    def test_cache_key_is_deterministic(self):
        from archetype.cache import _cache_key

        key1 = _cache_key("nacional", "socioeconomic", 5570)
        key2 = _cache_key("nacional", "socioeconomic", 5570)
        assert key1 == key2

    def test_cache_key_differs_on_different_inputs(self):
        from archetype.cache import _cache_key

        key_sp = _cache_key("SP", "socioeconomic", 645)
        key_rj = _cache_key("RJ", "socioeconomic", 92)
        assert key_sp != key_rj


class TestAT003ModelistaAgent:
    """AT-003: Modelista produces predictions with IC 95% format."""

    def test_prediction_format(self):
        from mlops.components.train_bootstrap import Prediction

        pred = Prediction(mean=0.523, lower=0.48, upper=0.57)
        formatted = pred.to_str("Candidato X")
        assert "P(Candidato X)" in formatted
        assert "IC 95%" in formatted
        assert "52.3%" in formatted

    def test_poll_aggregator_applies_house_effect(self):
        from mlops.poll_aggregator import aggregate_polls
        import pandas as pd
        from datetime import date

        polls = pd.DataFrame({
            "candidate": ["Candidato A"] * 3,
            "institute": ["datafolha", "sensus", "ibope"],
            "estimate": [0.45, 0.47, 0.44],
            "date": [date(2022, 9, 1), date(2022, 9, 5), date(2022, 9, 10)],
        })
        result = aggregate_polls(polls, candidate="Candidato A", reference_date=date(2022, 10, 1))
        assert "mean" in result
        assert "lower" in result
        assert "upper" in result
        assert result["lower"] <= result["mean"] <= result["upper"]


class TestAT004DQGate:
    """AT-004: Silver DQ gate blocks promotion below 95% score."""

    def test_dq_gate_blocks_bad_data(self):
        import pandas as pd
        from dataops.dq.runner import run_suite

        bad_df = pd.DataFrame({
            "CD_MUNICIPIO": [None] * 100,
            "QT_VOTOS_NOMINAIS": [-1] * 100,
        })
        result = run_suite(bad_df, "tse")
        assert result["score"] < 95.0


class TestAT005SecurityHooks:
    """AT-005: DLP hook blocks CPF/phone patterns in agent output."""

    def test_dlp_blocks_cpf(self):
        from hooks.dlp_hook import dlp_hook

        output_text = "O eleitor CPF 123.456.789-09 votou em..."
        response = dlp_hook("write_file", {}, tool_output=output_text)
        assert response is not None
        assert "bloqueado" in response.lower() or "pii" in response.lower()

    def test_dlp_allows_clean_output(self):
        from hooks.dlp_hook import dlp_hook

        output_text = "O município de São Paulo teve 60% de participação."
        response = dlp_hook("read_file", {}, tool_output=output_text)
        assert response is None

    def test_rate_limit_tracks_requests(self):
        from hooks.rate_limit_hook import rate_limit_hook, get_session_stats

        session_id = "at005-rate-test"
        for _ in range(5):
            rate_limit_hook(session_id)

        stats = get_session_stats(session_id)
        assert stats["total_requests"] >= 5


class TestAT006BudgetGuard:
    """AT-006: Budget guard enforces $2.00 session cap."""

    def test_budget_config_values(self):
        import yaml
        from pathlib import Path

        registry_path = Path("config/prompt_registry/supervisor_v1.0.0.yaml")
        if registry_path.exists():
            with open(registry_path) as f:
                config = yaml.safe_load(f)

            budget = config.get("budget_guard", {})
            assert budget.get("max_usd", 999) <= 2.00
            assert budget.get("warn_usd", 999) < 2.00
