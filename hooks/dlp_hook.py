"""DLP hook — LGPD compliance: block PII in agent outputs."""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("spepe.hooks.dlp")

_PII_PATTERNS = [
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "CPF"),
    (re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"), "CNPJ"),
    (re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b"), "email"),
    (re.compile(r"\b\d{5}-\d{3}\b"), "CEP"),
    (re.compile(r"\bRG\s*:?\s*\d{1,2}\.\d{3}\.\d{3}-\d\b", re.IGNORECASE), "RG"),
    (re.compile(r"\b\(\d{2}\)\s*\d{4,5}-\d{4}\b"), "telefone"),
]


def check_dlp(text: str) -> list[tuple[str, str]]:
    """Return list of (pattern_match, pii_type) for any PII found."""
    violations = []
    for pattern, pii_type in _PII_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            violations.append((match, pii_type))
    return violations


def dlp_hook(tool_name: str, tool_input: dict, tool_output: str | None = None) -> str | None:
    """Hook to block PII in tool outputs. Returns block message or None."""
    text_to_check = tool_output or str(tool_input)
    violations = check_dlp(text_to_check)

    if violations:
        pii_types = list({v[1] for v in violations})
        msg = (
            f"LGPD DLP BLOCK: PII detectado no output do tool '{tool_name}': {pii_types}. "
            "Dados do SPEPE devem estar em nível agregado (município/cluster), nunca individual. "
            "Verifique se a query não está retornando dados pessoais."
        )
        logger.warning(msg)
        return msg

    return None
