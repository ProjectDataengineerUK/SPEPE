from __future__ import annotations

import logging
import os

import anthropic
import pandas as pd

logger = logging.getLogger("spepe.archetype.labels")


def generate_archetype_label(
    cluster_id: int,
    top_features: dict[str, float],
    electoral_pattern: str,
    n_municipios: int,
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    features_str = "\n".join(
        f"- {feat}: {val:.3f}" for feat, val in list(top_features.items())[:10]
    )

    prompt = f"""Você é um cientista político brasileiro especialista em sociologia eleitoral.

Com base nas características abaixo de um cluster de municípios brasileiros, crie um NOME SOCIOLÓGICO conciso (3-6 palavras) que capture a essência deste arquétipo do eleitorado.

Cluster {cluster_id} — {n_municipios} municípios:

Top características (valores médios do cluster):
{features_str}

Padrão eleitoral histórico:
{electoral_pattern}

Responda APENAS com o nome do arquétipo. Exemplos de estilo:
- "Cidades Industriais do Interior"
- "Metrópoles Progressistas Diversas"
- "Agropecuária Conservadora do Cerrado"
- "Periferias Urbanas Vulneráveis"

Nome do arquétipo:"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Erro ao gerar label para cluster {cluster_id}: {e}")
        return f"Arquétipo {cluster_id}"


def label_all_clusters(
    df: pd.DataFrame,
    labels: list[int],
    feature_cols: list[str],
) -> dict[int, str]:
    df = df.copy()
    df["cluster"] = labels
    result = {}

    for cid in sorted(set(labels)):
        if cid == -1:
            result[-1] = "Municípios Atípicos (noise)"
            continue

        cluster_df = df[df["cluster"] == cid]
        means = cluster_df[feature_cols].mean()
        top_features = dict(means.nlargest(10))

        electoral_cols = [c for c in feature_cols if "votos" in c or "pct" in c]
        electoral_pattern = cluster_df[electoral_cols].mean().to_string() if electoral_cols else "N/A"

        result[cid] = generate_archetype_label(
            cluster_id=cid,
            top_features=top_features,
            electoral_pattern=electoral_pattern,
            n_municipios=len(cluster_df),
        )
        logger.info(f"Cluster {cid}: '{result[cid]}' ({len(cluster_df)} municípios)")

    return result
