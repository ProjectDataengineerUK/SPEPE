"""Output validators for electoral domain — forecast format + PII redaction + disclaimer gate.

`validate_disclaimer` is a HARD gate (binário): output sem disclaimer correspondente
ao trigger detectado é rejeitado. Não é métrica de qualidade.

Referência: DESIGN_SPEPE.md — Política de Disclaimer (enforcement 3 camadas).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from hooks.disclaimer_hook import (
    detect_required_disclaimers,
    disclaimer_hook,
    audit_disclaimer,
)


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    remediated: str | None = None


_FORECAST_REQUIRED = [
    (r"P\(.+?\)\s*=\s*\d+", "formato P(X) = NN%"),
    (r"IC\s*95%", "intervalo de confiança IC 95%"),
    (r"(?i)disclaimer|fins\s+analíticos|fins\s+educacionais|não\s+constitui\s+previsão", "disclaimer obrigatório"),
]

_CERTAINTY_FORBIDDEN = [
    r"(?i)\bcertamente\b",
    r"(?i)\bcom certeza\b",
    r"(?i)\bvai\s+vencer\b",
    r"(?i)\b100\s*%\b",
    r"(?i)\bgarantido\b",
    r"(?i)\bimpossível\s+perder\b",
]

_PII_PATTERNS = [
    (r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", "[CPF-REDACTED]"),
    (r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", "[CNPJ-REDACTED]"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL-REDACTED]"),
    (r"\b(?:\+55\s?)?(?:\(?\d{2}\)?\s?)(?:9\s?)?\d{4}[-\s]?\d{4}\b", "[PHONE-REDACTED]"),
    (r"\b\d{5}-\d{3}\b", "[CEP-REDACTED]"),
]


def validate_forecast(text: str) -> ValidationResult:
    for pattern, label in _FORECAST_REQUIRED:
        if not re.search(pattern, text):
            return ValidationResult(False, f"Elemento obrigatório ausente: {label}")
    for pattern in _CERTAINTY_FORBIDDEN:
        if re.search(pattern, text):
            match = re.search(pattern, text)
            return ValidationResult(False, f"Linguagem de certeza proibida: '{match.group()}'")
    return ValidationResult(True)


def validate_no_pii(text: str) -> ValidationResult:
    remediated = text
    found = False
    for pattern, replacement in _PII_PATTERNS:
        if re.search(pattern, remediated):
            remediated = re.sub(pattern, replacement, remediated)
            found = True
    if found:
        return ValidationResult(False, "PII detectado e removido", remediated=remediated)
    return ValidationResult(True)


def validate_input_injection(text: str) -> ValidationResult:
    """Block common prompt injection patterns."""
    _injection_patterns = [
        r"(?i)ignore\s+(previous|all|your)\s+instructions",
        r"(?i)você\s+agora\s+é",
        r"(?i)esqueça\s+(seu|o)\s+sistema",
        r"(?i)new\s+instructions?:",
        r"(?i)system\s+prompt\s+override",
        r"(?i)jailbreak",
        r"(?i)DAN\s+mode",
    ]
    for pattern in _injection_patterns:
        if re.search(pattern, text):
            return ValidationResult(False, f"Possível prompt injection detectado: {pattern}")
    return ValidationResult(True)


def validate_disclaimer(text: str) -> ValidationResult:
    """Hard binary gate: if a trigger is present, the corresponding disclaimer MUST be present.

    If missing, the hook injects the template and we return the remediated text.
    """
    required = detect_required_disclaimers(text)
    if not required:
        return ValidationResult(True)

    remediated, was_modified = disclaimer_hook(text, agent_name=None)
    if was_modified:
        audit = audit_disclaimer(remediated)
        reason = (
            f"Disclaimer obrigatório ausente — tipos requeridos: {required}. "
            f"Injetado automaticamente."
        )
        return ValidationResult(False, reason, remediated=remediated)
    return ValidationResult(True)


AGENT_VALIDATORS: dict[str, list] = {
    "modelista_bayesiano": [validate_forecast, validate_no_pii, validate_disclaimer],
    "explicador":          [validate_no_pii, validate_disclaimer],
    "narrador":            [validate_no_pii, validate_disclaimer],
    "analista":            [validate_no_pii, validate_disclaimer],
    "coletor":             [validate_no_pii, validate_disclaimer],
    "perfilador":          [validate_no_pii, validate_disclaimer],
    "vigilante":           [validate_no_pii, validate_disclaimer],
}
