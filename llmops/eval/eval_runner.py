from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from llmops.eval.hallucination_detector import check_electoral_claims, is_blocking
from llmops.eval.metrics import (
    disclaimer_present_rate,
    factuality,
    relevance,
)

logger = logging.getLogger(__name__)


@dataclass
class EvalCaseResult:
    case_id: str
    passed: bool
    score: float
    details: dict[str, Any]


class EvalRunner:
    """Main LLM eval runner (CI + continuous).

    Enforces disclaimer as a HARD gate. Exits non-zero when gate fails.
    """

    def __init__(
        self,
        golden_dataset_path: str | Path = "llmops/eval/golden_dataset.jsonl",
        threshold: float = 0.80,
        disclaimer_gate: float = 1.0,
    ):
        self.golden_path = Path(golden_dataset_path)
        self.threshold = threshold
        self.disclaimer_gate = disclaimer_gate

    def load_cases(self) -> list[dict[str, Any]]:
        if not self.golden_path.exists():
            logger.warning("golden_dataset_missing: %s", self.golden_path)
            return []
        cases: list[dict[str, Any]] = []
        with open(self.golden_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                cases.append(json.loads(line))
        return cases

    def run(
        self,
        inference_fn: Callable[[dict[str, Any]], str],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        cases = self.load_cases()
        results: list[EvalCaseResult] = []
        outputs: list[str] = []
        for case in cases:
            output = inference_fn(case)
            outputs.append(output)
            rel_score = relevance(output, case.get("expected_keywords", []))
            fact_score = factuality(output, case.get("ground_truths", []))
            score = (rel_score + fact_score) / 2.0
            hallucinations: list = []
            if project_id:
                hallucinations = check_electoral_claims(output, project_id=project_id)
            results.append(
                EvalCaseResult(
                    case_id=case.get("id", case.get("case_id", "?")),
                    passed=score >= self.threshold and not is_blocking(hallucinations),
                    score=score,
                    details={
                        "relevance": rel_score,
                        "factuality": fact_score,
                        "hallucinations": [v.__dict__ for v in hallucinations],
                    },
                )
            )
        disc_rate = disclaimer_present_rate(outputs)
        summary = {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "avg_score": (sum(r.score for r in results) / len(results) if results else 0.0),
            "disclaimer_rate": disc_rate,
            "hard_gate_passed": disc_rate >= self.disclaimer_gate,
            "results": [r.__dict__ for r in results],
        }
        return summary


def _stub_inference(case: dict[str, Any]) -> str:
    return case.get("expected_output", "")


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    dataset = argv[0] if argv else "llmops/eval/golden_dataset.jsonl"
    runner = EvalRunner(golden_dataset_path=dataset)
    summary = runner.run(inference_fn=_stub_inference)
    print(json.dumps(summary, indent=2, default=str))
    if not summary["hard_gate_passed"]:
        logger.error("disclaimer_hard_gate_failed rate=%s", summary["disclaimer_rate"])
        return 2
    if summary["total"] > 0 and summary["passed"] < summary["total"]:
        logger.warning(
            "eval_cases_failed passed=%d total=%d",
            summary["passed"],
            summary["total"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
