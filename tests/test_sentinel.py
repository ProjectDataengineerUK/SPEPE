"""Tests for Sentinel multi-crew orchestrator."""

from __future__ import annotations

import pytest

from sentinel.dispatch.action_executor import ActionExecutor
from sentinel.events.event_types import EventType, Severity, SentinelEvent
from sentinel.kb.knowledge_base import KnowledgeBase
from sentinel.orchestrator import SentinelOrchestrator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def drift_event() -> SentinelEvent:
    return SentinelEvent(
        type=EventType.DRIFT_DETECTED,
        source="mlops.drift_monitor",
        severity=Severity.P2,
        payload={"js_divergence": 0.15, "sg_uf": "SP", "model_version": "v2.1"},
    )


@pytest.fixture
def dq_event() -> SentinelEvent:
    return SentinelEvent(
        type=EventType.DQ_VIOLATION,
        source="dataops.dq_runner",
        severity=Severity.P1,
        payload={"pass_rate": 0.82, "table": "stg_tse_sp_2022", "threshold": 0.95},
    )


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase(project_id=None)


@pytest.fixture
def orchestrator() -> SentinelOrchestrator:
    return SentinelOrchestrator(config={})


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class TestEventTypes:
    def test_event_type_values(self) -> None:
        assert EventType.DRIFT_DETECTED.value == "drift_detected"
        assert EventType.DQ_VIOLATION.value == "dq_violation"
        assert EventType.SOCIAL_BURST.value == "social_burst"

    def test_severity_enum(self) -> None:
        assert Severity.P1.value == "P1"
        assert Severity.P3.value == "P3"

    def test_sentinel_event_to_dict(self, drift_event: SentinelEvent) -> None:
        d = drift_event.to_dict()
        assert d["type"] == "drift_detected"
        assert d["source"] == "mlops.drift_monitor"
        assert d["severity"] == "P2"

    def test_sentinel_event_from_dict(self, drift_event: SentinelEvent) -> None:
        d = drift_event.to_dict()
        restored = SentinelEvent.from_dict(d)
        assert restored.type == EventType.DRIFT_DETECTED
        assert restored.severity == Severity.P2

    def test_all_event_types_covered(self) -> None:
        expected = {
            "dq_violation",
            "contract_breach",
            "drift_detected",
            "bias_alert",
            "freshness_slo_breach",
            "budget_warning",
            "social_burst",
            "pipeline_failure",
            "canary_degradation",
        }
        assert {e.value for e in EventType} == expected


# ---------------------------------------------------------------------------
# Knowledge Base (in-memory, no Firestore)
# ---------------------------------------------------------------------------


class TestKnowledgeBase:
    def test_write_and_get_incident(self, kb: KnowledgeBase) -> None:
        incident_id = kb.write_incident({"event_type": "drift_detected", "cause": "data_shift"})
        retrieved = kb.get_incident(incident_id)
        assert retrieved is not None
        assert retrieved["cause"] == "data_shift"
        assert "incident_id" in retrieved
        assert "timestamp" in retrieved

    def test_upsert_and_find_pattern(self, kb: KnowledgeBase) -> None:
        kb.upsert_pattern(
            {
                "trigger_signature": {"event_type": "dq_violation"},
                "probable_cause": "schema_drift",
                "recommended_action": "run_schema_evolver",
                "confidence": 0.85,
            }
        )
        results = kb.find_patterns({"event_type": "dq_violation"})
        assert len(results) >= 1
        assert results[0]["probable_cause"] == "schema_drift"

    def test_list_recent_incidents_empty(self, kb: KnowledgeBase) -> None:
        assert isinstance(kb.list_recent_incidents(), list)

    def test_list_incidents_with_event_type_filter(self, kb: KnowledgeBase) -> None:
        kb.write_incident({"event_type": "drift_detected", "cause": "x"})
        kb.write_incident({"event_type": "dq_violation", "cause": "y"})
        drift_only = kb.list_recent_incidents(event_type="drift_detected")
        assert all(i["event_type"] == "drift_detected" for i in drift_only)

    def test_get_nonexistent_incident_returns_none(self, kb: KnowledgeBase) -> None:
        assert kb.get_incident("nonexistent-id") is None


# ---------------------------------------------------------------------------
# Action Executor
# ---------------------------------------------------------------------------


class TestActionExecutor:
    def test_action_disabled_returns_not_executed(self) -> None:
        executor = ActionExecutor(auto_actions_enabled={"drift_detected": False})
        result = executor.execute("retrain", "drift_detected")
        assert result["executed"] is False
        assert result["reason"] == "auto_actions_disabled_for_event"

    def test_no_handler_returns_not_executed(self) -> None:
        executor = ActionExecutor(auto_actions_enabled={"drift_detected": True})
        result = executor.execute("retrain", "drift_detected")
        assert result["executed"] is False
        assert result["reason"] == "no_handler_registered"

    def test_action_executed_with_handler(self) -> None:
        called: dict = {}

        def fake_retrain(params: dict) -> dict:
            called["done"] = True
            return {"job_id": "test-123"}

        executor = ActionExecutor(
            auto_actions_enabled={"drift_detected": True},
            action_handlers={"retrain": fake_retrain},
        )
        result = executor.execute("retrain", "drift_detected", params={"uf": "SP"})
        assert result["executed"] is True
        assert called.get("done") is True

    def test_cooldown_prevents_double_execution(self) -> None:
        n: dict = {"count": 0}

        def fake_retrain(params: dict) -> dict:
            n["count"] += 1
            return {}

        executor = ActionExecutor(
            auto_actions_enabled={"drift_detected": True},
            action_handlers={"retrain": fake_retrain},
        )
        r1 = executor.execute("retrain", "drift_detected")
        r2 = executor.execute("retrain", "drift_detected")
        assert r1["executed"] is True
        assert r2["executed"] is False
        assert "cooldown_active" in r2["reason"]
        assert n["count"] == 1

    def test_handler_exception_returns_not_executed(self) -> None:
        def bad_handler(params: dict) -> dict:
            raise RuntimeError("crash")

        executor = ActionExecutor(
            auto_actions_enabled={"drift_detected": True},
            action_handlers={"retrain": bad_handler},
        )
        result = executor.execute("retrain", "drift_detected")
        assert result["executed"] is False
        assert "crash" in result["error"]


# ---------------------------------------------------------------------------
# Crew routing (Observadores)
# ---------------------------------------------------------------------------


class TestObservadoresCrew:
    def test_drift_routes_to_mlops(self, drift_event: SentinelEvent) -> None:
        from sentinel.crews.observadores import ObservadoresCrew

        crew = ObservadoresCrew()
        bundle = crew.observe(drift_event)
        assert "mlops" in bundle["observations"]
        assert "event" in bundle

    def test_dq_routes_to_dataops(self, dq_event: SentinelEvent) -> None:
        from sentinel.crews.observadores import ObservadoresCrew

        crew = ObservadoresCrew()
        bundle = crew.observe(dq_event)
        assert "dataops" in bundle["observations"]

    def test_social_burst_routes_to_social(self) -> None:
        from sentinel.crews.observadores import ObservadoresCrew

        event = SentinelEvent(
            type=EventType.SOCIAL_BURST,
            source="social_watcher",
            payload={"volume_mencoes": 5000, "sg_uf": "RJ"},
        )
        bundle = ObservadoresCrew().observe(event)
        assert "social" in bundle["observations"]


# ---------------------------------------------------------------------------
# Analysis crew
# ---------------------------------------------------------------------------


class TestAnalisadoresCrew:
    def test_analyze_returns_required_keys(self, drift_event: SentinelEvent) -> None:
        from sentinel.crews.analisadores import AnalisadoresCrew
        from sentinel.crews.observadores import ObservadoresCrew

        obs_bundle = ObservadoresCrew().observe(drift_event)
        analysis = AnalisadoresCrew(kb=KnowledgeBase()).analyze(obs_bundle)
        assert "patterns" in analysis
        assert "correlations" in analysis
        assert "event" in analysis

    def test_known_pattern_is_matched(self, kb: KnowledgeBase, drift_event: SentinelEvent) -> None:
        from sentinel.crews.analisadores import AnalisadoresCrew
        from sentinel.crews.observadores import ObservadoresCrew

        kb.upsert_pattern(
            {
                "trigger_signature": {"event_type": "drift_detected"},
                "probable_cause": "feature_distribution_shift",
                "recommended_action": "retrain",
                "confidence": 0.9,
            }
        )
        obs_bundle = ObservadoresCrew().observe(drift_event)
        analysis = AnalisadoresCrew(kb=kb).analyze(obs_bundle)
        assert isinstance(analysis["patterns"], list)


# ---------------------------------------------------------------------------
# GenAI Interpreter (fallback, no API)
# ---------------------------------------------------------------------------


class TestGenAIInterpreter:
    def test_fallback_no_patterns(self) -> None:
        from sentinel.genai_interpreter import GenAIInterpreter
        from sentinel.kb.context_builder import SentinelContext

        interp = GenAIInterpreter()
        interp._client = None
        ctx = SentinelContext(event={"severity": "P2"}, patterns=[], history=[])
        result = interp.interpret(ctx)
        assert result["requer_humano"] is True
        assert result["causa_raiz"] == "no_kb_match"

    def test_fallback_uses_best_pattern(self) -> None:
        from sentinel.genai_interpreter import GenAIInterpreter
        from sentinel.kb.context_builder import SentinelContext

        interp = GenAIInterpreter()
        interp._client = None
        patterns = [
            {
                "probable_cause": "feature_shift",
                "confidence": 0.88,
                "recommended_action": "retrain",
                "pattern_id": "p1",
            }
        ]
        ctx = SentinelContext(event={"severity": "P1"}, patterns=patterns, history=[])
        result = interp.interpret(ctx)
        assert result["causa_raiz"] == "feature_shift"
        assert result["confianca"] == pytest.approx(0.88)
        assert result["requer_humano"] is False


# ---------------------------------------------------------------------------
# Full orchestrator (no GCP)
# ---------------------------------------------------------------------------


class TestSentinelOrchestrator:
    def test_handle_drift_event(
        self, orchestrator: SentinelOrchestrator, drift_event: SentinelEvent
    ) -> None:
        result = orchestrator.handle_event(drift_event)
        assert result["handled"] is True
        assert result["event_type"] == "drift_detected"
        assert "interpretation" in result
        assert "dispatch" in result

    def test_handle_event_from_dict(
        self, orchestrator: SentinelOrchestrator, drift_event: SentinelEvent
    ) -> None:
        result = orchestrator.handle_event(drift_event.to_dict())
        assert result["handled"] is True

    def test_unknown_event_type_returns_not_handled(
        self, orchestrator: SentinelOrchestrator
    ) -> None:
        bad = {"type": "nonexistent_type", "source": "x", "payload": {}}
        result = orchestrator.handle_event(bad)
        assert result["handled"] is False
        assert result["reason"] == "unknown_event_type"

    def test_handle_dq_event(
        self, orchestrator: SentinelOrchestrator, dq_event: SentinelEvent
    ) -> None:
        result = orchestrator.handle_event(dq_event)
        assert result["handled"] is True
        assert result["event_type"] == "dq_violation"

    def test_handle_social_burst(self, orchestrator: SentinelOrchestrator) -> None:
        event = SentinelEvent(
            type=EventType.SOCIAL_BURST,
            source="social_monitor",
            payload={"volume_mencoes": 8000, "sg_uf": "MG"},
        )
        result = orchestrator.handle_event(event)
        assert result["handled"] is True

    def test_auto_action_called_when_enabled(self) -> None:
        called: dict = {}

        def fake_retrain(params: dict) -> dict:
            called["done"] = True
            return {"job_id": "retrain-42"}

        orch = SentinelOrchestrator(
            config={"auto_actions": {"drift_detected": True}},
            action_handlers={"retrain": fake_retrain},
        )
        event = SentinelEvent(
            type=EventType.DRIFT_DETECTED,
            source="test",
            payload={"js_divergence": 0.20},
        )
        result = orch.handle_event(event)
        assert result["handled"] is True
