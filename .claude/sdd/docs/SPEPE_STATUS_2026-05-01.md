# SPEPE — Status Técnico e Roadmap de Conclusão
**Data:** 2026-05-01 | **Versão analisada:** v1.0.1

---

## 1. Estado Atual — 10 Módulos + UI

### Resumo Executivo

| # | Módulo | % | Bloqueador principal |
|---|--------|---|----------------------|
| 1 | Eleições TSE | 75% | 27 UFs não ingeridas |
| 2 | IBGE / Dados Públicos | 65% | Join TSE↔IBGE frágil; Silver BQ parcial |
| 3 | Pesquisas Eleitorais | 60% | Silver/Gold ausentes; job fora do CI/CD |
| 4 | Redes Sociais | 40% | Sentimento stub; YouTube/TikTok ausentes; sem Silver |
| 5 | Segurança Pública | 55% | Sem Silver/Gold; job fora do CI/CD |
| 6 | Saúde (DataSUS) | 35% | Formato DBC não implementado; `PySUS` ausente |
| 7 | Economia (DIEESE/CETIC) | 45% | Sem Silver/Gold; jobs fora do CI/CD |
| 8 | MLOps / Predição | 60% | Pipeline só compilado; dados Gold insuficientes |
| 9 | Agentes / UI | 70% | 3 agentes novos não roteados; `USE_BIGQUERY` não setado |
| 10 | Infra / Segurança | 80% | 6 jobs fora do deploy.yml; Secrets não criados no SM |
| 11 | Dashboard + Admin | 65% | 3 abas placeholder; Admin sem persistência Firestore |

---

## 2. Diagnóstico Detalhado por Módulo

### Módulo 1 — Eleições TSE (75%)

**Pronto:**
- `tse_client.py` — download, descompressão, normalização
- `tse_ingest_job.py` — Cloud Run Job funcional e testado
- `silver_transformer.py` — Bronze → Silver, join TSE+IBGE, DQ gate, BQ ✅
- `gold_builder.py` — Silver → Gold (3 fact tables), BQ ✅ smoke test AC 2022 passou
- `depara_municipios.py` — join TSE ↔ IBGE por aproximação

**Pendências:**
- Apenas AC 2022 ingerida — 26 UFs restantes
- `fact_municipio_eleicao` produz ~17 colunas vs ~200 features especificadas (faltam IBGE enriched)
- `fact_ibge_municipio` mencionado no CLAUDE.md mas não existe no `gold_builder.py`
- Join IBGE tem 0% match em algumas UFs (depara por `ibge_code // 10` é aproximação)

---

### Módulo 2 — IBGE / Dados Públicos (65%)

**Pronto:**
- `ibge_client.py` — SIDRA API + Localidades API
- `ibge_sync_job.py` — Cloud Run Job

**Pendências:**
- Silver IBGE no BQ ainda vazia (só TSE verificado)
- `dim_territorio` Terraform table não populada
- Join TSE↔IBGE frágil para UFs sem correspondência direta no depara

---

### Módulo 3 — Pesquisas Eleitorais (60%)

**Pronto:**
- `polls_client.py` — TSE PesqEle CSV + PDF pipeline (pdfplumber + LLM fallback) + Atlas Político + house_effect (17 institutos calibrados)
- `pesquisas_ingest_job.py` — Cloud Run Job completo
- `poll_aggregator.py` — agregação com house effect + IC 95%

**Pendências:**
- `spepe-pesquisas-ingest` **não está** no loop de update do `deploy.yml` (só 5 jobs originais)
- Nenhuma transformação Silver para dados de pesquisa (`silver_transformer.py` só processa TSE)
- `fact_pesquisa` Gold retorna DataFrame vazio (sem Bronze pesquisas em produção)
- Sem schedule periódico (deveria rodar semanalmente durante 2026)

---

### Módulo 4 — Redes Sociais (40%)

**Pronto:**
- `social_client.py` — Twitter/X API v2 (paginação, rate limit) + Facebook Graph API
- `digital_client.py` — Google Trends (pytrends) + Meta Ads Library
- `social_ingest_job.py` — Cloud Run Job com candidatos 2026 configurados
- `semantic_layer.py` — views SQL para sentimento (`vw_sentimento_candidato`) escritas

**Pendências:**
- Sentimento é **rule-based** (palavras-chave) — Vertex AI NLP não implementado (Fase 2)
- **YouTube e TikTok ausentes** em `social_client.py` (só X e Facebook)
- `spepe-social-ingest` **não está** no `deploy.yml`
- Tabela Silver `social_mencoes_br` **não existe no Terraform** (views referenciam ela)
- Nenhum agente conectado a dados sociais ao vivo

---

### Módulo 5 — Segurança Pública (55%)

**Pronto:**
- `seguranca_client.py` — IVS (IPEADATA OData) + Atlas da Violência (CSV IPEA) + SINESP
- `security_ingest_job.py` — Cloud Run Job completo
- `analista_seguranca` agent no registry
- `fact_seguranca_municipio` table no Terraform

**Pendências:**
- `spepe-security-ingest` **não está** no `deploy.yml`
- Silver transformation para segurança não implementada
- Gold builder não consome dados de segurança
- Aba "Segurança" no dashboard é placeholder

---

### Módulo 6 — Saúde / DataSUS (35%)

**Pronto:**
- `datasus_client.py` — estrutura IPEADATA fallback (2 séries: mortalidade infantil + materna) + ANS beneficiários
- `datasus_ingest_job.py` — Cloud Run Job
- `fact_saude_municipio` table no Terraform

**Pendências:**
- DataSUS FTP real usa formato **DBC (binário proprietário)** — precisa `PySUS` ou `datasus-python` **não está em `requirements.txt`**
- `datasus_client.py` só usa IPEADATA como fallback, não dados reais TABWIN/FTP
- Sem Silver/Gold para saúde
- `spepe-datasus-ingest` **não está** no `deploy.yml`
- Aba "Saúde" no dashboard é placeholder

---

### Módulo 7 — Economia (DIEESE / CETIC) (45%)

**Pronto:**
- `dieese_client.py` — cesta básica DIEESE + fallback IPEADATA por capital de UF
- `dieese_ingest_job.py` — Cloud Run Job
- `cetic_client.py` — TIC Domicílios (acesso digital por município)
- `cetic_ingest_job.py` — Cloud Run Job
- `fact_economico_municipio` table no Terraform

**Pendências:**
- `spepe-dieese-ingest` e `spepe-cetic-ingest` **não estão** no `deploy.yml`
- Silver/Gold para indicadores econômicos não implementados
- URL DIEESE (`processamentoRemuneracaoRetornoCSV.do`) é endpoint legado — validar disponibilidade

---

### Módulo 8 — MLOps / Predição (60%)

**Pronto:**
- `pymc_model.py` — modelo hierárquico logístico real (PyMC 5)
- `shap_explainer.py` — SHAP com TreeExplainer
- `vertex_pipeline.py` — KFP 2.x compilado (`output/ml_pipeline.yaml`)
- `poll_aggregator.py` — house effect + IC 95%
- `drift_monitor.py`, `bias_monitor.py` — JS divergence + bias por UF/quintil
- `canary_manager.py`, `auto_rollback.py` — Brier score gate
- `eval/eval_runner.py` — LLM eval gate (score 0.995, 82 testes passando)

**Pendências:**
- Pipeline Vertex AI não submetido (só compilado localmente)
- Dados de treinamento insuficientes (só AC 2022 — precisa 27 UFs × múltiplos cargos)
- `spepe_mlops` BQ dataset existe mas vazio (sem `model_evaluations`, `fact_predictions`)
- `prediction_store.py` não testado em produção

---

### Módulo 9 — Agentes / UI (70%)

**Pronto:**
- `supervisor.py` — DOMA loop, budget $2/sessão, routing, `run_dataops_job`
- `gemini_agent.py` — Vertex AI wrapper com streaming
- `loader.py` — carrega registry/*.md automaticamente
- **10 agentes** no registry: 7 originais + `analista_seguranca`, `sentinela_social`, `contextualizador_saude`
- `dashboard_api.py` (3.840 linhas) — FastAPI completo com BQ + mock fallback

**Pendências:**
- 3 novos agentes (`analista_seguranca`, `sentinela_social`, `contextualizador_saude`) estão no registry mas **não aparecem no `_SYSTEM` prompt do supervisor** → não são roteados
- `chainlit_app.py` ausente de `ui/` (CLAUDE.md referencia, mas entry point real é `dashboard_api.py`)
- `USE_BIGQUERY=true` **não está configurado** como env var no Cloud Run service → dashboard exibe mock mesmo com dados no BQ

---

### Módulo 10 — Infraestrutura / Segurança (80%)

**Pronto:**
- 22 arquivos Terraform (IAM, WIF, IAP, Cloud Run, BQ, GCS, Pub/Sub, Firestore, Secrets, etc.)
- 5 workflows CI/CD (ci, deploy, security, ml_pipeline, canary_deploy)
- 8 hooks: DLP, cost_guard, rate_limit, security_hook, audit_hook, disclaimer_hook, output_compressor, context_budget

**Pendências:**
- `deploy.yml` atualiza apenas **5 de 11 Cloud Run Jobs** — faltam: `spepe-social-ingest`, `spepe-pesquisas-ingest`, `spepe-security-ingest`, `spepe-datasus-ingest`, `spepe-dieese-ingest`, `spepe-cetic-ingest`
- Secrets não criados no Secret Manager (ANTHROPIC_API_KEY, META_APP_TOKEN, YOUTUBE_API_KEY)
- IAP não provisionado (listado como pendente no CLAUDE.md)

---

### UI — Dashboard + Admin (65%)

**spepe-app.html (1.694 linhas) — 9 abas:**

| Aba | Estado |
|-----|--------|
| Dashboard (KPIs + Chart.js) | ✅ Funcional, mock→BQ automático |
| Mapa (Leaflet choropleth 6 níveis) | ✅ BQ queries completas, mock fallback |
| Socioeconômico | 🔴 Placeholder |
| Segurança | 🔴 Placeholder |
| Saúde | 🔴 Placeholder |
| Pesquisas | ⚠️ Parcial |
| Predição | ⚠️ Parcial |
| Perfis Eleitorais | ⚠️ Parcial |
| Relatórios | ⚠️ Parcial |

**admin.html (1.013 linhas) — 4 seções:**
- Sentinel (WebSocket `/ws/sentinel`) ✅
- Gestão de Usuários (CRUD `/admin/api/users`) ⚠️ em memória
- Controle de Acesso ⚠️ em memória
- Jobs / Operações (dispara Cloud Run Jobs via API) ⚠️ sem histórico

**index.html (1.200 linhas)** — Landing page estática, ~90% completa.

---

## 3. Sequência Lógica de Conclusão

> **Princípio:** Resolver infraestrutura primeiro → depois dados → depois análise → depois UI.
> Cada fase desbloqueia a próxima. Não adianta construir análise sem dados.

---

### FASE 0 — Desbloqueios Imediatos (1-2 dias)
> Sem custo de desenvolvimento, máximo impacto.

| Tarefa | Arquivo | Por quê |
|--------|---------|---------|
| 0.1 Adicionar 6 jobs faltantes ao `deploy.yml` | `.github/workflows/deploy.yml` | Jobs novos correm imagem stale a cada deploy |
| 0.2 Setar `USE_BIGQUERY=true` no Cloud Run service | `infra/terraform/cloud_run.tf` | Dashboard mostra mock mesmo com Gold populado |
| 0.3 Criar Secrets no Secret Manager | GCP console / Terraform | ANTHROPIC_API_KEY, META_APP_TOKEN, YOUTUBE_API_KEY |
| 0.4 Adicionar 3 agentes ao `_SYSTEM` do supervisor | `agents/supervisor.py` | `analista_seguranca`, `sentinela_social`, `contextualizador_saude` invisíveis |
| 0.5 Persistência Firestore no Admin | `ui/dashboard_api.py` | Usuários/permissões resetam ao reiniciar container |

---

### FASE 1 — Pipeline de Dados Completo (1-2 semanas)
> Pré-requisito para TUDO: agentes, MLOps, dashboard com dados reais.

| Tarefa | Módulos afetados | Prioridade |
|--------|-----------------|------------|
| 1.1 Ingerir 27 UFs TSE 2022 + IBGE | 1, 2 | 🔴 Crítico |
| 1.2 Corrigir join TSE↔IBGE (depara robusto) | 1, 2 | 🔴 Crítico |
| 1.3 Adicionar `PySUS` em `requirements.txt` | 6 | 🔴 Bloqueador |
| 1.4 Estender `silver_transformer.py` para Pesquisas | 3 | 🟠 Alta |
| 1.5 Estender `silver_transformer.py` para Segurança | 5 | 🟠 Alta |
| 1.6 Estender `silver_transformer.py` para Saúde | 6 | 🟠 Alta |
| 1.7 Estender `silver_transformer.py` para Social | 4 | 🟡 Média |
| 1.8 Criar tabela `social_mencoes_br` no Terraform | 4 | 🟡 Média |
| 1.9 Estender `gold_builder.py` para todos os domínios | 3, 5, 6, 7 | 🟠 Alta |
| 1.10 Validar `fact_ibge_municipio` ou criar no gold_builder | 1, 2 | 🟡 Média |

---

### FASE 2 — Módulos de Dados Individuais (2-4 semanas)
> Completar cada fonte de dados end-to-end (Bronze → Silver → Gold).

**Pesquisas (Módulo 3) — ~1 semana:**
- Rodar `spepe-pesquisas-ingest` em produção para ano 2026
- Silver transform: normalizar schema TSE PesqEle + Atlas
- Gold: popular `fact_pesquisa` com house_effect aplicado
- Adicionar schedule semanal (Pub/Sub trigger ou Cloud Scheduler)

**Segurança (Módulo 5) — ~3 dias:**
- Rodar `spepe-security-ingest` para 27 UFs
- Silver transform: IVS + Atlas Violência + SINESP
- Gold: popular `fact_seguranca_municipio`

**Saúde / DataSUS (Módulo 6) — ~1 semana:**
- Adicionar `PySUS==0.3.x` em `requirements.txt`
- Reescrever `datasus_client.py` para usar FTP real (SIM/SINASC DBF/DBC)
- Silver + Gold: `fact_saude_municipio`

**Economia (Módulo 7) — ~3 dias:**
- Validar endpoints DIEESE e CETIC
- Silver + Gold: `fact_economico_municipio`

**Redes Sociais (Módulo 4) — ~1 semana:**
- Adicionar YouTube Data API v3 ao `social_client.py`
- Silver table `social_mencoes_br` criada no BQ
- Sentimento NLP básico via Vertex AI `text-multilingual-v1` (substituir rule-based)
- TikTok (Fase 2+, API restrita)

---

### FASE 3 — UI com Dados Reais (1 semana)
> Dashboard passa de demo para produção.

| Tarefa | Aba/Componente |
|--------|----------------|
| 3.1 Popular aba Socioeconômico | Buscar `fact_economico_municipio` + IBGE |
| 3.2 Popular aba Segurança | Buscar `fact_seguranca_municipio` + correlação voto |
| 3.3 Popular aba Saúde | Buscar `fact_saude_municipio` |
| 3.4 Popular aba Pesquisas | Buscar `fact_pesquisa` + house effect chart |
| 3.5 Popular aba Predição | Conectar ao `prediction_store` pós-MLOps |
| 3.6 Admin: persistência Firestore | Salvar users/access em `spepe_sessions` |

---

### FASE 4 — MLOps em Produção (2-3 semanas)
> Só faz sentido após Fase 1 completa (dados Gold suficientes).

| Tarefa | Arquivo |
|--------|---------|
| 4.1 Submeter pipeline Vertex AI com dados Gold completos | `mlops/vertex_pipeline.py` |
| 4.2 Popular `spepe_mlops.model_evaluations` | `mlops/eval/eval_runner.py` |
| 4.3 Popular `spepe_mlops.fact_predictions` | `mlops/prediction_store.py` |
| 4.4 Testar `drift_monitor.py` com dados reais | `mlops/monitoring/drift_monitor.py` |
| 4.5 Testar canary deployment 10% | `mlops/deployment/canary_manager.py` |
| 4.6 Conectar aba Predição ao prediction_store | `ui/dashboard_api.py` |

---

### FASE 5 — Hardening e Produção (1 semana)
> Pré-requisito para promoção ao `spepe-prod`.

| Tarefa |
|--------|
| 5.1 Provisionar IAP via Terraform |
| 5.2 Validar Brier score < 0.20, drift < 0.10, LLM eval > 0.85 |
| 5.3 Configurar Cloud Scheduler para jobs periódicos (TSE snapshot anual, pesquisas semanal, social diário) |
| 5.4 Testar todos os 11 Cloud Run Jobs end-to-end para todas as 27 UFs |
| 5.5 Tag v2.0.0 → terraform apply spepe-prod |

---

## 4. Prioridades por Urgência

### 🔴 Crítico — Resolver esta semana

1. `deploy.yml` — adicionar 6 jobs ao loop de update
2. `USE_BIGQUERY=true` no Cloud Run service
3. Ingerir 27 UFs TSE 2022 (pré-requisito para tudo)
4. `PySUS` em `requirements.txt` (desbloqueia DataSUS)
5. 3 agentes novos no supervisor routing

### 🟠 Alta — Resolver nas próximas 2 semanas

6. `silver_transformer.py` — extender para Pesquisas, Segurança, Saúde
7. `gold_builder.py` — extender para todos os domínios
8. Rodar jobs de ingestão Pesquisas + Segurança em produção
9. Secrets no Secret Manager
10. Corrigir join TSE↔IBGE (depara robusto)

### 🟡 Média — Completar no mês

11. DataSUS FTP real (reescrever `datasus_client.py` com PySUS)
12. Silver/Gold para economia (DIEESE/CETIC)
13. YouTube na `social_client.py`
14. NLP sentimento via Vertex AI
15. Abas UI Socioeconômico/Segurança/Saúde com dados reais
16. Admin persistência Firestore

### 🟢 Normal — Pós-v1.5

17. TikTok API
18. Schedule automático dos jobs
19. Submeter pipeline Vertex AI
20. MLOps em produção (prediction_store + fact_predictions)
21. Canary deployment testado
22. IAP provisionado
23. Tag v2.0.0 + terraform apply spepe-prod

---

## 5. Dependências Críticas (Diagrama)

```
27 UFs TSE+IBGE ingeridos
       │
       ├─► Silver transformer estendido (Pesquisas, Seg, Saúde, Social)
       │          │
       │          ├─► Gold builder estendido (todos os domínios)
       │          │          │
       │          │          ├─► Dashboard UI com dados reais (Fases 3+)
       │          │          ├─► MLOps treino (dados suficientes)
       │          │          └─► Agentes com contexto real
       │          │
       │          └─► fact_pesquisa populado
       │                     │
       │                     └─► poll_aggregator → Predição
       │
       └─► Fase 5: Hardening → spepe-prod
```

---

## 6. Referências de Código

| Arquivo | Linha chave | Nota |
|---------|-------------|------|
| `.github/workflows/deploy.yml` | "Update Cloud Run Jobs" step | Adicionar 6 jobs |
| `agents/supervisor.py` | `_SYSTEM` string (~L36) | Adicionar 3 agentes |
| `dataops/silver_transformer.py` | `transform_to_silver()` | Estender para novos domínios |
| `dataops/gold_builder.py` | `build_gold()` L45 | Adicionar chamadas para novos domínios |
| `infra/terraform/cloud_run.tf` | env vars do service | Adicionar `USE_BIGQUERY=true` |
| `requirements.txt` | após google-cloud-* | Adicionar `PySUS>=0.3` |
