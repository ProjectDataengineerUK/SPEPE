from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_municipios_df():
    rng = np.random.default_rng(42)
    n = 200
    return pd.DataFrame({
        "cd_municipio": range(3500000, 3500000 + n),
        "nm_municipio": [f"Municipio_{i}" for i in range(n)],
        "sg_uf": ["SP"] * n,
        "idhm_2010": rng.uniform(0.5, 0.9, n),
        "renda_media_domiciliar": rng.uniform(800, 5000, n),
        "pct_analfabetos": rng.uniform(0.01, 0.25, n),
        "pct_rural": rng.uniform(0.0, 0.8, n),
        "pib_per_capita": rng.uniform(5000, 80000, n),
        "pct_votos_pt_2022": rng.uniform(0.1, 0.8, n),
        "pct_votos_pl_2022": rng.uniform(0.1, 0.8, n),
        "populacao_2022": rng.integers(5000, 12_000_000, n),
        "taxa_desemprego": rng.uniform(0.03, 0.25, n),
        "anos_estudo_medio": rng.uniform(5.0, 12.0, n),
    })


class TestFeatureSelection:
    def test_select_features_returns_available_cols(self, sample_municipios_df):
        from archetype.features import select_features
        _, cols = select_features(sample_municipios_df, "socioeconomic")
        assert len(cols) > 0
        assert all(c in sample_municipios_df.columns for c in cols)

    def test_prepare_feature_matrix_no_nans(self, sample_municipios_df):
        from archetype.features import prepare_feature_matrix, select_features
        _, cols = select_features(sample_municipios_df, "socioeconomic+electoral")
        X = prepare_feature_matrix(sample_municipios_df, cols)
        assert not X.isnull().any().any()


class TestClustering:
    def test_kmeans_returns_valid_labels(self, sample_municipios_df):
        from archetype.clustering import run_kmeans
        from archetype.features import prepare_feature_matrix, select_features
        from archetype.reduction import normalize, reduce_pca

        _, cols = select_features(sample_municipios_df, "socioeconomic+electoral")
        X = prepare_feature_matrix(sample_municipios_df, cols).values
        X_s = normalize(X)
        X_pca = reduce_pca(X_s, n_components=5)
        labels = run_kmeans(X_pca, k=4)
        assert len(labels) == len(sample_municipios_df)
        assert set(labels).issubset(set(range(4)))

    def test_silhouette_positive_for_good_clusters(self, sample_municipios_df):
        from archetype.clustering import compute_silhouette, run_kmeans
        from archetype.features import prepare_feature_matrix, select_features
        from archetype.reduction import normalize, reduce_pca

        _, cols = select_features(sample_municipios_df, "socioeconomic+electoral")
        X = prepare_feature_matrix(sample_municipios_df, cols).values
        X_s = normalize(X)
        X_pca = reduce_pca(X_s, n_components=5)
        labels = run_kmeans(X_pca, k=4)
        score = compute_silhouette(X_pca, labels)
        assert score >= 0.0


class TestReduction:
    def test_pca_reduces_dimensions(self, sample_municipios_df):
        from archetype.features import prepare_feature_matrix, select_features
        from archetype.reduction import normalize, reduce_pca

        _, cols = select_features(sample_municipios_df, "socioeconomic+electoral")
        X = prepare_feature_matrix(sample_municipios_df, cols).values
        X_s = normalize(X)
        X_pca = reduce_pca(X_s, n_components=5)
        assert X_pca.shape == (len(sample_municipios_df), 5)


class TestCards:
    def test_cards_count_matches_clusters(self, sample_municipios_df):
        from archetype.cards import build_archetype_cards
        from archetype.clustering import run_kmeans
        from archetype.features import prepare_feature_matrix, select_features
        from archetype.reduction import normalize, reduce_pca

        _, cols = select_features(sample_municipios_df, "socioeconomic+electoral")
        X = prepare_feature_matrix(sample_municipios_df, cols).values
        X_pca = reduce_pca(normalize(X), n_components=5)
        labels = list(run_kmeans(X_pca, k=3))
        label_names = {i: f"Arquétipo {i}" for i in range(3)}
        cards = build_archetype_cards(sample_municipios_df, labels, label_names, cols)
        assert len(cards) == 3

    def test_card_to_markdown(self, sample_municipios_df):
        from archetype.cards import build_archetype_cards
        from archetype.clustering import run_kmeans
        from archetype.features import prepare_feature_matrix, select_features
        from archetype.reduction import normalize, reduce_pca

        _, cols = select_features(sample_municipios_df, "socioeconomic+electoral")
        X = prepare_feature_matrix(sample_municipios_df, cols).values
        X_pca = reduce_pca(normalize(X), n_components=5)
        labels = list(run_kmeans(X_pca, k=2))
        label_names = {0: "Tipo A", 1: "Tipo B"}
        cards = build_archetype_cards(sample_municipios_df, labels, label_names, cols)
        md = cards[0].to_markdown()
        assert "Arquétipo" in md
        assert "Municípios" in md


class TestCache:
    def test_save_and_load_cache(self, tmp_path, monkeypatch, sample_municipios_df):
        import archetype.cache as cache_module
        monkeypatch.setattr(cache_module, "CACHE_DIR", tmp_path / "cache")
        from archetype.cache import load_pipeline_result, save_pipeline_result

        save_pipeline_result(
            escopo="SP",
            feature_set="socioeconomic",
            labels=[0, 1, 0, 1],
            embedding_2d=np.zeros((4, 2)),
            silhouette=0.55,
            algorithm="kmeans_fallback",
            label_names={0: "A", 1: "B"},
            n_rows=4,
        )
        result = load_pipeline_result("SP", "socioeconomic", 4)
        assert result is not None
        assert result["silhouette"] == 0.55
        assert result["algorithm"] == "kmeans_fallback"
