"""Schema evolver — applies backward-compatible schema migrations automatically.

Rules:
- ADDITIVE changes (new nullable column) → auto-apply
- ADDITIVE changes (new required column with default) → auto-apply with default
- BREAKING changes (drop / rename / type change) → BLOCK + alert steward

Targets: BigQuery schemas. Local Parquet files tolerate schema drift by default.

Referência: DESIGN_SPEPE.md — Decisão 17 (self-healing).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("spepe.dataops.schema")


class ChangeType(str, Enum):
    ADDITIVE_NULLABLE = "additive_nullable"
    ADDITIVE_REQUIRED = "additive_required"
    BREAKING_DROP = "breaking_drop"
    BREAKING_RENAME = "breaking_rename"
    BREAKING_TYPE_CHANGE = "breaking_type_change"


@dataclass
class SchemaChange:
    change_type: ChangeType
    column: str
    details: dict[str, Any] = field(default_factory=dict)
    is_breaking: bool = False


@dataclass
class EvolutionPlan:
    applied: list[SchemaChange] = field(default_factory=list)
    blocked: list[SchemaChange] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocked


class SchemaEvolver:
    """Compares old vs. new schema and plans evolution."""

    def __init__(self, auto_apply_additive: bool = True):
        self.auto_apply_additive = auto_apply_additive

    def diff(self, old_schema: dict[str, str], new_schema: dict[str, str]) -> list[SchemaChange]:
        """Diff two schemas.

        Args:
            old_schema: {column_name: type} — prior committed schema
            new_schema: {column_name: type} — candidate schema
        """
        changes: list[SchemaChange] = []
        old_cols, new_cols = set(old_schema), set(new_schema)

        for col in new_cols - old_cols:
            changes.append(
                SchemaChange(
                    change_type=ChangeType.ADDITIVE_NULLABLE,
                    column=col,
                    details={"new_type": new_schema[col]},
                )
            )

        for col in old_cols - new_cols:
            changes.append(
                SchemaChange(
                    change_type=ChangeType.BREAKING_DROP,
                    column=col,
                    details={"old_type": old_schema[col]},
                    is_breaking=True,
                )
            )

        for col in old_cols & new_cols:
            if old_schema[col] != new_schema[col]:
                # BigQuery allows widening (INT → FLOAT, INT → NUMERIC).
                if _is_widening(old_schema[col], new_schema[col]):
                    changes.append(
                        SchemaChange(
                            change_type=ChangeType.ADDITIVE_NULLABLE,
                            column=col,
                            details={
                                "from": old_schema[col],
                                "to": new_schema[col],
                                "strategy": "widening",
                            },
                        )
                    )
                else:
                    changes.append(
                        SchemaChange(
                            change_type=ChangeType.BREAKING_TYPE_CHANGE,
                            column=col,
                            details={
                                "from": old_schema[col],
                                "to": new_schema[col],
                            },
                            is_breaking=True,
                        )
                    )
        return changes

    def plan(self, old_schema: dict[str, str], new_schema: dict[str, str]) -> EvolutionPlan:
        """Build an evolution plan: which changes to auto-apply vs. block."""
        plan = EvolutionPlan()
        for change in self.diff(old_schema, new_schema):
            if change.is_breaking:
                plan.blocked.append(change)
            elif self.auto_apply_additive:
                plan.applied.append(change)
            else:
                plan.blocked.append(change)
        return plan


_WIDENING_RULES = {
    ("INT64", "FLOAT64"),
    ("INT64", "NUMERIC"),
    ("INT64", "BIGNUMERIC"),
    ("FLOAT64", "BIGNUMERIC"),
    ("NUMERIC", "BIGNUMERIC"),
    ("int64", "float64"),
}


def _is_widening(old_type: str, new_type: str) -> bool:
    return (old_type, new_type) in _WIDENING_RULES


def evolve_schema(
    old_schema: dict[str, str],
    new_schema: dict[str, str],
    auto_apply_additive: bool = True,
) -> EvolutionPlan:
    """Convenience function: evolve a schema or return blocking plan."""
    evolver = SchemaEvolver(auto_apply_additive=auto_apply_additive)
    plan = evolver.plan(old_schema, new_schema)
    if plan.ok:
        logger.info("schema_evolution_ok additive=%d", len(plan.applied))
    else:
        logger.warning(
            "schema_evolution_blocked breaking=%d additive=%d",
            len(plan.blocked),
            len(plan.applied),
        )
    return plan
