"""CatBoost/LightGBM swing predictor para eleições majoritárias brasileiras.

Prediz o swing eleitoral (pct_votos_2026 − pct_votos_2022) por candidato/município.

Cargo suportado: Presidente, Governador (majoritário).
NÃO usar para Deputado Federal (proporcional → use quociente_eleitoral.py).

Sprint B — Piloto RJ (Governador 2026).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    pass

try:
    import catboost  # noqa: F401
    from catboost import CatBoostRegressor, Pool

    _CATBOOST_AVAILABLE = True
except ImportError:
    _CATBOOST_AVAILABLE = False

try:
    import lightgbm as lgb  # noqa: F401

    _LIGHTGBM_AVAILABLE = True
except ImportError:
    _LIGHTGBM_AVAILABLE = False

from mlops.shared_schema import INCUMBENCY_FEATURE_COLS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature schema
# ---------------------------------------------------------------------------

SWING_FEATURE_COLS: list[str] = [
    # baseline
    "pct_votos_historico",
    # território
    "taxa_abstencao",
    "abstencao_zona_std",
    "n_zonas",
    "nm_regiao",
    # IBGE estrutural
    "log_populacao",
    "log_renda",
    "taxa_alfabetizacao",
    # incumbência
    "is_incumbent_int",
    "trocou_partido_int",
    # pesquisas
    "delta_poll",
    "media_intencao_voto_uf",
    "media_rejeicao",
    "delta_rejeicao",
    # segurança — peso alto no RJ
    "log_homicidio",
    # social
    "pct_beneficiarios_bf",
]

_CAT_FEATURE_NAMES: list[str] = ["nm_regiao", "nm_candidato"]

_NUM_FEATURE_COLS: list[str] = [
    c for c in SWING_FEATURE_COLS if c not in _CAT_FEATURE_NAMES
]

_MISSING_NUM_FILL: float = 0.0
_MISSING_CAT_FILL: str = "DESCONHECIDO"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _resolve_features(
    df: pd.DataFrame,
    cat_feature_names: list[str],
    num_feature_cols: list[str],
    include_nm_candidato: bool = False,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Prepara X com imputação de missings; retorna (X, num_cols_usados, cat_cols_usados)."""
    num_cols_used = [c for c in num_feature_cols if c in df.columns]
    cat_cols_used = [c for c in cat_feature_names if c in df.columns]

    if include_nm_candidato and "nm_candidato" in df.columns:
        if "nm_candidato" not in cat_cols_used:
            cat_cols_used.append("nm_candidato")

    all_cols = num_cols_used + cat_cols_used
    X = df[all_cols].copy()

    for col in num_cols_used:
        X[col] = X[col].fillna(_MISSING_NUM_FILL).astype(float)

    for col in cat_cols_used:
        X[col] = X[col].fillna(_MISSING_CAT_FILL).astype(str)

    return X, num_cols_used, cat_cols_used


# ---------------------------------------------------------------------------
# SwingModel
# ---------------------------------------------------------------------------


class SwingModel:
    """CatBoost swing predictor: prediz pct_2026 − pct_2022 por candidato/município.

    Cargo suportado: Presidente, Governador (majoritário).
    NÃO usar para Deputado Federal (proporcional → use quociente_eleitoral.py).
    """

    def __init__(self, cargo: str = "governador", uf: str | None = None) -> None:
        self.cargo = cargo.lower()
        self.uf = uf.upper() if uf else None
        self._model: CatBoostRegressor | None = None
        self._lgbm_model = None
        self._backend: str | None = None
        self._feature_cols: list[str] = []
        self._cat_feature_names: list[str] = []
        self._num_feature_cols: list[str] = []
        self._trained_at: str | None = None
        self._include_nm_candidato: bool = False

    # ------------------------------------------------------------------
    # fit
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame) -> "SwingModel":
        """Treina o modelo de swing.

        df deve ter colunas:
          nm_candidato, sg_uf, cd_municipio, ano_eleicao,
          pct_votos (resultado real da eleição atual),
          pct_votos_historico (baseline = eleição anterior),
          + features opcionais: abstencao_2022, regiao_rj, is_incumbent_int, etc.

        Target = pct_votos − pct_votos_historico (swing).
        """
        _required = {"pct_votos", "pct_votos_historico"}
        missing = _required - set(df.columns)
        if missing:
            raise ValueError(f"Colunas obrigatórias ausentes: {missing}")

        df = df.copy()
        df["_swing_target"] = df["pct_votos"] - df["pct_votos_historico"]

        # nm_candidato como feature categórica se disponível
        self._include_nm_candidato = "nm_candidato" in df.columns

        X, num_used, cat_used = _resolve_features(
            df,
            _CAT_FEATURE_NAMES,
            _NUM_FEATURE_COLS,
            include_nm_candidato=self._include_nm_candidato,
        )
        y = df["_swing_target"].values.astype(float)

        self._num_feature_cols = num_used
        self._cat_feature_names = cat_used
        self._feature_cols = num_used + cat_used

        if not self._feature_cols:
            raise ValueError(
                "Nenhuma feature disponível no DataFrame. "
                f"Esperadas: {SWING_FEATURE_COLS}"
            )

        if len(X) < 10:
            logger.warning(
                "Dataset muito pequeno para treino (n=%d). Resultados podem ser instáveis.",
                len(X),
            )

        if _CATBOOST_AVAILABLE:
            self._fit_catboost(X, y, cat_used)
            self._backend = "catboost"
        elif _LIGHTGBM_AVAILABLE:
            self._fit_lightgbm(X, y, cat_used)
            self._backend = "lightgbm"
        else:
            raise ImportError(
                "Nenhum backend disponível. Instale catboost ou lightgbm: "
                "pip install catboost  # ou  pip install lightgbm"
            )

        self._trained_at = datetime.now(tz=timezone.utc).isoformat()
        logger.info(
            "SwingModel treinado: cargo=%s uf=%s backend=%s features=%d n=%d",
            self.cargo,
            self.uf,
            self._backend,
            len(self._feature_cols),
            len(X),
        )
        return self

    def _fit_catboost(
        self, X: pd.DataFrame, y: np.ndarray, cat_cols: list[str]
    ) -> None:
        from catboost import CatBoostRegressor, Pool

        cat_indices = [X.columns.tolist().index(c) for c in cat_cols if c in X.columns]

        pool = Pool(X, label=y, cat_features=cat_indices)
        self._model = CatBoostRegressor(
            iterations=500,
            learning_rate=0.05,
            depth=6,
            loss_function="RMSE",
            eval_metric="RMSE",
            random_seed=42,
            verbose=False,
            allow_writing_files=False,
        )
        self._model.fit(pool)

    def _fit_lightgbm(
        self, X: pd.DataFrame, y: np.ndarray, cat_cols: list[str]
    ) -> None:
        import lightgbm as lgb

        for col in cat_cols:
            if col in X.columns:
                X[col] = X[col].astype("category")

        self._lgbm_model = lgb.LGBMRegressor(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            random_state=42,
            verbose=-1,
        )
        self._lgbm_model.fit(X, y)

    # ------------------------------------------------------------------
    # predict
    # ------------------------------------------------------------------

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """Retorna swing previsto (array de floats)."""
        self._check_fitted()
        X, _, _ = _resolve_features(
            df,
            self._cat_feature_names,
            self._num_feature_cols,
            include_nm_candidato=self._include_nm_candidato,
        )
        X = self._align_columns(X)
        return self._raw_predict(X)

    def _raw_predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._backend == "catboost":
            from catboost import Pool

            cat_indices = [
                X.columns.tolist().index(c)
                for c in self._cat_feature_names
                if c in X.columns
            ]
            pool = Pool(X, cat_features=cat_indices)
            return self._model.predict(pool).astype(float)
        else:
            import lightgbm as lgb

            for col in self._cat_feature_names:
                if col in X.columns:
                    X[col] = X[col].astype("category")
            return self._lgbm_model.predict(X).astype(float)

    def _align_columns(self, X: pd.DataFrame) -> pd.DataFrame:
        """Garante que X tem exatamente as colunas de treino na ordem correta."""
        for col in self._feature_cols:
            if col not in X.columns:
                if col in self._cat_feature_names:
                    X[col] = _MISSING_CAT_FILL
                else:
                    X[col] = _MISSING_NUM_FILL
        return X[self._feature_cols]

    # ------------------------------------------------------------------
    # predict_proba_eleito
    # ------------------------------------------------------------------

    def predict_proba_eleito(
        self, df: pd.DataFrame, threshold: float = 0.0
    ) -> np.ndarray:
        """Converte swing previsto em probabilidade de vitória (sigmoid sobre margem).

        threshold=0.0 → prob > 0.5 significa que o candidato mantém ou cresce.
        """
        swing = self.predict(df)
        margin = swing - threshold
        # Escala: 1 pp de swing = ~2 unidades de logit (calibração empírica)
        return _sigmoid(margin * 2.0)

    # ------------------------------------------------------------------
    # simulate_monte_carlo
    # ------------------------------------------------------------------

    def simulate_monte_carlo(
        self,
        df: pd.DataFrame,
        n_sim: int = 5000,
        noise_pct: float = 0.03,
    ) -> pd.DataFrame:
        """Monte Carlo: adiciona ruído gaussiano nas features numéricas, roda n_sim vezes.

        Retorna DataFrame com colunas:
          nm_candidato, swing_mean, swing_std, swing_q05, swing_q95, prob_vitoria.
        """
        self._check_fitted()
        rng = np.random.default_rng(seed=42)
        num_cols = [c for c in self._num_feature_cols if c in df.columns]
        results: list[np.ndarray] = []

        for _ in range(n_sim):
            df_noisy = df.copy()
            for col in num_cols:
                sigma = noise_pct * df_noisy[col].abs().clip(lower=1e-6)
                df_noisy[col] = df_noisy[col] + rng.normal(0, sigma)
            results.append(self.predict(df_noisy))

        sims = np.stack(results)  # (n_sim, n_rows)

        swing_mean = sims.mean(axis=0)
        swing_std = sims.std(axis=0)
        swing_q05 = np.percentile(sims, 5, axis=0)
        swing_q95 = np.percentile(sims, 95, axis=0)
        prob_vitoria = (sims >= 0).mean(axis=0)

        out = pd.DataFrame(
            {
                "swing_mean": swing_mean,
                "swing_std": swing_std,
                "swing_q05": swing_q05,
                "swing_q95": swing_q95,
                "prob_vitoria": prob_vitoria,
            }
        )

        if "nm_candidato" in df.columns:
            out.insert(0, "nm_candidato", df["nm_candidato"].values)
        if "cd_municipio" in df.columns:
            out.insert(1, "cd_municipio", df["cd_municipio"].values)

        return out.reset_index(drop=True)

    # ------------------------------------------------------------------
    # shap_values
    # ------------------------------------------------------------------

    def shap_values(self, df: pd.DataFrame) -> pd.DataFrame:
        """SHAP values — retorna DataFrame (n_rows × n_features) indexado por nome de feature."""
        self._check_fitted()
        X, _, _ = _resolve_features(
            df,
            self._cat_feature_names,
            self._num_feature_cols,
            include_nm_candidato=self._include_nm_candidato,
        )
        X = self._align_columns(X)

        if self._backend == "catboost":
            return self._shap_catboost(X)
        else:
            return self._shap_lightgbm(X)

    def _shap_catboost(self, X: pd.DataFrame) -> pd.DataFrame:
        from catboost import Pool

        cat_indices = [
            X.columns.tolist().index(c)
            for c in self._cat_feature_names
            if c in X.columns
        ]
        pool = Pool(X, cat_features=cat_indices)
        shap_matrix = self._model.get_feature_importance(pool, type="ShapValues")
        # shap_matrix shape: (n_rows, n_features + 1) — last col is bias
        shap_vals = shap_matrix[:, :-1]
        return pd.DataFrame(shap_vals, columns=self._feature_cols)

    def _shap_lightgbm(self, X: pd.DataFrame) -> pd.DataFrame:
        try:
            import shap

            explainer = shap.TreeExplainer(self._lgbm_model)
            shap_vals = explainer.shap_values(X)
            return pd.DataFrame(shap_vals, columns=self._feature_cols)
        except ImportError:
            # Fallback: feature importances broadcast para todas as linhas
            logger.warning(
                "shap não instalado. Retornando feature_importances_ normalizadas."
            )
            importances = self._lgbm_model.feature_importances_
            importances_norm = importances / (importances.sum() + 1e-12)
            repeated = np.tile(importances_norm, (len(X), 1))
            return pd.DataFrame(repeated, columns=self._feature_cols)

    # ------------------------------------------------------------------
    # save / load
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Salva modelo em disco: model.cbm (ou model.lgbm) + metadata.json."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)

        metadata = {
            "cargo": self.cargo,
            "uf": self.uf,
            "backend": self._backend,
            "feature_cols": self._feature_cols,
            "cat_feature_names": self._cat_feature_names,
            "num_feature_cols": self._num_feature_cols,
            "include_nm_candidato": self._include_nm_candidato,
            "trained_at": self._trained_at,
        }

        with open(path / "metadata.json", "w", encoding="utf-8") as fh:
            json.dump(metadata, fh, ensure_ascii=False, indent=2)

        if self._backend == "catboost":
            self._model.save_model(str(path / "model.cbm"))
        else:
            import lightgbm as lgb

            self._lgbm_model.booster_.save_model(str(path / "model.lgbm"))

        logger.info("SwingModel salvo em %s (backend=%s)", path, self._backend)

    @classmethod
    def load(cls, path: str | Path) -> "SwingModel":
        """Carrega modelo do disco."""
        path = Path(path)
        with open(path / "metadata.json", encoding="utf-8") as fh:
            meta = json.load(fh)

        obj = cls(cargo=meta["cargo"], uf=meta.get("uf"))
        obj._backend = meta["backend"]
        obj._feature_cols = meta["feature_cols"]
        obj._cat_feature_names = meta["cat_feature_names"]
        obj._num_feature_cols = meta["num_feature_cols"]
        obj._include_nm_candidato = meta.get("include_nm_candidato", False)
        obj._trained_at = meta.get("trained_at")

        if obj._backend == "catboost":
            from catboost import CatBoostRegressor

            obj._model = CatBoostRegressor()
            obj._model.load_model(str(path / "model.cbm"))
        else:
            import lightgbm as lgb

            obj._lgbm_model = lgb.Booster(model_file=str(path / "model.lgbm"))

        logger.info("SwingModel carregado de %s (backend=%s)", path, obj._backend)
        return obj

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _check_fitted(self) -> None:
        if self._backend is None:
            raise RuntimeError(
                "SwingModel não treinado. Chame fit() antes de predict()."
            )
        if self._backend == "catboost" and self._model is None:
            raise RuntimeError("Modelo CatBoost não inicializado corretamente.")
        if self._backend == "lightgbm" and self._lgbm_model is None:
            raise RuntimeError("Modelo LightGBM não inicializado corretamente.")

    def __repr__(self) -> str:
        status = "treinado" if self._backend else "não treinado"
        return (
            f"SwingModel(cargo={self.cargo!r}, uf={self.uf!r}, "
            f"backend={self._backend!r}, status={status!r})"
        )
