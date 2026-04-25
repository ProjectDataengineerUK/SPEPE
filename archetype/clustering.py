from __future__ import annotations

import logging
import numpy as np

logger = logging.getLogger("spepe.archetype.clustering")


def run_hdbscan(
    X: np.ndarray,
    min_cluster_size: int = 30,
    min_samples: int = 5,
) -> np.ndarray:
    try:
        import hdbscan
    except ImportError:
        raise ImportError("hdbscan não instalado. Execute: pip install hdbscan")

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_method="eom",
        prediction_data=True,
    )
    return clusterer.fit_predict(X)


def run_kmeans(X: np.ndarray, k: int = 8, random_state: int = 42) -> np.ndarray:
    from sklearn.cluster import KMeans

    model = KMeans(n_clusters=k, random_state=random_state, n_init="auto")
    return model.fit_predict(X)


def run_gmm(X: np.ndarray, n_components: int = 8, random_state: int = 42) -> np.ndarray:
    from sklearn.mixture import GaussianMixture

    model = GaussianMixture(n_components=n_components, random_state=random_state)
    return model.fit_predict(X)


def compute_silhouette(X: np.ndarray, labels: np.ndarray) -> float:
    from sklearn.metrics import silhouette_score

    mask = labels != -1
    if mask.sum() < 2 or len(set(labels[mask])) < 2:
        return 0.0
    return float(silhouette_score(X[mask], labels[mask]))


def select_best_algorithm(
    X: np.ndarray,
    silhouette_threshold: float = 0.45,
    min_cluster_size: int = 30,
) -> tuple[np.ndarray, str, float]:
    labels = run_hdbscan(X, min_cluster_size=min_cluster_size)
    score = compute_silhouette(X, labels)
    logger.info(f"HDBSCAN silhouette={score:.3f}, clusters={len(set(labels)) - (1 if -1 in labels else 0)}")

    if score >= silhouette_threshold:
        return labels, "hdbscan", score

    logger.warning(f"HDBSCAN silhouette {score:.3f} < {silhouette_threshold}. Fallback para K-means k=8.")
    labels_km = run_kmeans(X, k=8)
    score_km = compute_silhouette(X, labels_km)
    logger.info(f"K-means silhouette={score_km:.3f}")
    return labels_km, "kmeans_fallback", score_km
