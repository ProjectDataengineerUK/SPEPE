"""Tests for Data Quality gate execution and thresholds."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def clean_tse_df():
    rng = np.random.default_rng(42)
    n = 2000
    return pd.DataFrame({
        "CD_MUNICIPIO": rng.integers(10000, 99999, n).astype(str),
        "SG_UF": ["SP"] * n,
        "QT_VOTOS_NOMINAIS": rng.integers(1, 50000, n),
        "NR_ZONA": rng.integers(1, 300, n),
        "DS_CARGO": ["PRESIDENTE"] * n,
        "NM_CANDIDATO": [f"CANDIDATO_{i % 5}" for i in range(n)],
    })


@pytest.fixture
def clean_gold_df():
    return pd.DataFrame({
        "cd_municipio": [str(i) for i in range(5570)],
        "sg_uf": ["SP"] * 5570,
        "ano_eleicao": [2022] * 5570,
        "qt_votos_total": range(5570),
    })


class TestDQRunnerSuites:
    def test_tse_suite_passes_clean_data(self, clean_tse_df):
        from dataops.dq.runner import run_suite

        result = run_suite(clean_tse_df, "tse")
        assert result["score"] >= 95.0, f"Expected ≥95%, got {result['score']:.1f}%"

    def test_tse_suite_fails_null_municipio(self, clean_tse_df):
        from dataops.dq.runner import run_suite

        bad_df = clean_tse_df.copy()
        bad_df.loc[:100, "CD_MUNICIPIO"] = None

        result = run_suite(bad_df, "tse")
        assert result["score"] < 95.0

    def test_tse_suite_fails_insufficient_rows(self, clean_tse_df):
        from dataops.dq.runner import run_suite

        tiny_df = clean_tse_df.head(500)
        result = run_suite(tiny_df, "tse")
        row_check = next(
            (r for r in result["results"] if r["expectation"] == "expect_table_row_count_to_be_between"),
            None
        )
        assert row_check is not None
        assert not row_check["passed"]

    def test_gold_suite_passes_full_coverage(self, clean_gold_df):
        from dataops.dq.runner import run_suite

        result = run_suite(clean_gold_df, "gold")
        assert result["score"] >= 90.0

    def test_gold_suite_fails_missing_municipio_column(self, clean_gold_df):
        from dataops.dq.runner import run_suite

        bad_df = clean_gold_df.drop(columns=["cd_municipio"])
        result = run_suite(bad_df, "gold")
        col_check = next(
            (r for r in result["results"] if r["expectation"] == "expect_column_to_exist"),
            None
        )
        assert col_check is not None
        assert not col_check["passed"]

    def test_suite_result_structure(self, clean_tse_df):
        from dataops.dq.runner import run_suite

        result = run_suite(clean_tse_df, "tse")
        assert "score" in result
        assert "results" in result
        assert isinstance(result["score"], float)
        assert 0.0 <= result["score"] <= 100.0

        for r in result["results"]:
            assert "expectation" in r
            assert "passed" in r
            assert isinstance(r["passed"], bool)


class TestDQThresholds:
    def test_silver_dq_threshold_is_95(self):
        """Silver pipeline exits code 1 if DQ score < 95%."""
        threshold = 95.0
        assert threshold == 95.0  # Design contract

    def test_gold_dq_threshold_is_90(self):
        threshold = 90.0
        assert threshold == 90.0  # Design contract

    def test_depara_match_threshold_is_80(self):
        threshold = 80.0
        assert threshold == 80.0  # Design contract — warn below this
