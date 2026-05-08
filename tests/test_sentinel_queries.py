"""Unit tests for ui.sentinel_queries (BigQuery / Cloud Run mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _classify rules — the heart of the status logic
# ---------------------------------------------------------------------------


class TestClassify:
    """Verify the row_count / freshness / dq_score → status mapping."""

    def setup_method(self) -> None:
        from ui.sentinel_queries import _classify

        self._classify = _classify

    def test_zero_rows_is_error(self) -> None:
        assert self._classify(0, 1.0) == "error"
        assert self._classify(0, None) == "error"

    def test_freshness_none_is_error(self) -> None:
        assert self._classify(100, None) == "error"

    def test_freshness_above_72h_is_error(self) -> None:
        assert self._classify(100, 80.0) == "error"

    def test_freshness_between_24_and_72h_is_warn(self) -> None:
        assert self._classify(100, 50.0) == "warn"
        assert self._classify(100, 24.1) == "warn"

    def test_low_dq_score_is_warn(self) -> None:
        assert self._classify(100, 5.0, dq_score=0.85) == "warn"

    def test_healthy_returns_ok(self) -> None:
        assert self._classify(100, 1.0) == "ok"
        assert self._classify(100, 1.0, dq_score=0.95) == "ok"

    def test_high_dq_does_not_override_freshness(self) -> None:
        assert self._classify(100, 50.0, dq_score=0.99) == "warn"


# ---------------------------------------------------------------------------
# Maturity score — AT-013 example values
# ---------------------------------------------------------------------------


class TestMaturityScore:
    """compute_maturity_score returns ints 0-100 and is monotonic."""

    def test_perfect_inputs_score_high(self) -> None:
        from ui.sentinel_queries import compute_maturity_score

        jobs = [{"status": "ok"}] * 20
        gold = [{"freshness_hours": 1.0, "dq_score": 0.99, "status": "ok"}] * 17
        silver = [{"freshness_hours": 1.0, "status": "ok"}] * 10
        mlops = {"brier_score": 0.05, "js_divergence": 0.02, "eval_score": 0.99}
        agents = [{"calls_24h": 100, "errors_24h": 0, "p99_latency_s": 1.0}] * 10

        m = compute_maturity_score(jobs, gold, silver, mlops, agents)
        # With brier=0.05 / drift=0.02 / eval=0.99 the formula caps near 89
        # for MLOps; thresholds reflect the formula bounds in Pattern 7.
        assert m["dataops"] >= 95
        assert m["mlops"] >= 85
        assert m["llmops"] >= 95
        assert isinstance(m["dataops"], int)

    def test_at013_example_approximates_design(self) -> None:
        """
        DEFINE AT-013: 15/20 jobs ok, DQ > 0.95, eval_runner 0.995
        → DataOps≈78, MLOps≈65, LLMOps≈82 (approx).
        """
        from ui.sentinel_queries import compute_maturity_score

        jobs = [{"status": "ok"}] * 15 + [{"status": "warn"}] * 5
        gold = [{"freshness_hours": 5.0, "dq_score": 0.95, "status": "ok"}] * 17
        silver = [{"freshness_hours": 30.0, "status": "warn"}] * 5
        mlops = {"brier_score": 0.18, "js_divergence": 0.10, "eval_score": 0.995}
        agents = [{"calls_24h": 50, "errors_24h": 1, "p99_latency_s": 2.0}] * 8 + [
            {"calls_24h": 0, "errors_24h": 0, "p99_latency_s": 0.0}
        ] * 2

        m = compute_maturity_score(jobs, gold, silver, mlops, agents)
        # Expected target ~78 / ~65 / ~82 — allow ±15 tolerance to keep the
        # test robust against weight tuning.
        assert 60 <= m["dataops"] <= 95
        assert 50 <= m["mlops"] <= 80
        assert 70 <= m["llmops"] <= 95

    def test_clamped_to_0_100(self) -> None:
        from ui.sentinel_queries import compute_maturity_score

        m = compute_maturity_score([], [], [], {}, [])
        for k in ("dataops", "mlops", "llmops"):
            assert 0 <= m[k] <= 100

    def test_high_drift_collapses_mlops(self) -> None:
        from ui.sentinel_queries import compute_maturity_score

        mlops = {"brier_score": 0.30, "js_divergence": 0.30, "eval_score": 0.0}
        m = compute_maturity_score([], [], [], mlops, [])
        assert m["mlops"] == 0


# ---------------------------------------------------------------------------
# query_gold_storage / query_silver_storage / query_views_existence — mocked
# ---------------------------------------------------------------------------


def _make_bq_mock(rows: list[dict]) -> MagicMock:
    client = MagicMock()
    job = MagicMock()
    job.result.return_value = iter([_FakeRow(r) for r in rows])
    client.query.return_value = job
    return client


class _FakeRow:
    """Mimic google.cloud.bigquery.Row enough for our code paths."""

    def __init__(self, d: dict):
        self._d = d

    def __getitem__(self, k):
        return self._d[k]

    def keys(self):
        return self._d.keys()


class TestQueryGoldStorage:
    def test_existing_table_status_ok(self) -> None:
        from ui import sentinel_queries

        rows = [
            {
                "table_name": "fact_municipio_eleicao",
                "total_rows": 2_400_000,
                "total_logical_bytes": 50_000_000,
                "freshness_hours": 12,
                "storage_last_modified_time": "2026-05-08T12:00:00",
            }
        ]
        with patch.object(sentinel_queries, "_bq", return_value=_make_bq_mock(rows)):
            out = sentinel_queries.query_gold_storage()
        match = [t for t in out if t["table"] == "fact_municipio_eleicao"][0]
        assert match["status"] == "ok"
        assert match["row_count"] == 2_400_000

    def test_missing_table_status_error(self) -> None:
        from ui import sentinel_queries

        with patch.object(sentinel_queries, "_bq", return_value=_make_bq_mock([])):
            out = sentinel_queries.query_gold_storage()
        # all 17 declared tables marked as missing -> error
        assert len(out) == len(sentinel_queries.GOLD_TABLES)
        assert all(t["status"] == "error" for t in out)
        assert all(t["alert_message"] for t in out)

    def test_zero_rows_status_error(self) -> None:
        from ui import sentinel_queries

        rows = [
            {
                "table_name": "fact_social_municipio",
                "total_rows": 0,
                "total_logical_bytes": 0,
                "freshness_hours": 1,
                "storage_last_modified_time": "2026-05-08T12:00:00",
            }
        ]
        with patch.object(sentinel_queries, "_bq", return_value=_make_bq_mock(rows)):
            out = sentinel_queries.query_gold_storage()
        match = [t for t in out if t["table"] == "fact_social_municipio"][0]
        assert match["status"] == "error"
        assert match["row_count"] == 0


class TestQuerySilverStorage:
    def test_silver_includes_freshness(self) -> None:
        from ui import sentinel_queries

        rows = [
            {
                "table_name": "tse_SP_2022",
                "total_rows": 10_000,
                "total_logical_bytes": 1_000_000,
                "freshness_hours": 2,
                "storage_last_modified_time": "2026-05-08T13:00:00",
            }
        ]
        with patch.object(sentinel_queries, "_bq", return_value=_make_bq_mock(rows)):
            out = sentinel_queries.query_silver_storage()
        assert len(out) == 1
        assert out[0]["status"] == "ok"
        assert out[0]["freshness_hours"] == 2.0


class TestQueryViewsExistence:
    def test_missing_view_marked_error(self) -> None:
        from ui import sentinel_queries

        # Simulate that only one view exists in BQ
        rows = [{"table_name": "vw_candidato_360"}]
        with patch.object(sentinel_queries, "_bq", return_value=_make_bq_mock(rows)):
            out = sentinel_queries.query_views_existence()
        present = [v for v in out if v["view"] == "vw_candidato_360"]
        missing = [v for v in out if v["view"] == "vw_social_crise_detector"]
        assert present and present[0]["status"] == "ok"
        assert missing and missing[0]["status"] == "error"
        assert missing[0]["alert_message"] == "view não encontrada no BQ"


# ---------------------------------------------------------------------------
# query_jobs_executions — mocked Cloud Run
# ---------------------------------------------------------------------------


class TestQueryJobsExecutions:
    def test_never_run_and_failed_jobs(self) -> None:
        """AT-005 + AT-006 — jobs nunca executados / com falha."""
        import sys
        import types

        # Build a stand-in google.cloud.run_v2 module so the lazy import
        # inside query_jobs_executions resolves even without the GCP SDK.
        run_v2_stub = types.ModuleType("google.cloud.run_v2")

        class _State:
            CONDITION_SUCCEEDED = "CONDITION_SUCCEEDED"

        class _Condition:
            State = _State

        run_v2_stub.Condition = _Condition  # type: ignore[attr-defined]

        from ui import sentinel_queries

        class FakeName:
            def __init__(self, full: str):
                self.name = full

        jobs_returned = [
            FakeName(
                f"projects/x/locations/southamerica-east1/jobs/{sentinel_queries.JOB_NAMES[0]}"
            ),
            FakeName(
                f"projects/x/locations/southamerica-east1/jobs/{sentinel_queries.JOB_NAMES[1]}"
            ),
        ]

        jobs_client_mock = MagicMock()
        jobs_client_mock.list_jobs.return_value = iter(jobs_returned)

        class FakeCondition:
            def __init__(self, state, reason="EXECUTION_FAILED", type_="Completed"):
                self.state = state
                self.reason = reason
                self.type_ = type_

        class FakeExec:
            def __init__(self, succeeded: bool):
                state = "CONDITION_SUCCEEDED" if succeeded else "FAILED"
                self.conditions = [FakeCondition(state)]
                self.completion_time = "2026-05-08T01:00:00"

        exec_client_mock = MagicMock()

        def list_executions(parent, page_size):  # noqa: ARG001
            if parent.endswith(sentinel_queries.JOB_NAMES[0]):
                return iter([])
            return iter([FakeExec(succeeded=False)])

        exec_client_mock.list_executions.side_effect = list_executions
        run_v2_stub.JobsClient = lambda: jobs_client_mock  # type: ignore[attr-defined]
        run_v2_stub.ExecutionsClient = lambda: exec_client_mock  # type: ignore[attr-defined]

        # Inject the stub into sys.modules
        sys.modules["google.cloud.run_v2"] = run_v2_stub
        # Also make sure `from google.cloud import run_v2` works
        import google.cloud as gc

        old_attr = getattr(gc, "run_v2", None)
        gc.run_v2 = run_v2_stub  # type: ignore[attr-defined]
        try:
            out = sentinel_queries.query_jobs_executions()
        finally:
            if old_attr is None:
                try:
                    delattr(gc, "run_v2")
                except AttributeError:
                    pass
            else:
                gc.run_v2 = old_attr  # type: ignore[attr-defined]

        by_name = {j["job"]: j for j in out}
        # Job 0 — deployed but never executed
        assert by_name[sentinel_queries.JOB_NAMES[0]]["status"] == "error"
        assert by_name[sentinel_queries.JOB_NAMES[0]]["last_status"] == "NEVER_RUN"
        # Job 1 — last execution failed
        assert by_name[sentinel_queries.JOB_NAMES[1]]["status"] == "warn"
        # Other declared jobs — not deployed
        not_deployed = [j for n, j in by_name.items() if n not in sentinel_queries.JOB_NAMES[:2]]
        assert all(j["status"] == "error" and not j["deployed"] for j in not_deployed)


# ---------------------------------------------------------------------------
# Module import sanity
# ---------------------------------------------------------------------------


def test_module_imports_without_gcp_sdk() -> None:
    """Ensure the module is importable in CI without google.cloud installed."""
    import importlib

    mod = importlib.import_module("ui.sentinel_queries")
    assert mod.GOLD_TABLES
    assert mod.SEMANTIC_VIEWS
    assert mod.JOB_NAMES


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
