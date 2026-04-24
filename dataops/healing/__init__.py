"""Pipeline healing package — DataOps L5 self-healing capability.

Detects common failures (schema drift, null explosion, corrupted files) and
applies automatic correction or routes to manual queue.
"""
from dataops.healing.pipeline_healer import PipelineHealer, heal_pipeline_failure
from dataops.healing.schema_evolver import SchemaEvolver, evolve_schema

__all__ = [
    "PipelineHealer",
    "heal_pipeline_failure",
    "SchemaEvolver",
    "evolve_schema",
]
