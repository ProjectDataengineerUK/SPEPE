"""Cloud Run Job: Silver → Gold (3 fact tables)."""
from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.gold_build")


def main() -> None:
    from dataops.gold_builder import build_gold
    from dataops.dq.runner import run_suite
    from pathlib import Path
    import pandas as pd

    use_bq = bool(os.environ.get("GCP_PROJECT_ID"))
    result = build_gold(use_bigquery=use_bq)

    if result.get("status") == "error":
        logger.error(f"Gold build falhou: {result.get('message')}")
        sys.exit(1)

    gold_path = Path("data/gold/fact_municipio_eleicao.parquet")
    if gold_path.exists():
        df_gold = pd.read_parquet(gold_path)
        dq_result = run_suite(df_gold, "gold")
        logger.info(f"Gold DQ: score={dq_result['score']:.1f}% passed={dq_result['passed']}/{dq_result['total']}")

    logger.info(f"Gold build concluído: {result.get('tables')}")


if __name__ == "__main__":
    main()
