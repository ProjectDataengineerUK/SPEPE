# SPEPE — Claude Code Guide

**Sistema de Perfilamento do Eleitorado e Previsão Eleitoral**
Análise eleitoral brasileira via multi-agentes LLM com arquitetura Medallion no GCP.

**Estado atual:** v4.2 — Design completo, Fase 1 em implementação
**Próximo:** v1.0.0 — Produção com 5 domínios de dados (2026-Q2)

---

## Visão Estratégica (Brainstorm)

SPEPE não é um sistema único — é uma **plataforma de inteligência eleitoral** com **5 motores de dados independentes** convergindo para um único núcleo de decisão:

| Domínio | Missão | Fontes |
|---------|--------|--------|
| **Social** | Radar narrativo + sentimento | Twitter/X, Facebook, YouTube, TikTok |
| **Pesquisas** | Intenção de voto + evolução | TSE, Atlas, institutos de pesquisa |
| **Dados Públicos** | Contexto estrutural | IBGE (SIDRA), DATASUS |
| **Eleições** | Comportamento histórico | TSE histórico (2018, 2022, 2026) |
| **MLOps** | Predição + cenários | Vertex AI, PyMC, SHAP |

**Princípio-mãe:** Ingestão separada, dados padronizados, análise unificada.

---

## Arquitetura do sistema (Fase 1 — Atual)

```
Usuário (Chainlit / Dashboard)
    │
    ▼
Supervisor — Claude Sonnet 4.6  (agents/supervisor.py)
    │  Protocolo DOMA: Decompose → Orchestrate → Manage budget → Agent chain
    │  Budget: $2.00/sessão | Máx. 5 hops por turno
    │
    ├─► run_dataops_job → Cloud Run Jobs (execução real de ETL)
    │
    └─► route_to_agent → GeminiAgent  (agents/gemini_agent.py)
            │
            ├── coletor            gemini-2.5-flash  /coletar
            ├── analista           gemini-2.5-pro    /analisar /perfil
            ├── perfilador         gemini-2.5-flash  /arquétipos
            ├── modelista_bayesiano gemini-2.5-pro   /prever /simular
            ├── explicador         gemini-2.5-pro    /explicar /shap
            ├── narrador           gemini-2.0-flash  /relatorio
            └── vigilante          gemini-2.0-flash  /monitorar /drift

Dados — Arquitetura Medallion:
  Bronze  → GCS  raw/{source}/{year}/{UF}/  (parquet imutável)
  Silver  → BigQuery spepe_silver           (limpo, joined TSE+IBGE)
  Gold    → BigQuery spepe_gold             (fatos agregados, particionado)

GCP — região principal: southamerica-east1 (LGPD)
      Vertex AI:        us-central1         (Vertex não existe em SA-east1)
```

---

## Estrutura de diretórios

```
agents/
  supervisor.py          # Claude Sonnet — roteador principal, DOMA loop
  gemini_agent.py        # Wrapper Gemini via Vertex AI
  loader.py              # Carrega registry/*.md e instancia GeminiAgents
  tools.py               # run_dataops_job — executa Cloud Run Jobs
  registry/              # Prompts dos 7 agentes (*.md com frontmatter YAML)

config/
  settings.py            # Pydantic Settings — lê .env
  session_state.py       # Estado por sessão (budget, turns, artifacts)
  logging_config.py
  exceptions.py

dataops/
  bronze_writer.py       # GCS ou local — raw/{source}/{year}/{uf}/
  silver_transformer.py  # Bronze → Silver (normaliza, join TSE+IBGE, DQ)
  gold_builder.py        # Silver → Gold (fatos agregados)
  depara_municipios.py   # Join TSE código ↔ IBGE código
  clients/
    tse_client.py        # Download TSE (zips TSE) + normalize_columns()
    ibge_client.py       # SIDRA API + Localidades API
    digital_client.py    # pytrends (Google Trends) + Meta Graph API
  jobs/
    tse_ingest_job.py    # Cloud Run Job: TSE → Bronze
    ibge_sync_job.py     # Cloud Run Job: IBGE → Bronze
    digital_ingest_job.py # Cloud Run Job: Trends + Meta → Bronze
    silver_transform_job.py
    gold_build_job.py

ui/
  chainlit_app.py        # Entry point Chainlit — monta FastAPI + importa dashboard_api
  dashboard_api.py       # FastAPI routes: /dash /api/* /ws/chat
  static/
    spepe-app.html       # Dashboard HTML (Chart.js, multi-cargo, chat↔sync)

mlops/
  vertex_pipeline.py     # KFP pipeline (usa kfp — não kfp.v2)
  pymc_model.py          # Modelo Bayesiano PyMC
  shap_explainer.py      # SHAP explainability
  poll_aggregator.py     # Agrega pesquisas eleitorais
  components/
    train_bootstrap.py
    evaluate.py
    hptuning.py          # Vertex AI HyperparameterTuningJob
    promote.py
  deployment/
    canary_manager.py    # Cloud Run traffic split 10% challenger
    auto_rollback.py     # Reverte se Brier score degradar
  monitoring/
    drift_monitor.py     # JS divergence — publica Pub/Sub se > 0.10
    bias_monitor.py      # Métricas por sg_uf e quintil de renda
    pubsub_publisher.py
  prediction_store.py    # BigQuery fact_predictions + deferred eval
  eval/
    eval_runner.py       # Gate de qualidade LLM contra golden_dataset.jsonl
    metrics.py
    golden_dataset.jsonl

security/
  secret_manager.py      # ENV → Secret Manager com cache
  output_validators.py   # Validação output por agente
  iap_config.yaml

hooks/
  cost_guard_hook.py     # Bloqueia se budget esgotado
  dlp_hook.py            # Mascaramento CPF/CNPJ/telefone
  rate_limit_hook.py
  security_hook.py
  audit_hook.py
  output_compressor_hook.py
  context_budget_hook.py

archetype/               # Clustering HDBSCAN + UMAP para perfis eleitorais
infra/terraform/         # IaC completo — ver seção Terraform abaixo
tests/
```

---

## Comandos de desenvolvimento

```bash
# Instalar dependências
pip install -r requirements.txt

# Rodar UI Chainlit localmente
chainlit run ui/chainlit_app.py --port 8080 --host 0.0.0.0

# Dashboard HTML
# Acesse http://localhost:8080/dash  (servido pelo dashboard_api.py)

# Rodar jobs de ingestão localmente (sem GCS)
python -m dataops.jobs.tse_ingest_job --uf SP --year 2022
python -m dataops.jobs.ibge_sync_job --uf SP
python -m dataops.jobs.digital_ingest_job

# Transformação Silver → Gold
python -m dataops.jobs.silver_transform_job --uf SP --year 2022
python -m dataops.jobs.gold_build_job

# Compilar pipeline Vertex AI
python -c "from mlops.vertex_pipeline import compile_pipeline; compile_pipeline()"

# Rodar eval LLM
python mlops/eval/eval_runner.py

# Testes
pytest tests/ -v
```

---

## Variáveis de ambiente

Ver `.env.example` para a lista completa. Mínimo para rodar localmente:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GCP_PROJECT_ID=spepe-dev       # opcional localmente
USE_BIGQUERY=false             # usa parquet local em vez de BigQuery
DEFAULT_UF=SP
DEFAULT_ANO=2022
```

Para GCP produção — todas as secrets via Secret Manager, não .env.

**NUNCA commitar `.env`** — está no `.gitignore`.

---

## Regras de código

### Imports — clientes corretos
```python
# TSE
from dataops.clients.tse_client import download_tse_resultados, normalize_columns

# IBGE
from dataops.clients.ibge_client import fetch_sidra_indicators, load_municipios

# Digital
from dataops.clients.digital_client import fetch_trends, fetch_meta_ads
```

`mcp_servers.*` foi removido — não usar.

### KFP — namespace correto (KFP 2.x)
```python
from kfp import dsl, compiler
from kfp.dsl import component, Input, Output, Dataset, Model, Metrics
# NÃO usar: from kfp.v2 import ...
```

### Vertex AI — região correta
```python
# Vertex AI NÃO existe em southamerica-east1
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
# Cloud Run, GCS, BigQuery → southamerica-east1
```

### Bronze writer
```python
from dataops.bronze_writer import write_bronze

write_bronze(
    df=df,
    source="tse",          # tse | ibge | digital
    year=2022,
    uf="SP",
    filename="resultados_SP_2022.parquet",
    use_gcs=bool(os.environ.get("GCS_BUCKET")),
)
```

### Dashboard API — servir HTML do container
```python
# CORRETO — path relativo ao arquivo Python
from pathlib import Path
html_path = Path(__file__).parent / "static" / "spepe-app.html"

# ERRADO — não existe dentro do container Docker
html_path = os.path.expanduser("~/.agent/diagrams/spepe-app.html")
```

### Budget por sessão
- Limite: `$2.00` — definido em `config/session_state.py:BUDGET_USD`
- Aviso em `$1.50` — `BUDGET_WARN_USD`
- `BudgetExceededError` é lançado automaticamente pelo `cost_guard_hook.py`

---

## Terraform — o que existe e o que faz

| Arquivo | Recursos |
|---------|----------|
| `main.tf` | Provider, backend GCS (`prefix = "spepe"`) |
| `variables.tf` | `project_id`, `region`, `vertex_region`, `environment`, `app_image`, `wif_pool_id`, `github_repo` |
| `gcs.tf` | Bucket `spepe_data` — Bronze layer |
| `bigquery.tf` | Datasets Silver + Gold + tabelas fact |
| `bigquery_mlops.tf` | Dataset MLOps — model_evaluations, bias_metrics, fact_predictions |
| `cloud_run.tf` | Serviço principal Chainlit/FastAPI |
| `cloud_run_canary.tf` | Traffic split para challenger |
| `cloud_run_jobs.tf` | 5 Cloud Run Jobs (tse_ingest, ibge_sync, digital_ingest, silver_transform, gold_build) |
| `secrets.tf` | Secret Manager: ANTHROPIC_API_KEY, META_APP_TOKEN, YOUTUBE_API_KEY + IAM |
| `artifact_registry.tf` | Repositório Docker `spepe` + IAM |
| `pubsub.tf` | Topic `drift-detected` para auto-retrain loop |
| `eventarc.tf` | Trigger Pub/Sub → Cloud Run Job retrain |
| `firestore.tf` | Coleção `spepe_sessions` — memória de sessão |
| `monitoring.tf` | Budget alerts 50%/90%/100%, audit log sink |
| `outputs.tf` | URLs e IDs dos recursos criados |

### Bootstrap (primeiro deploy)
```bash
# 1. Criar bucket de state manualmente
gcloud storage buckets create gs://SEU_PROJECT_ID-terraform-state --location=southamerica-east1

# 2. Init com backend
cd infra/terraform
terraform init -backend-config="bucket=SEU_PROJECT_ID-terraform-state"

# 3. Plan
terraform plan -var="project_id=SEU_PROJECT_ID" -var="admin_email=SEU@EMAIL.COM" \
  -var="app_image=southamerica-east1-docker.pkg.dev/SEU_PROJECT_ID/spepe/app:SHA" \
  -var="wif_pool_id=SEU_WIF_POOL" -var="github_repo=owner/repo"

# 4. Apply
terraform apply [mesmas vars]
```

---

## CI/CD — GitHub Actions

| Workflow | Trigger | O que faz |
|----------|---------|-----------|
| `ci.yml` | PR / push | Lint, testes, eval LLM, security scan |
| `deploy.yml` | Push main | Build → Artifact Registry → deploy staging → smoke test → prod |
| `ml_pipeline.yml` | Schedule / manual | Compila e submete pipeline Vertex AI |
| `canary_deploy.yml` | Manual | Canary 10% → avalia → promove ou rollback |

Todos usam Workload Identity Federation — sem chaves SA em secrets.

---

## 7 Agentes — referência rápida

| Agent ID | Modelo | Comando | Responsabilidade |
|----------|--------|---------|-----------------|
| `coletor` | Gemini 2.5 Flash | `/coletar {UF} {ano}` | Sumariza resultado de ingestão TSE/IBGE já executada |
| `analista` | Gemini 2.5 Pro | `/analisar`, `/perfil` | Cruza resultados eleitorais × socioeconômico IBGE |
| `perfilador` | Gemini 2.5 Flash | `/arquétipos`, `/perfis` | HDBSCAN + UMAP para identificar clusters de eleitorado |
| `modelista_bayesiano` | Gemini 2.5 Pro | `/prever`, `/simular` | Previsão probabilística PyMC com IC 95% |
| `explicador` | Gemini 2.5 Pro | `/explicar`, `/shap` | SHAP em linguagem natural |
| `narrador` | Gemini 2.0 Flash | `/relatorio`, `/narrar` | Relatório acessível para não-técnicos |
| `vigilante` | Gemini 2.0 Flash | `/monitorar`, `/drift` | JS divergence + bias por UF e quintil de renda |

Prompts em `agents/registry/*.md` (frontmatter YAML + system prompt Markdown).

---

## Escopo de Dados por Fase

### Fase 1 — v1.0.0 (MVP Produção)
**Dados:** TSE 2022 + IBGE + Histórico 2018/2022 fixo
**Cobertura:** Todas as 27 UFs
**Atualização:** TSE ingerido uma vez (snapshot), IBGE anual
**Modelos:** Bayesiano (PyMC) — baseline de previsão
**Consumo:** Dashboard tático + 7 agentes Claude/Gemini

### Fase 2 — v1.5 (Social + Contexto)
**Dados adicionais:** Twitter/X, Facebook, DATASUS
**Atualização:** Social em tempo real (streaming), DATASUS mensal
**Modelos:** NLP (sentimento, polarização), score territorial
**Consumo:** Alertas de crise, semantic layer views

### Fase 3+ — v2.0+ (Completo)
**Dados adicionais:** YouTube, TikTok, pesquisas de institutos, dados históricos granulares
**Modelos:** Feature store maduro, ensemble de modelos
**Consumo:** API REST interna, Looker Studio, auto-retrain

---

## Fluxo de dados — Fase 1

```
FONTES (Fase 1)
  TSE (zip) ────────────────────────┐
  IBGE SIDRA API ───────────────── ├─► Bronze (GCS parquet)
  IBGE Localidades API ──────────── ┤    raw/{source}/{year}/{UF}/
  Google Trends (pytrends) ──────────┤ ← Opcional: para teste
  Meta Ad Library API ────────────────┘ ← Opcional: para teste
           │
           ▼ silver_transformer.py
  Silver (BigQuery spepe_silver)
    tse_{uf}_{year}  — normalizado + joined IBGE + DQ score
           │
           ▼ gold_builder.py
  Gold (BigQuery spepe_gold)
    fact_municipio_eleicao  — particionado por ano_eleicao, clusterizado por sg_uf
    fact_ibge_municipio     — indicadores socioeconômicos
    fact_candidato_eleicao  — agregado por candidato
           │
           ▼
  MLOps (BigQuery spepe_mlops)
    model_evaluations | bias_metrics | fact_predictions
           │
           ▼ [7 Agentes Claude/Gemini]
  UI (Chainlit + Dashboard)
```

### Adições Fase 2+

```
FONTES (Fase 2+)
  Twitter/X API ─────────────┐
  Facebook Graph API ────────┤
  YouTube Data API ──────────├─► Bronze (Cloud Storage streaming)
  TikTok API ─────────────────┤
  DATASUS API ────────────────┘
           │
           ▼ [NLP, Clustering]
  Silver (streaming tables)
    stg_social_event
    stg_datasus_monthly
           │
           ▼ [Feature engineering]
  Gold + Feature Store
```

---

## Segurança e compliance

- **DLP**: `hooks/dlp_hook.py` mascara CPF, CNPJ, telefone em toda saída
- **Secret Manager**: `security/secret_manager.py` — fallback ENV → GCP Secret Manager
- **LGPD**: dados eleitorais são dados públicos (TSE); indicadores IBGE são agregados; sem PII de eleitores individuais
- **Retenção**: Silver 90 dias (BigQuery partition expiry); Bronze indefinido em GCS
- **IAP**: configurado em `security/iap_config.yaml` — provisionar via `google_iap_*` no Terraform para prod
- **allUsers em dev**: `cloud_run.tf` abre acesso público apenas em `environment == "dev"`

---

## Roadmap — 4 Fases

### Fase 1 (v1.0.0 — Atual, ~2026-Q2)
**MVP: Pesquisas + Dados Públicos + Histórico Fixo**
- ✅ Arquitetura Medallion single-project (spepe-dev)
- ✅ TSE (pesquisas) + IBGE (contexto) + Histórico 2018/2022 ingeridos
- ✅ 7 agentes Claude/Gemini (análise, predição, narrativa)
- ⏳ Todas as 27 UFs + dados reais
- ⏳ Dashboard tático (multi-cargo, comparação UFs)
- ⏳ CI/CD completo (test → staging → prod)

### Fase 2 (v1.5 — ~2026-Q4)
**Adição: Social + DATASUS + Camada Semântica**
- Módulo social (Twitter/X, Facebook): sentimento + polarização
- DATASUS: contexto de saúde + vulnerabilidade territorial
- Semantic layer: views de consumo (vw_sentimento_municipio, etc.)
- Alertas de crise por narrativa/tema
- NLP melhorado (Vertex AI)

### Fase 3 (v2.0 — ~2027-Q2)
**MLOps Formal + Vertex AI Pipelines**
- Modelo de cenários (PyMC + Bayesiano)
- Feature store (características sociais, pesquisas, estruturais)
- Score territorial (risco político, força narrativa)
- Auto-retrain com drift detection
- Canary deployment (10% challenger)

### Fase 4 (v2.5+ — 2027+)
**Otimização de Custos + Feature Store Maduro**
- Segregação em projetos GCP (core-analytics, social, pesquisas, dados-públicos, eleições)
- Folders por ambiente (dev/stg/prod)
- API interna de inteligência (REST)
- Dashboard executivo (Looker Studio)
- Automação pesada

---

## Arquitetura Futura — Multi-Projeto (Fases 2+)

Após validar Fase 1, SPEPE migrará para **3+ projetos GCP por ambiente**:

```
org-eleicoes/
  ├── folder-dev/
  │   ├── prj-dev-core-analytics      (BigQuery central, semantic layer)
  │   ├── prj-dev-data-platform       (Bronze/Silver — será quebrado em: social/pesquisas/publicos/eleicoes)
  │   └── prj-dev-ml-platform         (Vertex AI, feature store, modelos)
  │
  ├── folder-stg/
  │   ├── prj-stg-core-analytics
  │   ├── prj-stg-data-platform
  │   └── prj-stg-ml-platform
  │
  └── folder-prod/
      ├── prj-prod-core-analytics
      ├── prj-prod-data-platform (→ social/pesquisas/publicos/eleicoes)
      └── prj-prod-ml-platform
```

**Modelo Lógico Comum** (todos os projetos):

Dimensões-mãe:
- `dim_tempo` — data, semana, mês, ano eleitoral
- `dim_territorio` — UF, município (IBGE), região
- `dim_candidato` — id, nome, partido, cargo
- `dim_tema` — tema político (saúde, economia, etc.)
- `dim_fonte` — social, pesquisa, IBGE, DATASUS

Fatos:
- `fato_social` — agregado por hora/dia × tema × candidato × UF
- `fato_pesquisa` — intenção × instituto × data × candidato × UF
- `fato_eleicao` — votos históricos 2018/2022/2026 × cargo × UF
- `fato_ibge` — indicadores estruturais × UF × período
- `fato_datasus` — saúde pública × UF × período

Chaves-mestras: `cod_municipio_ibge`, `uf`, `ano_eleitoral`, `data_referencia`

---

## Pendências v1.0.0 — Crítico

### 🔴 **Bloqueia Deploy**
- [ ] **URGENTE**: Revogar ANTHROPIC_API_KEY exposta em `.env` — ir em console.anthropic.com
- [ ] Committar 4 arquivos .py pendentes + gitignore audit logs
- [ ] Adicionar `__main__` em mlops/eval/eval_runner.py para CI

### 🟠 **Valida Funcionalidade (Fase 1)**
- [ ] Pipeline end-to-end: ingerir **todas 27 UFs** 2022 (TSE + IBGE)
- [ ] Validar coluna `cd_cargo` em Gold (quebra filtro multi-cargo)
- [ ] Testes passando: pytest + eval_runner + security scan
- [ ] Compilar e testar Vertex AI pipeline KFP 2.x

### 🟡 **Produção Segura**
- [ ] Secrets em Secret Manager (ANTHROPIC_API_KEY, META_APP_TOKEN, YOUTUBE_API_KEY)
- [ ] Provisionar IAP via Terraform (security/iap_config.yaml → google_iap_*)
- [ ] Validar imports: nenhuma referência a `mcp_servers.*`

### 🟢 **Infraestrutura**
- [ ] Documentar .env local (README: Quick Start)
- [ ] Release v1.0.0: branch release/ → tag v1.0.0 → push
- [ ] Deploy: Terraform apply staging → deploy.yml workflow → prod

---

## Pendências Conhecidas — Documento

Para referência histórica (pode ser feito pós-v1.0.0):
- [ ] `drift_config.yaml` — existe em mlops/monitoring/, já resolvido
- [ ] IAP não provisionado (vide Pendências Críticas acima)
