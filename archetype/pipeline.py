from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from archetype.cache import load_pipeline_result, save_pipeline_result
from archetype.cards import build_archetype_cards
from archetype.clustering import select_best_algorithm
from archetype.features import prepare_feature_matrix, select_features
from archetype.labels import label_all_clusters
from archetype.reduction import normalize, reduce_pca, reduce_umap

logger = logging.getLogger("spepe.archetype.pipeline")

GOLD_TABLE_PATH = "data/gold/fact_municipio_eleicao.parquet"
GEOJSON_PATH = "data/geo/municipios_br.geojson"


def run_archetype_pipeline(
    df: pd.DataFrame,
    feature_set: str = "socioeconomic+electoral",
    escopo: str = "BR",
    min_cluster_size: int = 30,
    silhouette_threshold: float = 0.45,
    n_pca_components: int = 50,
    use_cache: bool = True,
) -> dict:
    X_df, feature_cols = select_features(df, feature_set)
    n_rows = len(X_df)

    if use_cache:
        cached = load_pipeline_result(escopo, feature_set, n_rows)
        if cached:
            logger.info("Resultado do pipeline carregado do cache.")
            return cached

    X = prepare_feature_matrix(X_df, feature_cols).values
    X_scaled = normalize(X)
    X_pca = reduce_pca(X_scaled, n_components=n_pca_components)

    labels, algorithm, silhouette = select_best_algorithm(
        X_pca,
        silhouette_threshold=silhouette_threshold,
        min_cluster_size=min_cluster_size,
    )

    embedding_2d = reduce_umap(X_pca)

    label_names = label_all_clusters(df, list(labels), feature_cols)
    cards = build_archetype_cards(df, list(labels), label_names, feature_cols)

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    noise_pct = float((labels == -1).sum() / len(labels) * 100) if -1 in labels else 0.0

    result = {
        "labels": list(labels),
        "embedding_2d": embedding_2d,
        "silhouette": silhouette,
        "algorithm": algorithm,
        "n_clusters": n_clusters,
        "noise_pct": noise_pct,
        "label_names": label_names,
        "cards": cards,
        "feature_cols": feature_cols,
        "escopo": escopo,
        "n_rows": n_rows,
        "feature_set": feature_set,
    }

    save_pipeline_result(
        escopo=escopo,
        feature_set=feature_set,
        labels=list(labels),
        embedding_2d=embedding_2d,
        silhouette=silhouette,
        algorithm=algorithm,
        label_names=label_names,
        n_rows=n_rows,
    )

    return result


def run_archetype_pipeline_cli(
    escopo: str = "BR",
    feature_set: str = "socioeconomic+electoral",
    gold_path: str = GOLD_TABLE_PATH,
) -> None:
    from archetype.visualizer import build_brazil_choropleth

    if not Path(gold_path).exists():
        print(f"Arquivo Gold não encontrado: {gold_path}")
        print("Execute /coletar {UF} {ano} primeiro.")
        return

    df = pd.read_parquet(gold_path)

    if escopo != "BR":
        uf_col = next((c for c in df.columns if "sg_uf" in c.lower()), None)
        if uf_col:
            df = df[df[uf_col] == escopo.upper()]

    if df.empty:
        print(f"Nenhum dado disponível para escopo: {escopo}")
        return

    result = run_archetype_pipeline(df, feature_set=feature_set, escopo=escopo)

    print(f"\n=== Arquétipos do Eleitorado — {escopo} ===")
    print(f"Algoritmo: {result['algorithm']} | Silhouette: {result['silhouette']:.3f}")
    print(f"Clusters: {result['n_clusters']} | Noise: {result['noise_pct']:.1f}%\n")

    for card in result["cards"]:
        print(card.to_markdown())

    df_out = df.copy()
    df_out["cluster"] = result["labels"]
    df_out["archetype_label"] = df_out["cluster"].map(result["label_names"])

    map_path = f"output/maps/arquetipos_{escopo}.html"
    if Path(GEOJSON_PATH).exists():
        build_brazil_choropleth(df_out, GEOJSON_PATH, map_path)
        print(f"\nMapa salvo em: {map_path}")
    else:
        print(f"\nGeoJSON não encontrado em {GEOJSON_PATH} — mapa não gerado.")
        print("Baixe o GeoJSON de municípios do IBGE e salve em data/geo/municipios_br.geojson")
