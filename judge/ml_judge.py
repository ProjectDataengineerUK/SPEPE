from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from judge.fairness_auditor import FairnessReport, audit_fairness
from judge.independent_backtester import BacktestResult, run_independent_backtest
from judge.technical_report import TechnicalReport, generate_technical_report

logger = logging.getLogger(__name__)


@dataclass
class JudgeVerdict:
    verdict: str
    rationale: str
    report: TechnicalReport
    backtest: BacktestResult
    fairness: FairnessReport
    metadata: dict[str, Any] = field(default_factory=dict)


class MLJudge:
    """Independent auditor. Zero imports from mlops/ or agents/.

    Produces a formal parecer: Aprovado / Aprovado com ressalvas / Reprovado.
    """

    APPROVED = "Aprovado"
    APPROVED_WITH_RESERVATIONS = "Aprovado com ressalvas"
    REJECTED = "Reprovado"

    def __init__(self, config_path: str | Path | None = None):
        self.config = self._load_config(config_path)
        self.thresholds = self.config.get("thresholds", {})

    def _load_config(self, config_path: str | Path | None) -> dict:
        default = Path(__file__).parent / "judge_config.yaml"
        path = Path(config_path) if config_path else default
        if not path.exists():
            logger.warning("judge_config_missing_using_defaults: %s", path)
            return {}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f).get("judge", {})

    def audit(
        self,
        project_id: str,
        model_version: str,
        predictions: list[dict] | None = None,
        fairness_rows: list[dict] | None = None,
    ) -> JudgeVerdict:
        backtest = run_independent_backtest(
            project_id=project_id,
            model_version=model_version,
            rows=predictions,
        )
        fairness = audit_fairness(
            project_id=project_id,
            model_version=model_version,
            dimensions=self.config.get("fairness", {}).get("protected_attributes"),
            rows=fairness_rows,
            gap_threshold_pp=self.thresholds.get("fairness_gap_max_pp", 15.0),
            bucket_min_size=self.config.get("fairness", {}).get("bucket_min_size", 50),
        )
        verdict, rationale = self._decide(backtest, fairness)
        report = generate_technical_report(
            model_version=model_version,
            backtest=backtest,
            fairness=fairness,
            verdict=verdict,
            rationale=rationale,
            thresholds=self.thresholds,
        )
        return JudgeVerdict(
            verdict=verdict,
            rationale=rationale,
            report=report,
            backtest=backtest,
            fairness=fairness,
            metadata=report.metadata,
        )

    def _decide(self, backtest: BacktestResult, fairness: FairnessReport) -> tuple[str, str]:
        reasons: list[str] = []
        hard_fail = False
        soft_fail = False

        min_samples = self.thresholds.get("min_samples_for_audit", 200)
        if backtest.n_samples < min_samples:
            return (
                self.REJECTED,
                f"Amostra insuficiente para auditoria: {backtest.n_samples} < {min_samples}.",
            )

        brier_max = self.thresholds.get("brier_max", 0.25)
        if backtest.brier_score > brier_max:
            reasons.append(f"Brier {backtest.brier_score:.4f} > limite {brier_max:.4f}")
            hard_fail = True
        elif backtest.brier_score > 0.80 * brier_max:
            reasons.append(f"Brier {backtest.brier_score:.4f} proximo ao limite {brier_max:.4f}")
            soft_fail = True

        ece_max = self.thresholds.get("calibration_error_max", 0.05)
        if backtest.calibration_error > ece_max:
            reasons.append(f"Calibration error {backtest.calibration_error:.4f} > {ece_max:.4f}")
            hard_fail = True

        cov_min = self.thresholds.get("ic_coverage_min", 0.92)
        cov_max = self.thresholds.get("ic_coverage_max", 0.98)
        if backtest.ic_coverage < cov_min or backtest.ic_coverage > cov_max:
            reasons.append(
                f"IC coverage {backtest.ic_coverage:.3f} fora da faixa "
                f"[{cov_min:.2f}, {cov_max:.2f}]"
            )
            soft_fail = True

        gap_max = self.thresholds.get("fairness_gap_max_pp", 15.0)
        if fairness.max_gap_pp > gap_max:
            reasons.append(f"Fairness gap {fairness.max_gap_pp:.2f}pp > {gap_max:.2f}pp")
            hard_fail = True

        if hard_fail:
            verdict = self.REJECTED
        elif soft_fail:
            verdict = self.APPROVED_WITH_RESERVATIONS
        else:
            verdict = self.APPROVED
            reasons.append("Todas as metricas dentro dos limites.")
        return verdict, " | ".join(reasons)
