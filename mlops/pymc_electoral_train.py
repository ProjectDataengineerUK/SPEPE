"""PyMC Electoral Model training pipeline — real electoral prediction.

Two-model strategy:
  Model 1: pymc_train.py (demographic baseline) — 4 IBGE features, fast
  Model 2: pymc_electoral_train.py (THIS) — historical + polls + candidate + IBGE

Expected Brier OOS (2022): 0.10–0.18 (vs 0.15–0.25 for naive historical baseline).
Brier gate: < 0.18 (realista para multi-candidato municipal). If w_hist 95% HDI contains 0, model is broken.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.pymc_electoral_train")

_MODEL_VERSION = "pymc-electoral-v1"
_BRIER_GATE_OOS = 0.18  # realista para multi-candidato municipal (baseline ~0.15–0.25)
_BRIER_GAP_GATE = 0.08  # permite algum overfitting controlado
_DIVERGENCE_GATE = 0.002  # < 8 divergências absolutas em 4000 draws


def train_electoral_model(
    project_id: str | None = None,
    dataset_id: str = "spepe_mlops",
    draws: int = 1000,
    tune: int = 2000,
    chains: int = 4,
    target_accept: float = 0.95,
    save_trace: bool = True,
) -> dict:
    """Train hierarchical Bayesian electoral prediction model.

    Features (in order of expected importance):
      1. pct_votos_historico  (30%) — strongest predictor, 2018→2022 lag
      2. media_intencao_voto  (25%) — aggregated polls per (candidato, UF, ano)
      3. IBGE demographics    (25%) — populacao, renda, educação (log+z-scored)
      4. Candidate intercept  (15%) — partial pooling across top-10 + OTHER
      5. UF intercept          (5%) — partial pooling across 27 UFs

    Target: pct_votos ∈ (0,1) per (municipio, candidato, ano_eleicao)
    Temporal split: train=2018, val=2022 (out-of-sample)
    """
    try:
        import pymc as pm
        import arviz as az
    except ImportError:
        raise ImportError("PyMC required: pip install pymc arviz pytensor")

    from google.cloud import bigquery
    from mlops.eval.electoral_dataset_builder import build_electoral_dataset
    from mlops.pymc_electoral_model import (
        build_electoral_model,
        prepare_electoral_features,
        sample_electoral_posterior,
    )

    project_id = project_id or os.environ.get("GCP_PROJECT_ID", "spepe-prod")

    # ── Step 1: Load full dataset ────────────────────────────────────────────
    logger.info("Loading electoral dataset (split_mode='all')...")
    df = build_electoral_dataset(project_id=project_id, write_to_bq=False, split_mode="all")
    if df.empty:
        raise ValueError("Electoral dataset is empty")

    # ── Step 2: Train / val split ────────────────────────────────────────────
    df_train = df[df["split_type"] == "train"].copy().reset_index(drop=True)
    df_val = df[df["split_type"] == "validate"].copy().reset_index(drop=True)

    if df_train.empty:
        raise ValueError("Train partition (2018) is empty")
    if df_val.empty:
        logger.warning("Validation partition (2022) is empty — no OOS Brier")

    logger.info(
        "Train: %d rows | Val: %d rows | UFs: %d",
        len(df_train),
        len(df_val),
        df_train["sg_uf"].nunique(),
    )

    # ── Step 3: Stratified sub-sampling (train only) ─────────────────────────
    max_rows = int(os.environ.get("PYMC_MAX_ROWS", "20000"))
    if len(df_train) > max_rows:
        n_uf = df_train["sg_uf"].nunique()
        rows_per_uf = max(20, max_rows // n_uf)
        df_train = (
            df_train.groupby("sg_uf", group_keys=False)
            .apply(lambda g: g.sample(min(len(g), rows_per_uf), random_state=42))
            .reset_index(drop=True)
        )
        if len(df_train) > max_rows:
            df_train = df_train.sample(max_rows, random_state=42).reset_index(drop=True)
        logger.info("Sub-sampled train to %d rows (%d UFs)", len(df_train), n_uf)

    # QC: ensure y is in (0, 1) for Beta likelihood
    df_train = df_train[df_train["y"].between(0.0001, 0.9999)].reset_index(drop=True)

    # ── Step 4: Feature preparation ──────────────────────────────────────────
    logger.info("Preparing electoral features...")
    train_features, train_stats = prepare_electoral_features(df_train, train_stats=None)

    logger.info(
        "Model dims: n_uf=%d  n_cand=%d  n_demo=%d  n_train=%d",
        train_features["n_uf"],
        train_features["n_cand"],
        train_features["n_demo"],
        len(train_features["y"]),
    )
    logger.info(
        "Train — histórico fill: %.1f%%  poll fill: %.1f%%",
        100 * (train_features["is_new"] == 0).mean(),
        100 * (train_features["poll_missing"] == 0).mean(),
    )

    # ── Step 5: Build + sample ───────────────────────────────────────────────
    logger.info("Building electoral model...")
    model = build_electoral_model(train_features)

    logger.info(
        "Sampling %d draws × %d chains (tune=%d, target_accept=%.2f)...",
        draws,
        chains,
        tune,
        target_accept,
    )
    idata = sample_electoral_posterior(
        model,
        draws=draws,
        tune=tune,
        chains=chains,
        target_accept=target_accept,
        random_seed=42,
    )
    logger.info("Sampling complete")

    # ── Step 6: Convergence diagnostics ─────────────────────────────────────
    summary = az.summary(
        idata,
        var_names=["mu_uf", "sd_uf", "sd_cand", "w_hist", "w_poll", "kappa"],
    )
    rhat_max = float(summary["r_hat"].max())
    ess_bulk_min = float(summary["ess_bulk"].min())

    n_divergent = int(idata.sample_stats["diverging"].sum().values)
    n_total = idata.posterior.sizes["draw"] * idata.posterior.sizes["chain"]
    pct_divergent = 100.0 * (n_divergent / n_total)

    ess_threshold = max(400, int(0.4 * draws * chains))

    # Validate w_hist posterior — 95% HDI must exclude 0
    w_hist_hdi = az.hdi(idata, var_names=["w_hist"], hdi_prob=0.95)["w_hist"].values
    w_hist_excludes_zero = float(w_hist_hdi[0]) > 0
    w_poll_hdi = az.hdi(idata, var_names=["w_poll"], hdi_prob=0.95)["w_poll"].values
    w_poll_excludes_zero = float(w_poll_hdi[0]) > 0

    checks: dict[str, bool] = {
        "rhat_converged": rhat_max < 1.01,
        "ess_sufficient": ess_bulk_min > ess_threshold,
        "no_divergences": n_divergent < 10 and (n_divergent / n_total) < _DIVERGENCE_GATE,
        "w_hist_informative": w_hist_excludes_zero,
    }

    logger.info(
        "Diagnostics: Rhat=%.4f  ESS=%.0f/%d  Div=%.2f%%",
        rhat_max,
        ess_bulk_min,
        ess_threshold,
        pct_divergent,
    )
    logger.info(
        "w_hist 95%% HDI: [%.2f, %.2f]  excludes_zero=%s",
        w_hist_hdi[0],
        w_hist_hdi[1],
        w_hist_excludes_zero,
    )
    logger.info(
        "w_poll 95%% HDI: [%.2f, %.2f]  excludes_zero=%s",
        w_poll_hdi[0],
        w_poll_hdi[1],
        w_poll_excludes_zero,
    )

    if not w_hist_excludes_zero:
        logger.error(
            "w_hist 95%% HDI contains 0 — historical feature not loading. "
            "Check historical join in electoral_dataset_builder."
        )

    # ── Step 7: In-sample Brier ──────────────────────────────────────────────
    logger.info("Computing in-sample Brier score...")
    with model:
        ppc_train = pm.sample_posterior_predictive(idata, random_seed=42)
    y_pred_train = ppc_train.posterior_predictive["y_obs"].mean(dim=["chain", "draw"]).values
    brier_train = float(np.mean((y_pred_train - train_features["y"]) ** 2))
    logger.info("Brier train (in-sample): %.4f", brier_train)

    # ── Step 8: OOS Brier (val 2022) ─────────────────────────────────────────
    brier_oos: float | None = None
    if not df_val.empty:
        df_val_clean = df_val[df_val["y"].between(0.0001, 0.9999)].copy()

        # Garantir que UFs de val estão em train — evita IndexError em PyTensor
        train_ufs = set(df_train["sg_uf"].unique())
        val_ufs = set(df_val_clean["sg_uf"].unique())
        unknown_ufs = val_ufs - train_ufs
        if unknown_ufs:
            logger.warning(
                "Val tem UFs não vistas em treino: %s — serão removidas da validação",
                unknown_ufs,
            )
            df_val_clean = df_val_clean[df_val_clean["sg_uf"].isin(train_ufs)].copy()

        # prepare_electoral_features usa train_stats["top_candidates"]:
        # candidatos novos mapeados para OTHER automaticamente.
        val_features, _ = prepare_electoral_features(df_val_clean, train_stats=train_stats)

        # Garantir que uf_idx de val não excede n_uf de train (sanidade extra)
        _uf_idx_ok = val_features["uf_idx"].max() < train_features["n_uf"]
        if not _uf_idx_ok:
            logger.error(
                "uf_idx val excede n_uf train (%d >= %d) — abortando OOS Brier",
                val_features["uf_idx"].max(),
                train_features["n_uf"],
            )
            # brier_oos permanece None; não bloqueia o modelo

        if _uf_idx_ok:
            logger.info("Computing OOS Brier (val 2022, %d rows)...", len(df_val_clean))
            try:
                with model:
                    pm.set_data(
                        {
                            "X_data": val_features["X_demo"],
                            "hist_data": val_features["hist"],
                            "poll_data": val_features["poll"],
                            "is_new_data": val_features["is_new"],
                            "poll_missing_data": val_features["poll_missing"],
                            "uf_idx_data": val_features["uf_idx"],
                            "cand_idx_data": val_features["cand_idx"],
                        }
                    )
                    ppc_val = pm.sample_posterior_predictive(idata, random_seed=42)
                y_pred_val = (
                    ppc_val.posterior_predictive["y_obs"].mean(dim=["chain", "draw"]).values
                )
                brier_oos = float(np.mean((y_pred_val - val_features["y"]) ** 2))
                logger.info("Brier OOS (val 2022): %.4f (gate < %.2f)", brier_oos, _BRIER_GATE_OOS)

                # Bug 4: logar baseline Brier para contexto comparativo
                hist_fill_rate = (val_features["is_new"] == 0).mean()
                if hist_fill_rate > 0:
                    y_hist_baseline = val_features["hist"].copy()
                    y_hist_baseline[val_features["is_new"] == 1] = val_features["y"][
                        val_features["is_new"] == 1
                    ].mean()
                    brier_baseline = float(np.mean((y_hist_baseline - val_features["y"]) ** 2))
                    logger.info(
                        "Baseline Brier (histórico puro): %.4f | Improvement: %.1f%%",
                        brier_baseline,
                        100 * (1 - brier_oos / brier_baseline) if brier_baseline > 0 else 0,
                    )

            finally:
                # Restore to train data
                with model:
                    pm.set_data(
                        {
                            "X_data": train_features["X_demo"],
                            "hist_data": train_features["hist"],
                            "poll_data": train_features["poll"],
                            "is_new_data": train_features["is_new"],
                            "poll_missing_data": train_features["poll_missing"],
                            "uf_idx_data": train_features["uf_idx"],
                            "cand_idx_data": train_features["cand_idx"],
                        }
                    )

        if brier_oos is not None:
            brier_gap = brier_oos - brier_train
            checks["brier_oos_acceptable"] = brier_oos < _BRIER_GATE_OOS
            checks["brier_gap_acceptable"] = brier_gap < _BRIER_GAP_GATE
            logger.info("Brier gap (OOS - train): %.4f (gate < %.2f)", brier_gap, _BRIER_GAP_GATE)

    all_checks_passed = all(checks.values())
    logger.info("All checks: %s | passed=%s", checks, all_checks_passed)
    if not all_checks_passed:
        failed = [k for k, v in checks.items() if not v]
        logger.warning("Failed checks: %s", failed)

    # ── Step 9: Persist idata + stats to GCS ─────────────────────────────────
    artifact_path: str | None = None
    gcs_bucket = os.environ.get("GCS_BUCKET", "")
    ts = pd.Timestamp.utcnow().strftime("%Y%m%dT%H%M%S")
    model_prefix = f"models/{_MODEL_VERSION}/{ts}"

    if save_trace and gcs_bucket:
        try:
            from google.cloud import storage as gcs

            gcs_client = gcs.Client(project=project_id)
            bucket = gcs_client.bucket(gcs_bucket)

            # idata → netCDF4
            with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
                idata_path = f.name
            idata.to_netcdf(idata_path)
            sha256 = hashlib.sha256(open(idata_path, "rb").read()).hexdigest()
            blob_idata = f"{model_prefix}/idata.nc"
            bucket.blob(blob_idata).upload_from_filename(idata_path)
            artifact_path = f"gs://{gcs_bucket}/{blob_idata}"
            os.remove(idata_path)
            logger.info("idata saved → %s (sha256: %s...)", artifact_path, sha256[:12])

            # norm_stats
            blob_norm = f"{model_prefix}/norm_stats.json"
            bucket.blob(blob_norm).upload_from_string(
                json.dumps(train_stats, indent=2), content_type="application/json"
            )

            # manifest (written last — atomicity marker)
            manifest = {
                "model_version": _MODEL_VERSION,
                "trained_at": ts,
                "blobs": {"idata": blob_idata, "norm_stats": blob_norm},
                "sha256": {"idata": sha256},
                "artifact_path": artifact_path,
                "complete": True,
            }
            bucket.blob(f"{model_prefix}/manifest.json").upload_from_string(
                json.dumps(manifest, indent=2), content_type="application/json"
            )
            logger.info("Manifest written → gs://%s/%s/manifest.json", gcs_bucket, model_prefix)

        except Exception as exc:
            logger.error("GCS save failed: %s — BQ metadata still saved", exc)

    # ── Step 10: BQ metadata ─────────────────────────────────────────────────
    if save_trace:
        try:
            client = bigquery.Client(project=project_id)
            meta = pd.DataFrame(
                {
                    "model_version": _MODEL_VERSION,
                    "timestamp": pd.Timestamp.utcnow().isoformat(),
                    "n_draws": draws,
                    "n_tune": tune,
                    "n_chains": chains,
                    "target_accept": target_accept,
                    "rhat_max": rhat_max,
                    "ess_bulk_min": ess_bulk_min,
                    "ess_threshold": ess_threshold,
                    "pct_divergences": pct_divergent,
                    "brier_train": brier_train,
                    "brier_oos": brier_oos if brier_oos is not None else float("nan"),
                    "w_hist_hdi_low": float(w_hist_hdi[0]),
                    "w_hist_hdi_high": float(w_hist_hdi[1]),
                    "w_poll_hdi_low": float(w_poll_hdi[0]),
                    "w_poll_hdi_high": float(w_poll_hdi[1]),
                    "all_checks_passed": bool(all_checks_passed),
                    "n_uf": train_features["n_uf"],
                    "n_cand": train_features["n_cand"],
                    "train_sample_count": len(train_features["y"]),
                    "val_sample_count": len(df_val),
                    "artifact_path": artifact_path or "",
                },
                index=[0],
            )
            table_id = f"{project_id}.{dataset_id}.electoral_model_metadata"
            client.load_table_from_dataframe(
                meta,
                table_id,
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
            ).result()
            logger.info("Electoral model metadata saved → %s", table_id)
        except Exception as exc:
            logger.error("BQ metadata write failed: %s", exc)

    return {
        "model": model,
        "idata": idata,
        "summary": summary,
        "brier_train": brier_train,
        "brier_oos": brier_oos,
        "rhat_max": rhat_max,
        "ess_bulk_min": ess_bulk_min,
        "pct_divergences": pct_divergent,
        "checks_passed": all_checks_passed,
        "checks": checks,
        "w_hist_hdi": w_hist_hdi.tolist(),
        "w_poll_hdi": w_poll_hdi.tolist(),
        "n_train": len(train_features["y"]),
        "n_val": len(df_val),
        "artifact_path": artifact_path,
        "train_stats": train_stats,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    result = train_electoral_model(
        draws=int(os.environ.get("PYMC_DRAWS", "1000")),
        tune=int(os.environ.get("PYMC_TUNE", "2000")),
        chains=int(os.environ.get("PYMC_CHAINS", "4")),
        target_accept=float(os.environ.get("PYMC_TARGET_ACCEPT", "0.95")),
        save_trace=True,
    )

    print("\n" + "=" * 70)
    print("PyMC Electoral Model — Training Summary")
    print("=" * 70)
    print(f"Model:              {_MODEL_VERSION}")
    print(f"Brier train:        {result['brier_train']:.4f}")
    brier_oos = result.get("brier_oos")
    print(
        f"Brier OOS (2022):   {brier_oos:.4f}  (gate < {_BRIER_GATE_OOS})"
        if brier_oos
        else "Brier OOS:          N/A"
    )
    print(f"Rhat Max:           {result['rhat_max']:.4f}  (target < 1.01)")
    print(
        f"ESS Bulk Min:       {result['ess_bulk_min']:.0f}  (threshold {result['ess_bulk_min']:.0f})"
    )
    print(
        f"Divergences:        {result['pct_divergences']:.2f}%  (gate < {_DIVERGENCE_GATE * 100:.1f}%)"
    )
    w = result["w_hist_hdi"]
    print(f"w_hist 95% HDI:     [{w[0]:.2f}, {w[1]:.2f}]  (must exclude 0)")
    w2 = result["w_poll_hdi"]
    print(f"w_poll 95% HDI:     [{w2[0]:.2f}, {w2[1]:.2f}]  (must exclude 0)")
    print(f"All checks passed:  {result['checks_passed']}")
    print(f"Train samples:      {result['n_train']}")
    print(f"Val samples:        {result['n_val']}")
    if result.get("artifact_path"):
        print(f"Artifact:           {result['artifact_path']}")
    print("=" * 70)
