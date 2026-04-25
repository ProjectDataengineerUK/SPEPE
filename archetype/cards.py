from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass
class ArchetypeCard:
    cluster_id: int
    label: str
    n_municipios: int
    pct_total: float
    top_features: list[dict]
    electoral_history: dict
    representative_municipios: list[str]
    noise_pct: float = 0.0

    def to_markdown(self) -> str:
        features_md = "\n".join(
            f"  - **{f['feature']}**: {f['value']:.3f} (média nacional: {f['national_avg']:.3f})"
            for f in self.top_features[:5]
        )
        hist_md = "\n".join(
            f"  - {ano}: {candidato} ({pct:.1f}%)"
            for ano, (candidato, pct) in self.electoral_history.items()
        )
        reps = ", ".join(self.representative_municipios[:5])
        return f"""### Arquétipo {self.cluster_id}: {self.label}
- **Municípios**: {self.n_municipios} ({self.pct_total:.1f}% do Brasil)
- **Exemplos**: {reps}

**Top características diferenciadoras:**
{features_md}

**Histórico eleitoral (candidato dominante):**
{hist_md}
"""


def build_archetype_cards(
    df: pd.DataFrame,
    labels: list[int],
    label_names: dict[int, str],
    feature_cols: list[str],
    municipio_col: str = "nm_municipio",
) -> list[ArchetypeCard]:
    df = df.copy()
    df["cluster"] = labels
    n_total = len(df)
    national_means = df[feature_cols].mean()
    cards = []

    for cid in sorted(set(labels)):
        cluster_df = df[df["cluster"] == cid]
        cluster_means = cluster_df[feature_cols].mean()

        diff = (cluster_means - national_means).abs()
        top_feat_names = diff.nlargest(10).index.tolist()
        top_features = [
            {
                "feature": feat,
                "value": float(cluster_means[feat]),
                "national_avg": float(national_means[feat]),
            }
            for feat in top_feat_names
        ]

        electoral_cols = {
            2014: [c for c in feature_cols if "2014" in c and "pct_votos" in c],
            2018: [c for c in feature_cols if "2018" in c and "pct_votos" in c],
            2022: [c for c in feature_cols if "2022" in c and "pct_votos" in c],
        }
        history = {}
        for ano, cols in electoral_cols.items():
            if cols:
                best = cluster_df[cols].mean().idxmax()
                pct = float(cluster_df[cols].mean().max() * 100)
                history[str(ano)] = (
                    best.replace("pct_votos_", "").replace(f"_{ano}", "").upper(),
                    pct,
                )

        reps = []
        if municipio_col in cluster_df.columns:
            reps = cluster_df[municipio_col].dropna().head(5).tolist()

        cards.append(
            ArchetypeCard(
                cluster_id=cid,
                label=label_names.get(cid, f"Arquétipo {cid}"),
                n_municipios=len(cluster_df),
                pct_total=len(cluster_df) / n_total * 100,
                top_features=top_features,
                electoral_history=history,
                representative_municipios=reps,
            )
        )

    return cards
