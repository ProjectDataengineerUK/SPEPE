"""LLM eval runner — executes golden dataset against live agents."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys

from mlops.eval.metrics import evaluate_response, EvalResult

logger = logging.getLogger("spepe.mlops.eval")

GOLDEN_DATASET_PATH = Path(__file__).parent / "golden_dataset.jsonl"
EVAL_SCORE_THRESHOLD = 0.85
EVAL_REPORT_PATH = Path("output/mlops/eval_report.json")
_REGISTRY_DIR = Path(__file__).parent.parent.parent / "agents" / "registry"
_SUPERVISOR_YAML = (
    Path(__file__).parent.parent.parent / "config" / "prompt_registry" / "supervisor_v1.0.0.yaml"
)
_AGENT_FILE_MAP = {
    "modelista": "modelista-bayesiano",
    "analista": "analista-eleitoral",
}


def load_golden_dataset(limit: int | None = None) -> list[dict]:
    items = []
    with open(GOLDEN_DATASET_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items[:limit] if limit else items


def _load_agent_system_prompt(agent_name: str) -> str:
    import re as _re
    import yaml as _yaml

    file_stem = _AGENT_FILE_MAP.get(agent_name, agent_name)
    md_path = _REGISTRY_DIR / f"{file_stem}.md"
    if md_path.exists():
        content = md_path.read_text(encoding="utf-8")
        m = _re.match(r"^---\n(.*?)\n---\n(.*)", content, _re.DOTALL)
        return m.group(2).strip() if m else content

    if _SUPERVISOR_YAML.exists():
        data = _yaml.safe_load(_SUPERVISOR_YAML.read_text(encoding="utf-8"))
        return str(data.get("system_prompt", ""))

    return "Você é um especialista em análise eleitoral brasileira."


def generate_responses_live(dataset: list[dict]) -> dict[str, str]:
    """Generate responses via Claude Haiku for each eval item."""
    import anthropic

    client = anthropic.Anthropic()
    responses: dict[str, str] = {}

    for item in dataset:
        eval_id = item["id"]
        agent = item.get("agent", "supervisor")
        user_input = item.get("input", "")
        system_prompt = _load_agent_system_prompt(agent)

        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_input}],
            )
            responses[eval_id] = msg.content[0].text
            logger.info("Generated response for %s (%s)", eval_id, agent)
        except Exception as exc:
            logger.warning("Failed to generate response for %s: %s", eval_id, exc)
            responses[eval_id] = ""

    return responses


def run_eval_on_dataset(dataset: list[dict], responses: dict[str, str]) -> dict:
    """Evaluate responses against the given dataset items."""
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

        results.append(
            {
                "id": result.eval_id,
                "agent": result.agent,
                "score": result.score,
                "passed": result.passed,
                "feedback": result.feedback,
            }
        )
        total_score += result.score
        if result.passed:
            passed += 1

    n = len(dataset)
    overall_score = total_score / n if n > 0 else 0.0
    ci_pass = overall_score >= EVAL_SCORE_THRESHOLD

    report = {
        "overall_score": overall_score,
        "ci_pass": ci_pass,
        "threshold": EVAL_SCORE_THRESHOLD,
        "total": n,
        "passed": passed,
        "failed": n - passed,
        "results": results,
    }

    EVAL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVAL_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if not ci_pass:
        logger.warning("LLM eval FALHOU: score=%.3f < %.2f", overall_score, EVAL_SCORE_THRESHOLD)
    else:
        logger.info("LLM eval OK: score=%.3f passed=%d/%d", overall_score, passed, n)

    return report


# Keep old name for backwards compat
def run_eval_offline(responses: dict[str, str]) -> dict:
    return run_eval_on_dataset(load_golden_dataset(), responses)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="Run SPEPE LLM eval suite")
    parser.add_argument(
        "--responses-file",
        type=str,
        default=None,
        help="JSON file with pre-generated responses (format: {eval_id: response})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of evaluations (default: 10 for live mode, all for offline)",
    )
    args = parser.parse_args()

    logger.info("Starting SPEPE LLM eval runner...")

    if args.responses_file:
        with open(args.responses_file, encoding="utf-8") as f:
            responses = json.load(f)
        logger.info("Loaded %d pre-generated responses", len(responses))
        dataset = load_golden_dataset()
    elif os.environ.get("ANTHROPIC_API_KEY"):
        ci_limit = args.limit or int(os.environ.get("EVAL_CI_LIMIT", "10"))
        logger.info("Generating live responses via Claude Haiku (limit=%d)...", ci_limit)
        dataset = load_golden_dataset(limit=ci_limit)
        responses = generate_responses_live(dataset)
    else:
        logger.error("Provide --responses-file or set ANTHROPIC_API_KEY")
        sys.exit(1)

    report = run_eval_on_dataset(dataset, responses)

    print(f"\n{'=' * 60}")
    print(f"EVAL REPORT: {report['overall_score']:.3f} (threshold: {report['threshold']})")
    print(f"Results: {report['passed']}/{report['total']} passed")
    print(f"Report saved to: {EVAL_REPORT_PATH}")
    print(f"{'=' * 60}\n")

    sys.exit(0 if report["ci_pass"] else 1)
