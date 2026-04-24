"""Data Quality runner — executes GE-style checks and reports to Cloud Logging."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger("spepe.dataops.dq")

EXPECTATIONS_DIR = Path(__file__).parent


def run_suite(df: pd.DataFrame, suite_name: str) -> dict:
    suite_path = EXPECTATIONS_DIR / f"expectations_{suite_name}.json"
    if not suite_path.exists():
        logger.warning(f"Suite não encontrada: {suite_path}")
        return {"status": "skipped", "score": 100.0, "failures": []}

    with open(suite_path, encoding="utf-8") as f:
        suite = json.load(f)

    failures = []
    passed = 0
    total = 0

    for exp in suite.get("expectations", []):
        total += 1
        result = _evaluate_expectation(df, exp)
        if result["success"]:
            passed += 1
        else:
            failures.append(result)

    score = (passed / total * 100) if total > 0 else 100.0
    run_result = {
        "suite": suite_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "score": score,
        "passed": passed,
        "total": total,
        "failures": failures,
        "status": "pass" if score >= 95.0 else "fail",
    }

    _log_result(run_result)
    return run_result


def _evaluate_expectation(df: pd.DataFrame, expectation: dict) -> dict:
    exp_type = expectation.get("type", "")
    kwargs = expectation.get("kwargs", {})

    try:
        if exp_type == "expect_column_values_to_not_be_null":
            col = kwargs["column"]
            if col not in df.columns:
                return {"success": False, "type": exp_type, "reason": f"Coluna '{col}' ausente"}
            null_count = int(df[col].isna().sum())
            success = null_count == 0
            return {"success": success, "type": exp_type, "column": col, "null_count": null_count}

        elif exp_type == "expect_column_values_to_be_between":
            col = kwargs["column"]
            if col not in df.columns:
                return {"success": False, "type": exp_type, "reason": f"Coluna '{col}' ausente"}
            min_val = kwargs.get("min_value")
            max_val = kwargs.get("max_value")
            series = pd.to_numeric(df[col], errors="coerce")
            if min_val is not None:
                fails = int((series < min_val).sum())
            else:
                fails = 0
            if max_val is not None:
                fails += int((series > max_val).sum())
            return {"success": fails == 0, "type": exp_type, "column": col, "violations": fails}

        elif exp_type == "expect_table_row_count_to_be_between":
            n = len(df)
            min_val = kwargs.get("min_value", 0)
            max_val = kwargs.get("max_value", float("inf"))
            success = min_val <= n <= max_val
            return {"success": success, "type": exp_type, "row_count": n}

        elif exp_type == "expect_column_to_exist":
            col = kwargs["column"]
            return {"success": col in df.columns, "type": exp_type, "column": col}

        else:
            return {"success": True, "type": exp_type, "note": "Tipo não implementado — skipped"}

    except Exception as e:
        return {"success": False, "type": exp_type, "error": str(e)}


def _log_result(result: dict) -> None:
    log_fn = logger.info if result["status"] == "pass" else logger.warning
    log_fn(
        "DQ suite %s: score=%.1f%% passed=%d/%d",
        result["suite"],
        result["score"],
        result["passed"],
        result["total"],
    )

    try:
        from google.cloud import logging as cloud_logging
        client = cloud_logging.Client()
        cloud_logger = client.logger("spepe-dataops-dq")
        cloud_logger.log_struct(result, severity="INFO" if result["status"] == "pass" else "WARNING")
    except Exception:
        pass
