"""LLM eval runner — executes golden dataset against live agents."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

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


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="Run SPEPE LLM eval suite")
    parser.add_argument(
        "--responses-file",
        type=str,
        default=None,
        help="JSON file with pre-generated responses (format: {eval_id: response})"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of evaluations (for testing)"
    )
    args = parser.parse_args()

    logger.info("Starting SPEPE LLM eval runner...")

    responses = {}
    if args.responses_file:
        with open(args.responses_file, "r", encoding="utf-8") as f:
            responses = json.load(f)
        logger.info(f"Loaded {len(responses)} pre-generated responses from {args.responses_file}")
    else:
        logger.info("No responses file provided — using empty responses (all will fail)")

    report = run_eval_offline(responses)

    ci_pass = report["ci_pass"]
    exit_code = 0 if ci_pass else 1

    print(f"\n{'='*60}")
    print(f"EVAL REPORT: {report['overall_score']:.3f} (threshold: {report['threshold']})")
    print(f"Results: {report['passed']}/{report['total']} passed")
    print(f"Report saved to: {EVAL_REPORT_PATH}")
    print(f"{'='*60}\n")

    sys.exit(exit_code)
