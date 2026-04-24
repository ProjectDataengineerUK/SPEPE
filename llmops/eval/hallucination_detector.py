from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


CLAIM_PATTERN = re.compile(
    r"(\w[\w\s]+?)\s+(\d+[\.,]\d+)\s*%\s+em\s+([A-Z]{2})",
    re.IGNORECASE,
)


@dataclass
class HallucinationViolation:
    claim: str
    candidato: str
    sg_uf: str
    claimed_pct: float
    actual_pct: float | None
    diff_pp: float | None


def _bq_client(project_id: str):
    try:
        from google.cloud import bigquery

        return bigquery.Client(project=project_id)
    except Exception as exc:
        logger.info("bigquery_unavailable_for_halluc_check: %s", exc)
        return None


def check_electoral_claims(
    output: str,
    project_id: str,
    ano_eleicao: int = 2022,
    divergence_threshold_pp: float = 5.0,
    fetcher=None,
) -> list[HallucinationViolation]:
    """Detects `Candidato XX,YY% em UF` claims and compares with Gold.

    Blocks output if any claim diverges by more than `divergence_threshold_pp`.
    `fetcher` is an optional callable `(candidato, uf, ano) -> float | None`
    used in tests to bypass BigQuery.
    """
    violations: list[HallucinationViolation] = []
    fetch = fetcher or _default_fetcher(project_id, ano_eleicao)

    for match in CLAIM_PATTERN.finditer(output):
        candidato_raw, pct_str, uf = match.group(1), match.group(2), match.group(3)
        candidato = candidato_raw.strip().split()[-1]
        try:
            claimed = float(pct_str.replace(",", "."))
        except ValueError:
            continue
        actual = fetch(candidato, uf.upper())
        diff = abs((actual - claimed)) if actual is not None else None
        if actual is None:
            continue
        if diff is not None and diff > divergence_threshold_pp:
            violations.append(
                HallucinationViolation(
                    claim=match.group(0).strip(),
                    candidato=candidato,
                    sg_uf=uf.upper(),
                    claimed_pct=claimed,
                    actual_pct=actual,
                    diff_pp=diff,
                )
            )
    return violations


def _default_fetcher(project_id: str, ano: int):
    client = _bq_client(project_id)
    if client is None:
        return lambda _c, _u: None

    def fetch(candidato: str, uf: str) -> float | None:
        query = f"""
            SELECT pct_votos_validos
            FROM `{project_id}.spepe_gold.fact_municipio_eleicao`
            WHERE LOWER(nm_candidato) LIKE LOWER(@name)
              AND sg_uf = @uf AND ano_eleicao = @ano
            LIMIT 1
        """
        from google.cloud import bigquery

        job = client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("name", "STRING", f"%{candidato}%"),
                    bigquery.ScalarQueryParameter("uf", "STRING", uf),
                    bigquery.ScalarQueryParameter("ano", "INT64", ano),
                ]
            ),
        )
        rows = list(job.result())
        if not rows:
            return None
        return float(rows[0].pct_votos_validos)

    return fetch


def is_blocking(violations: list[HallucinationViolation]) -> bool:
    return any(v.diff_pp is not None and v.diff_pp > 5.0 for v in violations)
