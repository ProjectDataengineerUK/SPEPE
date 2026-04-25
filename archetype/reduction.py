from __future__ import annotations

import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler


def normalize(X: np.ndarray) -> np.ndarray:
    return StandardScaler().fit_transform(X)


def reduce_pca(X: np.ndarray, n_components: int = 50) -> np.ndarray:
    n_components = min(n_components, X.shape[1], X.shape[0] - 1)
    return PCA(n_components=n_components, random_state=42).fit_transform(X)


def reduce_umap(X: np.ndarray, n_components: int = 2, random_state: int = 42) -> np.ndarray:
    try:
        import umap
    except ImportError:
        raise ImportError("umap-learn não instalado. Execute: pip install umap-learn")

    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=15,
        min_dist=0.1,
        random_state=random_state,
    )
    return reducer.fit_transform(X)
