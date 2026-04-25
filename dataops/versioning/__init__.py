"""Data versioning — BigQuery Table Snapshots for Gold layer (7-day retention).

Referência: DESIGN_SPEPE.md — Decisão 17 (DataOps L5 data versioning).
"""

from dataops.versioning.snapshot_manager import SnapshotManager, take_snapshot

__all__ = ["SnapshotManager", "take_snapshot"]
