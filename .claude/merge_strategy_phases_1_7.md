---
name: Merge Strategy — DataOps (1-2) + ML (3-7)
type: project
---

# Merge Strategy: DataOps Agent + ML Agent

**Status:** DataOps complete ✅ | ML in progress ⏳

---

## Files Modified by DataOps (Phases 1-2)

| File | Changes | Risk Level |
|------|---------|-----------|
| `mlops/pymc_model.py` | Lines 15-183 rewritten (non-centered, 212 lines total) | 🔴 HIGH |
| `mlops/pymc_train.py` | Multiple sections (lines 91-236), bug fixes | 🔴 HIGH |
| `mlops/eval/training_dataset_builder.py` | Lines 64-195 modified (temporal joins, features) | 🟡 MEDIUM |
| `mlops/eval/eval_runner.py` | 232 lines added (diagnostics functions) | 🟡 MEDIUM |
| `dataops/clients/candidato_2026_client.py` | NEW file (160 lines) | 🟢 LOW |
| `requirements.txt` | 1 line added (arviz>=0.17.0) | 🟢 LOW |

---

## Expected ML Agent Changes (Phases 3-7)

**Phase 3:** Rewrite `mlops/pymc_model.py` — build_hierarchical_model()
- Non-centered parameterization (mu_a/s_a, mu_b/s_b)
- Learned phi (Gamma dispersion)
- Beta likelihood with proper alpha/beta params

**Phase 5:** Modify `mlops/pymc_train.py` — sampling section
- Increase draws: 1000 → 2000
- Increase chains: 2 → 4
- Add init="jitter+adapt_diag"

**Phase 6:** Expand `mlops/eval/eval_runner.py` — diagnostics functions
- 9 ArviZ diagnostic checks
- Plotting functions (trace, energy, PPC)
- MAE/CRPS/calibration metrics

**Phase 7:** New gate function in `mlops/eval/eval_runner.py`
- Hard promotion gate (all 9 checks)
- BigQuery logging

---

## Conflict Points & Resolution

### 1. `mlops/pymc_model.py` — HIGH CONFLICT RISK

**DataOps did:**
- Lines 15-183: Rewrote with non-centered params, learned phi, domain-aware priors
- Function signature unchanged: `build_hierarchical_model(X, y, uf_idx, ...)`
- Return: `model` (PyMC context manager)

**ML Agent will do:**
- Phases 3: Rewrite same function with nearly identical logic
- Add docstring with detailed architecture
- Same output format

**Resolution strategy:**
1. **IF no conflicts:** Use DataOps version (already has non-centered + phi)
2. **IF conflicts on logic:** Manually merge docstrings + minor improvements from ML
3. **Test:** `python -c "from mlops.pymc_model import build_hierarchical_model; print('✅')"`

**Action:** After ML completes, run:
```bash
git show a8a3ffd5911fe3269:mlops/pymc_model.py > /tmp/dataops_pymc_model.py
git show af548a896a93ac7e0:mlops/pymc_model.py > /tmp/ml_pymc_model.py
diff -u /tmp/dataops_pymc_model.py /tmp/ml_pymc_model.py | head -50
```

---

### 2. `mlops/pymc_train.py` — HIGH CONFLICT RISK

**DataOps did:**
- Line 94: Extract `y = df["y_continuous"].values` (fixes NameError)
- Lines 109-112: Reduce features to 11 core + temporal
- Various logging/formatting changes

**ML Agent will do:**
- Phase 5: Modify sampling call (draws, chains, init)
- Change defaults in function signature (line 18-22)

**Resolution strategy:**
1. Keep DataOps bug fixes (y extraction, feature selection)
2. Apply ML Agent sampling changes (draws=2000, chains=4)
3. Manually merge if both agents touch same region

**Action:** After ML completes:
```bash
git diff a8a3ffd5911fe3269..af548a896a93ac7e0 -- mlops/pymc_train.py
```

---

### 3. `mlops/eval/eval_runner.py` — MEDIUM CONFLICT RISK

**DataOps did:**
- Added 232 lines of diagnostic plotting functions
- Likely added some of Phase 6 diagnostics prematurely

**ML Agent will do:**
- Phase 6: Add `evaluate_pymc_convergence()` with all 9 checks
- Phase 7: Add `gate_model_promotion()` function

**Resolution strategy:**
1. Check if DataOps overlapped with Phase 6
2. If yes, keep better implementation (likely ML)
3. If no, merge both additions

**Action:** Review DataOps additions to eval_runner.py:
```bash
git show a8a3ffd5911fe3269:mlops/eval/eval_runner.py | tail -50
```

---

## Merge Process (After ML Agent Completes)

### Step 1: Check for auto-merge conflicts
```bash
cd C:/Users/User/ProjetosAgents/SPEPE

# Get latest commit from ML Agent
ml_commit=$(git log --oneline | grep "Phase 3-7" | head -1 | cut -d' ' -f1)

# Try auto-merge (may fail)
git merge $ml_commit 2>&1 | grep -E "CONFLICT|Auto-merging"
```

### Step 2: If conflicts, manually resolve
```bash
# List conflicted files
git status | grep "both modified"

# For each file:
git mergetool mlops/pymc_model.py
git mergetool mlops/pymc_train.py
git mergetool mlops/eval/eval_runner.py
```

### Step 3: Verify merged result
```bash
# Syntax check
python -m py_compile mlops/pymc_model.py mlops/pymc_train.py mlops/eval/eval_runner.py

# Import check
python -c "
from mlops.pymc_model import build_hierarchical_model
from mlops.pymc_train import train_pymc_model
from mlops.eval.eval_runner import evaluate_pymc_convergence, gate_model_promotion
print('✅ All imports OK')
"
```

### Step 4: Final merge commit
```bash
git add -A
git commit -m "Merge Phases 1-7: DataOps + ML refactors

Consolidates:
- DataOps (51ca30f, 4055226): 6 bugs fixed, features 2026-aware
- ML (XXX, XXX): PyMC non-centered, robust sampling, diagnostics, gate

Result: Phases 1-7 complete, model ready for training validation.

Co-Authored-By: DataOps Specialist <noreply@anthropic.com>
Co-Authored-By: ML Specialist <noreply@anthropic.com>"

git push origin main
```

---

## Testing After Merge

### Smoke Test 1: Imports
```bash
python -c "
import mlops.pymc_model
import mlops.pymc_train
import mlops.eval.eval_runner
import mlops.eval.training_dataset_builder
print('✅ All modules import successfully')
"
```

### Smoke Test 2: Model construction
```bash
python << 'EOF'
import numpy as np
from mlops.pymc_model import build_hierarchical_model

# Dummy data
X = np.random.randn(100, 12)
y = np.random.uniform(0.01, 0.99, 100)
uf_idx = np.repeat(np.arange(4), 25)

model = build_hierarchical_model(X, y, uf_idx, 4, 12)
print(f"✅ Model created: {type(model)}")
print(f"✅ Prior predictive possible: {model is not None}")
EOF
```

### Smoke Test 3: Requirements
```bash
pip install -r requirements.txt

# Check critical deps
python -c "import pymc; import arviz; import pytensor; print('✅ All deps OK')"
```

---

## Rollback Plan

If merge introduces bugs:

```bash
# Revert to DataOps version (known working)
git revert HEAD --no-edit

# Or revert to pre-merge
git reset --hard a8a3ffd5911fe3269

# Debug separately, re-do merge
```

---

## Timeline

- ✅ **2026-05-13 ~00:30** — DataOps complete (51ca30f, 4055226)
- ⏳ **2026-05-13 ~11:30** — ML Agent completes (ETA)
- 🔄 **2026-05-13 ~12:00** — Manual merge + testing
- 🎯 **2026-05-13 ~13:00** — Validation complete, ready for training

---

**Status:** Awaiting ML Agent completion. Will proceed with merge once notified.
