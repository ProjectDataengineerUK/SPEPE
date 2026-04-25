"""Pipeline healer — detects & remediates common Bronze/Silver failures.

Failure modes handled:
- schema_drift: new/missing column → delegate to SchemaEvolver (if backward-compatible)
- null_explosion: null rate jumps above baseline → quarantine partition + alert
- corrupted_file: file unreadable / wrong format → reprocess last valid version
- duplicate_pk: primary key duplicates → dedup with latest timestamp wins

Referência: DESIGN_SPEPE.md — Decisão 17.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

logger = logging.getLogger("spepe.dataops.healing")


class FailureType(str, Enum):
    SCHEMA_DRIFT = "schema_drift"
    NULL_EXPLOSION = "null_explosion"
    CORRUPTED_FILE = "corrupted_file"
    DUPLICATE_PK = "duplicate_pk"
    ROW_COUNT_DROP = "row_count_drop"
    UNKNOWN = "unknown"


@dataclass
class HealingAction:
    failure_type: FailureType
    action: str  # "auto_heal" | "quarantine" | "manual_queue"
    details: dict[str, Any] = field(default_factory=dict)
    recovered_df: pd.DataFrame | None = None


class PipelineHealer:
    """Detects failure type and applies remediation.

    Typical use inside a Cloud Run Job:
        healer = PipelineHealer()
        if detected := healer.detect(df_new, df_baseline):
            action = healer.heal(df_new, detected)
            if action.action == "manual_queue":
                alert_steward(action)
                return
            df_new = action.recovered_df or df_new
    """

    def __init__(
        self,
        null_rate_max_delta: float = 0.10,
        row_count_min_ratio: float = 0.50,
    ):
        self.null_rate_max_delta = null_rate_max_delta
        self.row_count_min_ratio = row_count_min_ratio

    def detect(
        self,
        df_new: pd.DataFrame,
        df_baseline: pd.DataFrame | None = None,
        pk_columns: list[str] | None = None,
    ) -> FailureType | None:
        """Classify the failure (if any). Returns None when data looks healthy."""
        if df_new is None or df_new.empty:
            return FailureType.CORRUPTED_FILE

        if pk_columns:
            pk_present = [c for c in pk_columns if c in df_new.columns]
            if pk_present and df_new[pk_present].duplicated().any():
                return FailureType.DUPLICATE_PK

        if df_baseline is not None and not df_baseline.empty:
            baseline_cols = set(df_baseline.columns)
            new_cols = set(df_new.columns)
            if baseline_cols.symmetric_difference(new_cols):
                return FailureType.SCHEMA_DRIFT

            if len(df_new) < self.row_count_min_ratio * len(df_baseline):
                return FailureType.ROW_COUNT_DROP

            for col in baseline_cols & new_cols:
                base_null_rate = df_baseline[col].isna().mean()
                new_null_rate = df_new[col].isna().mean()
                if new_null_rate - base_null_rate > self.null_rate_max_delta:
                    logger.warning(
                        "null_rate_jump col=%s base=%.3f new=%.3f",
                        col,
                        base_null_rate,
                        new_null_rate,
                    )
                    return FailureType.NULL_EXPLOSION

        return None

    def heal(
        self,
        df_new: pd.DataFrame,
        failure: FailureType,
        df_baseline: pd.DataFrame | None = None,
        pk_columns: list[str] | None = None,
        watermark_col: str | None = None,
    ) -> HealingAction:
        """Attempt remediation based on failure type."""
        if failure == FailureType.DUPLICATE_PK and pk_columns:
            pk_cols = [c for c in pk_columns if c in df_new.columns]
            if watermark_col and watermark_col in df_new.columns:
                dedup = df_new.sort_values(watermark_col).drop_duplicates(
                    subset=pk_cols, keep="last"
                )
            else:
                dedup = df_new.drop_duplicates(subset=pk_cols, keep="last")
            return HealingAction(
                failure_type=failure,
                action="auto_heal",
                details={
                    "strategy": "dedup_latest",
                    "removed": len(df_new) - len(dedup),
                },
                recovered_df=dedup,
            )

        if failure == FailureType.SCHEMA_DRIFT:
            return HealingAction(
                failure_type=failure,
                action="manual_queue",
                details={
                    "strategy": "delegate_to_schema_evolver",
                    "new_cols": (
                        list(set(df_new.columns) - set(df_baseline.columns))
                        if df_baseline is not None
                        else []
                    ),
                    "missing_cols": (
                        list(set(df_baseline.columns) - set(df_new.columns))
                        if df_baseline is not None
                        else []
                    ),
                },
            )

        if failure == FailureType.NULL_EXPLOSION:
            return HealingAction(
                failure_type=failure,
                action="quarantine",
                details={"strategy": "quarantine_partition_and_alert_steward"},
            )

        if failure == FailureType.CORRUPTED_FILE:
            return HealingAction(
                failure_type=failure,
                action="manual_queue",
                details={"strategy": "reprocess_last_valid_version"},
            )

        if failure == FailureType.ROW_COUNT_DROP:
            return HealingAction(
                failure_type=failure,
                action="quarantine",
                details={"strategy": "alert_steward"},
            )

        return HealingAction(
            failure_type=FailureType.UNKNOWN,
            action="manual_queue",
            details={"strategy": "alert_steward"},
        )


def heal_pipeline_failure(
    df_new: pd.DataFrame,
    df_baseline: pd.DataFrame | None = None,
    pk_columns: list[str] | None = None,
    watermark_col: str | None = None,
) -> HealingAction | None:
    """Convenience function: detect + heal in one call."""
    healer = PipelineHealer()
    failure = healer.detect(df_new, df_baseline, pk_columns)
    if failure is None:
        return None
    return healer.heal(df_new, failure, df_baseline, pk_columns, watermark_col)
