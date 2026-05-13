---
name: Next Steps — Model Training (Phases 2-3 Validation)
type: project
---

# Next Steps: Model Training & Phase Validation

**Trigger:** When Cloud Run deployment completes  
**Action:** Run PyMC model + validate Phases 2-3 in parallel  
**Target:** 2026-05-13 (segunda) onwards

---

## Ready-to-Run Commands

### Phase A: Trigger Cloud Run Training Job
```bash
# Deploy/update the spepe-pymc-train job to Cloud Run
gcloud run jobs deploy spepe-pymc-train \
  --image=southamerica-east1-docker.pkg.dev/spepe-prod/spepe/app:latest \
  --region=southamerica-east1 \
  --set-env-vars="PYMC_DRAWS=2000,PYMC_TUNE=1500,PYMC_CHAINS=4,PYMC_TARGET_ACCEPT=0.95" \
  --cpu=2 \
  --memory=4Gi \
  --timeout=3600s

# Execute training job
gcloud run jobs execute spepe-pymc-train \
  --region=southamerica-east1 \
  --wait
```

### Phase B: Run Model Locally (Dev)
```bash
# For testing before Cloud Run deploy
cd C:/Users/User/ProjetosAgents/SPEPE

python -m mlops.pymc_train \
  --draws=2000 \
  --tune=1500 \
  --chains=4 \
  --target_accept=0.95 \
  --save_trace=true

# Expected output:
# - Loading training dataset (17 fontes, 2.826 obs, 12 features)
# - Building hierarchical model (non-centered)
# - Sampling 2000 draws × 4 chains
# - evaluate_pymc_convergence: 9 checks
# - gate_model_promotion: PROMOTED or REJECTED
# - Metadata saved to BigQuery
```

---

## Phase 2 Validation (Features 2026-aware)

**What to verify:**
1. ✅ Training dataset has 12 features (not 14)
   ```sql
   SELECT COUNT(DISTINCT feature_name) 
   FROM spepe_mlops.training_dataset_features
   ```

2. ✅ Features include temporal lag: `pct_votos_partido_anterior`
   ```sql
   SELECT COUNT(*) 
   FROM spepe_gold.fact_municipio_eleicao
   WHERE pct_votos_partido_anterior > 0
   ```

3. ✅ Social RED features removed (YouTube, RSS)
   - Confirm: `sentimento_positivo`, `sentimento_negativo`, `polarizacao_entropia` still present
   - Confirm: NO YouTube/RSS columns

4. ✅ 2026 candidates validated
   ```bash
   python -c "
   from dataops.clients.candidato_2026_client import fetch_candidatos_oficiais_2026
   df = fetch_candidatos_oficiais_2026()
   print(f'Registered 2026 candidates: {len(df)}')
   print(df[['candidato', 'sg_partido']].head())
   "
   ```

---

## Phase 3 Validation (Non-Centered PyMC)

**What to verify:**

1. ✅ Model architecture is non-centered
   ```python
   from mlops.pymc_model import build_hierarchical_model
   import inspect
   
   source = inspect.getsource(build_hierarchical_model)
   assert "mu_a + s_a * a_raw" in source  # Non-centered intercept
   assert "mu_b[None, :] + s_b[None, :] * b_raw" in source  # Non-centered slopes
   assert "phi = pm.Gamma" in source  # Learned dispersion
   print("✅ Non-centered parameterization confirmed")
   ```

2. ✅ Sampling parameters are robust
   ```python
   from mlops.pymc_train import train_pymc_model
   import inspect
   
   sig = inspect.signature(train_pymc_model)
   defaults = {p: sig.parameters[p].default for p in sig.parameters}
   
   assert defaults['draws'] == 2000, "draws should be 2000"
   assert defaults['chains'] == 4, "chains should be 4"
   assert defaults['target_accept'] == 0.95, "target_accept should be 0.95"
   print("✅ Robust sampling parameters confirmed")
   ```

3. ✅ 9 Diagnostics implemented
   ```python
   from mlops.eval.eval_runner import evaluate_pymc_convergence
   
   # Expects idata, y_test → returns 9 checks
   checks = [
       'rhat_converged', 'ess_sufficient', 'bfmi_healthy',
       'no_divergences', 'loo_pareto_ok', 'mae_acceptable',
       'crps_acceptable', 'coverage_95', 'calibration_ok'
   ]
   print(f"✅ All {len(checks)} diagnostics implemented")
   ```

4. ✅ Hard gate implemented
   ```python
   from mlops.eval.eval_runner import gate_model_promotion
   
   # Gate returns PROMOTED only if ALL 9 checks pass
   # Otherwise REJECTED → must increase draws/tune and resample
   print("✅ Hard gate (all-or-nothing) implemented")
   ```

---

## Expected Results

### If Training Succeeds (PROMOTED)

```
Model Status: ✅ PROMOTED

Diagnostics Summary:
  [1/9] Rhat: 0.99 (target < 1.01) ✅
  [2/9] ESS bulk: 1247 (target > 1000) ✅
  [3/9] BFMI: 0.45 (target > 0.3) ✅
  [4/9] Divergences: 0.08% (target < 0.1%) ✅
  [5/9] Pareto-k: 96.2% OK (target > 95%) ✅
  [6/9] MAE: 0.043 (target < 0.05) ✅
  [7/9] CRPS: 0.038 (target < 0.04) ✅
  [8/9] Coverage: 94.5% (target 92-98%) ✅
  [9/9] ECE: 0.028 (target < 0.03) ✅

→ Deploy model to Vertex AI serving
→ Set up auto-retrain (scheduled weekly)
→ Enable monitoring/drift detection
```

### If Training Fails (REJECTED)

```
Model Status: ❌ REJECTED

Failed checks:
  - Divergences: 0.15% > 0.1% ❌
  - Rhat: 1.02 > 1.01 ❌

Action Required:
  1. Increase draws: 2000 → 3000
  2. Increase tune: 1500 → 2000
  3. Increase chains: 4 → 6
  4. Retry sampling
  5. Re-evaluate gate
```

---

## Parallel Execution Plan

**When deployment completes:**

```
Cloud Run Deploy (DONE)
    ↓
    ├─► Agent 1: Phase 2 Validation (Features)
    │   - Load training_dataset
    │   - Count features: expect 12
    │   - Verify temporal features present
    │   - Check 2026 candidates
    │   - Expected time: 5 min
    │
    └─► Agent 2: Phase 3 Validation (PyMC)
        - Run train_pymc_model
        - Automatic evaluate_pymc_convergence
        - gate_model_promotion → PROMOTED/REJECTED
        - Save trace to BigQuery
        - Expected time: 45-60 min (4 chains × 2000 draws)

Results → Consolidated report
    → If PROMOTED: Deploy to production
    → If REJECTED: Debug + retry
```

---

## How to Trigger

When ready, run:

```bash
# Tell Claude to start Phase 2-3 validation in parallel
# Message: "já pode rodar modelo paralelo fase 2 e 3"
```

Claude will:
1. Launch 2 AgentSpec agents in parallel
2. Agent 1: Phase 2 validation (features, candidates, data quality)
3. Agent 2: Phase 3 validation (model training + 9 diagnostics + gate)
4. Monitor both until completion
5. Consolidate results
6. Report PROMOTED/REJECTED status

---

**Status:** Ready for signal. Awaiting deployment completion.

**Last updated:** 2026-05-13 00:30 UTC
