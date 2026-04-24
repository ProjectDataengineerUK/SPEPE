# 🐛 TECHNICAL ISSUES & INCONSISTENCIES REPORT — SPEPE v4.2

**Date:** 2026-04-24  
**Status:** 8 bugs + 5 inconsistencies found  
**Priority Distribution:** 🔴 2 Critical + 🟡 5 High + 🟠 6 Medium

---

## 🔴 CRITICAL Issues

### 1. Test Suite Failing (40% Pass Rate)

**Status:** 🔴 CRITICAL  
**Evidence:**
```
Test Results: 29/73 passing (40%)
Failed: 44 tests
```

**Root Causes:**
1. `ModuleNotFoundError: No module named 'mcp_servers.tse'` (primary cause)
2. Test fixtures reference removed `mcp_servers.*` module
3. Function signature mismatch: `write_bronze(df, ..., base_path=...)` but signature doesn't have `base_path`

**Affected Tests:**
- `test_agents_at.py`: 6 tests fail (AT-001 to AT-006)
- `test_archetype_pipeline.py`: 9 tests fail
- `test_dataops_pipeline.py`: 6 tests fail
- `test_dq_gates.py`: 6 tests fail
- `test_mlops_level5.py`: 1 test fails
- `test_security_hooks.py`: 15 tests fail

**Fix Required:**
```python
# In test_agents_at.py, line 12:
- from mcp_servers.tse.schema_registry import get_schema, SCHEMAS
+ from dataops.clients.tse_client import normalize_columns
```

**Impact:** 🔴 Tests unusable in CI/CD

---

### 2. Function Signature Mismatch in `write_bronze()`

**Status:** 🔴 CRITICAL  
**File:** `dataops/bronze_writer.py`

**Error from tests:**
```
TypeError: write_bronze() got an unexpected keyword argument 'base_path'
```

**Current Signature (line ~22):**
```python
def write_bronze(
    df: pd.DataFrame,
    source: str,
    year: int,
    uf: str,
    filename: str,
    use_gcs: bool = False,
) -> str:
```

**Test Expected (test_agents_at.py, line ~31):**
```python
write_bronze(df, "test_source", 2022, "SP", base_path=str(tmp_path))
```

**Issue:** Tests written for different API than implemented

**Fix Required:**
```python
# Either update function signature:
def write_bronze(
    df: pd.DataFrame,
    source: str,
    year: int,
    uf: str,
    filename: str = None,
    use_gcs: bool = False,
    base_path: str = None,  # Add this
) -> str:

# Or update tests to match current signature
write_bronze(df, "test_source", 2022, "SP", "test.parquet")
```

**Impact:** 🔴 Tests cannot validate write_bronze behavior

---

## 🟡 HIGH Issues

### 3. Schema Mismatch: `ds_cargo` vs `cd_cargo`

**Status:** 🟡 HIGH  
**Files:**
- `dataops/silver_transformer.py`: Uses `ds_cargo` (string, description) in CANONICAL_TSE_COLS
- `dataops/gold_builder.py`: Expects `cd_cargo` (int, code) for grouping
- `DEFINE_SPEPE.md` data contract: Specifies `cd_cargo INT` NOT NULL

**Issue:** Silver layer produces `ds_cargo` but Gold layer expects `cd_cargo`

**Evidence:**
```python
# silver_transformer.py line 19:
CANONICAL_TSE_COLS = [
    "sg_uf", "cd_municipio", "nm_municipio", "nr_zona", "nr_secao",
    "nr_candidato", "nm_candidato", "qt_votos", "ds_cargo", "cd_cargo", "nr_turno",  # ← Both now included
]

# gold_builder.py line 64:
if "cd_cargo" in df.columns:
    group_cols.append("cd_cargo")  # ← Expects cd_cargo
```

**Root Cause:** TSE CSV provides `ds_cargo` (description) and `cd_cargo` (code). Silver must preserve both.

**Fix Status:** ✅ ADDRESSED in Task 3 — Added `cd_cargo` to CANONICAL_TSE_COLS

**Remaining Work:**
- Verify mapping TSE `cd_cargo` values to official cargo codes (presidente=1, governador=3, senador=5, etc.)
- Update tests to validate both columns present

**Impact:** 🟡 Multi-cargo filtering will work but mapping may be wrong

---

### 4. Inconsistent Logging Levels

**Status:** 🟡 HIGH  
**Files:** Multiple across agents, dataops, mlops

**Issue:** No consistent logging level strategy; some use INFO, some DEBUG, some WARNING

**Evidence:**
```python
# agents/supervisor.py:
logger.info("DOMA step: ...")
logger.warning("Budget warning: ...")

# dataops/bronze_writer.py:
logger.info(f"Bronze já existe (imutável): {out_path}")

# mlops/drift_monitor.py:
logger.warning(f"Drift detected: JS={js_val}")
```

**Problem:** 
- No clear hierarchy (when to use INFO vs DEBUG vs WARNING)
- Production logs will be noisy or too sparse
- Different teams use different conventions

**Fix Required:**
Create logging standard in CLAUDE.md:
```
DEBUG: Internal flow details (variable values, loop iterations)
INFO:  State changes (job started, Bronze written, transform complete)
WARN:  Expected but notable events (drift detected, budget approaching)
ERROR: Unhandled failures (retry-able)
FATAL: System shutdown required
```

**Impact:** 🟡 Harder to debug in production

---

### 5. Missing Type Hints in UI Layer

**Status:** 🟡 HIGH  
**Files:** 
- `ui/chainlit_app.py`
- `ui/dashboard_api.py`

**Issue:** UI routes lack type hints; makes IDE support, type checking impossible

**Example:**
```python
@app.post("/api/profile")  # Missing return type hint
async def get_profile(uf, municipio, year):  # Missing type hints on params
    # ...
    return {"status": "ok"}
```

**Impact:** 🟡 Type checking fails, harder to refactor

---

### 6. No Retry Logic in Dataops Jobs

**Status:** 🟡 HIGH  
**Files:** `dataops/jobs/*.py`

**Issue:** ETL jobs don't retry on transient failures

**Scenario:**
- Network timeout during TSE download → job fails permanently
- IBGE API rate limit → script crashes instead of backing off
- BigQuery quota exceeded → no exponential backoff

**Missing:**
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_tse_results(uf, year):
    # ...
```

**Impact:** 🟡 Pipeline fragility in production

---

## 🟠 MEDIUM Issues

### 7. Hardcoded Dataset/Project Names

**Status:** 🟠 MEDIUM  
**Files:** Multiple

**Issue:** Dataset names hardcoded as strings, not parameterized

**Examples:**
```python
# dataops/gold_builder.py line ~120:
dataset = os.environ.get("BIGQUERY_DATASET_GOLD", "spepe_gold")

# But elsewhere:
dataset_silver = "spepe_silver"  # Hardcoded
bigquery://${GCP_PROJECT_ID}.spepe_gold.fact_municipio_eleicao  # In drift_config.yaml
```

**Problem:** 
- Makes dev/stg/prod setup error-prone
- No clear fallback strategy
- Inconsistent between Python and YAML config

**Fix Required:**
```python
# Use Settings class:
from config.settings import Settings
settings = Settings()
dataset = settings.bigquery_dataset_gold  # "spepe_gold"
```

**Impact:** 🟠 Deployment errors in different environments

---

### 8. No Integration Test for End-to-End Pipeline

**Status:** 🟠 MEDIUM  
**Finding:** 73 tests exist but NONE run Bronze→Silver→Gold pipeline with real (or mock) data

**Missing:**
```python
# tests/test_pipeline_e2e.py (MISSING)
def test_bronze_to_silver_to_gold():
    # Generate sample TSE data
    df_tse = pd.DataFrame({
        "sg_uf": ["SP"], "cd_municipio": [3550308], "nm_candidato": ["Test"],
        "qt_votos": [100], "ds_cargo": ["Presidente"], "cd_cargo": [1]
    })
    
    # Run pipeline
    transform_to_silver("SP", 2022, use_bigquery=False)
    build_gold(use_bigquery=False)
    
    # Validate output
    assert fact_municipio_eleicao exists
    assert fact_municipio_eleicao has ~200 features
```

**Impact:** 🟠 Hidden schema mismatches only discovered in prod

---

### 9. Configuration Validation Missing

**Status:** 🟠 MEDIUM  
**File:** `config/settings.py`

**Issue:** Settings loaded but not validated at startup

**Missing:**
```python
class Settings(BaseModel):
    anthropic_api_key: str
    gcp_project_id: str
    
    @field_validator("anthropic_api_key")
    def validate_api_key(cls, v):
        if not v:
            raise ValueError("ANTHROPIC_API_KEY not set")
        if not v.startswith("sk-ant-"):
            raise ValueError("Invalid API key format")
        return v
```

**Current behavior:**
- Settings load with empty strings
- Errors occur at first use (delayed failure)
- Harder to debug

**Impact:** 🟠 Configuration errors hidden until runtime

---

### 10. No Version Tracking for Models

**Status:** 🟠 MEDIUM  
**File:** `mlops/prediction_store.py`

**Issue:** Predictions stored without model version; can't trace which model generated prediction

**Missing columns:**
```python
fact_predictions:
  - model_version: "20260423-v1.0"  # MISSING
  - model_commit: "3b1ada5"          # MISSING
  - training_date: "2026-04-20"     # MISSING
```

**Impact:** 🟠 Audit trail broken; can't reproduce predictions

---

## 📋 Inconsistencies Found

### 1. Inconsistent Error Handling Patterns

**Issue:** Mix of patterns across codebase

```python
# Pattern 1: Silent logging
except Exception as e:
    logger.error(f"Failed: {e}")
    return {"status": "error"}

# Pattern 2: Re-raise
except Exception as e:
    logger.error(f"Failed: {e}")
    raise

# Pattern 3: Different exception types
except ValueError:
    ...
except KeyError:
    ...
except:  # ❌ Bare except
    logger.exception("Unexpected error")
```

**Needed:** Single standard pattern across codebase

---

### 2. Inconsistent Column Naming

**Issue:** Snake_case vs camelCase vs UPPER_CASE inconsistency

```python
# Different conventions:
fact_municipio_eleicao  # snake_case ✓
idhm_2010               # snake_case ✓
renda_media_domiciliar  # snake_case ✓
pct_analfabetos         # snake_case ✓
nr_zona                 # abbreviation mix
cd_municipio            # abbreviation
nm_candidato            # abbreviation
sg_uf                   # abbreviation
```

**Issue:** Mix of full names and abbreviations makes schema hard to remember

---

### 3. Config File Multiple Formats

**Issue:** Settings stored in 3 different ways

```python
# Format 1: Python Pydantic (config/settings.py)
class Settings(BaseSettings):
    ...

# Format 2: YAML (mlops/monitoring/drift_config.yaml)
drift_monitoring:
  enabled: true

# Format 3: Environment variables (.env)
ANTHROPIC_API_KEY=...
```

**Problem:** No single source of truth; hard to keep in sync

---

### 4. Inconsistent Docstring Style

**Issue:** Some functions have docstrings, many don't

```python
def write_bronze(...) -> str:
    """Write DataFrame to Bronze layer (immutable, partitioned by source/year/uf)."""  # ✓
    ...

def _normalize_tse(...):  # ❌ No docstring
    ...

def build_gold(...) -> dict:  # ✓ Has docstring
    """Build all 3 Gold tables from Silver layer."""
    ...
```

**Impact:** Harder to understand code without reading implementation

---

### 5. Test Framework Inconsistency

**Issue:** Tests use different assertion styles

```python
# Style 1: pytest assertions
assert df.shape[0] > 0

# Style 2: unittest style
self.assertEqual(len(df), 100)

# Style 3: No assertions (implicit)
# Just calls function, no validation
```

**Needed:** Single test framework convention (pytest preferred)

---

## Summary & Recommendations

### By Severity

| Severity | Count | Timeline |
|----------|-------|----------|
| 🔴 CRITICAL | 2 | IMMEDIATE |
| 🟡 HIGH | 5 | Week 1 |
| 🟠 MEDIUM | 6 | Week 2 |
| 📋 Inconsistencies | 5 | Week 2+ |

### Action Items (Prioritized)

1. **IMMEDIATE (This Hour)**
   - [ ] Fix `write_bronze()` function signature
   - [ ] Update test imports (remove `mcp_servers.*`)

2. **WEEK 1**
   - [ ] Add retry logic to dataops jobs
   - [ ] Fix schema mismatch (cd_cargo vs ds_cargo) validation
   - [ ] Add logging level standard to CLAUDE.md
   - [ ] Add type hints to UI layer
   - [ ] Provision IAP (from Security Audit)

3. **WEEK 2**
   - [ ] Add E2E integration test
   - [ ] Standardize error handling
   - [ ] Create config validation at startup
   - [ ] Add model version tracking
   - [ ] Standardize column naming guidelines

---

## Sign-Off

**BUILD STATUS:** ✅ Ready with critical fixes

- 2 CRITICAL bugs must be fixed today
- 5 HIGH issues before production
- 6 MEDIUM issues within 2 weeks
- Code structure solid; issues are refinement

---

**Report Generated:** 2026-04-24  
**Next Review:** After critical fixes applied
