"""Model promotion: champion/challenger comparison, Vertex Model Registry, rollback support."""
from __future__ import annotations

import json
import logging
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("spepe.mlops.promote")

MODEL_DIR = Path("output/models")
CHAMPION_PATH = MODEL_DIR / "champion.pkl"
CHAMPION_META_PATH = MODEL_DIR / "champion_meta.json"
CHALLENGER_PATH = MODEL_DIR / "challenger.pkl"
CHALLENGER_META_PATH = MODEL_DIR / "challenger_meta.json"


def promote_if_better(
    new_model,
    new_metrics: dict,
    metric_key: str = "brier_score",
    higher_is_better: bool = False,
    save_as_challenger: bool = True,
) -> bool:
    """Promote new model to champion if it outperforms current champion.

    With save_as_challenger=True (default for canary flow), the new model is first
    saved as challenger. Actual promotion to champion happens via canary_manager after
    the 48h traffic-split evaluation window.
    """
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if CHAMPION_META_PATH.exists():
        with open(CHAMPION_META_PATH, encoding="utf-8") as f:
            champion_meta = json.load(f)
        champion_score = champion_meta.get("metrics", {}).get(metric_key, 0.0 if higher_is_better else float("inf"))
    else:
        champion_score = 0.0 if higher_is_better else float("inf")
        champion_meta = {}

    new_score = new_metrics.get(metric_key, 0.0)
    should_promote = (new_score > champion_score) if higher_is_better else (new_score < champion_score)

    if should_promote and save_as_challenger:
        _save_challenger(new_model, new_metrics, champion_score, metric_key)
        logger.info(
            "Challenger saved for canary: %s=%.4f vs champion=%.4f",
            metric_key, new_score, champion_score,
        )
        return True

    if should_promote:
        _save_champion(new_model, new_metrics, champion_score, metric_key)
        _register_vertex(new_model, new_metrics)
        return True

    logger.info("Champion mantido: novo=%.4f <= atual=%.4f", new_score, champion_score)
    return False


def finalize_promotion(rollback: bool = False) -> None:
    """Called by canary_manager after evaluation window: promote challenger or rollback."""
    if rollback:
        if CHALLENGER_PATH.exists():
            CHALLENGER_PATH.unlink()
        if CHALLENGER_META_PATH.exists():
            CHALLENGER_META_PATH.unlink()
        logger.warning("Canary rollback: challenger discarded, champion remains")
        return

    if not CHALLENGER_PATH.exists():
        logger.error("No challenger found to finalize promotion")
        return

    raw = CHALLENGER_PATH.read_bytes()
    if len(raw) < 4:
        raise RuntimeError("Arquivo challenger.pkl corrompido")
    challenger_model = pickle.loads(raw)
    with open(CHALLENGER_META_PATH, encoding="utf-8") as f:
        challenger_meta = json.load(f)

    _save_champion(challenger_model, challenger_meta["metrics"],
                   challenger_meta.get("previous_champion_score", 0.0),
                   challenger_meta.get("metric_key", "brier_score"))
    CHALLENGER_PATH.unlink(missing_ok=True)
    CHALLENGER_META_PATH.unlink(missing_ok=True)
    _register_vertex(challenger_model, challenger_meta["metrics"])
    logger.info("Canary promotion finalized: challenger is now champion")


def _save_challenger(model, metrics: dict, prev_score: float, metric_key: str) -> None:
    with open(CHALLENGER_PATH, "wb") as f:
        pickle.dump(model, f)
    meta = {
        "metrics": metrics,
        "metric_key": metric_key,
        "previous_champion_score": prev_score,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(CHALLENGER_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _save_champion(model, metrics: dict, prev_score: float, metric_key: str) -> None:
    with open(CHAMPION_PATH, "wb") as f:
        pickle.dump(model, f)
    meta = {
        "metrics": metrics,
        "promoted_from": "challenger",
        "previous_champion_score": prev_score,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(CHAMPION_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info("Champion saved: %s=%.4f (was %.4f)", metric_key, metrics.get(metric_key, 0), prev_score)


def load_champion():
    if not CHAMPION_PATH.exists():
        raise FileNotFoundError("Nenhum modelo champion disponível. Execute o pipeline de treino primeiro.")
    raw = CHAMPION_PATH.read_bytes()
    if len(raw) < 4:
        raise RuntimeError("Arquivo champion.pkl corrompido")
    return pickle.loads(raw)


def _register_vertex(model, metrics: dict) -> None:
    project = os.environ.get("GCP_PROJECT_ID", "")
    if not project:
        return
    try:
        from google.cloud import aiplatform
        aiplatform.init(project=project, location=os.environ.get("VERTEX_LOCATION", "us-central1"))

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp:
            pickle.dump(model, tmp)

        model_obj = aiplatform.Model.upload(
            display_name="spepe-electoral-model",
            artifact_uri=f"gs://{project}-models/",
            serving_container_image_uri="us-docker.pkg.dev/vertex-ai/prediction/sklearn-cpu.1-3:latest",
            description=f"SPEPE electoral model | accuracy={metrics.get('accuracy', 0):.3f}",
        )
        logger.info(f"Modelo registrado no Vertex AI Model Registry: {model_obj.resource_name}")
    except Exception as e:
        logger.warning(f"Vertex Model Registry falhou: {e}")
