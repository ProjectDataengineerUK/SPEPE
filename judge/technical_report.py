from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from judge.fairness_auditor import FairnessReport
from judge.independent_backtester import BacktestResult


@dataclass
class TechnicalReport:
    markdown: str
    verdict: str
    metadata: dict


def generate_technical_report(
    model_version: str,
    backtest: BacktestResult,
    fairness: FairnessReport,
    verdict: str,
    rationale: str,
    thresholds: dict,
) -> TechnicalReport:
    now = datetime.now(timezone.utc).isoformat()
    brier = backtest.brier_score
    lines = [
        f"# Parecer Tecnico — Auditoria Independente v{model_version}",
        "",
        f"**Modelo auditado:** v{model_version}",
        f"**Data:** {now}",
        "**Auditor:** ML Judge (Gemini 2.5 Pro)",
        "**Isolamento:** total (sem imports de mlops/ ou agents/)",
        "",
        "## Metodologia",
        "- Backtest independente contra `spepe_mlops.ground_truth` (shadow=true).",
        "- Calibration error calculado via binning (10 bins).",
        "- Fairness audit: Equalized Odds sobre atributos protegidos "
        f"({', '.join(fairness.dimensions)}).",
        "",
        "## Metricas",
        f"- Brier score: **{brier:.4f}** (limite: {thresholds.get('brier_max', 0.25)})",
        f"- Calibration error (ECE): **{backtest.calibration_error:.4f}** "
        f"(limite: {thresholds.get('calibration_error_max', 0.05)})",
        f"- IC coverage: **{backtest.ic_coverage:.2%}** "
        f"(faixa: {thresholds.get('ic_coverage_min', 0.92):.0%}"
        f"–{thresholds.get('ic_coverage_max', 0.98):.0%})",
        f"- MAE: **{backtest.mae:.4f}**",
        f"- N amostras: **{backtest.n_samples}**",
        "",
        "## Fairness — Equalized Odds",
        f"- Gap maximo: **{fairness.max_gap_pp:.2f} pp** "
        f"(limite: {thresholds.get('fairness_gap_max_pp', 15.0)} pp)",
        f"- Violacoes: **{len([f for f in fairness.findings if f.gap_pp > thresholds.get('fairness_gap_max_pp', 15.0)])}**",
    ]

    if fairness.findings:
        lines.append("")
        lines.append("### Findings por bucket")
        lines.append("| Dimensao | Bucket | TPR | FPR | Gap (pp) |")
        lines.append("|----------|--------|-----|-----|----------|")
        for f in fairness.findings:
            lines.append(
                f"| {f.dimension} | {f.bucket} | {f.tpr:.3f} | {f.fpr:.3f} | "
                f"{f.gap_pp:.2f} |"
            )

    lines.extend(
        [
            "",
            "## Limitacoes",
            "- Audit depende de `ground_truth` disponivel (TSE oficial) — janela <= 7 dias apos eleicao.",
            "- Shadow predictions podem ter distribuicao diferente da producao.",
            "- Calibration error sensivel a N pequeno por bucket.",
            "",
            "## Recomendacao final",
            f"**{verdict}**",
            "",
            rationale,
            "",
            "---",
            "_Parecer gerado automaticamente pelo ML Judge. Persistido em "
            "`spepe_mlops.audit_reports`._",
        ]
    )
    markdown = "\n".join(lines)
    metadata = {
        "model_version": model_version,
        "generated_at": now,
        "verdict": verdict,
        "brier_score": brier,
        "calibration_error": backtest.calibration_error,
        "ic_coverage": backtest.ic_coverage,
        "fairness_max_gap_pp": fairness.max_gap_pp,
        "n_samples": backtest.n_samples,
    }
    return TechnicalReport(markdown=markdown, verdict=verdict, metadata=metadata)
