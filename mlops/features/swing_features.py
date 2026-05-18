"""
Monta o feature DataFrame para o SwingModel a partir de múltiplas fontes.
Pode usar BigQuery (quando disponível) ou parquet local.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.features.swing")

_NUM_FILL = 0.0
_CAT_FILL = "DESCONHECIDO"


def build_swing_features(
    df_votos: pd.DataFrame,
    df_ibge: pd.DataFrame | None,
    df_abstencao: pd.DataFrame | None,
    df_incumbencia: pd.DataFrame | None,
    df_regiao_rj: pd.DataFrame | None,
) -> pd.DataFrame:
    """
    Retorna DataFrame com todas as features para o SwingModel, incluindo:
    - pct_votos_historico (baseline = pct do mesmo candidato na eleição anterior)
    - swing_target = pct_votos - pct_votos_historico
    - features IBGE, abstenção, incumbência, região RJ
    Missings preenchidos com 0.0 para numéricos e "DESCONHECIDO" para categorias.
    """
    df = df_votos.copy()

    # --- baseline histórico ---
    anos = sorted(df["ano_eleicao"].unique())
    if len(anos) >= 2:
        ano_atual = anos[-1]
        ano_anterior = anos[-2]
        historico = (
            df[df["ano_eleicao"] == ano_anterior][["nm_candidato", "sg_uf", "pct_votos"]]
            .rename(columns={"pct_votos": "pct_votos_historico"})
        )
        df = df[df["ano_eleicao"] == ano_atual].merge(
            historico, on=["nm_candidato", "sg_uf"], how="left"
        )
    else:
        df["pct_votos_historico"] = _NUM_FILL

    df["pct_votos_historico"] = df["pct_votos_historico"].fillna(_NUM_FILL)
    df["swing_target"] = df["pct_votos"] - df["pct_votos_historico"]

    # --- IBGE ---
    ibge_cols = ["log_populacao", "log_renda", "taxa_alfabetizacao"]
    if df_ibge is not None and not df_ibge.empty:
        df = df.merge(
            df_ibge[["cd_municipio"] + [c for c in ibge_cols if c in df_ibge.columns]],
            on="cd_municipio",
            how="left",
        )
    for col in ibge_cols:
        if col not in df.columns:
            df[col] = _NUM_FILL
        else:
            df[col] = df[col].fillna(_NUM_FILL)

    # --- abstenção ---
    if df_abstencao is not None and not df_abstencao.empty:
        join_cols = [c for c in ["sg_uf", "cd_municipio"] if c in df_abstencao.columns]
        df = df.merge(
            df_abstencao[join_cols + ["taxa_abstencao"]],
            on=join_cols,
            how="left",
        )
    if "taxa_abstencao" not in df.columns:
        df["taxa_abstencao"] = _NUM_FILL
    else:
        df["taxa_abstencao"] = df["taxa_abstencao"].fillna(_NUM_FILL)

    # --- incumbência ---
    if df_incumbencia is not None and not df_incumbencia.empty:
        df = df.merge(
            df_incumbencia[["nm_candidato", "sg_uf", "is_incumbent", "trocou_partido"]],
            on=["nm_candidato", "sg_uf"],
            how="left",
        )
    for col in ["is_incumbent", "trocou_partido"]:
        if col not in df.columns:
            df[col] = _NUM_FILL
        else:
            df[col] = df[col].fillna(_NUM_FILL)
    df["is_incumbent_int"] = df["is_incumbent"].astype(int)
    df["trocou_partido_int"] = df["trocou_partido"].astype(int)

    # --- região RJ ---
    if df_regiao_rj is not None and not df_regiao_rj.empty:
        df = df.merge(
            df_regiao_rj[["cd_municipio_ibge", "nm_regiao", "id_regiao"]].rename(
                columns={"cd_municipio_ibge": "cd_municipio"}
            ),
            on="cd_municipio",
            how="left",
        )
    for col in ["nm_regiao"]:
        if col not in df.columns:
            df[col] = _CAT_FILL
        else:
            df[col] = df[col].fillna(_CAT_FILL)
    if "id_regiao" not in df.columns:
        df["id_regiao"] = _NUM_FILL
    else:
        df["id_regiao"] = df["id_regiao"].fillna(_NUM_FILL)

    df["sg_partido"] = df["sg_partido"].fillna(_CAT_FILL)
    df["nm_candidato"] = df["nm_candidato"].fillna(_CAT_FILL)

    return df.reset_index(drop=True)


def build_swing_features_from_bq(
    project_id: str,
    cargo: int,
    uf: str,
    gold_dataset: str = "spepe_gold",
    silver_dataset: str = "spepe_silver",
    mlops_dataset: str = "spepe_mlops",
) -> pd.DataFrame:
    """
    Constrói feature matrix para SwingModel diretamente do BigQuery Gold.
    Usa: fact_municipio_candidato_eleicao, fact_ibge_municipio,
         fact_abstencao_secao, dim_incumbencia, dim_regiao_rj.
    """
    try:
        from google.cloud import bigquery
    except ImportError:
        raise ImportError("google-cloud-bigquery não instalado. Execute: pip install google-cloud-bigquery")

    client = bigquery.Client(project=project_id)

    sql_votos = f"""
        SELECT
            nm_candidato,
            sg_uf,
            cd_municipio,
            ano_eleicao,
            pct_votos,
            sg_partido
        FROM `{project_id}.{gold_dataset}.fact_candidato_eleicao`
        WHERE sg_uf = '{uf}'
          AND cd_cargo = {cargo}
        ORDER BY ano_eleicao, nm_candidato
    """
    df_votos = client.query(sql_votos).to_dataframe()

    sql_ibge = f"""
        SELECT
            cd_municipio,
            LOG(NULLIF(populacao_total, 0)) AS log_populacao,
            LOG(NULLIF(renda_per_capita, 0)) AS log_renda,
            taxa_alfabetizacao
        FROM `{project_id}.{gold_dataset}.fact_ibge_municipio`
        WHERE sg_uf = '{uf}'
    """
    df_ibge = client.query(sql_ibge).to_dataframe()

    try:
        sql_abstencao = f"""
            SELECT sg_uf, cd_municipio, AVG(taxa_abstencao) AS taxa_abstencao
            FROM `{project_id}.{silver_dataset}.fact_abstencao_secao`
            WHERE sg_uf = '{uf}'
            GROUP BY sg_uf, cd_municipio
        """
        df_abstencao = client.query(sql_abstencao).to_dataframe()
    except Exception:
        df_abstencao = None

    try:
        sql_inc = f"""
            SELECT nm_candidato, sg_uf, is_incumbent, trocou_partido
            FROM `{project_id}.{mlops_dataset}.dim_incumbencia`
            WHERE sg_uf = '{uf}'
        """
        df_incumbencia = client.query(sql_inc).to_dataframe()
    except Exception:
        df_incumbencia = None

    try:
        sql_regiao = f"""
            SELECT cd_municipio_ibge, nm_regiao, id_regiao
            FROM `{project_id}.{gold_dataset}.dim_regiao_rj`
        """
        df_regiao_rj = client.query(sql_regiao).to_dataframe()
    except Exception:
        df_regiao_rj = None

    return build_swing_features(
        df_votos=df_votos,
        df_ibge=df_ibge,
        df_abstencao=df_abstencao,
        df_incumbencia=df_incumbencia,
        df_regiao_rj=df_regiao_rj,
    )


def build_swing_features_from_local(
    data_dir: str | Path,
    cargo: int,
    uf: str,
) -> pd.DataFrame:
    """
    Fallback: lê parquet locais de data/silver/*.parquet.
    Retorna DataFrame com as mesmas colunas que build_swing_features_from_bq,
    com zeros onde dados IBGE/abstenção/incumbência não disponíveis.
    """
    data_dir = Path(data_dir)
    uf_lower = uf.lower()

    # Localizar arquivos de votos
    votos_candidates = list(data_dir.glob(f"*tse*{uf_lower}*.parquet")) + \
                       list(data_dir.glob(f"*tse*{uf}*.parquet")) + \
                       list(data_dir.glob(f"*{uf_lower}*.parquet"))

    if not votos_candidates:
        logger.warning("Nenhum parquet de votos encontrado em %s para UF=%s", data_dir, uf)
        return pd.DataFrame()

    df_votos = pd.read_parquet(votos_candidates[0])

    # Filtrar por cargo se coluna disponível
    if "cd_cargo" in df_votos.columns:
        df_votos = df_votos[df_votos["cd_cargo"] == cargo]

    # Garantir colunas mínimas
    for col in ["nm_candidato", "sg_uf", "cd_municipio", "ano_eleicao", "pct_votos", "sg_partido"]:
        if col not in df_votos.columns:
            df_votos[col] = _NUM_FILL if col not in ["nm_candidato", "sg_uf", "sg_partido"] else _CAT_FILL

    # Tentar carregar IBGE local
    ibge_candidates = list(data_dir.glob(f"*ibge*{uf_lower}*.parquet")) + \
                      list(data_dir.glob(f"*ibge*{uf}*.parquet"))
    df_ibge = None
    if ibge_candidates:
        raw = pd.read_parquet(ibge_candidates[0])
        df_ibge = pd.DataFrame()
        if "cd_municipio" in raw.columns:
            df_ibge["cd_municipio"] = raw["cd_municipio"]
        for src, dst in [("populacao_total", "log_populacao"), ("renda_per_capita", "log_renda")]:
            if src in raw.columns:
                df_ibge[dst] = np.log(raw[src].replace(0, np.nan)).fillna(_NUM_FILL)
            else:
                df_ibge[dst] = _NUM_FILL
        df_ibge["taxa_alfabetizacao"] = raw.get("taxa_alfabetizacao", pd.Series(_NUM_FILL, index=raw.index))

    return build_swing_features(
        df_votos=df_votos,
        df_ibge=df_ibge,
        df_abstencao=None,
        df_incumbencia=None,
        df_regiao_rj=None,
    )
