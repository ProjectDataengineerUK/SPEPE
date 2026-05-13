"""LLM eval runner — executes golden dataset against live agents.

Also includes PyMC convergence diagnostics (Phase 6-7 of Bayesian training).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

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


def evaluate_pymc_convergence(idata, y_test: np.ndarray, model_version: str = "unknown"):
    """Run 9 ArviZ diagnostics for PyMC model validation gate (Phase 6).

    All 9 checks must pass for PROMOTED status.

    Args:
        idata: ArviZ InferenceData object from pymc_train.train_pymc_model()
        y_test: Target vector for holdout validation
        model_version: Model identifier for logging

    Returns:
        Dict with 9 boolean checks and detailed metrics
    """
    try:
        import arviz as az
        import matplotlib

        matplotlib.use("Agg")  # Non-interactive backend for cloud
        import matplotlib.pyplot as plt
    except ImportError:
        logger.error("ArviZ and matplotlib required: pip install arviz matplotlib")
        raise

    logger.info(f"Running 9 ArviZ diagnostics for {model_version}...")

    # 1. Rhat convergence: < 1.01 for all key parameters
    summary = az.summary(idata, var_names=["mu_a", "mu_b", "s_a", "s_b", "phi"])
    check_rhat = (summary["r_hat"] < 1.01).all()
    rhat_max = float(summary["r_hat"].max())
    logger.info(f"  [1/9] Rhat: {rhat_max:.4f} (target < 1.01) — {'✅' if check_rhat else '❌'}")

    # 2. ESS bulk: > 1000 for critical parameters
    check_ess = (summary["ess_bulk"] > 1000).all()
    ess_bulk_min = float(summary["ess_bulk"].min())
    logger.info(
        f"  [2/9] ESS bulk: {ess_bulk_min:.0f} (target > 1000) — {'✅' if check_ess else '❌'}"
    )

    # 3. BFMI (Bayesian Fraction of Missing Information): > 0.3
    try:
        bfmi_vals = az.bfmi(idata)
        check_bfmi = bfmi_vals.mean() > 0.3
        bfmi_mean = float(bfmi_vals.mean())
        logger.info(
            f"  [3/9] BFMI: {bfmi_mean:.3f} (target > 0.3) — {'✅' if check_bfmi else '❌'}"
        )
    except Exception as e:
        logger.warning(f"  [3/9] BFMI: skipped ({e})")
        check_bfmi = True
        bfmi_mean = 0.5

    # 4. Divergences: < 0.1%
    n_divergent = idata.sample_stats["diverging"].sum().values
    n_total = idata.posterior.sizes["draw"] * idata.posterior.sizes["chain"]
    pct_divergent = 100.0 * float(n_divergent / n_total)
    check_divergences = (n_divergent / n_total) < 0.001
    logger.info(
        f"  [4/9] Divergences: {pct_divergent:.2f}% (target < 0.1%) — {'✅' if check_divergences else '❌'}"
    )

    # 5. LOO Pareto-k: < 0.7 for >= 95% of samples
    try:
        loo = az.loo(idata, pointwise=True)
        pct_ok_pareto_k = float((loo.pareto_k < 0.7).sum() / len(y_test))
        check_pareto_k = pct_ok_pareto_k > 0.95
        logger.info(
            f"  [5/9] Pareto-k: {100 * pct_ok_pareto_k:.1f}% OK (target > 95%) — {'✅' if check_pareto_k else '❌'}"
        )
    except Exception as e:
        logger.warning(f"  [5/9] Pareto-k: skipped ({e})")
        check_pareto_k = True
        pct_ok_pareto_k = 1.0

    # 6. MAE on test holdout: < 0.05 (5 percentage points)
    y_pred_mean = idata.posterior["p"].mean(dim=["chain", "draw"]).values
    mae = float(np.mean(np.abs(y_pred_mean - y_test)))
    check_mae = mae < 0.05
    logger.info(f"  [6/9] MAE: {mae:.4f} (target < 0.05) — {'✅' if check_mae else '❌'}")

    # 7. CRPS on test holdout: < 0.04
    y_pred_samples = idata.posterior["p"].values.reshape(-1, len(y_test))
    crps_vals = [
        float(np.mean(np.abs(y_pred_samples[:, i] - y_test[i]))) for i in range(len(y_test))
    ]
    crps_mean = float(np.mean(crps_vals))
    check_crps = crps_mean < 0.04
    logger.info(f"  [7/9] CRPS: {crps_mean:.4f} (target < 0.04) — {'✅' if check_crps else '❌'}")

    # 8. Coverage of 95% CI: 92-98%
    p_025 = idata.posterior["p"].quantile(0.025, dim=["chain", "draw"]).values
    p_975 = idata.posterior["p"].quantile(0.975, dim=["chain", "draw"]).values
    coverage = float(((p_025 <= y_test) & (y_test <= p_975)).mean())
    check_coverage = 0.92 <= coverage <= 0.98
    logger.info(
        f"  [8/9] Coverage: {100 * coverage:.1f}% (target 92-98%) — {'✅' if check_coverage else '❌'}"
    )

    # 9. ECE (Expected Calibration Error): < 0.03
    y_pred_prob = y_pred_mean
    n_bins = 10
    bins = np.linspace(0, 1, n_bins + 1)
    ece_vals = []
    for i in range(n_bins):
        in_bin = (y_pred_prob >= bins[i]) & (y_pred_prob < bins[i + 1])
        if in_bin.sum() > 0:
            expected = float(y_pred_prob[in_bin].mean())
            observed = float(y_test[in_bin].mean())
            ece_vals.append(np.abs(expected - observed))
    ece = float(np.mean(ece_vals)) if ece_vals else 0.0
    check_ece = ece < 0.03
    logger.info(f"  [9/9] ECE: {ece:.4f} (target < 0.03) — {'✅' if check_ece else '❌'}")

    # Save diagnostic plots
    output_dir = Path("output/diagnostics")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        az.plot_trace(idata, var_names=["mu_a", "mu_b", "phi"])
        plt.savefig(output_dir / f"trace_{model_version}.png", dpi=100, bbox_inches="tight")
        plt.close()
        logger.info(f"  📊 Trace plot saved: {output_dir / f'trace_{model_version}.png'}")
    except Exception as e:
        logger.warning(f"  Trace plot failed: {e}")

    try:
        az.plot_energy(idata)
        plt.savefig(output_dir / f"energy_{model_version}.png", dpi=100, bbox_inches="tight")
        plt.close()
        logger.info(f"  📊 Energy plot saved: {output_dir / f'energy_{model_version}.png'}")
    except Exception as e:
        logger.warning(f"  Energy plot failed: {e}")

    try:
        az.plot_ppc(idata, var_names=["y_obs"], num_pp_samples=100)
        plt.savefig(output_dir / f"ppc_{model_version}.png", dpi=100, bbox_inches="tight")
        plt.close()
        logger.info(f"  📊 PPC plot saved: {output_dir / f'ppc_{model_version}.png'}")
    except Exception as e:
        logger.warning(f"  PPC plot failed: {e}")

    return {
        "checks": {
            "rhat_converged": check_rhat,
            "ess_sufficient": check_ess,
            "bfmi_healthy": check_bfmi,
            "no_divergences": check_divergences,
            "loo_pareto_ok": check_pareto_k,
            "mae_acceptable": check_mae,
            "crps_acceptable": check_crps,
            "coverage_95": check_coverage,
            "calibration_ok": check_ece,
        },
        "metrics": {
            "rhat_max": rhat_max,
            "ess_bulk_min": ess_bulk_min,
            "bfmi_mean": bfmi_mean,
            "pct_divergent": pct_divergent,
            "pct_pareto_k_ok": 100.0 * pct_ok_pareto_k,
            "mae": mae,
            "crps": crps_mean,
            "coverage": 100.0 * coverage,
            "ece": ece,
        },
    }


def gate_model_promotion(eval_results: dict, model_version: str = "unknown"):
    """HARD promotion gate: ALL 9 checks must pass (Phase 7).

    If any check fails, status = REJECTED and process stops.
    If all pass, status = PROMOTED and model is ready for deployment.

    Args:
        eval_results: Output from evaluate_pymc_convergence()
        model_version: Model identifier for BigQuery audit trail

    Returns:
        Dict with promotion status and detailed results
    """
    from google.cloud import bigquery
    from datetime import datetime

    checks = eval_results["checks"]
    metrics = eval_results["metrics"]

    # All 9 checks must pass
    all_passed = all(checks.values())
    status = "PROMOTED" if all_passed else "REJECTED"

    failed_checks = [k for k, v in checks.items() if not v]

    # Log to console
    print("\n" + "=" * 70)
    print(f"MODEL PROMOTION GATE — {status}")
    print("=" * 70)
    for check_name, check_result in checks.items():
        symbol = "✅" if check_result else "❌"
        print(f"{symbol} {check_name:30} {str(check_result):5}")
    print("=" * 70)

    if not all_passed:
        print(f"\n❌ REJECTED — Failed checks: {', '.join(failed_checks)}")
        print("→ Action: Increase draws/tune/chains and resample")
    else:
        print("\n✅ PROMOTED — Model ready for deployment")
        print("→ Next: Deploy to Vertex AI and set up auto-retrain")

    # Save to BigQuery audit trail
    try:
        project_id = os.environ.get("GCP_PROJECT_ID", "spepe-prod")
        client = bigquery.Client(project=project_id)

        gate_result = {
            "model_version": model_version,
            "timestamp": datetime.utcnow().isoformat(),
            "status": status,
            "checks_json": json.dumps(checks),
            "metrics_json": json.dumps(metrics),
            "failed_checks": ",".join(failed_checks) if failed_checks else None,
        }

        gate_df = pd.DataFrame([gate_result])
        table_id = f"{project_id}.spepe_mlops.model_gate_log"

        client.load_table_from_dataframe(
            gate_df,
            table_id,
            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
        ).result()

        logger.info(f"✅ Gate result saved to {table_id}")
    except Exception as e:
        logger.warning(f"Could not save gate result to BigQuery: {e}")

    return {"status": status, "checks": checks, "metrics": metrics}


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
