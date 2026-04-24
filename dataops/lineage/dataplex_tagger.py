"""Dataplex lineage tagger for Bronze→Silver→Gold pipeline runs."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("spepe.dataops.lineage")

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "spepe-dev")
DATAPLEX_LOCATION = os.environ.get("GCP_REGION", "southamerica-east1")


def tag_lineage_event(
    source_table: str,
    target_table: str,
    pipeline_run_id: str,
    rows_written: int,
    dq_score: float,
) -> bool:
    """Register a lineage event in Dataplex Data Lineage API."""
    try:
        from google.cloud import datacatalog_lineage_v1
        client = datacatalog_lineage_v1.LineageClient()

        process = datacatalog_lineage_v1.Process(
            display_name=f"spepe-pipeline-{pipeline_run_id}",
            attributes={"pipeline": "spepe-medallion", "run_id": pipeline_run_id},
        )
        process = client.create_process(
            parent=f"projects/{GCP_PROJECT}/locations/{DATAPLEX_LOCATION}",
            process=process,
        )

        run = datacatalog_lineage_v1.Run(
            display_name=pipeline_run_id,
            state=datacatalog_lineage_v1.Run.State.COMPLETED,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
        )
        run = client.create_run(parent=process.name, run=run)

        event = datacatalog_lineage_v1.LineageEvent(
            sources=[datacatalog_lineage_v1.EntityReference(fully_qualified_name=f"bigquery:{source_table}")],
            targets=[datacatalog_lineage_v1.EntityReference(fully_qualified_name=f"bigquery:{target_table}")],
        )
        client.create_lineage_event(parent=run.name, lineage_event=event)

        logger.info(f"Dataplex lineage registrado: {source_table} → {target_table}")
        return True

    except ImportError:
        logger.debug("google-cloud-datacatalog-lineage não disponível — lineage local apenas")
        _log_local_lineage(source_table, target_table, pipeline_run_id, rows_written, dq_score)
        return False
    except Exception as e:
        logger.warning(f"Dataplex lineage falhou: {e}")
        _log_local_lineage(source_table, target_table, pipeline_run_id, rows_written, dq_score)
        return False


def _log_local_lineage(source: str, target: str, run_id: str, rows: int, dq_score: float) -> None:
    import json
    from pathlib import Path
    lineage_path = Path("output/logs/lineage.jsonl")
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "source": source,
        "target": target,
        "rows_written": rows,
        "dq_score": dq_score,
    }
    with open(lineage_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
