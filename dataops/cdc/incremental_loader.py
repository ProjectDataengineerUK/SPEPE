"""Incremental loader — CDC via watermark column.

Loads only rows with `updated_at > last_watermark` to avoid expensive full-refresh.
Falls back to full-refresh if source has no watermark column.

Referência: DESIGN_SPEPE.md — Decisão 17 (DataOps L5).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger("spepe.dataops.cdc")

_CONFIG_PATH = Path(__file__).parent / "cdc_config.yaml"


@dataclass
class LoadResult:
    source: str
    rows_loaded: int
    strategy: str
    watermark_from: str | None
    watermark_to: str | None
    fell_back_to_full_refresh: bool = False


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}


class IncrementalLoader:
    """Loads a new Bronze partition incrementally using watermark-based CDC."""

    def __init__(self, source: str, config: dict | None = None):
        self.source = source
        self.config = config or _load_config()
        self.source_cfg = (self.config.get("sources", {}) or {}).get(source, {})
        self.defaults = self.config.get("defaults", {}) or {}
        self.watermark_col = self.source_cfg.get("watermark_column")
        self.strategy = self.source_cfg.get("strategy", "full_refresh")
        self.fallback = self.source_cfg.get("fallback", "full_refresh")

    def _read_watermark_from_bq(self) -> datetime | None:
        """Read last watermark from checkpoint table. Returns None on failure."""
        try:
            from google.cloud import bigquery  # type: ignore
        except ImportError:
            logger.debug("BigQuery SDK not available; skipping checkpoint read")
            return None

        table = self.defaults.get(
            "checkpoint_table", "spepe_mlops.cdc_checkpoints"
        )
        import os

        project = os.environ.get("GCP_PROJECT_ID", "")
        if not project:
            return None

        client = bigquery.Client(project=project)
        query = f"""
            SELECT last_watermark FROM `{project}.{table}`
            WHERE source = @source
            ORDER BY updated_at DESC LIMIT 1
        """
        try:
            job = client.query(
                query,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("source", "STRING", self.source)
                    ]
                ),
            )
            rows = list(job.result())
            if rows:
                return rows[0].last_watermark
        except Exception as exc:
            logger.warning("Could not read watermark for %s: %s", self.source, exc)
        return None

    def _write_watermark_to_bq(self, watermark: datetime) -> None:
        """Persist new watermark."""
        try:
            from google.cloud import bigquery  # type: ignore
        except ImportError:
            logger.debug("BigQuery SDK not available; skipping checkpoint write")
            return

        import os

        project = os.environ.get("GCP_PROJECT_ID", "")
        if not project:
            return
        table = self.defaults.get(
            "checkpoint_table", "spepe_mlops.cdc_checkpoints"
        )
        client = bigquery.Client(project=project)
        row = {
            "source": self.source,
            "last_watermark": watermark.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            errors = client.insert_rows_json(f"{project}.{table}", [row])
            if errors:
                logger.warning(
                    "Checkpoint write errors for %s: %s", self.source, errors
                )
        except Exception as exc:
            logger.warning("Checkpoint write failed for %s: %s", self.source, exc)

    def load(self, df_bronze: pd.DataFrame) -> tuple[pd.DataFrame, LoadResult]:
        """Filter Bronze dataframe to only delta since last watermark.

        If watermark column missing, returns full dataframe (fallback).
        """
        if (
            not self.watermark_col
            or self.watermark_col not in df_bronze.columns
        ):
            logger.info(
                "source=%s strategy=fallback_full_refresh reason=no_watermark",
                self.source,
            )
            return (
                df_bronze,
                LoadResult(
                    source=self.source,
                    rows_loaded=len(df_bronze),
                    strategy="full_refresh",
                    watermark_from=None,
                    watermark_to=None,
                    fell_back_to_full_refresh=True,
                ),
            )

        last_watermark = self._read_watermark_from_bq()
        if last_watermark is None:
            logger.info(
                "source=%s first_run full_refresh rows=%d",
                self.source,
                len(df_bronze),
            )
            watermark_series = pd.to_datetime(df_bronze[self.watermark_col], errors="coerce")
            new_watermark = watermark_series.max()
            delta_df = df_bronze
            watermark_from = None
        else:
            watermark_series = pd.to_datetime(df_bronze[self.watermark_col], errors="coerce")
            mask = watermark_series > pd.Timestamp(last_watermark)
            delta_df = df_bronze[mask].copy()
            new_watermark = watermark_series.max()
            watermark_from = last_watermark.isoformat() if last_watermark else None

        max_rows = self.defaults.get("max_delta_rows_per_batch", 500000)
        if len(delta_df) > max_rows:
            logger.warning(
                "source=%s delta=%d > max=%d; truncating",
                self.source,
                len(delta_df),
                max_rows,
            )
            delta_df = delta_df.head(max_rows)

        if pd.notna(new_watermark):
            self._write_watermark_to_bq(new_watermark.to_pydatetime())

        logger.info(
            "source=%s strategy=incremental rows=%d watermark=%s",
            self.source,
            len(delta_df),
            new_watermark,
        )
        return (
            delta_df,
            LoadResult(
                source=self.source,
                rows_loaded=len(delta_df),
                strategy=self.strategy,
                watermark_from=watermark_from,
                watermark_to=(
                    new_watermark.isoformat() if pd.notna(new_watermark) else None
                ),
                fell_back_to_full_refresh=False,
            ),
        )


def load_incremental(
    df_bronze: pd.DataFrame, source: str
) -> tuple[pd.DataFrame, LoadResult]:
    """Convenience wrapper to instantiate + load."""
    loader = IncrementalLoader(source)
    return loader.load(df_bronze)
