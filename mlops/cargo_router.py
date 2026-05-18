"""
Roteador de cargo → modelo de predição correto.

Presidente  (cd_cargo=1)  → SwingModel  (majoritário nacional)
Governador  (cd_cargo=3)  → SwingModel  (majoritário estadual)
Dep. Federal (cd_cargo=6) → DeputadoModel (proporcional + D'Hondt)
Senador     (cd_cargo=5)  → SwingModel  (majoritário estadual — 1/3 ou 2/3 vagas)
Dep. Estadual (cd_cargo=7) → DeputadoModel (proporcional estadual)
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("spepe.mlops.cargo_router")

CD_CARGO_NAMES: dict[int, str] = {
    1: "presidente",
    3: "governador",
    5: "senador",
    6: "deputado_federal",
    7: "deputado_estadual",
    8: "deputado_distrital",
    11: "vereador",
    13: "prefeito",
}

MAJORITARIO_CARGOS = {1, 3, 5, 13}   # → SwingModel
PROPORCIONAL_CARGOS = {6, 7, 8, 11}  # → DeputadoModel


def get_model_type(cd_cargo: int) -> str:
    """Retorna 'swing' | 'deputado' | 'unknown'."""
    if cd_cargo in MAJORITARIO_CARGOS:
        return "swing"
    if cd_cargo in PROPORCIONAL_CARGOS:
        return "deputado"
    return "unknown"


def load_model(cd_cargo: int, uf: str, model_dir: str | Path):
    """
    Carrega modelo do disco para o cargo/UF.
    Retorna None se arquivo não encontrado.
    """
    from mlops.swing_model import SwingModel
    from mlops.deputado_model import DeputadoModel

    model_dir = Path(model_dir)
    cargo_name = CD_CARGO_NAMES.get(cd_cargo, f"cargo_{cd_cargo}")
    model_type = get_model_type(cd_cargo)

    if model_type == "swing":
        path = model_dir / f"swing_{cargo_name}_{uf}.pkl"
        if not path.exists():
            logger.warning("Modelo SwingModel não encontrado: %s", path)
            return None
        return SwingModel.load(path)

    if model_type == "deputado":
        path = model_dir / f"deputado_{cargo_name}_{uf}.pkl"
        if not path.exists():
            logger.warning("Modelo DeputadoModel não encontrado: %s", path)
            return None
        return DeputadoModel.load(path)

    logger.warning("cd_cargo=%d não mapeado para nenhum modelo", cd_cargo)
    return None


def predict(
    cd_cargo: int,
    uf: str,
    df: pd.DataFrame,
    model_dir: str | Path,
    n_sim: int = 1000,
) -> dict:
    """
    Interface unificada de predição.
    Retorna dict com:
      {
        "cargo": str,
        "uf": str,
        "modelo": "swing" | "deputado",
        "candidatos": [{"nm_candidato": ..., "swing_mean": ..., "prob_vitoria": ...}]
        | [{"nm_candidato": ..., "prob_eleicao": ..., "cadeiras_partido": ...}]
      }
    """
    cargo_name = CD_CARGO_NAMES.get(cd_cargo, f"cargo_{cd_cargo}")
    model_type = get_model_type(cd_cargo)

    model = load_model(cd_cargo, uf, model_dir)
    if model is None:
        return {
            "cargo": cargo_name,
            "uf": uf,
            "modelo": model_type,
            "candidatos": [],
            "erro": f"Modelo não encontrado para cargo={cargo_name} uf={uf}",
        }

    if model_type == "swing":
        mc_df = model.simulate_monte_carlo(df, n_sim=n_sim)
        candidatos = mc_df.rename(
            columns={
                "swing_mean": "swing_mean",
                "prob_vitoria": "prob_vitoria",
            }
        ).to_dict(orient="records")

    else:
        mc_df = model.simulate_monte_carlo(df, n_sim=n_sim)
        candidatos = mc_df[
            ["nm_candidato", "sg_partido", "prob_eleicao", "votos_mean", "votos_q05", "votos_q95"]
        ].to_dict(orient="records")
        # Enriquecer com cadeiras previstas por partido
        from mlops.quociente_eleitoral import VAGAS_DEP_FEDERAL
        vagas = VAGAS_DEP_FEDERAL.get(uf, 8)
        df_partido = (
            mc_df.groupby("sg_partido")["votos_mean"].sum()
            .reset_index()
            .rename(columns={"votos_mean": "votos_previstos_uf"})
        )
        cadeiras = model.predict_cadeiras(df_partido, vagas=vagas)
        for c in candidatos:
            c["cadeiras_partido"] = cadeiras.get(c.get("sg_partido", ""), 0)

    return {
        "cargo": cargo_name,
        "uf": uf,
        "modelo": model_type,
        "candidatos": candidatos,
    }
