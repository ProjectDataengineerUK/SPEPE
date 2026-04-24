"""LLM evaluation metrics: relevance, factuality, disclaimer presence."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class EvalResult:
    eval_id: str
    agent: str
    score: float
    passed: bool
    checks: dict[str, bool]
    feedback: list[str]


def evaluate_response(response: str, golden: dict) -> EvalResult:
    checks = {}
    feedback = []

    expected_patterns = golden.get("expected_patterns", [])
    for pattern in expected_patterns:
        found = bool(re.search(re.escape(pattern), response, re.IGNORECASE))
        checks[f"contains_{pattern[:20]}"] = found
        if not found:
            feedback.append(f"Pattern ausente: '{pattern}'")

    forbidden_words = golden.get("forbidden_words", [])
    for word in forbidden_words:
        found = bool(re.search(re.escape(word), response, re.IGNORECASE))
        checks[f"no_{word[:20]}"] = not found
        if found:
            feedback.append(f"Jargão proibido encontrado: '{word}'")

    if golden.get("required_fields"):
        checks["has_required_fields"] = all(
            field.lower() in response.lower() for field in golden["required_fields"]
        )
        if not checks["has_required_fields"]:
            missing = [f for f in golden["required_fields"] if f.lower() not in response.lower()]
            feedback.append(f"Campos obrigatórios ausentes: {missing}")

    if golden.get("check_routing"):
        agent_triggers = ["/prever", "/arquétipos", "/perfil", "/coletar", "/explicar", "/relatorio"]
        checks["has_routing"] = any(t in response for t in agent_triggers)
        if not checks["has_routing"]:
            feedback.append("Supervisor não indicou roteamento para agente especializado.")

    agent = golden.get("agent", "")
    if agent == "modelista":
        checks["has_disclaimer"] = any(
            kw in response.upper() for kw in ["DISCLAIMER", "AVISO", "ALERTA", "⚠️"]
        )
        if not checks["has_disclaimer"]:
            feedback.append("Modelista não incluiu disclaimer eleitoral obrigatório.")

    n_checks = len(checks)
    if n_checks == 0:
        score = 1.0
    else:
        score = sum(checks.values()) / n_checks

    return EvalResult(
        eval_id=golden.get("id", "unknown"),
        agent=agent,
        score=score,
        passed=score >= 0.85,
        checks=checks,
        feedback=feedback,
    )
