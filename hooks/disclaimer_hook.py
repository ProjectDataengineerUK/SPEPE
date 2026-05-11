"""Disclaimer hook — enforcement universal (gate binário).

Toda saída do SPEPE que contenha dado eleitoral, probabilidade, análise ou previsão
DEVE incluir disclaimer explícito. Este hook detecta o tipo de output (4 triggers),
verifica a presença do disclaimer correspondente e injeta se ausente.

`disclaimer_present` não é métrica — é gate binário.

Referência: DESIGN_SPEPE.md — Política de Disclaimer.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import yaml

from config.exceptions import DisclaimerMissingError

logger = logging.getLogger("spepe.hooks.disclaimer")

_TEMPLATES_PATH = Path(__file__).parent.parent / "security" / "disclaimer_templates.yaml"

# Detecta outputs de previsão que exigem gate binário (HTTP 422 se disclaimer ausente).
_FORECAST_GATE_PATTERN = re.compile(
    r"%.*?(?:município|municípios|candidato|votos|previsão|previsto)|"
    r"(?:município|municípios|candidato|votos|previsão|previsto).*?%",
    re.IGNORECASE | re.DOTALL,
)

_DISCLAIMER_MANDATORY_FRAGMENT = re.compile(
    r"Esta projeção é baseada em dados históricos",
    re.IGNORECASE,
)

TRIGGERS: dict[str, re.Pattern[str]] = {
    "tipo_a_previsao": re.compile(
        r"P\(|IC\s*9[05]%|\d+[\.,]\d+\s*%.*(?:voto|eleição|candidato|turno)|"
        r"(?:probabilidade|chance|previsão)\s+de\s+\d",
        re.IGNORECASE,
    ),
    "tipo_b_dados": re.compile(
        r"IDHM|renda\s+média|IBGE|SHAP|resultado\s+(?:de\s+)?20(?:18|22)|"
        r"municípios?\s+com",
        re.IGNORECASE,
    ),
    "tipo_c_pesquisa": re.compile(
        r"pesquisa|instituto\s+\w+|PesqEle|margem\s+de\s+erro|" r"intenção\s+de\s+voto",
        re.IGNORECASE,
    ),
    "tipo_d_recomendacao": re.compile(
        r"recomend[ao]|sugir[oa]|estratégi[ao]|foco\s+em|priorizar",
        re.IGNORECASE,
    ),
}


@lru_cache(maxsize=1)
def _load_templates() -> dict[str, str]:
    if not _TEMPLATES_PATH.exists():
        logger.error("disclaimer_templates.yaml não encontrado em %s", _TEMPLATES_PATH)
        return {}
    try:
        return yaml.safe_load(_TEMPLATES_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.error("Falha ao carregar disclaimer_templates: %s", exc)
        return {}


def detect_required_disclaimers(output: str) -> list[str]:
    """Return list of disclaimer type keys required by the content of `output`."""
    return [t for t, pattern in TRIGGERS.items() if pattern.search(output)]


def _disclaimer_already_present(output: str, template: str) -> bool:
    """Heuristic: check if the first meaningful line of the template is present."""
    if not template:
        return True
    head = template.strip().splitlines()[0].strip()
    if len(head) < 10:
        head = template.strip()[:40]
    return head in output


def is_forecast_output(output: str) -> bool:
    """Return True if `output` is a forecast result requiring mandatory disclaimer gate."""
    return bool(_FORECAST_GATE_PATTERN.search(output))


def forecast_disclaimer_present(output: str) -> bool:
    """Return True if the mandatory forecast disclaimer fragment is present."""
    return bool(_DISCLAIMER_MANDATORY_FRAGMENT.search(output))


def disclaimer_hook(output: str, agent_name: str | None = None) -> tuple[str, bool]:
    """Inject disclaimers into output if missing.

    For forecast outputs (percentage + electoral keywords) this is a **hard gate**:
    raises DisclaimerMissingError (HTTP 422) when the mandatory disclaimer is absent
    instead of injecting it silently.

    Returns:
        (output_final, was_modified)

    Raises:
        DisclaimerMissingError: when a forecast output is missing the required disclaimer.
    """
    if is_forecast_output(output) and not forecast_disclaimer_present(output):
        logger.error(
            "disclaimer_gate_blocked agent=%s reason=forecast_output_missing_disclaimer",
            agent_name,
        )
        raise DisclaimerMissingError(agent_name=agent_name)

    templates = _load_templates()
    if not templates:
        return output, False

    required = detect_required_disclaimers(output)
    if not required:
        return output, False

    missing = [t for t in required if not _disclaimer_already_present(output, templates.get(t, ""))]

    if not missing:
        logger.debug("disclaimer_ok agent=%s required=%s", agent_name, required)
        return output, False

    disclaimer_block = "\n\n---\n" + "\n\n".join(
        templates[t].strip() for t in missing if t in templates
    )
    logger.info("disclaimer_injected agent=%s types=%s", agent_name, missing)
    return output + disclaimer_block, True


def audit_disclaimer(output: str, agent_name: str | None = None) -> dict:
    """Return structured audit record (for Cloud Logging)."""
    required = detect_required_disclaimers(output)
    templates = _load_templates()
    present = [t for t in required if _disclaimer_already_present(output, templates.get(t, ""))]
    return {
        "agent": agent_name,
        "required": required,
        "present": present,
        "missing": [t for t in required if t not in present],
        "disclaimer_rate": (len(present) / len(required)) if required else 1.0,
    }
