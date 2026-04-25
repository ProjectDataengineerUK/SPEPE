"""Auto-rollback: monitors challenger Brier score every 6h; reverts if > champion * 1.05."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mlops.deployment.canary_manager import CanaryState, rollback_canary, promote_canary

logger = logging.getLogger(__name__)

ROLLBACK_THRESHOLD = 1.05  # 5% worse than champion triggers rollback
CHECK_INTERVAL_SECONDS = 6 * 3600
EVAL_WINDOW_HOURS = 48
COOLDOWN_HOURS = 72  # Minimum hours between retrain runs (prevents retrain loops)


@dataclass
class RollbackDecision:
    action: str  # "rollback" | "promote" | "continue"
    reason: str
    challenger_brier: float
    champion_brier: float
    ratio: float


def _fetch_brier_score(
    revision: str, window_hours: int, project_id: str
) -> float | None:
    """Fetch average Brier score for a Cloud Run revision from fact_predictions."""
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project_id)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=window_hours)
        ).isoformat()
        query = f"""
            SELECT AVG(brier_score) as avg_brier
            FROM `{project_id}.spepe_mlops.fact_predictions`
            WHERE model_version = '{revision}'
              AND brier_score IS NOT NULL
              AND prediction_date >= '{cutoff}'
        """
        result = list(client.query(query).result())
        if result and result[0]["avg_brier"] is not None:
            return float(result[0]["avg_brier"])
    except Exception as exc:
        logger.warning("Failed to fetch Brier score for %s: %s", revision, exc)
    return None


def evaluate_canary(state: CanaryState, window_hours: int = 6) -> RollbackDecision:
    project_id = state.project_id

    challenger_brier = _fetch_brier_score(
        state.challenger_revision, window_hours, project_id
    )
    champion_brier = _fetch_brier_score(
        state.champion_revision, window_hours, project_id
    )

    if challenger_brier is None or champion_brier is None:
        logger.info("Insufficient data for canary evaluation — continuing")
        return RollbackDecision(
            action="continue",
            reason="Insufficient prediction data",
            challenger_brier=challenger_brier or 0.0,
            champion_brier=champion_brier or 0.0,
            ratio=0.0,
        )

    ratio = challenger_brier / (champion_brier + 1e-10)
    if ratio > ROLLBACK_THRESHOLD:
        return RollbackDecision(
            action="rollback",
            reason=f"Challenger Brier ({challenger_brier:.4f}) > champion ({champion_brier:.4f}) × {ROLLBACK_THRESHOLD}",
            challenger_brier=challenger_brier,
            champion_brier=champion_brier,
            ratio=ratio,
        )

    return RollbackDecision(
        action="promote",
        reason=f"Challenger Brier ({challenger_brier:.4f}) ≤ champion ({champion_brier:.4f}) × {ROLLBACK_THRESHOLD}",
        challenger_brier=challenger_brier,
        champion_brier=champion_brier,
        ratio=ratio,
    )


def watch_canary(state: CanaryState, max_hours: int = EVAL_WINDOW_HOURS) -> str:
    """Blocking watch loop. Returns 'promoted' or 'rolled_back'."""
    deadline = datetime.now(timezone.utc) + timedelta(hours=max_hours)
    checks = 0

    while datetime.now(timezone.utc) < deadline:
        time.sleep(CHECK_INTERVAL_SECONDS)
        checks += 1
        decision = evaluate_canary(state)
        logger.info(
            "Canary check #%d: action=%s ratio=%.3f",
            checks,
            decision.action,
            decision.ratio,
        )

        if decision.action == "rollback":
            rollback_canary(state)
            return "rolled_back"
        if decision.action == "promote":
            promote_canary(state)
            return "promoted"

    logger.warning(
        "Canary window expired without clear decision — promoting challenger"
    )
    promote_canary(state)
    return "promoted"


def check_cooldown(project_id: str) -> bool:
    """Returns True if cooldown period has passed (safe to retrain)."""
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project_id)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=COOLDOWN_HOURS)
        ).isoformat()
        query = f"""
            SELECT COUNT(*) as recent_retrains
            FROM `{project_id}.spepe_mlops.model_evaluations`
            WHERE evaluated_at >= '{cutoff}'
        """
        result = list(client.query(query).result())
        recent = int(result[0]["recent_retrains"]) if result else 0
        return recent == 0
    except Exception as exc:
        logger.warning("Cooldown check failed: %s — assuming safe", exc)
        return True
