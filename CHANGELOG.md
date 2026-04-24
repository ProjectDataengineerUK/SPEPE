# CHANGELOG — SPEPE

Formato: [Semantic Versioning](https://semver.org/) + [Conventional Commits](https://www.conventionalcommits.org/)

## [v1.0.0] — 2026-04-24: Design Complete, Architecture Validated

### ✅ Destaques

- **Bronze → Silver → Gold pipeline** — validado end-to-end com dados mock
  - TSE data normalization com `normalize_columns()`
  - IBGE integration via `join_tse_ibge()` + de-para municipality mapping
  - Gold layer aggregation em fact tables com suporte multi-cargo
  - Data quality scoring (DQ gates)

- **Schema preservation** — coluna `cd_cargo` mantida para filtros multi-cargo (Presidente, Governador, etc)

- **Mock-based validation** — 9 integration tests passing, nenhuma dependência externa (GCS, BigQuery, IBGE API)
  - `test_pipeline_e2e_mock.py` (6 testes)
  - `test_schema_gold_cd_cargo.py` (3 testes)

- **CI/CD pipeline** — GitHub Actions com lint, test, LLM eval, security scan
  - Ruff linting + format check
  - pytest com coverage report
  - eval_runner.py LLM quality gate (threshold 0.85)
  - TruffleHog secret scanning

- **Code cleanup**
  - Removido `mcp_servers.*` imports do main code (apenas MCP server impl mantido)
  - Fixed Arrow string dtype issues in `gold_builder.py`
  - Adicionar `__main__` ao `eval_runner.py` para CI execution

### 📋 Escopo Fase 1

**Dados** — SP 2022 + histórico 2018/2022 (27 UFs):
- TSE electoral results (Bronze)
- IBGE socioeconomic indicators (Bronze)
- Silver: normalized + joined
- Gold: 3 fact tables (municipio_eleicao, secao_eleicao, candidato_dia)

**Infraestrutura** — Local (dev mode):
- GCS/BigQuery integrations ready (use_bigquery flag)
- Bronze writer com metadata (_ingested_at, _source)
- Secret Manager placeholder para prod

### 🚀 Próximas fases (Backlog)

- **Fase 1.5** — Social data (Google Trends, Meta Ads, YouTube)
- **Fase 2.0** — MLOps (PyMC Bayesian model, SHAP, predictions)
- **Fase 2.5** — Production hardening (IAP, monitoring, auto-retrain)

### 📦 Dependências

Ver `requirements.txt` e `requirements-dev.txt`.

Mínimas para rodar localmente:
```bash
pip install -r requirements-dev.txt
pytest tests/ -v
python mlops/eval/eval_runner.py
```

---

**Commits nesta versão:**
- d8b8d55: test: add pipeline end-to-end validation
- 38315a6: fix: preserve cd_cargo in Gold schema
- c8d237c: feat: add __main__ to eval_runner
- ee3d230: chore: consolidate v4.2 changes
- 1703beb: security: document API key remediation
- bf91621: docs: consolidate CLAUDE.md roadmap
