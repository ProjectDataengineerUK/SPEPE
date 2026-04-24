"""Tests for DLP, rate limit, security, and BigQuery cost hooks."""
import time
from unittest.mock import patch

import pytest


class TestDLPHook:
    def test_blocks_cpf_pattern(self):
        from hooks.dlp_hook import check_dlp

        violations = check_dlp("Eleitor CPF 123.456.789-09 registrado")
        assert len(violations) > 0
        assert any("cpf" in v.lower() or "123" in v for v in violations)

    def test_blocks_phone_pattern(self):
        from hooks.dlp_hook import check_dlp

        violations = check_dlp("Contato: (11) 99999-8888")
        assert len(violations) > 0

    def test_blocks_email(self):
        from hooks.dlp_hook import check_dlp

        violations = check_dlp("Email do eleitor: joao.silva@example.com")
        assert len(violations) > 0

    def test_blocks_cep(self):
        from hooks.dlp_hook import check_dlp

        violations = check_dlp("CEP de residência: 01310-100")
        assert len(violations) > 0

    def test_allows_aggregate_data(self):
        from hooks.dlp_hook import check_dlp

        safe_text = "São Paulo: 60.3% intenção de voto, 5570 municípios analisados"
        violations = check_dlp(safe_text)
        assert len(violations) == 0

    def test_dlp_hook_returns_block_message(self):
        from hooks.dlp_hook import dlp_hook

        result_with_pii = {"output": "Eleitor CPF 111.222.333-44 compareceu"}
        response = dlp_hook("bash", result_with_pii, session_id="test")
        assert response is not None

    def test_dlp_hook_returns_none_for_clean(self):
        from hooks.dlp_hook import dlp_hook

        clean_result = {"output": "Taxa de abstenção: 20.3% em 2022"}
        response = dlp_hook("read_file", clean_result, session_id="test")
        assert response is None


class TestRateLimitHook:
    def test_allows_requests_under_limit(self):
        from hooks.rate_limit_hook import rate_limit_hook, _request_log, _session_request_counts

        session_id = "rl-test-under"
        _request_log[session_id].clear()
        _session_request_counts[session_id] = 0

        for _ in range(10):
            response = rate_limit_hook("any_tool", {}, session_id=session_id)
            assert response is None

    def test_blocks_on_session_limit(self):
        from hooks.rate_limit_hook import (
            rate_limit_hook, _session_request_counts, MAX_REQUESTS_PER_SESSION
        )

        session_id = "rl-test-session-limit"
        _session_request_counts[session_id] = MAX_REQUESTS_PER_SESSION

        response = rate_limit_hook("any_tool", {}, session_id=session_id)
        assert response is not None
        assert "limit" in response.lower() or "excedido" in response.lower()

    def test_session_stats_tracking(self):
        from hooks.rate_limit_hook import rate_limit_hook, get_session_stats, _session_request_counts

        session_id = "rl-test-stats"
        _session_request_counts[session_id] = 0

        for _ in range(3):
            rate_limit_hook("tool", {}, session_id=session_id)

        stats = get_session_stats(session_id)
        assert "session_count" in stats
        assert stats["session_count"] >= 3


class TestSecurityHook:
    def test_blocks_sql_injection_drop(self):
        from hooks.security_hook import check_sql_injection

        result = check_sql_injection("SELECT * FROM table; DROP TABLE users;")
        assert result is not None

    def test_blocks_sql_injection_union(self):
        from hooks.security_hook import check_sql_injection

        result = check_sql_injection("' UNION ALL SELECT password FROM admin --")
        assert result is not None

    def test_allows_clean_sql(self):
        from hooks.security_hook import check_sql_injection

        result = check_sql_injection(
            "SELECT cd_municipio, SUM(qt_votos) FROM spepe_silver.tse_2022 "
            "WHERE ano_eleicao = 2022 GROUP BY cd_municipio LIMIT 100"
        )
        assert result is None

    def test_blocks_bigquery_cost_no_filter(self):
        from hooks.security_hook import check_bigquery_cost

        expensive_query = "SELECT * FROM spepe_gold.fact_municipio_eleicao"
        result = check_bigquery_cost(expensive_query)
        assert result is not None

    def test_allows_bigquery_with_where(self):
        from hooks.security_hook import check_bigquery_cost

        safe_query = (
            "SELECT * FROM spepe_gold.fact_municipio_eleicao "
            "WHERE ano_eleicao = 2022"
        )
        result = check_bigquery_cost(safe_query)
        assert result is None

    def test_allows_bigquery_with_limit(self):
        from hooks.security_hook import check_bigquery_cost

        safe_query = (
            "SELECT * FROM spepe_silver.tse_2022 LIMIT 1000"
        )
        result = check_bigquery_cost(safe_query)
        assert result is None


class TestAuditHook:
    def test_audit_logs_tool_usage(self):
        from hooks.audit_hook import audit_tool_usage

        with patch("hooks.audit_hook._get_cloud_logger") as mock_logger:
            mock_log = mock_logger.return_value
            audit_tool_usage(
                "read_file",
                {"file": "test.py"},
                session_id="audit-test-001",
                agent_name="Analista",
            )
            mock_log.log_struct.assert_called_once()
            call_args = mock_log.log_struct.call_args[0][0]
            assert call_args["tool"] == "read_file"
            assert call_args["session_id"] == "audit-test-001"
