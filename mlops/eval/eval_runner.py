"""LLM eval runner — executes golden dataset against live agents."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from mlops.eval.metrics import evaluate_response, EvalResult

logger = logging.getLogger("spepe.mlops.eval")

GOLDEN_DATASET_PATH = Path(__file__).parent / "golden_dataset.jsonl"
EVAL_SCORE_THRESHOLD = 0.85
EVAL_REPORT_PATH = Path("output/mlops/eval_report.json")


def load_golden_dataset(limit: int | None = None) -> list[dict]:
    items = []
    with open(GOLDEN_DATASET_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items[:limit] if limit else items


def run_eval_offline(responses: dict[str, str]) -> dict:
    """Run eval against pre-generated responses (for CI)."""
    dataset = load_golden_dataset()
    results = []
    total_score = 0.0
    passed = 0

    for item in dataset:
        eval_id = item.get("id", "")
        response = responses.get(eval_id, "")
        if not response:
            result = EvalResult(
                eval_id=eval_id,
                agent=item.get("agent", ""),
                score=0.0,
                passed=False,
                checks={},
                feedback=["Sem resposta disponível para este eval_id"],
            )
        else:
            result = evaluate_response(response, item)

        results.append({
            "id": result.eval_id,
            "agent": result.agent,
            "score": result.score,
            "passed": result.passed,
            "feedback": result.feedback,
        })
        total_score += result.score
        if result.passed:
            passed += 1

    overall_score = total_score / len(dataset) if dataset else 0.0
    ci_pass = overall_score >= EVAL_SCORE_THRESHOLD

    report = {
        "overall_score": overall_score,
        "ci_pass": ci_pass,
        "threshold": EVAL_SCORE_THRESHOLD,
        "total": len(dataset),
        "passed": passed,
        "failed": len(dataset) - passed,
        "results": results,
    }

    EVAL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVAL_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if not ci_pass:
        logger.warning(f"LLM eval FALHOU: score={overall_score:.3f} < {EVAL_SCORE_THRESHOLD}")
    else:
        logger.info(f"LLM eval OK: score={overall_score:.3f} passed={passed}/{len(dataset)}")

    return report
