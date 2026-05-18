"""
Modelo para cargos proporcionais (Deputado Federal/Estadual).

Arquitetura em 2 camadas:
  Layer 1: CatBoost prediz votos por partido na circunscrição (UF)
  Layer 2: simulate_quociente() distribui cadeiras entre partidos (D'Hondt)
  Layer 3: ranking candidatos dentro do partido por votos previstos
  Layer 4: Monte Carlo → prob_eleicao por candidato
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.deputado_model")

try:
    from catboost import CatBoostRegressor
    _CATBOOST_AVAILABLE = True
except ImportError:
    from sklearn.ensemble import GradientBoostingRegressor as CatBoostRegressor  # type: ignore[no-redef]
    _CATBOOST_AVAILABLE = False
    logger.warning("CatBoost não disponível — usando sklearn GradientBoostingRegressor como fallback")

_PARTIDO_FEATURES_UF = [
    "pct_votos_historico_partido",
    "log_populacao",
    "log_renda",
    "n_candidatos_partido",
    "n_incumbentes_partido",
]

_CANDIDATO_FEATURES = [
    "pct_votos_historico",
    "is_incumbent_int",
    "trocou_partido_int",
    "log_populacao",
    "log_renda",
    "rank_historico_partido",
]


def _make_regressor(n_estimators: int = 500, learning_rate: float = 0.05):
    if _CATBOOST_AVAILABLE:
        return CatBoostRegressor(
            iterations=n_estimators,
            learning_rate=learning_rate,
            depth=6,
            loss_function="RMSE",
            verbose=0,
            random_seed=42,
        )
    return CatBoostRegressor(
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        max_depth=6,
        random_state=42,
    )


class DeputadoModel:
    def __init__(self, uf: str):
        self.uf = uf
        self.model_partido = _make_regressor()
        self.model_candidato = _make_regressor()
        self._fitted = False

    def _build_partido_features(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
        """Agrega features por partido (UF-level)."""
        agg = (
            df.groupby("sg_partido")
            .agg(
                votos_partido_uf=("votos_partido_uf", "first"),
                pct_votos_historico_partido=("pct_votos_historico", "mean"),
                log_populacao=("log_populacao", "mean"),
                log_renda=("log_renda", "mean"),
                n_candidatos_partido=("nm_candidato", "count"),
                n_incumbentes_partido=("is_incumbent_int", "sum"),
            )
            .reset_index()
        )
        feats = agg[[c for c in _PARTIDO_FEATURES_UF if c in agg.columns]].fillna(0.0)
        for col in _PARTIDO_FEATURES_UF:
            if col not in feats.columns:
                feats[col] = 0.0
        return feats[_PARTIDO_FEATURES_UF], agg["votos_partido_uf"]

    def _build_candidato_features(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
        """Features por candidato (pct dentro do partido)."""
        df = df.copy()
        df["rank_historico_partido"] = (
            df.groupby(["sg_partido", "ano_eleicao"])["pct_votos_historico"]
            .rank(ascending=False, method="min")
        )
        total_partido = df.groupby("sg_partido")["votos_candidato"].transform("sum")
        df["pct_votos_no_partido"] = df["votos_candidato"] / total_partido.replace(0, np.nan)
        df["pct_votos_no_partido"] = df["pct_votos_no_partido"].fillna(0.0)

        feats = df[[c for c in _CANDIDATO_FEATURES if c in df.columns]].fillna(0.0)
        for col in _CANDIDATO_FEATURES:
            if col not in feats.columns:
                feats[col] = 0.0
        return feats[_CANDIDATO_FEATURES], df["pct_votos_no_partido"]

    def fit(self, df: pd.DataFrame) -> "DeputadoModel":
        """
        df colunas: nm_candidato, sg_partido, cd_municipio, ano_eleicao,
                    votos_candidato, votos_partido_uf (total UF),
                    pct_votos_historico (candidato), is_incumbent_int, trocou_partido_int,
                    log_populacao, log_renda
        Treina:
          - model_partido: prediz votos_partido_uf (features UF-level)
          - model_candidato: prediz pct_votos dentro do partido
        """
        X_partido, y_partido = self._build_partido_features(df)
        self.model_partido.fit(X_partido.values, y_partido.values)

        X_cand, y_cand = self._build_candidato_features(df)
        self.model_candidato.fit(X_cand.values, y_cand.values)

        self._fitted = True
        logger.info("DeputadoModel[%s] treinado — %d candidatos, %d partidos", self.uf, len(df), X_partido.shape[0])
        return self

    def predict_cadeiras(self, df_partido: pd.DataFrame, vagas: int | None = None) -> dict[str, int]:
        """
        Prevê cadeiras por partido usando D'Hondt.
        df_partido: nm_partido, votos_previstos_uf
        Usa mlops.quociente_eleitoral.simulate_quociente()
        """
        from mlops.quociente_eleitoral import simulate_quociente, VAGAS_DEP_FEDERAL
        vagas = vagas or VAGAS_DEP_FEDERAL.get(self.uf, 8)

        col_partido = next((c for c in ["nm_partido", "sg_partido"] if c in df_partido.columns), None)
        if col_partido is None:
            raise ValueError("df_partido precisa ter coluna 'nm_partido' ou 'sg_partido'")

        votos_dict = dict(
            zip(df_partido[col_partido], df_partido["votos_previstos_uf"].astype(int))
        )
        return simulate_quociente(votos_dict, vagas)

    def simulate_monte_carlo(
        self, df: pd.DataFrame, n_sim: int = 5000, noise_pct: float = 0.05
    ) -> pd.DataFrame:
        """
        Retorna DataFrame com colunas:
        nm_candidato, sg_partido, prob_eleicao, votos_mean, votos_q05, votos_q95
        """
        from mlops.quociente_eleitoral import VAGAS_DEP_FEDERAL, simulate_quociente

        vagas = VAGAS_DEP_FEDERAL.get(self.uf, 8)
        df = df.copy()

        if not self._fitted:
            logger.warning("DeputadoModel não treinado — usando votos_candidato como baseline")
            if "votos_candidato" not in df.columns:
                df["votos_candidato"] = 0
            votos_base = df["votos_candidato"].values.astype(float)
        else:
            X_cand, _ = self._build_candidato_features(df)
            pct_pred = np.clip(self.model_candidato.predict(X_cand.values), 0, 1)
            X_partido, _ = self._build_partido_features(df)
            votos_partido_pred = np.clip(self.model_partido.predict(X_partido.values), 0, None)
            partido_list = df["sg_partido"].drop_duplicates().reset_index(drop=True)
            votos_partido_map = dict(zip(partido_list, votos_partido_pred))
            votos_base = np.array([
                pct_pred[i] * votos_partido_map.get(df["sg_partido"].iloc[i], 0)
                for i in range(len(df))
            ])

        rng = np.random.default_rng(seed=42)
        candidatos = df["nm_candidato"].tolist()
        partidos = df["sg_partido"].tolist()
        n_cand = len(candidatos)

        eleito_count = np.zeros(n_cand, dtype=int)
        votos_sim_all = np.zeros((n_sim, n_cand), dtype=float)

        for s in range(n_sim):
            noise = rng.normal(loc=1.0, scale=noise_pct, size=n_cand)
            votos_sim = np.maximum(0, votos_base * noise)
            votos_sim_all[s] = votos_sim

            votos_partido: dict[str, int] = {}
            for i, p in enumerate(partidos):
                votos_partido[p] = votos_partido.get(p, 0) + int(votos_sim[i])

            cadeiras = simulate_quociente(votos_partido, vagas)

            por_partido: dict[str, list[tuple[int, float]]] = {}
            for i, p in enumerate(partidos):
                por_partido.setdefault(p, []).append((i, float(votos_sim[i])))

            for p, membros in por_partido.items():
                n_cadeiras = cadeiras.get(p, 0)
                ordenados = sorted(membros, key=lambda x: x[1], reverse=True)
                for rank, (idx, _) in enumerate(ordenados):
                    if rank < n_cadeiras:
                        eleito_count[idx] += 1

        prob_eleicao = eleito_count / n_sim
        votos_mean = votos_sim_all.mean(axis=0)
        votos_q05 = np.percentile(votos_sim_all, 5, axis=0)
        votos_q95 = np.percentile(votos_sim_all, 95, axis=0)

        return pd.DataFrame({
            "nm_candidato": candidatos,
            "sg_partido": partidos,
            "prob_eleicao": prob_eleicao,
            "votos_mean": votos_mean,
            "votos_q05": votos_q05,
            "votos_q95": votos_q95,
        }).sort_values("prob_eleicao", ascending=False).reset_index(drop=True)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)
        logger.info("DeputadoModel[%s] salvo em %s", self.uf, path)

    @classmethod
    def load(cls, path: str | Path) -> "DeputadoModel":
        with open(path, "rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise TypeError(f"Arquivo {path} não contém DeputadoModel — encontrado: {type(obj)}")
        return obj
