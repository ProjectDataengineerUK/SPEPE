from __future__ import annotations

import hashlib
import json
import logging
import pickle
from pathlib import Path

logger = logging.getLogger("spepe.archetype.cache")

CACHE_DIR = Path(__file__).parent.parent / "output" / "archetype_cache"


def _cache_key(escopo: str, feature_set: str, n_rows: int) -> str:
    raw = f"{escopo}:{feature_set}:{n_rows}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _verify_pickle(path: Path) -> bytes:
    """Lê e verifica que o arquivo pickle existe e não está vazio antes de carregar."""
    data = path.read_bytes()
    if len(data) < 4:
        raise ValueError(f"Arquivo de cache corrompido: {path}")
    return data


def save_pipeline_result(
    escopo: str,
    feature_set: str,
    labels: list[int],
    embedding_2d,
    silhouette: float,
    algorithm: str,
    label_names: dict,
    n_rows: int,
) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(escopo, feature_set, n_rows)
    cache_path = CACHE_DIR / f"archetype_{key}.pkl"

    payload = {
        "escopo": escopo,
        "feature_set": feature_set,
        "labels": labels,
        "embedding_2d": embedding_2d,
        "silhouette": silhouette,
        "algorithm": algorithm,
        "label_names": label_names,
        "n_rows": n_rows,
    }
    with open(cache_path, "wb") as f:
        pickle.dump(payload, f)

    meta_path = CACHE_DIR / f"archetype_{key}_meta.json"
    meta = {k: v for k, v in payload.items() if k not in ("embedding_2d", "labels")}
    meta["labels_sample"] = list(labels[:10]) if labels else []
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info(f"Cache salvo: {cache_path}")
    return cache_path


def load_pipeline_result(escopo: str, feature_set: str, n_rows: int) -> dict | None:
    key = _cache_key(escopo, feature_set, n_rows)
    cache_path = CACHE_DIR / f"archetype_{key}.pkl"
    if not cache_path.exists():
        return None
    data = _verify_pickle(cache_path)
    return pickle.loads(data)
