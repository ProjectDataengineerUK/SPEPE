"""Tests for MLOps Level 5: drift→retrain, canary, rollback, HP tuning, prediction store, bias."""
import os
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest


class TestDriftMonitor:
    def test_js_divergence_identical_distributions(self):
        from mlops.monitoring.drift_monitor import _js_divergence

        rng = np.random.default_rng(42)
        data = rng.normal(0, 1, 500)
        score = _js_divergence(data, data.copy())
        assert score < 0.01, "Identical distributions should have near-zero JS divergence"

    def test_js_divergence_different_distributions(self):
        from mlops.monitoring.drift_monitor import _js_divergence

        rng = np.random.default_rng(42)
        p = rng.normal(0, 1, 500)
        q = rng.normal(5, 1, 500)  # Very different mean
        score = _js_divergence(p, q)
        assert score > 0.05, "Clearly different distributions should have high JS divergence"

    def test_js_divergence_bounded(self):
        from mlops.monitoring.drift_monitor import _js_divergence

        rng = np.random.default_rng(42)
        p = rng.normal(0, 1, 200)
        q = rng.uniform(0, 1, 200)
        score = _js_divergence(p, q)
        assert 0.0 <= score <= 1.0

    def test_detect_drift_no_drift(self):
        from mlops.monitoring.drift_monitor import detect_drift, drift_summary

        rng = np.random.default_rng(42)
        n = 300
        ref = pd.DataFrame({
            "idhm_2010": rng.normal(0.7, 0.05, n),
            "renda_media_domiciliar": rng.normal(1500, 200, n),
        })
        cur = pd.DataFrame({
            "idhm_2010": rng.normal(0.7, 0.05, n),
            "renda_media_domiciliar": rng.normal(1500, 200, n),
        })

        with patch("mlops.monitoring.drift_monitor._load_config") as mock_cfg:
            mock_cfg.return_value = {
                "js_divergence_threshold": 0.10,
                "categorical_threshold": 0.05,
                "check_features": ["idhm_2010", "renda_media_domiciliar"],
            }
            results = detect_drift(ref, cur, publish=False)

        summary = drift_summary(results)
        assert not summary["drift_detected"]

    def test_detect_drift_triggers_on_shift(self):
        from mlops.monitoring.drift_monitor import detect_drift, drift_summary

        rng = np.random.default_rng(42)
        n = 300
        ref = pd.DataFrame({"idhm_2010": rng.normal(0.5, 0.03, n)})
        cur = pd.DataFrame({"idhm_2010": rng.normal(0.9, 0.03, n)})  # Huge shift

        with patch("mlops.monitoring.drift_monitor._load_config") as mock_cfg:
            mock_cfg.return_value = {
                "js_divergence_threshold": 0.10,
                "categorical_threshold": 0.05,
                "check_features": ["idhm_2010"],
            }
            results = detect_drift(ref, cur, publish=False)

        summary = drift_summary(results)
        assert summary["drift_detected"]
        assert "idhm_2010" in summary["drifted_names"]


class TestPubSubPublisher:
    def test_fallback_log_on_no_gcp(self, tmp_path):
        from mlops.monitoring.pubsub_publisher import _fallback_log

        with patch("mlops.monitoring.pubsub_publisher.Path") as mock_path_cls:
            fallback_path = tmp_path / "drift_events.jsonl"
            mock_path_cls.return_value = fallback_path
            fallback_path.parent.mkdir(parents=True, exist_ok=True)

            _fallback_log({"feature": "idhm_2010", "js_divergence": 0.15})

    def test_publish_returns_none_without_credentials(self):
        from mlops.monitoring.pubsub_publisher import publish_drift_event

        with patch("mlops.monitoring.pubsub_publisher._get_publisher", return_value=None):
            result = publish_drift_event("idhm_2010", 0.15, 0.10, project_id="spepe-test")
        assert result is None


class TestBiasMonitor:
    @pytest.fixture
    def predictions_df(self):
        rng = np.random.default_rng(42)
        n = 200
        ufs = ["SP", "MG", "RJ", "BA", "RS"] * (n // 5)
        return pd.DataFrame({
            "y_true": rng.integers(0, 2, n).astype(float),
            "y_pred": rng.uniform(0.1, 0.9, n),
            "sg_uf": ufs,
            "renda_media_domiciliar": rng.uniform(800, 3000, n),
            "pct_zona_rural": rng.uniform(0, 80, n),
        })

    def test_bias_monitor_returns_results(self, predictions_df):
        from mlops.monitoring.bias_monitor import run_bias_monitor

        with patch("mlops.monitoring.bias_monitor._export_to_bigquery"):
            results = run_bias_monitor(predictions_df, model_version="v1.0")

        assert len(results) > 0

    def test_bias_result_structure(self, predictions_df):
        from mlops.monitoring.bias_monitor import run_bias_monitor, BiasResult

        with patch("mlops.monitoring.bias_monitor._export_to_bigquery"):
            results = run_bias_monitor(predictions_df, model_version="v1.0")

        for r in results:
            assert isinstance(r, BiasResult)
            assert r.group_type in ("sg_uf", "income_quintile", "rural_urban")
            assert 0.0 <= r.brier_score <= 1.0
            assert r.ratio > 0.0

    def test_bias_alert_triggers_on_high_ratio(self):
        from mlops.monitoring.bias_monitor import BiasResult

        r = BiasResult(
            model_version="v1.0",
            group_type="sg_uf",
            group_value="AC",
            brier_score=0.35,
            global_brier=0.20,
            ratio=1.75,
            alert_triggered=True,
            n_samples=50,
        )
        assert r.alert_triggered

    def test_missing_columns_returns_empty(self):
        from mlops.monitoring.bias_monitor import run_bias_monitor

        bad_df = pd.DataFrame({"y_true": [0, 1], "y_pred": [0.3, 0.7]})
        with patch("mlops.monitoring.bias_monitor._export_to_bigquery"):
            results = run_bias_monitor(bad_df, model_version="v1.0")
        assert results == []


class TestPredictionStore:
    def test_prediction_record_to_bq_row(self):
        from mlops.prediction_store import PredictionRecord

        record = PredictionRecord(
            candidato="Lula",
            p_mean=0.52,
            p_lower=0.48,
            p_upper=0.56,
            model_version="v1.0",
            session_id="test-session-001",
            sg_uf="SP",
        )
        row = record.to_bq_row()

        assert row["candidato"] == "Lula"
        assert row["p_mean"] == 0.52
        assert row["actual_result"] is None
        assert "prediction_id" in row
        assert "prediction_date" in row

    def test_prediction_record_features_hash(self):
        from mlops.prediction_store import PredictionRecord

        features = {"idhm": 0.75, "renda": 1500.0}
        r1 = PredictionRecord("X", 0.5, 0.4, 0.6, "v1", "s1", features=features)
        r2 = PredictionRecord("X", 0.5, 0.4, 0.6, "v1", "s1", features=features)
        assert r1.to_bq_row()["features_hash"] == r2.to_bq_row()["features_hash"]

    def test_store_prediction_no_project_returns_false(self):
        from mlops.prediction_store import PredictionRecord, store_prediction

        record = PredictionRecord("Y", 0.4, 0.3, 0.5, "v1", "s1")
        with patch.dict(os.environ, {}, clear=True):
            result = store_prediction(record, project_id=None)
        assert result is False


class TestAutoRollback:
    def test_rollback_decision_on_high_brier(self):
        from mlops.deployment.auto_rollback import evaluate_canary, CanaryState

        state = CanaryState(
            service="spepe-prod",
            champion_revision="rev-champion",
            challenger_revision="rev-challenger",
            project_id="spepe-test",
            region="southamerica-east1",
        )

        with patch("mlops.deployment.auto_rollback._fetch_brier_score") as mock_brier:
            mock_brier.side_effect = lambda rev, *args, **kwargs: (
                0.30 if rev == "rev-challenger" else 0.20
            )
            decision = evaluate_canary(state)

        assert decision.action == "rollback"
        assert decision.ratio > 1.05

    def test_promote_decision_on_lower_brier(self):
        from mlops.deployment.auto_rollback import evaluate_canary, CanaryState

        state = CanaryState(
            service="spepe-prod",
            champion_revision="rev-champion",
            challenger_revision="rev-challenger",
            project_id="spepe-test",
            region="southamerica-east1",
        )

        with patch("mlops.deployment.auto_rollback._fetch_brier_score") as mock_brier:
            mock_brier.side_effect = lambda rev, *args, **kwargs: (
                0.18 if rev == "rev-challenger" else 0.20
            )
            decision = evaluate_canary(state)

        assert decision.action == "promote"
        assert decision.ratio < 1.05

    def test_continue_on_insufficient_data(self):
        from mlops.deployment.auto_rollback import evaluate_canary, CanaryState

        state = CanaryState(
            service="spepe-prod",
            champion_revision="rev-champion",
            challenger_revision="rev-challenger",
            project_id="spepe-test",
            region="southamerica-east1",
        )

        with patch("mlops.deployment.auto_rollback._fetch_brier_score", return_value=None):
            decision = evaluate_canary(state)

        assert decision.action == "continue"


class TestPromoteV2:
    def test_saves_challenger_not_champion(self, tmp_path):
        from mlops.components import promote as promote_mod
        from sklearn.linear_model import LogisticRegression

        with patch.object(promote_mod, "MODEL_DIR", tmp_path):
            with patch.object(promote_mod, "CHAMPION_PATH", tmp_path / "champion.pkl"):
                with patch.object(promote_mod, "CHAMPION_META_PATH", tmp_path / "champion_meta.json"):
                    with patch.object(promote_mod, "CHALLENGER_PATH", tmp_path / "challenger.pkl"):
                        with patch.object(promote_mod, "CHALLENGER_META_PATH", tmp_path / "challenger_meta.json"):
                            model = LogisticRegression()
                            result = promote_mod.promote_if_better(
                                model,
                                {"brier_score": 0.15},
                                metric_key="brier_score",
                                higher_is_better=False,
                                save_as_challenger=True,
                            )

        assert result is True
        assert (tmp_path / "challenger.pkl").exists()
        assert not (tmp_path / "champion.pkl").exists()
