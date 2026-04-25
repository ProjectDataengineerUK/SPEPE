from __future__ import annotations

import pandas as pd

SOCIOECONOMIC_FEATURES = [
    "idhm_2010",
    "idhm_educacao",
    "idhm_longevidade",
    "idhm_renda",
    "renda_media_domiciliar",
    "pct_analfabetos",
    "anos_estudo_medio",
    "pct_rural",
    "pib_per_capita",
    "taxa_desemprego",
    "pct_extrema_pobreza",
    "pct_agua_esgoto",
    "gini",
]

ELECTORAL_FEATURES = [
    "pct_votos_pt_2014",
    "pct_votos_psdb_2014",
    "pct_votos_pt_2018",
    "pct_votos_psl_2018",
    "pct_votos_pt_2022",
    "pct_votos_pl_2022",
    "pct_brancos_nulos_2022",
    "comparecimento_2022",
    "volatilidade_eleitoral_1418",
    "volatilidade_eleitoral_1822",
]

DIGITAL_FEATURES = [
    "google_trends_score",
    "meta_ads_spend_total",
    "youtube_views_total",
    "sentimento_medio",
]

GEOGRAPHIC_FEATURES = [
    "populacao_2022",
    "densidade_demografica",
    "area_km2",
    "regiao",
]

ALL_FEATURES = (
    SOCIOECONOMIC_FEATURES + ELECTORAL_FEATURES + DIGITAL_FEATURES + GEOGRAPHIC_FEATURES
)


def select_features(
    df: pd.DataFrame, feature_set: str = "all"
) -> tuple[pd.DataFrame, list[str]]:
    mapping = {
        "all": ALL_FEATURES,
        "socioeconomic": SOCIOECONOMIC_FEATURES,
        "electoral": ELECTORAL_FEATURES,
        "digital": DIGITAL_FEATURES,
        "geographic": GEOGRAPHIC_FEATURES,
        "socioeconomic+electoral": SOCIOECONOMIC_FEATURES + ELECTORAL_FEATURES,
    }
    cols = [c for c in mapping.get(feature_set, ALL_FEATURES) if c in df.columns]
    return df[cols].copy(), cols


def prepare_feature_matrix(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    X = df[feature_cols].copy()
    for col in X.select_dtypes(include=["object", "category"]).columns:
        X[col] = pd.Categorical(X[col]).codes.astype(float)
    X = X.fillna(X.median())
    return X
