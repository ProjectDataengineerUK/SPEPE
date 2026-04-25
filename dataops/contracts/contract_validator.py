"""Runtime validator for Data Contracts (ODCS-inspired YAML).

Usage:
    from dataops.contracts.contract_validator import validate_contract

    report = validate_contract(df, contract_path="dataops/contracts/bronze_to_silver.yaml",
                               source="tse")
    if not report.ok:
        raise ContractViolationError(report.violations)

Invocation points:
- silver_transform_job: before writing Silver (bronze_to_silver.yaml)
- gold_build_job: before writing Gold (silver_to_gold.yaml)
- vertex_pipeline (feature_extract): before training (gold_to_model.yaml)
- dashboard_api: on schema publish (gold_to_api.yaml)

Reference: DESIGN_SPEPE.md — Decisão 15.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

logger = logging.getLogger("spepe.dataops.contracts")


@dataclass
class ContractReport:
    ok: bool
    contract_path: str
    contract_version: str
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        head = "OK" if self.ok else "VIOLATED"
        return (
            f"[{head}] contract={self.contract_path} v={self.contract_version} "
            f"violations={len(self.violations)} warnings={len(self.warnings)}"
        )


class ContractViolationError(Exception):
    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__(f"Contract violated: {violations}")


def _load_contract(contract_path: str | Path) -> dict[str, Any]:
    path = Path(contract_path)
    if not path.exists():
        raise FileNotFoundError(f"Contract not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _check_columns(df: pd.DataFrame, required: list[str], report: ContractReport) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        report.violations.append(f"missing_required_columns={missing}")


def _check_types(df: pd.DataFrame, types: dict[str, str], report: ContractReport) -> None:
    for col, expected in types.items():
        if col not in df.columns:
            continue
        actual = str(df[col].dtype)
        # flexible match — accept int64/Int64, float64/Float64
        if expected.lower().replace("_", "") not in actual.lower().replace("_", ""):
            report.warnings.append(f"type_mismatch col={col} expected={expected} got={actual}")


def _check_no_nulls(df: pd.DataFrame, columns: list[str], report: ContractReport) -> None:
    for col in columns:
        if col not in df.columns:
            continue
        null_count = int(df[col].isna().sum())
        if null_count > 0:
            report.violations.append(f"null_in_required col={col} count={null_count}")


def _check_ranges(
    df: pd.DataFrame, ranges: dict[str, dict[str, float]], report: ContractReport
) -> None:
    for col, bounds in ranges.items():
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        if "min" in bounds and (series < bounds["min"]).any():
            report.violations.append(f"range_violation col={col} < min={bounds['min']}")
        if "max" in bounds and (series > bounds["max"]).any():
            report.violations.append(f"range_violation col={col} > max={bounds['max']}")


def _check_row_count_min(df: pd.DataFrame, minimum: int, report: ContractReport) -> None:
    if len(df) < minimum:
        report.violations.append(f"row_count_below_min rows={len(df)} min={minimum}")


def validate_contract(
    df: pd.DataFrame,
    contract_path: str | Path,
    source: str | None = None,
    raise_on_violation: bool = False,
) -> ContractReport:
    """Validate a dataframe against a contract YAML.

    Args:
        df: data to validate.
        contract_path: path to YAML file.
        source: for multi-source contracts (e.g. "tse" | "ibge" | "digital").
        raise_on_violation: if True, raise ContractViolationError on any violation.
    """
    contract = _load_contract(contract_path)
    report = ContractReport(
        ok=True,
        contract_path=str(contract_path),
        contract_version=str(contract.get("contract_version", "unknown")),
    )

    schema_req = contract.get("schema_requirements", {}) or {}
    gates = contract.get("quality_gates", {}) or {}
    top_gates = contract.get("gates", {}) or {}

    if source and source in schema_req:
        section = schema_req[source]
        if "required_columns" in section:
            _check_columns(df, section["required_columns"], report)
        if "types" in section:
            _check_types(df, section["types"], report)
        if "no_nulls" in section:
            _check_no_nulls(df, section["no_nulls"], report)
        if "ranges" in section:
            _check_ranges(df, section["ranges"], report)

    # Contract-wide schema sections (for non-source contracts like silver_to_gold)
    for name, section in schema_req.items():
        if source and name == source:
            continue
        if not isinstance(section, dict):
            continue
        if "required_columns" in section:
            _check_columns(df, section["required_columns"], report)

    # top-level gates
    if "null_pk_max" in gates:
        pass  # already enforced via no_nulls on PKs
    if "row_count_min" in top_gates:
        _check_row_count_min(df, int(top_gates["row_count_min"]), report)

    # required_features for gold_to_model.yaml
    required_features = contract.get("required_features", []) or []
    if required_features:
        cols = [f["name"] for f in required_features if isinstance(f, dict)]
        _check_columns(df, cols, report)
        ranges = {
            f["name"]: {"min": f["range"][0], "max": f["range"][1]}
            for f in required_features
            if isinstance(f, dict) and "range" in f
        }
        _check_ranges(df, ranges, report)

    report.ok = len(report.violations) == 0
    logger.info(report.summary())

    on_violation = contract.get("on_violation", "warn")
    if not report.ok and (raise_on_violation or on_violation == "block"):
        raise ContractViolationError(report.violations)
    return report
