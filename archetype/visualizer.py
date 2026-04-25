from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("spepe.archetype.visualizer")

ARCHETYPE_PALETTE = [
    "#e63946",
    "#457b9d",
    "#2a9d8f",
    "#e9c46a",
    "#f4a261",
    "#264653",
    "#a8dadc",
    "#f1faee",
    "#6a994e",
    "#bc4749",
    "#8338ec",
    "#fb5607",
    "#ffbe0b",
    "#3a86ff",
]


def build_brazil_choropleth(
    df_archetypes: pd.DataFrame,
    geojson_path: str,
    output_path: str,
    cd_municipio_col: str = "cd_municipio",
    archetype_col: str = "cluster",
    label_col: str = "archetype_label",
    municipio_name_col: str = "nm_municipio",
) -> str:
    try:
        import folium
        import geopandas as gpd
    except ImportError:
        raise ImportError(
            "folium e geopandas são necessários. Execute: pip install folium geopandas"
        )

    gdf = gpd.read_file(geojson_path)
    merged = gdf.merge(
        df_archetypes[[cd_municipio_col, archetype_col, label_col]],
        left_on="CD_MUN",
        right_on=cd_municipio_col,
        how="left",
    )

    m = folium.Map(
        location=[-14.235, -51.925],
        zoom_start=4,
        tiles="CartoDB positron",
    )

    unique_clusters = sorted(df_archetypes[archetype_col].unique())
    color_map = {
        cid: ARCHETYPE_PALETTE[i % len(ARCHETYPE_PALETTE)]
        for i, cid in enumerate(unique_clusters)
    }

    def style_fn(feature):
        cid = feature["properties"].get(archetype_col, -1)
        return {
            "fillColor": color_map.get(cid, "#cccccc"),
            "color": "white",
            "weight": 0.3,
            "fillOpacity": 0.75,
        }

    tooltip_fields = ["NM_MUN", label_col, archetype_col]
    tooltip_aliases = ["Município", "Arquétipo", "ID"]
    available_fields = [f for f in tooltip_fields if f in merged.columns]
    available_aliases = [
        a for f, a in zip(tooltip_fields, tooltip_aliases) if f in merged.columns
    ]

    folium.GeoJson(
        merged.__geo_interface__,
        style_function=style_fn,
        tooltip=folium.GeoJsonTooltip(
            fields=available_fields,
            aliases=available_aliases,
            localize=True,
        ),
        name="Arquétipos do Eleitorado",
    ).add_to(m)

    folium.LayerControl().add_to(m)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    m.save(output_path)
    logger.info(f"Mapa salvo em: {output_path}")
    return output_path


def build_umap_scatter(
    embedding_2d,
    labels,
    label_names: dict,
    output_path: str,
) -> str:
    try:
        import plotly.express as px
    except ImportError:
        logger.warning("plotly não disponível — scatter UMAP não gerado")
        return ""

    df_plot = pd.DataFrame(
        {
            "UMAP-1": embedding_2d[:, 0],
            "UMAP-2": embedding_2d[:, 1],
            "cluster": [str(lbl) for lbl in labels],
            "arquetipo": [label_names.get(lbl, f"Cluster {lbl}") for lbl in labels],
        }
    )

    fig = px.scatter(
        df_plot,
        x="UMAP-1",
        y="UMAP-2",
        color="cluster",
        hover_data=["arquetipo"],
        title="UMAP 2D — Arquétipos do Eleitorado Brasileiro",
        template="plotly_white",
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(output_path)
    return output_path
