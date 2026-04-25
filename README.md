# SPEPE

**Sistema de Perfilamento do Eleitorado e Previsão Eleitoral**

Plataforma de inteligência eleitoral brasileira com arquitetura Medallion no GCP, multi-agentes LLM (Claude + Gemini) e modelagem bayesiana. Cobre dados TSE, IBGE, pesquisas e mídias sociais para análise das 27 UFs.

> Estado atual: **v1.0.0** — infraestrutura GCP completa, pronto para `terraform apply`.

---

## Pré-requisitos

| Ferramenta | Versão mínima |
|-----------|--------------|
| Python | 3.12 |
| pip | 24+ |
| Docker | 20+ (para build de imagem) |
| Terraform | 1.5+ (para deploy GCP) |
| gcloud CLI | qualquer (para autenticação) |

---

## Quick Start — Local

```bash
# 1. Clone e configure o ambiente
git clone https://github.com/ProjectDataengineerUK/SPEPE.git
cd SPEPE
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Crie o .env a partir do exemplo
cp .env.example .env
# Edite .env: preencha ANTHROPIC_API_KEY (mínimo para rodar localmente)

# 3. Suba a UI Chainlit
chainlit run ui/chainlit_app.py --port 8080 --host 0.0.0.0

# 4. Acesse
#   Chat:      http://localhost:8080
#   Dashboard: http://localhost:8080/dash
```

**Variáveis mínimas para rodar localmente** (sem GCP):

```bash
ANTHROPIC_API_KEY=sk-ant-...
USE_BIGQUERY=false     # usa parquet local em vez de BigQuery
DEFAULT_UF=SP
DEFAULT_ANO=2022
```

Ver `.env.example` para a lista completa.

---

## Comandos dos jobs de dados

```bash
# Ingestão Bronze (sem GCS — grava parquet local)
python -m dataops.jobs.tse_ingest_job --uf SP --year 2022
python -m dataops.jobs.ibge_sync_job --uf SP
python -m dataops.jobs.digital_ingest_job

# Transformação Silver → Gold
python -m dataops.jobs.silver_transform_job --uf SP --year 2022
python -m dataops.jobs.gold_build_job

# Pipeline Vertex AI (KFP 2.x)
python -c "from mlops.vertex_pipeline import compile_pipeline; compile_pipeline()"

# Eval LLM (gate CI: score mínimo 0.85)
python mlops/eval/eval_runner.py

# Testes
pytest tests/ -v
```

---

## Arquitetura

```
Chainlit / Dashboard
        │
        ▼
   Supervisor (Claude Sonnet 4.6)
        │  Budget $2/sessão · DOMA loop · máx 5 hops
        │
        ├─► run_dataops_job → Cloud Run Jobs (ETL real)
        │
        └─► route_to_agent → GeminiAgent
                ├── coletor            gemini-2.5-flash
                ├── analista           gemini-2.5-pro
                ├── perfilador         gemini-2.5-flash
                ├── modelista_bayesiano gemini-2.5-pro
                ├── explicador         gemini-2.5-pro
                ├── narrador           gemini-2.0-flash
                └── vigilante          gemini-2.0-flash

Medallion:
  Bronze  → GCS  raw/{source}/{year}/{UF}/  (parquet imutável)
  Silver  → BigQuery spepe_silver
  Gold    → BigQuery spepe_gold
```

Região GCP: `southamerica-east1` (LGPD) · Vertex AI: `us-central1`

---

## Deploy GCP — Bootstrap

```bash
# 1. Crie o bucket de state do Terraform
gcloud storage buckets create gs://SEU_PROJECT_ID-terraform-state \
  --location=southamerica-east1

# 2. Init
cd infra/terraform
terraform init -backend-config="bucket=SEU_PROJECT_ID-terraform-state"

# 3. Plan
terraform plan \
  -var="project_id=SEU_PROJECT_ID" \
  -var="admin_email=SEU@EMAIL.COM" \
  -var="app_image=southamerica-east1-docker.pkg.dev/SEU_PROJECT_ID/spepe/app:SHA" \
  -var="github_repo=ProjectDataengineerUK/SPEPE" \
  -var="billing_account_id=XXXXXX-XXXXXX-XXXXXX"

# 4. Apply
terraform apply [mesmas vars]
```

Para staging/prod adicione também:
- `-var="environment=staging"`
- `-var="domain=spepe.seu-dominio.com"`

Guia completo: [`GCP_SETUP.md`](GCP_SETUP.md)

---

## CI/CD

| Workflow | Trigger | O que faz |
|----------|---------|-----------|
| `ci.yml` | PR / push main | Lint (ruff), testes, eval LLM, secret scan |
| `deploy.yml` | Push main / manual | Build → Artifact Registry → staging → prod |
| `canary_deploy.yml` | Manual | Canary 10% → avalia → promove ou rollback |
| `ml_pipeline.yml` | Schedule / manual | Compila e submete Vertex AI pipeline |
| `security.yml` | Schedule | Scan de vulnerabilidades |

Autenticação GCP via **Workload Identity Federation** — sem chaves SA em secrets.

GitHub Actions vars necessárias: `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`, `GCP_PROJECT_ID`

---

## Documentação

| Arquivo | Conteúdo |
|---------|----------|
| [`CLAUDE.md`](CLAUDE.md) | Guia técnico completo para desenvolvimento |
| [`GCP_SETUP.md`](GCP_SETUP.md) | Passo a passo de configuração GCP produção |
| [`CREDENTIALS_CHECKLIST.md`](CREDENTIALS_CHECKLIST.md) | Checklist de segurança de credenciais |
| [`PIPELINE_SCHEDULING.md`](PIPELINE_SCHEDULING.md) | Agendamento e atualização de dados |
| [`CHANGELOG.md`](CHANGELOG.md) | Histórico de versões |

---

## Segurança

- Dados eleitorais TSE e indicadores IBGE são **dados públicos** — sem PII de eleitores
- `hooks/dlp_hook.py` mascara CPF, CNPJ e telefone em toda saída dos agentes
- Secrets exclusivamente via GCP Secret Manager em produção (nunca em `.env`)
- **Nunca commite `.env`** — está no `.gitignore`
