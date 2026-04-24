from __future__ import annotations

from typing import Any


class Reporter:
    """Formats an incident as structured Markdown for operator consumption."""

    def format(
        self,
        event: dict[str, Any],
        interpretation: dict[str, Any],
        action_taken: str | None = None,
    ) -> str:
        lines = [
            f"# Sentinel Alert — {event.get('type')}",
            "",
            f"- **Severity:** {interpretation.get('severidade', event.get('severity'))}",
            f"- **Source:** {event.get('source')}",
            f"- **Timestamp:** {event.get('timestamp')}",
            f"- **Confidence:** {interpretation.get('confianca', 0.0):.2f}",
            "",
            "## Probable cause",
            interpretation.get("causa_raiz", "unknown"),
            "",
            "## Recommended action",
            interpretation.get("acao_recomendada", "requires_human_review"),
        ]
        if action_taken:
            lines.extend(["", "## Action taken", action_taken])
        refs = interpretation.get("referencias_kb") or []
        if refs:
            lines.extend(["", "## KB references"])
            for r in refs:
                lines.append(f"- {r}")
        if interpretation.get("requer_humano"):
            lines.extend(["", "> **HUMAN APPROVAL REQUIRED**"])
        return "\n".join(lines)
