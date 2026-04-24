from __future__ import annotations

import re

DISCLAIMER_MARKERS = (
    "previsao estatistica",
    "previsao probabilistica",
    "dados publicos",
    "nao constitui recomendacao",
    "pesquisa eleitoral",
    "margem de erro",
    "previsão estatística",
    "previsão probabilística",
    "dados públicos",
    "não constitui recomendação",
    "pesquisa registrada",
    "registrada no tse",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def disclaimer_present(text: str) -> bool:
    norm = _normalize(text)
    return any(m in norm for m in DISCLAIMER_MARKERS)


def disclaimer_present_rate(outputs: list[str]) -> float:
    """HARD gate metric. Should be 1.0 in production for regulated agents."""
    if not outputs:
        return 0.0
    hits = sum(1 for o in outputs if disclaimer_present(o))
    return hits / len(outputs)


def relevance(output: str, expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 1.0
    norm = _normalize(output)
    hits = sum(1 for kw in expected_keywords if kw.lower() in norm)
    return hits / len(expected_keywords)


def factuality(output: str, ground_truths: list[str]) -> float:
    if not ground_truths:
        return 1.0
    norm = _normalize(output)
    hits = sum(1 for gt in ground_truths if gt.lower() in norm)
    return hits / len(ground_truths)
