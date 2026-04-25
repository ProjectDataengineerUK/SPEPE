from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from judge.ml_judge import JudgeVerdict, MLJudge

logger = logging.getLogger(__name__)


@dataclass
class GateDecision:
    approved: bool
    verdict: str
    rationale: str
    archived_id: str | None = None


class PromotionGate:
    """Blocks promotion if ML Judge returns Reprovado; archives report in BQ."""

    def __init__(self, judge: MLJudge | None = None):
        self.judge = judge or MLJudge()

    def evaluate(
        self,
        project_id: str,
        model_version: str,
        predictions: list[dict] | None = None,
        fairness_rows: list[dict] | None = None,
    ) -> GateDecision:
        verdict_obj = self.judge.audit(
            project_id=project_id,
            model_version=model_version,
            predictions=predictions,
            fairness_rows=fairness_rows,
        )
        archived_id = self._archive(project_id, verdict_obj)
        approved = verdict_obj.verdict != MLJudge.REJECTED
        if not approved:
            logger.warning(
                "promotion_blocked model=%s verdict=%s",
                model_version,
                verdict_obj.verdict,
            )
        return GateDecision(
            approved=approved,
            verdict=verdict_obj.verdict,
            rationale=verdict_obj.rationale,
            archived_id=archived_id,
        )

    def _archive(self, project_id: str, verdict: JudgeVerdict) -> str | None:
        try:
            from google.cloud import bigquery

            client = bigquery.Client(project=project_id)
            table_id = f"{project_id}.spepe_mlops.audit_reports"
            row: dict[str, Any] = {
                "model_version": verdict.metadata.get("model_version"),
                "generated_at": verdict.metadata.get("generated_at"),
                "verdict": verdict.verdict,
                "brier_score": verdict.metadata.get("brier_score"),
                "calibration_error": verdict.metadata.get("calibration_error"),
                "ic_coverage": verdict.metadata.get("ic_coverage"),
                "fairness_max_gap_pp": verdict.metadata.get("fairness_max_gap_pp"),
                "n_samples": verdict.metadata.get("n_samples"),
                "report_markdown": verdict.report.markdown,
                "rationale": verdict.rationale,
            }
            errors = client.insert_rows_json(table_id, [row])
            if errors:
                logger.warning("audit_archive_errors=%s", errors)
                return None
            return f"{table_id}:{row['generated_at']}"
        except Exception as exc:
            logger.info("audit_archive_skipped_bq_unavailable: %s", exc)
            return None
