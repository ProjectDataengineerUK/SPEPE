"""Snapshot manager — BigQuery Table Snapshots for Gold tables.

Creates a point-in-time snapshot of a Gold table with 7-day retention so we can
roll back data (as opposed to only rolling back models).

Usage:
    sm = SnapshotManager(project_id="spepe-prod")
    snap = sm.create("spepe_gold.fact_municipio_eleicao", job_id="job-123")

Referência: DESIGN_SPEPE.md — Decisão 17.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("spepe.dataops.versioning")

_ALLOWED_DATASETS = {"spepe_gold", "spepe_silver", "spepe_mlops", "spepe_snapshots"}
_TABLE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$")
_SNAPSHOT_DATASET_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$")


def _validate_table_ref(table_ref: str) -> None:
    parts = table_ref.split(".")
    if len(parts) != 2:
        raise ValueError(f"source_table deve ser 'dataset.tabela': {table_ref}")
    dataset, table = parts
    if dataset not in _ALLOWED_DATASETS:
        raise ValueError(
            f"Dataset não permitido: {dataset}. Permitidos: {_ALLOWED_DATASETS}"
        )
    if not _TABLE_RE.match(table):
        raise ValueError(f"Nome de tabela inválido: {table}")


@dataclass
class SnapshotInfo:
    source_table: str
    snapshot_table: str
    snapshot_id: str
    created_at: str
    expires_at: str
    job_id: str | None = None
    row_count: int | None = None
    checksum: str | None = None


class SnapshotManager:
    def __init__(
        self,
        project_id: str | None = None,
        retention_days: int = 7,
        snapshot_dataset: str | None = None,
    ):
        self.project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self.retention_days = retention_days
        self.snapshot_dataset = snapshot_dataset or "spepe_snapshots"

    def _snapshot_name(self, source_table: str, job_id: str | None) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = source_table.split(".")[-1]
        suffix = f"_{job_id[:8]}" if job_id else ""
        return f"{base}_{ts}{suffix}"

    def create(
        self,
        source_table: str,
        job_id: str | None = None,
    ) -> SnapshotInfo:
        """Create a BigQuery table snapshot with retention expiry."""
        if not self.project_id:
            raise RuntimeError("GCP_PROJECT_ID not configured")

        _validate_table_ref(source_table)

        try:
            from google.cloud import bigquery  # type: ignore
        except ImportError as exc:
            raise RuntimeError("google-cloud-bigquery not installed") from exc

        client = bigquery.Client(project=self.project_id)
        snapshot_name = self._snapshot_name(source_table, job_id)
        snapshot_table = f"{self.snapshot_dataset}.{snapshot_name}"
        source_fq = f"{self.project_id}.{source_table}"
        snapshot_fq = f"{self.project_id}.{snapshot_table}"

        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=self.retention_days)
        expires_iso = expires.strftime("%Y-%m-%dT%H:%M:%SZ")

        query = """
        CREATE SNAPSHOT TABLE `{}`
        CLONE `{}`
        OPTIONS (
          expiration_timestamp = TIMESTAMP '{}'
        )
        """.format(snapshot_fq, source_fq, expires_iso)
        logger.info("Creating snapshot: %s", snapshot_fq)
        job = client.query(query)
        job.result()

        row_count = self._count_rows(client, snapshot_fq)
        snapshot_id = hashlib.sha256(
            f"{source_table}|{now.isoformat()}|{job_id or ''}".encode()
        ).hexdigest()[:16]

        info = SnapshotInfo(
            source_table=source_fq,
            snapshot_table=snapshot_fq,
            snapshot_id=snapshot_id,
            created_at=now.isoformat(),
            expires_at=expires.isoformat(),
            job_id=job_id,
            row_count=row_count,
        )
        self._register(info)
        return info

    def _count_rows(self, client, fq_table: str) -> int | None:
        try:
            q = "SELECT COUNT(*) AS n FROM `{}`".format(fq_table)
            for row in client.query(q).result():
                return int(row.n)
        except Exception as exc:
            logger.warning("row_count failed for %s: %s", fq_table, exc)
        return None

    def _register(self, info: SnapshotInfo) -> None:
        """Register snapshot metadata in sentinel_kb / spepe_mlops.snapshots."""
        try:
            from google.cloud import bigquery  # type: ignore

            client = bigquery.Client(project=self.project_id)
            table = f"{self.project_id}.spepe_mlops.snapshots"
            row = {
                "source_table": info.source_table,
                "snapshot_table": info.snapshot_table,
                "snapshot_id": info.snapshot_id,
                "created_at": info.created_at,
                "expires_at": info.expires_at,
                "job_id": info.job_id,
                "row_count": info.row_count,
            }
            errors = client.insert_rows_json(table, [row])
            if errors:
                logger.warning("snapshot_register errors: %s", errors)
        except Exception as exc:
            logger.warning("snapshot_register failed: %s", exc)


def take_snapshot(source_table: str, job_id: str | None = None) -> SnapshotInfo:
    """Convenience wrapper."""
    return SnapshotManager().create(source_table, job_id)
