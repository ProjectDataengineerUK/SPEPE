"""Compute abstencao features for the electoral model."""

ABSTENCAO_COLS = ["taxa_abstencao", "qt_aptos"]


def get_abstencao_features_sql(project_id: str, gold_dataset: str) -> str:
    """Return SQL to fetch abstencao features by (sg_uf, cd_municipio, nr_zona, nr_secao)."""
    gold = f"{project_id}.{gold_dataset}"
    return f"""
        SELECT
            sg_uf,
            cd_municipio,
            nr_zona,
            nr_secao,
            cd_cargo,
            nr_turno,
            ano_eleicao,
            qt_aptos,
            total_votos_secao,
            taxa_abstencao,
            AVG(taxa_abstencao) OVER (
                PARTITION BY sg_uf, cd_municipio, ano_eleicao, cd_cargo
            ) AS taxa_abstencao_municipio_avg
        FROM `{gold}.fact_abstencao_secao`
    """
