"""Shared feature schema for SPEPE PyMC models.

Both pymc_train.py (demographic baseline) and pymc_electoral_train.py
(full electoral model) must use these column names when reading from
fact_ibge_municipio or electoral_dataset_builder output.
"""

from __future__ import annotations

# IBGE demographic features — canonical column names (from Gold fact_ibge_municipio)
IBGE_DEMOGRAPHIC_COLS = [
    "populacao_total",
    "renda_per_capita",
    "taxa_alfabetizacao",
    "taxa_analfabetismo",
]

# Alias map: old name → canonical name (for backward compat + migration)
IBGE_COL_ALIASES: dict[str, str] = {
    "populacao": "populacao_total",
    "renda_media": "renda_per_capita",
    "pct_ensino_superior": "taxa_alfabetizacao",  # proxy — closest available
    "pct_analfabetos": "taxa_analfabetismo",
}


# Dynamic external features — available when social tokens are configured
DYNAMIC_FEATURE_COLS = [
    "sentimento_score",  # social media sentiment [-1, 1]
    "tendencia_busca",  # Google Trends normalized [0, 1]
    "gdelt_intensidade",  # GDELT event count (log-normalized)
    "gdelt_tom",  # GDELT tone [-1, 1]
    "reputacao_score",  # composite external signal [-1, 1]
]

# delta_poll: rate of change in poll intention (week-over-week)
POLL_DELTA_COL = "delta_poll"

# All features used in M2 (full electoral model)
ELECTORAL_M2_COLS = IBGE_DEMOGRAPHIC_COLS + [
    "pct_votos_historico",
    "media_intencao_voto_uf",
]

# All features used in M3 (with dynamic external signals)
ELECTORAL_M3_COLS = ELECTORAL_M2_COLS + DYNAMIC_FEATURE_COLS + [POLL_DELTA_COL]


def compute_reputacao_score(
    sentimento: float,
    gdelt_tom: float,
    tendencia_norm: float,
) -> float:
    """Composite external reputation signal.

    Weights: sentimento (0.5), gdelt_tom (0.3), tendencia (0.2).
    All inputs in [-1, 1]. Output in [-1, 1].
    """
    return 0.5 * sentimento + 0.3 * gdelt_tom + 0.2 * tendencia_norm


def resolve_ibge_columns(df_columns: list[str]) -> list[str]:
    """Return canonical column names available in df_columns.

    Accepts both canonical names and aliases. Raises ValueError if none found.
    """
    available = []
    for col in IBGE_DEMOGRAPHIC_COLS:
        if col in df_columns:
            available.append(col)
        else:
            # Try alias (reverse lookup)
            alias = next(
                (k for k, v in IBGE_COL_ALIASES.items() if v == col and k in df_columns),
                None,
            )
            if alias:
                available.append(alias)

    if not available:
        raise ValueError(
            f"None of the expected IBGE columns found. "
            f"Expected: {IBGE_DEMOGRAPHIC_COLS}. "
            f"Got: {df_columns[:20]}"
        )
    return available
