"""Cloud Run Job: Bronze → Silver with DQ gate."""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("spepe.jobs.silver_transform")

YEARS = [2014, 2018, 2022]
DQ_THRESHOLD = float(os.environ.get("DQ_SCORE_THRESHOLD", "95.0"))


def main(uf: str, years: list[int] | None = None) -> None:
    from dataops.silver_transformer import transform_to_silver

    use_bq = bool(os.environ.get("GCP_PROJECT_ID"))

    target_years = years or YEARS
    all_ok = True

    for year in target_years:
        logger.info(f"Silver transform: {uf}/{year}")
        result = transform_to_silver(uf, year, use_bigquery=use_bq)

        if result.get("status") == "error":
            logger.warning(f"Skipped {uf}/{year}: {result.get('message')}")
            continue

        dq_score = result.get("dq_score", 0.0)
        if dq_score < DQ_THRESHOLD:
            logger.error(
                f"DQ FALHOU {uf}/{year}: score={dq_score:.1f}% < {DQ_THRESHOLD}%. Bloqueando Gold build."
            )
            all_ok = False
        else:
            logger.info(
                f"Silver OK {uf}/{year}: {result.get('rows')} rows, DQ={dq_score:.1f}%"
            )

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    parser.add_argument("--years", nargs="+", type=int, default=YEARS)
    args = parser.parse_args()
    main(args.uf, args.years)
