"""CDC (Change Data Capture) package — incremental loading from Bronze.

Enables DataOps L5 capability: load only delta instead of full-refresh when
updated_at timestamp is available.
"""
from dataops.cdc.incremental_loader import IncrementalLoader, load_incremental

__all__ = ["IncrementalLoader", "load_incremental"]
