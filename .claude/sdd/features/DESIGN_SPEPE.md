# DESIGN: SPEPE — Sistema de Perfilamento do Eleitorado e Previsão Eleitoral

> Arquitetura técnica completa v4.2: 7 agentes analíticos + **Sentinel (orquestrador de monitoramento autônomo com 4 crews)**, pipeline Medallion Bronze→Silver→Gold com 9 fontes de dados, **feature matrix integrada com 4 dimensões estruturais** (histórico TSE + IBGE + fato_social + fact_pesquisa), modelo bayesiano único com IC 95%, SHAP explainability, chave global `cod_municipio_ibge`, Governança + Data Contracts + RBAC + Lineage, deploy GCP southamerica-east1 com MLOps Nível 5 + DataOps/LLMOps e compliance LGPD. **LLM = suporte analítico, nunca etapa primária de processamento.**

## Princípio Arquitetural — REGRA FINAL

| Camada | Papel |
|--------|-------|
| Dados estruturais (IBGE) | **Explicam** |
| Histórico eleitoral (TSE) | **Validam** |
| Pesquisas (fact_pesquisa) | **Calibram** |
| Sinal digital (fato_social) | **Antecipam** |
| Modelo bayesiano | **Integra** |

> Janela temporal: **2018 = baseline** · **2022 = referência recente** · **2026 = alvo de previsão**
> 2014 = contexto histórico auxiliar (não entra na feature matrix principal)

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | SPEPE |
| **Date** | 2026-04-23 |
| **Author** | design-agent |
| **DEFINE** | [DEFINE_SPEPE.md](./DEFINE_SPEPE.md) |
| **Status** | Ready for Build |
| **Version** | 4.2 — Sentinel: orquestrador multi-agent autônomo (4 crews), Governança, Data Contracts, RBAC, Lineage coluna, SLOs |

---

## Architecture Overview

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              SPEPE — Arquitetura Completa v2.0                           │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                           │
│  CAMADA DE APRESENTAÇÃO                                                                   │
│  ┌──────────────┐  /coletar /arquétipos /perfil /prever /explicar /relatorio /health     │
│  │   Usuário    │────────────────────────────────────────────────────────────────────►   │
│  └──────────────┘                                                                  │     │
│          ▲                                                                          ▼     │
│          │                                              ┌────────────────────────────┐   │
│          │                                              │       Supervisor            │   │
│          │                                              │   claude-opus-4-6           │   │
│          │                                              │   DOMA protocol, budget,    │   │
│          │                                              │   routing, validation        │   │
│          │                                              └───────────┬────────────────┘   │
│          │                        delega por slash command          │                    │
│          │         ┌──────────────┬───────────────┬────────────────┼──────────┬─────┐  │
│          │         ▼              ▼               ▼                ▼          ▼     ▼  │
│          │  ┌─────────────┐ ┌──────────┐ ┌─────────────┐ ┌──────────┐ ┌────────┐ ┌──┐│
│          │  │   Coletor   │ │Perfilador│ │  Analista   │ │Modelista │ │Narrador│ │Ex ││
│          │  │ /coletar    │ │/arquétipos│ │  /perfil    │ │ /prever  │ │/relator│ │pl ││
│          │  │ sonnet-4-6  │ │sonnet-4-6│ │ sonnet-4-6  │ │sonnet-4-6│ │haiku   │ │ic ││
│          │  └──────┬──────┘ └────┬─────┘ └──────┬──────┘ └─────┬────┘ └────────┘ └──┘│
│                    │             │               │              │                       │
│  CAMADA MCP        │             │               │              │                       │
│  ┌─────────────────┼─────────────┼───────────────┼──────────────┼──────────────────┐   │
│  │  ┌──────────┐ ┌─┴───────┐ ┌──┴───────┐ ┌─────┴──────┐ ┌────┴────┐             │   │
│  │  │  TSE MCP │ │IBGE MCP │ │Digital   │ │ Vertex MCP │ │Stats MCP│             │   │
│  │  │ CSV/Parq.│ │SIDRA API│ │Signal MCP│ │FeatureStore│ │SHAP/PyMC│             │   │
│  │  └────┬─────┘ └────┬────┘ └────┬─────┘ └─────┬──────┘ └─────────┘             │   │
│  └───────┼────────────┼───────────┼──────────────┼────────────────────────────────┘   │
│                        │                          │                                      │
│  CAMADA DE DADOS — MEDALLION                      │                                      │
│  ┌────────────────────────────────────────────────┼──────────────────────────────────┐  │
│  │  BRONZE (GCS raw/)          SILVER (BQ spepe_silver)      GOLD (BQ spepe_gold)   │  │
│  │  ┌─────────────────┐    ┌──────────────────┐    ┌───────────────────────────────┐│  │
│  │  │ tse_2014/       │    │ municipios_clean  │    │ fact_municipio_eleicao        ││  │
│  │  │ tse_2018/       │───►│ candidatos_clean  │───►│  ~200 features × 5570 munic. ││  │
│  │  │ tse_2022/       │    │ ibge_indicadores  │    │  × 2 eleições (2018/2022)    ││  │
│  │  │ ibge_censo/     │    │ social_signal     │    │ fato_social                  ││  │
│  │  │ ibge_sidra/     │    │ pesquisas_clean   │    │  município×semana LGPD-safe  ││  │
│  │  │ digital/        │    │ (GE validated,    │    │ fact_pesquisa (central)      ││  │
│  │  │ pesquisas/      │    │  DQ score ≥ 95%)  │    │  + record_confidence_score   ││  │
│  │  └─────────────────┘    └──────────────────┘    └───────────────────────────────┘│  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                           │
│  CAMADA ML — 3 ESTÁGIOS                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│  │  FEATURE MATRIX INTEGRADA (4 dimensões → modelo único)                             │ │
│  │  ┌─────────────────┐ ┌──────────────┐ ┌──────────────────┐ ┌────────────────────┐ │ │
│  │  │ Histórico (TSE) │ │ IBGE         │ │ fato_social      │ │ fact_pesquisa      │ │ │
│  │  │ 2018 + 2022     │ │ estrutural   │ │ município×semana │ │ record_conf_score  │ │ │
│  │  │ ~200 features   │ │ (contexto)   │ │ NLP + trends     │ │ house effect adj.  │ │ │
│  │  └────────┬────────┘ └──────┬───────┘ └────────┬─────────┘ └─────────┬──────────┘ │ │
│  │           └─────────────────┴──────────────────┴──────────────────────┘           │ │
│  │                                        ▼                                           │ │
│  │  ┌────────────────────┐               ┌──────────────────────────────────────────┐ │ │
│  │  │ STAGE 1: Arquétipos│               │ STAGE 2: Bayesiano + SHAP               │ │ │
│  │  │ → StandardScaler   │               │ Bootstrap IC 95% (statsmodels)          │ │ │
│  │  │ → PCA 50 comp.     │               │ PyMC HLM (produção)                     │ │ │
│  │  │ → HDBSCAN/K-means  │               │ poll aggregation + house eff.           │ │ │
│  │  │ → UMAP 2D          │               │ SHAP top-10 values                      │ │ │
│  │  │ → LLM label (supt) │               │ P(X)=N% [IC 95%: A%–B%]                │ │ │
│  │  │ → Folium BR map    │               │                                          │ │ │
│  │  └────────────────────┘               └──────────────────────────────────────────┘ │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                           │
│  CAMADA GCP — southamerica-east1                                                          │
│  Cloud Run │ BigQuery │ GCS │ Vertex AI Pipelines │ Firestore │ Dataplex │ IAP           │
│  Secret Manager │ Cloud Armor │ Cloud DLP │ Cloud Logging │ Looker Studio               │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Components

| Component | Purpose | Technology |
|-----------|---------|------------|
| **Supervisor** | Roteamento DOMA, orçamento, validação de output | claude-opus-4-6, Chainlit |
| **Coletor** | Ingesta 14 fontes, Bronze→Silver, DQ gates | claude-sonnet-4-6, MCP TSE/IBGE |
| **Perfilador** | Clustering HDBSCAN/UMAP, fichas, mapa Folium | claude-sonnet-4-6, scikit-learn |
| **Analista** | Cruzamento socioeconômico × histórico eleitoral | claude-sonnet-4-6 |
| **Modelista** | Bootstrap IC 95%, PyMC HLM, agregação pesquisas | claude-sonnet-4-6, statsmodels |
| **Narrador** | Tradução técnica → leigo, disclaimers automáticos | claude-haiku-4-5-20251001 |
| **Explicador** | SHAP top-10 em linguagem natural | claude-sonnet-4-6 |
| **MCP TSE** | Acesso a CSVs TSE 2014/2018/2022 via Parquet local | Python MCP server |
| **MCP IBGE** | SIDRA API + cache + Censo 2022 CSV | Python MCP server |
| **MCP Digital** | Aggregated social signal + Google Trends | Python MCP server |
| **MCP Vertex** | Feature Store read/write, Model Registry | google-cloud-aiplatform |
| **Bronze Layer** | Raw files imutáveis em GCS | GCS + Parquet |
| **Silver Layer** | Dados limpos e joinados | BigQuery spepe_silver |
| **Gold Layer** | 3 tabelas analíticas (~200 vars) | BigQuery spepe_gold |
| **ML Stage 1** | Arquétipos do eleitorado | HDBSCAN, UMAP, Folium |
| **ML Stage 2** | Sinal digital agregado | LGPD-safe aggregation |
| **ML Stage 3** | Previsão bayesiana + SHAP | statsmodels / PyMC |
| **DataOps** | Orquestração, DQ, linhagem | Cloud Composer / Cloud Scheduler |
| **MLOps** | Treino, avaliação, promoção de modelos | Vertex AI Pipelines (KFP v2) |
| **LLMOps** | Registry de prompts, eval CI, tracing | Git semver + Cloud Trace |
| **Security** | IAM, Secret Manager, IAP, DLP, LGPD | GCP security stack |

---

## Key Decisions

### Decision 1: Clustering Primário — HDBSCAN sobre K-means

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** Municípios brasileiros têm distribuição heterogênea — grandes metrópoles (SP, RJ) coexistem com pequenos municípios rurais. K-means força clusters esféricos de tamanho similar, inadequado para esta distribuição.

**Choice:** HDBSCAN como algoritmo primário, K-means e GMM como alternativas de comparação.

**Rationale:** HDBSCAN detecta clusters de forma arbitrária, lida com ruído (noise points = municípios atípicos), e não exige número de clusters pré-definido. Silhouette score ≥ 0.45 é o gate de aceitação.

**Alternatives Rejected:**
1. K-means puro — requer k fixo, sensível a outliers, clusters esféricos apenas
2. GMM puro — probabilístico mas assume distribuição gaussiana, problemático com muitos zeros (variáveis binárias)

**Consequences:**
- Aceita: HDBSCAN pode gerar noise points que precisam de tratamento especial
- Ganha: Número de arquétipos emergente dos dados (6–12), mais interpretável sociologicamente

---

### Decision 2: Modelo Bayesiano — statsmodels Bootstrap (MVP) → PyMC HLM (Produção)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** PyMC é o padrão-ouro para modelos hierárquicos bayesianos, mas tem curva de aprendizado, dependências pesadas e tempo de compilação (JAX/Theano). O MVP precisa entregar IC 95% funcional rapidamente.

**Choice:** statsmodels com bootstrap paramétrico para IC no MVP; PyMC NUTS sampler em produção.

**Rationale:** Bootstrap sobre regressão logística statsmodels entrega IC 95% válido, é reproduzível, sem dependências extras. PyMC é adicionado pós-validação do conceito.

**Alternatives Rejected:**
1. PyMC desde o início — tempo de setup alto, bloqueia entrega do MVP
2. IC frequentista simples (±1.96σ) — não captura incerteza de parâmetro, subestima IC em amostras pequenas

**Consequences:**
- Aceita: IC bootstrap pode ser conservador; não é verdadeiramente bayesiano
- Ganha: MVP funcional em dias, não semanas

---

### Decision 3: Orquestração DataOps — Cloud Scheduler + Cloud Run (MVP) → Cloud Composer (Produção)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** Cloud Composer (Airflow managed) custa $50–100/mês minimum. Para dados históricos estáticos (TSE eleições passadas), DAGs complexos são overkill no MVP.

**Choice:** Cloud Scheduler + Cloud Run jobs para orquestração no MVP. Cloud Composer quando o volume de DAGs justificar.

**Rationale:** TSE histórico é estático — coletor roda uma vez por eleição, não diariamente. Cloud Scheduler + HTTP trigger é suficiente. Economiza $600–1200/ano.

**Alternatives Rejected:**
1. Cloud Composer imediato — custo alto não justificado para dados estáticos
2. Cron local — não escala, sem retry automático, sem logging centralizado

**Consequences:**
- Aceita: Sem UI visual de DAGs no MVP (Cloud Composer tem UI Airflow)
- Ganha: MVP 80% mais barato em infraestrutura

---

### Decision 4: Mapa Interativo — Folium sobre Plotly/Altair

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** O mapa coroplético do Brasil com 5.570 municípios precisa ser interativo (hover, zoom, filtro por arquétipo) e exibível no Chainlit/Streamlit.

**Choice:** Folium (Leaflet.js wrapper) para o mapa interativo principal.

**Rationale:** Folium exporta HTML standalone, funciona no Chainlit via iframe, tem suporte nativo a GeoJSON brasileiro (IBGE), e tem melhor performance com 5.570 polígonos do que Plotly.

**Alternatives Rejected:**
1. Plotly Choropleth — performance inferior com muitos polígonos municipais, embedding mais complexo
2. Altair/Vega — sem suporte nativo a tile maps, difícil integrar GeoJSON do IBGE

**Consequences:**
- Aceita: Folium não tem reatividade nativa ao estado Python (é HTML puro)
- Ganha: Mapa funcional, bonito e performático com zero custo adicional

---

### Decision 5: Deploy — Cloud Run sobre GCE/GKE

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** O sistema tem padrão de uso intermitente (sessões analíticas, não tráfego constante). GCE e GKE têm custo fixo mesmo quando idle.

**Choice:** Cloud Run (serverless containers) para Streamlit/Chainlit.

**Rationale:** Scale-to-zero = zero custo quando inativo. Sessões SPEPE têm duração definida (~5–30 min). Auto-scaling para picos. Custo proporcional ao uso real.

**Alternatives Rejected:**
1. GCE e2-medium fixo — $25–30/mês mesmo sem uso
2. GKE Autopilot — overhead de cluster, complexidade desnecessária para 1 serviço

**Consequences:**
- Aceita: Cold start (~5s) na primeira requisição após período idle
- Ganha: Custo praticamente zero no MVP com uso esporádico

---

### Decision 6: Analytics SQL — BigQuery sobre DuckDB mantido

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** O DESIGN v1.0 usava DuckDB local. Para cloud + múltiplos usuários + Vertex AI integration + Dataplex lineage, BigQuery é necessário.

**Choice:** BigQuery como camada analítica principal. DuckDB mantido apenas para desenvolvimento local e testes.

**Rationale:** BigQuery integra nativamente com Vertex AI Feature Store, Dataplex, Looker Studio e IAP. Query Parquet direto do GCS sem schema migration. 1TB grátis/mês cobre o MVP.

**Alternatives Rejected:**
1. Manter DuckDB em produção — não escala, sem integração GCP, sem auditoria nativa
2. Redshift/Synapse — sem integração com Vertex AI, fora do ecossistema GCP

**Consequences:**
- Aceita: Custo de query aumenta com volume após 1TB/mês
- Ganha: Integração nativa com todo o stack GCP, linhagem automática no Dataplex

---

### Decision 7: Drift → Auto-retrain Loop (MLOps Nível 5)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** O drift_config.yaml detecta divergência JS > 0.10, mas não dispara retraining automaticamente — requer intervenção manual. Isso caracteriza Nível 1.5, não Nível 5.

**Choice:** Cloud Pub/Sub topic `spepe-drift-detected` → Eventarc trigger → Cloud Run job `retrain_trigger_job.py` → submete Vertex AI Pipeline automaticamente.

**Rationale:** Loop fechado: drift detectado → modelo retreinado → avaliado → promovido (se melhor) ou descartado. Zero intervenção humana para retraining rotineiro. Humano só é notificado se challenger não superar champion.

**Alternatives Rejected:**
1. Polling manual — latência alta, requer operador disponível
2. Cloud Scheduler periódico — retreina mesmo sem drift, desperdiça compute

**Consequences:**
- Aceita: risco de loop de retraining se o drift for persistente (mitigado por cooldown de 72h)
- Ganha: modelo sempre atualizado, MLOps Nível 5

---

### Decision 8: Canary / Shadow Deployment (MLOps Nível 5)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** Deploy atual substitui 100% do tráfego para o challenger — se o modelo for pior, todos os usuários são afetados antes do rollback.

**Choice:** Cloud Run traffic splitting: 10% → challenger, 90% → champion. Após 48h de monitoramento, `canary_manager.py` promove (100% challenger) ou reverte (100% champion).

**Rationale:** Cloud Run suporta traffic splitting nativo via `gcloud run services update-traffic`. Sem infraestrutura adicional. 10% expõe número suficiente de sessões para avaliação estatística significativa.

**Alternatives Rejected:**
1. Feature flag no código — complexidade adicional, difícil de limpar
2. Blue/Green completo — custo duplo de infrastructure durante o período canary

**Consequences:**
- Aceita: 10% dos usuários recebem modelo experimental durante 48h
- Ganha: rollback automático em < 5 minutos se Brier score degradar

---

### Decision 9: Auto-rollback por Degradação de Métrica (MLOps Nível 5)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** `promote.py` atual só promove — se o challenger degradar em produção (mesmo após backtest), não existe mecanismo automático de reversão.

**Choice:** `auto_rollback.py` monitora Brier score do challenger a cada 6h durante canary. Se Brier score > champion_brier × 1.05 (5% pior), reverte traffic split para 100% champion e notifica via Cloud Alerting.

**Rationale:** Brier score captura calibração probabilística — crítico para previsões eleitorais com IC 95%. Threshold 5% evita rollbacks por ruído estatístico.

**Alternatives Rejected:**
1. Rollback manual — derrota o propósito do Nível 5
2. Accuracy simples — não captura degradação de calibração, apenas de acerto binário

**Consequences:**
- Aceita: rollback gera notificação que requer análise humana (causa do drift)
- Ganha: zero impacto a usuários se challenger for pior

---

### Decision 10: Hyperparameter Tuning Automatizado (MLOps Nível 5)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** `ml_config.yaml` tem hiperparâmetros fixos (HDBSCAN min_cluster_size=30, n_bootstrap=1000). Podem não ser ótimos para dados de diferentes eleições ou escopos geográficos.

**Choice:** Vertex AI `HyperparameterTuningJob` como etapa anterior ao `train_bootstrap_component` no KFP pipeline. Otimiza: `min_cluster_size` (20–50), `n_bootstrap` (500–2000), `silhouette_threshold` (0.35–0.55).

**Rationale:** Vertex AI HyperparameterTuningJob integra nativamente com KFP v2. Grid search paralelo com até 10 trials simultaneamente. Custo pago apenas quando o pipeline roda (não é contínuo).

**Alternatives Rejected:**
1. Optuna local — não integra com Vertex, sem distributed tuning
2. Manual tuning — não escala, subjetivo, não reproduzível

**Consequences:**
- Aceita: +15–30min ao pipeline de retraining por HP tuning
- Ganha: modelo ótimo para cada janela de dados, não fixo em defaults de MVP

---

### Decision 11: Prediction Store + Deferred Evaluation (MLOps Nível 5)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** Predições atuais não são persistidas — impossível calcular accuracy real quando o resultado eleitoral acontecer. Sem ground truth histórico, não há como melhorar o modelo sistematicamente.

**Choice:** `mlops/prediction_store.py` grava toda predição em `spepe_mlops.fact_predictions` (BigQuery) com: session_id, candidato, uf, prediction_date, p_mean, p_lower, p_upper, model_version, features_hash. Quando resultado chegar, job `evaluate_deferred_job.py` cruza predições × resultados e atualiza `spepe_mlops.model_evaluations`.

**Rationale:** Eleições brasileiras têm ciclo de 4 anos — sem prediction store, cada ciclo começa do zero. Store permite análise retrospectiva completa e retreinamento com ground truth real.

**Alternatives Rejected:**
1. Logs apenas — sem schema, difícil de queryar, sem joins com resultados
2. Arquivo local — não persiste entre sessões, sem auditoria

**Consequences:**
- Aceita: todo output do Modelista gera um registro em BigQuery (overhead de ~50ms por predição)
- Ganha: flywheel de melhoria: predição → ground truth → retrain → predição melhor

---

### Decision 12: Bias / Fairness Monitoring por Grupo (MLOps Nível 5)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-18 |

**Context:** Drift geral (JS divergence) não detecta degradação diferencial — o modelo pode manter accuracy agregada mas piorar sistematicamente para municípios pequenos, rurais ou do Norte/Nordeste.

**Choice:** `bias_monitor.py` calcula métricas separadas por: (a) `sg_uf` (27 grupos), (b) quintil de `renda_media_domiciliar`, (c) `pct_zona_rural` > 50%. Se qualquer grupo tiver Brier score > média_global × 1.15, dispara alerta separado no Cloud Alerting.

**Rationale:** Modelos eleitorais com viés geográfico ou socioeconômico são problemáticos tanto cientificamente quanto politicamente. Threshold 15% captura viés real sem falsos positivos por tamanho amostral pequeno.

**Alternatives Rejected:**
1. Monitorar só aggregate — mascara degradação diferencial, eticamente problemático
2. Fairness por indivíduo — viola LGPD (dados individuais não disponíveis no sistema)

**Consequences:**
- Aceita: 27 grupos × 3 segmentações = 81 métricas por run (aceitável para BigQuery)
- Ganha: compliance com princípios de fairness em ML eleitoral, detecção precoce de viés regional

---

### Decision 13: LLM = Suporte Analítico — Nunca Etapa Primária de Processamento

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** A tentação em sistemas multi-agente é usar LLM para tudo — incluindo parsing de PDF, extração de tabelas, normalização de dados. Isso cria dependência de latência e custo não-determinístico no pipeline crítico de dados.

**Choice:** LLM (Claude/Gemini) é suporte analítico — interpretação, labeling sociológico, narrativa. O pipeline crítico (Bronze→Silver→Gold) não depende de LLM. PDF parsing: pdfplumber/camelot como parser primário; LLM como fallback somente se `parser_fail_rate > 30%`.

**Rationale:** Parser tradicional é determinístico, zero custo por chamada, latência < 100ms. LLM tem latência 1–5s e custo por token. Pipeline de dados deve ser reproduzível e auditável sem dependência de API externa.

**Alternatives Rejected:**
1. LLM para todo parsing PDF — custo $0.01–0.05 por PDF × milhares de PDFs = inviável
2. Nenhum fallback LLM — falha silenciosa em PDFs mal-formatados sem recuperação

**Consequences:**
- Aceita: PDFs onde parser falha em > 30% das células vão para fila LLM (latência maior)
- Ganha: pipeline crítico determinístico, custo previsível, auditável, sem dependência de API externa

---

### Decision 14: RBAC — Três Papéis Funcionais (não só IAM)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** IAP autentica usuários (quem pode entrar), mas não controla o que cada usuário pode fazer dentro do sistema. Um analista regional não deve ver dados de outras UFs; `house_effect_adj` por instituto é dado sensível que só admins devem acessar.

**Choice:** Três papéis funcionais mapeados a grupos Google Workspace:
- `spepe.viewer` — lê dashboards e outputs; sem acesso a dados brutos
- `spepe.analyst` — executa slash commands; acesso Gold com filtro de UF atribuída; sem column `house_effect_adj`
- `spepe.admin` — acesso completo; pode executar jobs DataOps e MLOps

**Rationale:** Granularidade mínima viável — qualquer modelo mais granular exige middleware custoso. Column-level security no BigQuery é nativo (IAM conditions + column-level ACL). Row-level security por UF via BigQuery Row Access Policies.

**Alternatives Rejected:**
1. IAM puro (sem papéis funcionais) — não captura restrição de UF nem de coluna
2. RBAC por recurso individual — ingerenciável com 133+ arquivos e 3 datasets

**Consequences:**
- Aceita: analistas precisam de UF atribuída antes do primeiro uso (onboarding step)
- Ganha: compliance LGPD, auditoria de acesso por papel, column-level security sem middleware

---

### Decision 15: Data Contracts como YAML (ODCS-inspired)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** Sem contratos formais, o produtor (job DataOps) e o consumidor (Modelista, Dashboard) têm expectativas implícitas sobre schema, freshness e completude. Qualquer mudança de schema quebra silenciosamente o consumidor.

**Choice:** Data contracts em YAML por transição de camada (Bronze→Silver, Silver→Gold, Gold→Modelo, Gold→API). Formato inspirado em ODCS (Open Data Contract Standard). Validados automaticamente no CI e no runtime por `contract_validator.py`.

**Rationale:** YAML é legível por humanos e máquinas. Validação no CI garante que produtor não quebra contrato sem revisão. Runtime validator rejeita dados que não cumprem o contrato antes de entrar na camada seguinte.

**Alternatives Rejected:**
1. Great Expectations como contrato — é ferramenta de DQ, não de contrato produtor/consumidor; sem versionamento semântico
2. Proto/Avro schema registry — overhead de infra não justificado para o volume do SPEPE

**Consequences:**
- Aceita: todo schema change requer atualização do contrato (PR obrigatório)
- Ganha: consumidores dependem do contrato, não do schema físico — desacoplamento real

---

### Decision 16: Sentinel — Orquestrador Autônomo de Monitoramento (Crew Architecture)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** O SPEPE tem 5+ pipelines DataOps, MLOps Nível 5, 7 agentes analíticos, contratos de dados, SLOs e RBAC. Cada componente já emite alertas individualmente — mas ninguém correlaciona os sinais. Um drift no modelo pode ter causa em DQ degradada, que tem causa em burst de volume social, que tem causa em evento político. Sem um orquestrador de monitoramento que cruze essas correlações, o operador recebe 5 alertas independentes sem saber a causa raiz.

**Choice:** Sentinel — um orquestrador multi-agent autônomo organizado em 4 crews especializadas, event-driven via Pub/Sub, com KB institucional em Firestore e interpretação via GenAI. Roda como Cloud Run service separado (always-on, low CPU).

**Rationale:** "Sentinel Architecture — A Fleet of AI Agents. One Intelligent Crew." Cada crew tem papel único: detectar → analisar → interpretar → despachar. O Sentinel não substitui o Supervisor (que serve usuários) — é ortogonal: monitora o sistema enquanto o Supervisor serve análises. KB auto-update garante aprendizado contínuo de padrões de incidentes.

**Alternatives Rejected:**
1. Vigilante como único monitor — reativo, sem correlação entre domínios, sem KB, sem crew
2. PagerDuty / Datadog externo — custo, sem integração com contexto eleitoral do SPEPE, sem GenAI interpreter
3. Alertas individuais por componente — operador recebe ruído sem causa raiz correlacionada

**Consequences:**
- Aceita: Sentinel é um serviço adicional (Cloud Run always-on ~$10–20/mês)
- Ganha: correlação automática de causas, sugestões de ação via GenAI, KB que aprende com incidentes, zero alerta órfão

---

### Decision 17: DataOps Nível 5 — CDC Incremental + Self-healing + Data Versioning + Real-time DQ

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** O DataOps atual (v3.0) opera em L3: pipelines automatizados com DQ gates e lineage. L5 exige que o pipeline se auto-repare, carregue incrementalmente (não full-refresh), versione cada snapshot de dado, monitore DQ em tempo real e otimize custos de forma autônoma.

**Choice:** Cinco capacidades adicionais:
1. **CDC incremental** — `incremental_loader.py` detecta novos arquivos Bronze via GCS event trigger (Pub/Sub), carrega apenas o delta, não faz full-refresh
2. **Self-healing** — `pipeline_healer.py` detecta falhas comuns (schema drift, null explosion, arquivo corrompido) e aplica correção automática; `schema_evolver.py` aplica migrações backward-compatible sem intervenção
3. **Data versioning** — `snapshot_manager.py` versiona cada build Gold com BigQuery Table Snapshots (retenção 7 dias); permite rollback de dados, não só de modelo
4. **Real-time DQ** — `realtime_dq.py` avalia DQ em stream (Dataflow/Pub/Sub) em vez de só em batch; alerta em < 60s após anomalia detectada
5. **Cost optimization** — `slot_optimizer.py` emite recomendações de slot reservation e alerta sobre queries sem filtro de partição antes de executar

**Rationale:** Full-refresh em 5570 municípios × 3 eleições custa tempo e compute desnecessários quando só novos dados chegaram. Self-healing elimina plantão humano para falhas rotineiras. Data versioning é o par do Model versioning — sem ele, L5 MLOps não tem ground truth reproduzível.

**Alternatives Rejected:**
1. Manter full-refresh — OK para dados históricos estáticos; inviável quando digital/pesquisas chegam diariamente
2. DVC para data versioning — overhead de configuração; BigQuery Table Snapshots é nativo e zero-config

**Consequences:**
- Aceita: CDC incremental requer campo `updated_at` em todas as fontes; fontes sem timestamp fazem full-refresh como fallback
- Ganha: pipeline DataOps L5 completo — auto-healing, incremental, versionado, DQ em tempo real, custo otimizado

---

### Decision 18: MLOps Nível 5 — Experiment Tracking + Feature Store Online + Shadow Mode + Significância Estatística

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** O MLOps atual (v3.0) opera em L4.5: drift→auto-retrain, canary, auto-rollback, HP tuning, prediction store, bias monitoring. Faltam para L5: rastreamento de experimentos, feature store online (serving em tempo real), shadow mode (challenger roda sem servir, só logando) e teste de significância estatística na comparação champion/challenger.

**Choice:** Quatro capacidades adicionais:
1. **Experiment Tracking** — `experiment_tracker.py` loga cada run (parâmetros, métricas, artifacts) no Vertex AI Experiments; habilita comparação entre runs e reprodutibilidade
2. **Feature Store Online** — `online_server.py` serve features em < 50ms via Vertex AI Feature Store Online (complementa o offline usado no treino); predições em tempo real usam features frescas
3. **Shadow Mode** — `shadow_mode.py` roda challenger em paralelo ao champion sem servir resultado ao usuário; acumula predições em `fact_predictions` com flag `shadow=true`; após 7 dias, comparação estatística auto-triggera promoção ou descarte
4. **Significância Estatística** — `significance_tester.py` usa McNemar test + permutation test para validar se challenger é estatisticamente melhor (p < 0.05), não apenas pontualmente melhor no Brier score

**Rationale:** Experiment tracking é pré-requisito para reprodutibilidade — sem ele, L5 é teatro. Shadow mode elimina o risco do canary (challenger nunca serve usuário real, só aprende). Significância estatística evita promoções falsas por ruído amostral.

**Alternatives Rejected:**
1. MLflow para experiment tracking — não integra com Vertex AI nativo; duplica infraestrutura
2. Canary sem shadow — expõe usuário real ao challenger; shadow é mais conservador e correto para dados eleitorais
3. Comparação só por Brier — Brier é necessário mas não suficiente; precisa de teste de hipótese

**Consequences:**
- Aceita: shadow mode dobra o compute de predição (champion + challenger rodam em paralelo)
- Ganha: MLOps L5 completo — cada experimento é reproduzível, challenger nunca expõe usuário, promoção estatisticamente fundada

---

### Decision 19: LLMOps Nível 5 — Semantic Cache + Eval Contínuo + Prompt A/B + Detecção de Alucinação + Context Management

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** O LLMOps atual (v3.0) opera em L3: prompt registry versionado, LLM eval em CI, Cloud Trace. Faltam para L5: cache semântico (custo), eval contínuo em produção (não só CI), A/B de prompts, detecção de alucinação, gestão de contexto e drift de output.

**Choice:** Seis capacidades adicionais:
1. **Semantic Cache** — `semantic_cache.py` usa Redis (Cloud Memorystore) + embeddings: se cosine similarity > 0.95 entre query atual e query cached, retorna resposta cached sem chamar LLM. Reduz custo em 40–60% para queries repetitivas
2. **Eval Contínuo em Produção** — `continuous_eval.py` amostra 5% dos outputs reais de produção (reservoir sampling), roda métricas de eval automaticamente, alerta se score cair abaixo do gate CI (0.85)
3. **Prompt A/B em Produção** — `prompt_ab_test.py` split 50/50 entre versão atual e nova versão de prompt; após N sessões (poder estatístico > 0.80), promove automaticamente a versão melhor; sem exposição total
4. **Detecção de Alucinação** — `hallucination_detector.py` verifica factualidade de claims numéricos contra Gold (ex: "Lula teve X% em SP" → consulta `fact_municipio_eleicao`); bloqueia output se divergência > 5pp
5. **Context Management** — `context_manager.py` monitora fill rate do contexto; ao atingir 80%, aciona sumarização automática preservando: decisões tomadas, dados críticos, comandos ativos; evita context overflow silencioso
6. **Output Drift Monitor** — `output_drift.py` monitora distribuição de outputs por agente: disclaimer_rate, confidence_score, token_count, response_time; alerta se distribuição desvia > 2σ do baseline

**Rationale:** Sem semantic cache, sessões repetitivas (ex: `/perfil SP 2022` por 100 usuários) custam 100× o necessário. Sem eval contínuo, degeneração de prompts em produção é invisível até que usuário reclame. Detecção de alucinação é crítica para dados eleitorais — claim factual errado é pior que "não sei".

**Alternatives Rejected:**
1. Cache por hash exato — inútil; queries em linguagem natural raramente são idênticas caractere a caractere
2. Eval só em CI — detecta regressões de prompt mas não degeneração de modelo em produção (model updates, context drift)
3. Nenhuma detecção de alucinação — risco reputacional e eleitoral inaceitável para claims numéricos

**Consequences:**
- Aceita: semantic cache adiciona ~10ms de latência por lookup (embedding); hallucination checker adiciona ~200ms (BigQuery query)
- Ganha: LLMOps L5 completo — custo reduzido, qualidade monitorada continuamente, alucinações bloqueadas, contexto gerenciado, prompts otimizados por dados reais

---

### Decision 20: Memória de Longo Prazo dos Agentes — Base Vetorial (Vertex AI Vector Search)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** A memória episódica atual dos agentes usa Firestore (key-value por `session_id`) — memória de curto prazo que se perde entre sessões. Agentes não lembram: análises anteriores de um município, padrões que já foram discutidos, contextos históricos relevantes. Cada sessão começa do zero, forçando o usuário a re-contextualizar.

**Choice:** Vertex AI Vector Search (Matching Engine) como base vetorial de memória de longo prazo. Cada output de agente com valor analítico é embedado e indexado. Na próxima sessão relevante, o sistema recupera os K vizinhos mais próximos semanticamente e injeta no contexto do agente como "memória recuperada".

**Estrutura da memória:**
- `memory_store/session_memory.py` — indexa outputs de agente pós-sessão
- `memory_store/retriever.py` — recupera K vizinhos por similaridade semântica (cosine) antes de cada resposta
- `memory_store/memory_types.py` — classifica memórias: `análise`, `padrão_eleitoral`, `alerta`, `decisão_modelo`, `contexto_político`
- Índice Vertex AI Vector Search: dimensão 768 (Gecko embeddings), ANN com ScaNN

**Rationale:** Municípios eleitorais têm padrões que persistem entre eleições. Uma análise de polarização em SP 2022 é contexto valioso para `/prever SP 2026`. Sem memória vetorial, o agente analisa cada sessão como se fosse a primeira vez. Com memória vetorial, o agente "lembra" e acumula inteligência eleitoral ao longo do tempo.

**Alternatives Rejected:**
1. Firestore apenas — key-value não permite busca semântica; recuperação exige query exata
2. pgvector (Cloud SQL) — não escala para milhares de sessões com busca ANN eficiente
3. Pinecone / Qdrant externo — fora do ecossistema GCP, viola LGPD (dados fora de southamerica-east1)

**Consequences:**
- Aceita: memórias antigas podem ser irrelevantes (mitigado por TTL de 1 ano + score de relevância mínimo 0.75)
- Ganha: agentes acumulam inteligência eleitoral entre sessões — efeito flywheel de conhecimento

---

### Decision 21: ML Judge — Agente Auditor Independente com Parecer Técnico

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** O SPEPE gera previsões eleitorais com IC 95% que são usadas para decisões estratégicas de campanha. Todos os componentes de avaliação (evaluate.py, significance_tester.py, bias_monitor.py) são desenvolvidos e executados pela mesma equipe que treina os modelos — sem independência. Em auditoria eleitoral, isso é inaceitável. Precisa de um agente que avalie os modelos de forma completamente independente, com acesso somente leitura, metodologia diferente e parecer formal estruturado.

**Choice:** ML Judge — um agente auditor independente com:
- **Isolamento total**: acesso somente leitura a `spepe_mlops.*`; sem acesso ao código de treino; sem acesso a `spepe_gold` (para evitar contaminação)
- **Metodologia independente**: implementa seus próprios backtests, usa apenas ground truth de eleições passadas
- **Parecer técnico estruturado**: documento formal com: metodologia, métricas auditadas, achados, limitações, recomendação (Aprovado / Aprovado com ressalvas / Reprovado)
- **Gatilho**: executa automaticamente antes de qualquer promoção champion/challenger; resultado bloqueia ou libera a promoção
- **Modelo separado**: usa Gemini 2.5 Pro via Vertex AI — diferente do Claude usado nos agentes analíticos (independência de modelo)

**Rationale:** Um modelo eleitoral com viés não detectado pode influenciar estratégias de campanha. A independência do auditor não é opcional em sistemas de alto impacto. Usar modelo diferente (Gemini vs. Claude) elimina correlação de erros sistêmicos entre agentes analíticos e auditor.

**Alternatives Rejected:**
1. Mesma equipe auditando — conflito de interesse, não satisfaz critérios de governança de ML eleitoral
2. Auditoria manual periódica — lenta, não escala, não integra ao pipeline de promoção
3. Mesmo modelo LLM (Claude) como auditor — correlação de erros; se Claude tem viés sistemático, auditor e auditado compartilham o mesmo viés

**Consequences:**
- Aceita: ML Judge adiciona 10–20min ao pipeline de promoção (executa backtests independentes)
- Ganha: cada promoção de modelo tem parecer técnico formal arquivado; rastreabilidade de auditoria completa; confiança externa no sistema

---

## File Manifest

### Domínio: Agentes Especializados (Novos)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 1 | `agents/registry/perfilador.md` | Create | Definição do agente Perfilador (HDBSCAN/UMAP/Folium) | @ai-data-engineer | None |
| 2 | `agents/registry/explicador.md` | Create | Definição do agente Explicador (SHAP NL) | @ai-data-engineer | None |
| 3 | `agents/registry/supervisor.md` | Update | Adicionar Perfilador + Explicador ao DOMA routing | @ai-data-engineer | 1, 2 |
| 4 | `agents/registry/coletor.md` | Update | Expandir para 14 fontes + Bronze layer protocol | @ai-data-engineer | None |
| 5 | `agents/registry/modelista.md` | Update | Adicionar bootstrap IC + poll aggregation + SHAP | @ai-data-engineer | None |

### Domínio: Módulo Arquétipos (Novo)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 6 | `archetype/__init__.py` | Create | Package init | @python-developer | None |
| 7 | `archetype/pipeline.py` | Create | Orquestrador: scaler → PCA → HDBSCAN → UMAP → label | @python-developer | 8, 9, 10 |
| 8 | `archetype/features.py` | Create | Seleção e normalização de features do fact_municipio_eleicao | @python-developer | None |
| 9 | `archetype/clustering.py` | Create | HDBSCAN primary, K-means e GMM como fallback, silhouette gate | @python-developer | None |
| 10 | `archetype/reduction.py` | Create | UMAP 2D para visualização, PCA para clustering | @python-developer | None |
| 11 | `archetype/visualizer.py` | Create | Folium mapa coroplético BR por arquétipo | @python-developer | 9 |
| 12 | `archetype/cards.py` | Create | Fichas por arquétipo: top-10 features, histórico 2018/22 (2014 auxiliar) | @python-developer | 9 |
| 13 | `archetype/labels.py` | Create | LLM-assisted sociological labeling via Claude | @python-developer | 9 |
| 14 | `archetype/cache.py` | Create | Cache de resultados de clustering (Parquet + pickle) | @python-developer | None |
| 15 | `tests/test_archetype_pipeline.py` | Create | Testes unitários e de integração do pipeline | @test-generator | 7–14 |

### Domínio: MCP Servers (Expansão)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 16 | `mcp_servers/tse/server.py` | Update | Adicionar suporte 2014/2018/2022, Bronze write, Parquet | @python-developer | None |
| 17 | `mcp_servers/tse/schema_registry.py` | Create | Schema por ano (2014 vs 2018 vs 2022 diferem) | @python-developer | None |
| 18 | `mcp_servers/ibge/server.py` | Update | Adicionar Censo 2022 setores + SIDRA rate-limit cache | @python-developer | None |
| 19 | `mcp_servers/ibge/sidra_client.py` | Create | Client SIDRA API com retry + cache local | @python-developer | None |
| 20 | `mcp_servers/ibge/censo_loader.py` | Create | Loader CSV Censo 2022 por UF | @python-developer | None |
| 21 | `mcp_servers/digital/__init__.py` | Create | Package para sinal digital | @python-developer | None |
| 22 | `mcp_servers/digital/server.py` | Create | MCP server: Meta Ad Library + Google Trends + YouTube | @python-developer | 23, 24, 25 |
| 23 | `mcp_servers/digital/meta_ads.py` | Create | Meta Ad Library reader (agregado por município) | @python-developer | None |
| 24 | `mcp_servers/digital/google_trends.py` | Create | Google Trends scraper (pytrends) com LGPD aggregate | @python-developer | None |
| 25 | `mcp_servers/digital/youtube.py` | Create | YouTube Data API v3 (canal candidato, views agregado) | @python-developer | None |
| 26 | `mcp_servers/pesquisas/server.py` | Create | Leitor de pesquisas eleitorais (TSE PesqEle + Atlas) | @python-developer | None |
| 27 | `mcp_servers/vertex/server.py` | Create | Vertex AI Feature Store read/write + Model Registry query | @ai-data-engineer | None |

### Domínio: DataOps Pipeline

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 28 | `dataops/__init__.py` | Create | Package init | @python-developer | None |
| 29 | `dataops/bronze_writer.py` | Create | Escrita imutável em GCS raw/ com particionamento por fonte/ano | @ai-data-engineer | None |
| 30 | `dataops/silver_transformer.py` | Create | TSE+IBGE join, de-para cd_municipio, schema enforcement | @ai-data-engineer | 29 |
| 31 | `dataops/gold_builder.py` | Create | Construção das 3 tabelas Gold com ~200 features | @ai-data-engineer | 30 |
| 32 | `dataops/depara_municipios.py` | Create | Tabela de-para TSE ↔ IBGE (cd_municipio reconciliation) | @python-developer | None |
| 33 | `dataops/dq/__init__.py` | Create | Package DQ | @data-quality-analyst | None |
| 34 | `dataops/dq/expectations_tse.json` | Create | Great Expectations suite para Silver TSE | @data-quality-analyst | None |
| 35 | `dataops/dq/expectations_ibge.json` | Create | Great Expectations suite para Silver IBGE | @data-quality-analyst | None |
| 36 | `dataops/dq/expectations_gold.json` | Create | Great Expectations suite para Gold tables | @data-quality-analyst | None |
| 37 | `dataops/dq/runner.py` | Create | Executor de suites GE com output para Cloud Logging | @data-quality-analyst | 34, 35, 36 |
| 38 | `dataops/dq/cloud_dq_rules.yaml` | Create | Cloud DQ rules YAML para BigQuery | @data-quality-analyst | None |
| 39 | `dataops/lineage/dataplex_tagger.py` | Create | Tag de linhagem no Dataplex por pipeline run | @ai-data-engineer | None |
| 40 | `dataops/lineage/lineage_config.yaml` | Create | Definição de linhagem fonte→Bronze→Silver→Gold | @ai-data-engineer | None |
| 41 | `dataops/jobs/tse_ingest_job.py` | Create | Cloud Run job: download TSE → Bronze | @ai-data-engineer | 29, 17 |
| 42 | `dataops/jobs/ibge_sync_job.py` | Create | Cloud Run job: IBGE SIDRA + Censo → Bronze | @ai-data-engineer | 29, 18, 19, 20 |
| 43 | `dataops/jobs/silver_transform_job.py` | Create | Cloud Run job: Bronze → Silver | @ai-data-engineer | 30, 37 |
| 44 | `dataops/jobs/gold_build_job.py` | Create | Cloud Run job: Silver → Gold | @ai-data-engineer | 31, 37 |
| 45 | `dataops/jobs/digital_ingest_job.py` | Create | Cloud Run job: sinal digital → Bronze | @ai-data-engineer | 29, 22 |
| 46 | `dataops/scheduler/cloud_scheduler_config.yaml` | Create | Cloud Scheduler triggers para jobs DataOps | @ai-data-engineer | 41–45 |

### Domínio: MLOps Pipeline

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 47 | `mlops/__init__.py` | Create | Package init | @python-developer | None |
| 48 | `mlops/vertex_pipeline.py` | Create | KFP v2 pipeline: feature_extract → train → evaluate → promote | @ai-data-engineer | 49, 50, 51, 52 |
| 49 | `mlops/components/feature_extract.py` | Create | KFP component: Gold → Feature Store snapshot | @ai-data-engineer | None |
| 50 | `mlops/components/train_bootstrap.py` | Create | KFP component: logistic regression + bootstrap IC | @ai-data-engineer | None |
| 51 | `mlops/components/evaluate.py` | Create | KFP component: backtesting 2018→2022, erro médio | @ai-data-engineer | None |
| 52 | `mlops/components/promote.py` | Create | KFP component: champion/challenger, Model Registry push | @ai-data-engineer | None |
| 52a | `mlops/components/nlp_social.py` | Create | KFP component: NLP agregado do fato_social (sentimento, volume, trends index) | @ai-data-engineer | None |
| 52b | `mlops/components/pdf_parser.py` | Create | KFP component: parsing PDFs pesquisas (pdfplumber/camelot first; LLM fallback se fail_rate > 30%) | @ai-data-engineer | None |
| 53 | `mlops/monitoring/drift_config.yaml` | Create | Vertex Model Monitoring: drift threshold 10% | @ai-data-engineer | None |
| 54 | `mlops/monitoring/alerts.yaml` | Create | Cloud Alerting rules para drift + accuracy degradation | @ai-data-engineer | None |
| 55 | `mlops/model_card.md` | Create | Documentação do modelo: métricas, limitações, backtesting | @python-developer | 51 |
| 56 | `mlops/feature_store_config.yaml` | Create | Vertex Feature Store: feature groups, serving config | @ai-data-engineer | None |
| 57 | `mlops/shap_explainer.py` | Create | SHAP TreeExplainer + summary_plot + top-10 values | @python-developer | None |
| 58 | `mlops/pymc_model.py` | Create | PyMC HLM (produção) — hierárquico por UF | @python-developer | None |
| 59 | `mlops/poll_aggregator.py` | Create | House effect adjustment, aggregação pesquisas | @python-developer | None |
| 60 | `tests/test_mlops_pipeline.py` | Create | Testes do pipeline KFP e SHAP | @test-generator | 48–57 |

### Domínio: LLMOps

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 61 | `llmops/__init__.py` | Create | Package init | @python-developer | None |
| 62 | `llmops/prompt_registry/` | Create (dir) | Diretório de prompts versionados por semver | @llm-specialist | None |
| 63 | `llmops/prompt_registry/supervisor_v1.0.0.yaml` | Create | Prompt Supervisor — DOMA + roteamento | @llm-specialist | None |
| 64 | `llmops/prompt_registry/coletor_v1.0.0.yaml` | Create | Prompt Coletor — ingesta + validação | @llm-specialist | None |
| 65 | `llmops/prompt_registry/perfilador_v1.0.0.yaml` | Create | Prompt Perfilador — arquétipos + fichas | @llm-specialist | None |
| 66 | `llmops/prompt_registry/analista_v1.0.0.yaml` | Create | Prompt Analista — cruzamento socioeconômico | @llm-specialist | None |
| 67 | `llmops/prompt_registry/modelista_v1.0.0.yaml` | Create | Prompt Modelista — IC 95% + premissas | @llm-specialist | None |
| 68 | `llmops/prompt_registry/narrador_v1.0.0.yaml` | Create | Prompt Narrador — simplificação + disclaimers | @llm-specialist | None |
| 69 | `llmops/prompt_registry/explicador_v1.0.0.yaml` | Create | Prompt Explicador — SHAP em linguagem natural | @llm-specialist | None |
| 70 | `llmops/registry_loader.py` | Create | Carrega prompt correto por agente + versão do registry | @python-developer | 63–69 |
| 71 | `llmops/eval/golden_dataset.jsonl` | Create | 50 queries de avaliação com expected outputs | @llm-specialist | None |
| 72 | `llmops/eval/eval_runner.py` | Create | Executor de avaliação LLM com score ≥ 0.85 gate | @llm-specialist | 71 |
| 73 | `llmops/eval/metrics.py` | Create | Métricas: relevância, factualidade, disclaimer_present | @llm-specialist | None |
| 74 | `llmops/tracing/cloud_trace.py` | Create | Cloud Trace spans por sessão/agente/token count | @python-developer | None |
| 75 | `llmops/dashboard/looker_studio_config.json` | Create | Config Looker Studio: DataOps + MLOps + LLMOps metrics | @ai-data-engineer | None |

### Domínio: Security & LGPD

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 76 | `security/__init__.py` | Create | Package init | @python-developer | None |
| 77 | `security/iam_policies.yaml` | Create | IAM least-privilege por service account | @aws-lambda-architect | None |
| 78 | `security/secret_manager.py` | Create | Helper Secret Manager (API keys, credenciais) | @python-developer | None |
| 79 | `security/iap_config.yaml` | Create | Identity-Aware Proxy config (autenticação de usuário) | @gcp-data-architect | None |
| 80 | `security/cloud_armor_rules.yaml` | Create | WAF rules: rate limiting, geo-block, SQL injection | @gcp-data-architect | None |
| 81 | `security/vpc_config.yaml` | Create | VPC Service Controls: BigQuery + GCS perimeter | @gcp-data-architect | None |
| 82 | `security/lgpd_compliance.md` | Create | Documentação LGPD: aggregate-only, southamerica-east1, DLP | @python-developer | None |
| 83 | `hooks/dlp_hook.py` | Create | Cloud DLP hook: bloqueia PII em outputs de agentes | @python-developer | None |
| 84 | `hooks/rate_limit_hook.py` | Create | Rate limiting por usuário/sessão | @python-developer | None |
| 85 | `hooks/security_hook.py` | Update | Adicionar: BigQuery cost guard, partition required, SQL inject BQ | @python-developer | None |
| 86 | `hooks/audit_hook.py` | Update | Redirecionar audit logs para Cloud Logging (Structured JSON) | @python-developer | None |

### Domínio: GCP Infrastructure (Terraform)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 87 | `infra/terraform/main.tf` | Create | Provider GCP, projeto, região southamerica-east1 | @gcp-data-architect | None |
| 88 | `infra/terraform/variables.tf` | Create | Variáveis: project_id, region, environment | @gcp-data-architect | None |
| 89 | `infra/terraform/gcs.tf` | Create | GCS bucket Bronze (raw/) + Silver (parquet/) | @gcp-data-architect | 88 |
| 90 | `infra/terraform/bigquery.tf` | Create | Datasets spepe_silver, spepe_gold + tabelas + IAM | @gcp-data-architect | 88 |
| 91 | `infra/terraform/cloud_run.tf` | Create | Cloud Run service: min_instances=0, IAP, 1vCPU/2GB | @gcp-data-architect | 88 |
| 92 | `infra/terraform/firestore.tf` | Create | Firestore (memória episódica): southamerica-east1 | @gcp-data-architect | 88 |
| 93 | `infra/terraform/vertex.tf` | Create | Vertex AI: Feature Store + Pipelines + Model Registry | @gcp-data-architect | 88 |
| 94 | `infra/terraform/dataplex.tf` | Create | Dataplex lake + zones (Bronze/Silver/Gold) + assets | @gcp-data-architect | 89, 90 |
| 95 | `infra/terraform/monitoring.tf` | Create | Cloud Alerting, Log Sinks, Budget Alerts ($50 cap) | @gcp-data-architect | None |
| 96 | `infra/terraform/security.tf` | Create | IAP, Cloud Armor, Secret Manager, VPC SC | @gcp-data-architect | 77, 79, 80, 81 |
| 97 | `infra/terraform/outputs.tf` | Create | Outputs: service URLs, dataset IDs, bucket names | @gcp-data-architect | None |

### Domínio: CI/CD

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 98 | `.github/workflows/ci.yml` | Create | Lint + tests + LLM-eval gate (score ≥ 0.85) | @ci-cd-specialist | 72 |
| 99 | `.github/workflows/deploy.yml` | Create | Build Docker → GCR → Cloud Run deploy | @ci-cd-specialist | None |
| 100 | `.github/workflows/ml_pipeline.yml` | Create | Trigger Vertex AI Pipeline on Gold update | @ci-cd-specialist | 48 |
| 101 | `.github/workflows/security.yml` | Create | Trivy scan + Secret scanning + DLP check | @ci-cd-specialist | None |
| 102 | `Dockerfile` | Create | Multi-stage build: Python 3.12, streamlit/chainlit | @python-developer | None |
| 103 | `cloudbuild.yaml` | Create | Cloud Build config para deploy alternativo | @gcp-data-architect | None |

### Domínio: Configuração e Ambiente

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 104 | `.env.example` | Update | Adicionar GCP_PROJECT_ID, GCS_BUCKET, BIGQUERY_DATASET, VERTEX_LOCATION | @python-developer | None |
| 105 | `config/gcp_config.yaml` | Create | Config GCP por ambiente (dev/staging/prod) | @python-developer | None |
| 106 | `config/ml_config.yaml` | Create | Hiperparâmetros ML: min_cluster_size HDBSCAN, n_bootstrap | @python-developer | None |
| 107 | `config/agent_config.yaml` | Update | Adicionar Perfilador + Explicador, modelos corretos | @python-developer | 1, 2 |
| 108 | `config/dataops_config.yaml` | Create | DQ thresholds, lineage config, partition strategy | @ai-data-engineer | None |
| 109 | `requirements.txt` | Update | Adicionar: hdbscan, umap-learn, shap, pymc, folium, google-cloud-* | @python-developer | None |
| 110 | `requirements-dev.txt` | Update | Adicionar: great_expectations, kfp, pytest-asyncio | @python-developer | None |

### Domínio: Testes e Validação

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 111 | `tests/test_dataops_pipeline.py` | Create | Bronze→Silver→Gold pipeline integration tests | @test-generator | 29–31 |
| 112 | `tests/test_dq_gates.py` | Create | GE suite execution tests | @test-generator | 34–37 |
| 113 | `tests/test_agents_at.py` | Create | Acceptance tests AT-001 a AT-006 automatizados | @test-generator | None |
| 114 | `tests/test_security_hooks.py` | Create | Testes hooks DLP, rate limit, security | @test-generator | 83, 84, 85 |
| 115 | `tests/fixtures/sample_tse_2022.parquet` | Create | Sample TSE SP 2022 para testes locais | @test-generator | None |
| 116 | `tests/fixtures/sample_ibge.parquet` | Create | Sample IBGE indicadores SP para testes | @test-generator | None |

**Total Arquivos v2.0: 116**

### Domínio: MLOps Nível 5 (Adições v3.0)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 117 | `mlops/monitoring/drift_monitor.py` | Create | Calcula JS divergence por feature; publica em Pub/Sub se > threshold | @ai-data-engineer | 53 |
| 118 | `mlops/monitoring/pubsub_publisher.py` | Create | Publisher para topic `spepe-drift-detected`; payload: feature, score, timestamp | @python-developer | None |
| 119 | `mlops/monitoring/bias_monitor.py` | Create | Métricas Brier por sg_uf + quintil renda + pct_rural; alerta se grupo > global × 1.15 | @ai-data-engineer | 51 |
| 120 | `mlops/deployment/__init__.py` | Create | Package init deployment | @python-developer | None |
| 121 | `mlops/deployment/canary_manager.py` | Create | Cloud Run traffic split 10/90; avalia challenger por 48h; promove ou reverte | @gcp-data-architect | 52 |
| 122 | `mlops/deployment/auto_rollback.py` | Create | Monitora Brier score challenger a cada 6h; rollback se > champion × 1.05 | @ai-data-engineer | 121 |
| 123 | `mlops/components/hptuning.py` | Create | Vertex AI HyperparameterTuningJob: min_cluster_size, n_bootstrap, silhouette_threshold | @ai-data-engineer | 48 |
| 124 | `mlops/vertex_pipeline.py` | Update | Inserir hptuning_component antes de train_bootstrap_component | @ai-data-engineer | 123 |
| 125 | `mlops/prediction_store.py` | Create | Grava predição em spepe_mlops.fact_predictions; cruza com ground truth pós-eleição | @ai-data-engineer | None |
| 126 | `mlops/components/promote.py` | Update | Adicionar suporte a rollback flag e cooldown de 72h pós-retrain | @ai-data-engineer | 122 |
| 127 | `dataops/jobs/retrain_trigger_job.py` | Create | Acionado por Eventarc (Pub/Sub drift-detected); submete Vertex Pipeline | @ai-data-engineer | 117, 118, 48 |
| 128 | `infra/terraform/eventarc.tf` | Create | Eventarc trigger: topic spepe-drift-detected → Cloud Run retrain job | @gcp-data-architect | 127 |
| 129 | `infra/terraform/cloud_run_canary.tf` | Create | Cloud Run traffic split config; revision tags champion/challenger | @gcp-data-architect | 121 |
| 130 | `infra/terraform/bigquery_mlops.tf` | Create | Dataset spepe_mlops: fact_predictions + model_evaluations + bias_metrics | @gcp-data-architect | None |
| 131 | `infra/terraform/pubsub.tf` | Create | Pub/Sub topic spepe-drift-detected + subscription para Eventarc | @gcp-data-architect | 128 |
| 132 | `.github/workflows/canary_deploy.yml` | Create | GitHub Actions: deploy challenger → iniciar canary_manager.py watch | @ci-cd-specialist | 121 |
| 133 | `tests/test_mlops_level5.py` | Create | Testes para drift→retrain loop, canary, rollback, HP tuning, prediction store, bias | @test-generator | 117–131 |

**Total Arquivos v3.0: 133 (+17)**

### Domínio: Governança, Lineage, Contratos e Access Control (Adições v4.0)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 134 | `governance/data_catalog.yaml` | Create | Classificação de dados por domínio: PII / público / restrito / interno | @data-quality-analyst | None |
| 135 | `governance/stewards.yaml` | Create | Papéis de data steward por domínio (Social, Pesquisas, Eleições, IBGE) | @data-quality-analyst | None |
| 136 | `governance/retention_policy.yaml` | Create | Política formal de retenção: Bronze indefinido, Silver 90 dias, Gold 4 anos, MLOps 2 anos | @data-quality-analyst | None |
| 137 | `governance/schema_change_process.md` | Create | Processo de aprovação de schema change: PR obrigatório + contrato atualizado + notificação consumidores | @data-quality-analyst | 140–143 |
| 138 | `dataops/lineage/column_lineage.yaml` | Create | Lineage por coluna: fonte original → transformação → tabela Gold → feature ML | @ai-data-engineer | 40 |
| 139 | `dataops/lineage/job_tracker.py` | Create | Registra qual Cloud Run Job gerou qual partição (job_id, timestamp, rows, checksum) | @python-developer | 39 |
| 140 | `dataops/contracts/bronze_to_silver.yaml` | Create | Contrato Bronze→Silver: schema TSE/IBGE/Digital, freshness SLA (TSE: 24h, IBGE: 72h), completude ≥ 95% municípios | @data-contracts-engineer | None |
| 141 | `dataops/contracts/silver_to_gold.yaml` | Create | Contrato Silver→Gold: DQ ≥ 95%, cod_municipio_ibge obrigatório, 2 eleições presentes | @data-contracts-engineer | None |
| 142 | `dataops/contracts/gold_to_model.yaml` | Create | Contrato Gold→Modelo: features obrigatórias, tipos, ranges válidos, record_confidence_score ≥ 0.80 | @data-contracts-engineer | None |
| 143 | `dataops/contracts/gold_to_api.yaml` | Create | Contrato Gold→Dashboard/API: colunas públicas vs. restritas, versioning semântico, SLA freshness 2h | @data-contracts-engineer | None |
| 144 | `dataops/contracts/contract_validator.py` | Create | Valida dados contra contrato YAML em runtime; bloqueia promoção se contrato violado | @python-developer | 140–143 |
| 145 | `security/rbac_config.yaml` | Create | RBAC: spepe.viewer / spepe.analyst (com uf_filter) / spepe.admin — mapeados a grupos Google Workspace | @gcp-data-architect | None |
| 146 | `security/column_security.yaml` | Create | Column-level ACL BigQuery: house_effect_adj, record_confidence_score → somente spepe.admin | @gcp-data-architect | 145 |
| 147 | `security/row_access_policies.sql` | Create | BigQuery Row Access Policies por sg_uf para spepe.analyst com uf_filter atribuída | @gcp-data-architect | 145 |
| 148 | `mlops/monitoring/slo_config.yaml` | Create | SLOs: Gold freshness < 2h, DQ score ≥ 95%, model Brier < 0.25, API p99 < 3s | @ai-data-engineer | None |
| 149 | `mlops/monitoring/freshness_monitor.py` | Create | Verifica timestamp última partição por tabela Gold; alerta Cloud Alerting se > SLO | @python-developer | 148 |
| 150 | `infra/terraform/bigquery_rbac.tf` | Create | IAM conditions BigQuery para RBAC: column ACL + Row Access Policies via Terraform | @gcp-data-architect | 145, 146, 147 |
| 151 | `tests/test_contracts.py` | Create | Testes de validação dos 4 contratos (schema, freshness, completude, ranges) | @test-generator | 140–144 |
| 152 | `tests/test_rbac.py` | Create | Testes RBAC: viewer não acessa Gold raw, analyst filtrado por UF, admin irrestrito | @test-generator | 145–147 |

**Total Arquivos v4.0: 152 (+19)**

### Domínio: Sentinel — Orquestrador Multi-Agent de Monitoramento (Adições v4.2)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 153 | `sentinel/__init__.py` | Create | Package init | @python-developer | None |
| 154 | `sentinel/orchestrator.py` | Create | Orquestrador principal: recebe evento Pub/Sub → roteia para crew → consolida → despacha | @genai-architect | 155–172 |
| 155 | `sentinel/events/event_types.py` | Create | Enum de eventos: dq_violation, contract_breach, drift_detected, bias_alert, freshness_slo_breach, budget_warning, social_burst, pipeline_failure, canary_degradation | @python-developer | None |
| 156 | `sentinel/events/event_bus.py` | Create | Subscriber Pub/Sub multi-topic; deserializa evento; publica para orchestrator | @python-developer | 155 |
| 157 | `sentinel/crews/observadores.py` | Create | Crew 1 — Detection: DataOpsWatcher + MLOpsWatcher + InfraWatcher + SocialWatcher | @ai-data-engineer | 158–161 |
| 158 | `sentinel/watchers/dataops_watcher.py` | Create | Monitora DQ gates, contract violations, freshness SLO, job failures em tempo real | @ai-data-engineer | None |
| 159 | `sentinel/watchers/mlops_watcher.py` | Create | Monitora drift JS > 10%, Brier score, canary degradation, HP tuning status | @ai-data-engineer | None |
| 160 | `sentinel/watchers/infra_watcher.py` | Create | Monitora Cloud Run health, budget consumed, API p99, Cloud Run job exit codes | @python-developer | None |
| 161 | `sentinel/watchers/social_watcher.py` | Create | Monitora bursts em fato_social: volume_mencoes > μ + 3σ em janela de 1h por UF | @ai-data-engineer | None |
| 162 | `sentinel/crews/analisadores.py` | Create | Crew 2 — Analysis: PatternDetector + AnomalyDetector + correlação cross-domínio | @ai-data-engineer | 163, 164 |
| 163 | `sentinel/analysts/pattern_detector.py` | Create | Detecta padrões históricos de incidentes na KB; compara com evento atual | @python-developer | 167 |
| 164 | `sentinel/analysts/anomaly_detector.py` | Create | Statistical anomaly: Z-score + IQR por métrica; correlaciona sinais de múltiplos watchers | @python-developer | None |
| 165 | `sentinel/crews/interpretadores.py` | Create | Crew 3 — Knowledge/KB: ContextBuilder + GenAI Interpreter (Claude/Gemini) + KB Updater | @genai-architect | 166–168 |
| 166 | `sentinel/kb/context_builder.py` | Create | Monta contexto: KB histórica + dados recentes + evento atual + correlações detectadas | @python-developer | 167 |
| 167 | `sentinel/kb/knowledge_base.py` | Create | CRUD Firestore KB: padrões de incidentes, root causes, playbooks, confidence scores | @python-developer | None |
| 168 | `sentinel/kb/kb_updater.py` | Create | Após resolução de incidente: atualiza KB com causa confirmada + ação tomada + outcome | @python-developer | 167 |
| 169 | `sentinel/crews/despachantes.py` | Create | Crew 4 — Output: Reporter + Dispatcher + ActionExecutor | @python-developer | 170–172 |
| 170 | `sentinel/dispatch/reporter.py` | Create | Formata incidente em Markdown estruturado: severidade, causa provável, ação sugerida, confiança | @python-developer | None |
| 171 | `sentinel/dispatch/dispatcher.py` | Create | Envia para: Slack webhook, Cloud Logging (structured), Pub/Sub `sentinel-alerts`, dashboard update | @python-developer | None |
| 172 | `sentinel/dispatch/action_executor.py` | Create | Executa ações autônomas aprovadas: dispara retrain, rollback, bloqueia pipeline, escala Cloud Run | @ai-data-engineer | None |
| 173 | `sentinel/genai_interpreter.py` | Create | LLM (Claude Sonnet): recebe contexto KB + evento → retorna causa raiz provável + ação recomendada + severidade | @genai-architect | 165, 166 |
| 174 | `config/sentinel_config.yaml` | Create | Thresholds por evento, modelos LLM por crew, Slack webhook, Pub/Sub topics, cooldown por tipo de ação | @python-developer | None |
| 175 | `infra/terraform/sentinel.tf` | Create | Cloud Run Sentinel (always-on, min_instances=1, 0.5vCPU/512MB); Pub/Sub subscriptions; Firestore collection `sentinel_kb` | @gcp-data-architect | None |
| 176 | `infra/terraform/pubsub_sentinel.tf` | Create | Topics: `sentinel-events` (entrada), `sentinel-alerts` (saída); subscriptions com retry 3x + DLQ | @gcp-data-architect | 175 |
| 177 | `tests/test_sentinel.py` | Create | Testes: event routing, crew isolation, KB CRUD, action executor dry-run, GenAI interpreter mock | @test-generator | 154–173 |

**Total Arquivos v4.2: 177 (+25)**

### Domínio: DataOps Nível 5 (Adições v4.3)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 178 | `dataops/cdc/incremental_loader.py` | Create | Detecta delta Bronze via GCS event (Pub/Sub); carrega apenas novos registros; fallback full-refresh se sem `updated_at` | @ai-data-engineer | None |
| 179 | `dataops/cdc/cdc_config.yaml` | Create | Fonte → estratégia CDC: TSE=event-triggered, IBGE=scheduled, digital=streaming, pesquisas=event-triggered | @ai-data-engineer | None |
| 180 | `dataops/healing/pipeline_healer.py` | Create | Detecta falhas comuns (schema drift, null explosion, arquivo corrompido); aplica correção ou roteia para fila manual | @ai-data-engineer | 181 |
| 181 | `dataops/healing/schema_evolver.py` | Create | Aplica migrações backward-compatible automaticamente (additive only); breaking changes → bloqueia + alerta steward | @ai-data-engineer | 137 |
| 182 | `dataops/versioning/snapshot_manager.py` | Create | BigQuery Table Snapshots por build Gold (retenção 7 dias); registra snapshot_id + job_id + timestamp no `sentinel_kb` | @python-developer | None |
| 183 | `dataops/dq/realtime_dq.py` | Create | DQ em stream via Pub/Sub + Dataflow Flex Template; alerta em < 60s após anomalia; complementa batch GE | @ai-data-engineer | None |
| 184 | `dataops/cost/slot_optimizer.py` | Create | Analisa INFORMATION_SCHEMA.JOBS; emite recomendações de slot reservation; alerta queries sem partition filter antes de executar | @ai-data-engineer | None |
| 185 | `dataops/profiler/auto_profiler.py` | Create | Perfil automático de cada novo dataset Bronze: distribuição, nulls, outliers, cardinalidade; persiste em `data_catalog.yaml` | @data-quality-analyst | 134 |
| 186 | `dataops/mesh/domain_registry.yaml` | Create | Data mesh: ownership por domínio (Social→analista social, TSE→admin, Pesquisas→analista sênior) | @data-quality-analyst | 135 |
| 187 | `infra/terraform/dataflow.tf` | Create | Dataflow Flex Template para real-time DQ streaming | @gcp-data-architect | 183 |
| 188 | `infra/terraform/pubsub_cdc.tf` | Create | GCS notification → Pub/Sub `bronze-new-file` → incremental_loader trigger | @gcp-data-architect | 178 |
| 189 | `tests/test_dataops_l5.py` | Create | Testes: CDC incremental delta, self-healing schema drift, snapshot rollback, realtime DQ mock, slot optimizer dry-run | @test-generator | 178–186 |

### Domínio: MLOps Nível 5 Completo (Adições v4.3)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 190 | `mlops/experiments/experiment_tracker.py` | Create | Loga cada run no Vertex AI Experiments: params, métricas, artifacts, parent_run; habilita comparação entre runs | @ai-data-engineer | None |
| 191 | `mlops/experiments/run_comparator.py` | Create | Compara runs no Vertex AI Experiments; identifica melhor configuração para HP tuning | @python-developer | 190 |
| 192 | `mlops/feature_store/online_server.py` | Create | Vertex AI Feature Store Online: serve features em < 50ms para predições em tempo real | @ai-data-engineer | None |
| 193 | `mlops/feature_store/feature_definitions.yaml` | Create | Define feature groups, entidades (municipio, candidato), TTL, servimento online vs. offline | @ai-data-engineer | 192 |
| 194 | `mlops/deployment/shadow_mode.py` | Create | Challenger roda em paralelo ao champion sem servir usuário; predições gravadas em `fact_predictions` com `shadow=true`; após 7 dias → significance test | @ai-data-engineer | 195 |
| 195 | `mlops/evaluation/significance_tester.py` | Create | McNemar test + permutation test (p < 0.05) para validar se challenger é estatisticamente melhor; retorna promote/discard com p-value | @python-developer | None |
| 196 | `mlops/components/continuous_train.py` | Create | KFP component: data-triggered training (GCS event → novo Gold snapshot → retrain automático); complementa drift-triggered | @ai-data-engineer | 190 |
| 197 | `mlops/model_cards/auto_updater.py` | Create | Atualiza `model_card.md` automaticamente pós-avaliação: métricas atuais, backtesting, bias scores, data de treino, features usadas | @python-developer | None |
| 198 | `infra/terraform/vertex_online_store.tf` | Create | Vertex AI Feature Store Online: entity types, feature configs, serving endpoints | @gcp-data-architect | 192, 193 |
| 199 | `tests/test_mlops_l5_advanced.py` | Create | Testes: experiment logging, online serving latência < 50ms, shadow mode isolation, significance test (synthetic data) | @test-generator | 190–197 |

### Domínio: LLMOps Nível 5 Completo (Adições v4.3)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 200 | `llmops/cache/semantic_cache.py` | Create | Redis Cloud Memorystore + embeddings; cosine similarity > 0.95 → cache hit; TTL 24h; métricas hit_rate por agente | @python-developer | None |
| 201 | `llmops/cache/cache_invalidator.py` | Create | Invalida cache quando Gold é atualizado (dados mudaram → respostas antigas podem estar desatualizadas) | @python-developer | 200 |
| 202 | `llmops/eval/continuous_eval.py` | Create | Reservoir sampling 5% outputs produção; roda métricas eval; alerta se score < 0.85; salva samples em `golden_dataset.jsonl` | @llm-specialist | 72, 73 |
| 203 | `llmops/eval/hallucination_detector.py` | Create | Verifica claims numéricos contra Gold (ex: "Lula 44% SP" → query `fact_municipio_eleicao`); bloqueia output se divergência > 5pp | @llm-specialist | None |
| 204 | `llmops/prompts/prompt_ab_test.py` | Create | Split 50/50 entre prompt atual e candidato; após N sessões (poder > 0.80) promove melhor versão automaticamente | @llm-specialist | None |
| 205 | `llmops/prompts/prompt_optimizer.py` | Create | Gradient-free prompt optimization: gera variações, avalia via eval runner, seleciona melhor; roda offline (não em produção) | @llm-specialist | 72 |
| 206 | `llmops/monitoring/output_drift.py` | Create | Monitora distribuição de outputs por agente: disclaimer_rate, confidence_score, token_count, response_time; alerta se > 2σ do baseline | @llm-specialist | None |
| 207 | `llmops/context/context_manager.py` | Create | Monitora fill rate do contexto por sessão; ao atingir 80% → sumarização automática preservando: decisões, dados críticos, comandos ativos | @python-developer | None |
| 208 | `llmops/cost/cost_attributor.py` | Create | Atribui custo por agente/sessão/usuário; persiste em Cloud Logging estruturado; dashboard Looker Studio por agente | @python-developer | None |
| 209 | `infra/terraform/redis.tf` | Create | Cloud Memorystore Redis (Basic, 1GB): semantic cache para LLMOps; southamerica-east1 | @gcp-data-architect | 200 |
| 210 | `tests/test_llmops_l5.py` | Create | Testes: cache hit/miss, hallucination detection (claim vs. Gold), context summarization trigger, cost attribution por agente | @test-generator | 200–208 |

**Total Arquivos v4.3: 210 (+33)**

### Domínio: Memória Vetorial de Longo Prazo (Adições v4.4)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 211 | `memory_store/__init__.py` | Create | Package init | @python-developer | None |
| 212 | `memory_store/memory_types.py` | Create | Enum de tipos de memória: análise, padrão_eleitoral, alerta, decisão_modelo, contexto_político | @python-developer | None |
| 213 | `memory_store/session_memory.py` | Create | Indexa output de agente no Vertex AI Vector Search pós-sessão; calcula embedding Gecko 768d | @ai-data-engineer | 212 |
| 214 | `memory_store/retriever.py` | Create | Recupera K vizinhos (K=5) por cosine similarity ≥ 0.75 antes de cada resposta de agente; injeta como contexto | @ai-data-engineer | 213 |
| 215 | `memory_store/memory_index_config.yaml` | Create | Vertex AI Vector Search: dimensão 768, ANN ScaNN, TTL 1 ano, namespace por agente | @ai-data-engineer | None |
| 216 | `memory_store/memory_manager.py` | Create | TTL enforcement (remove memórias > 1 ano), deduplicação por similaridade > 0.98, compactação periódica | @python-developer | 213, 214 |
| 217 | `infra/terraform/vector_search.tf` | Create | Vertex AI Matching Engine index + endpoint; us-central1; dimensão 768; ScaNN | @gcp-data-architect | None |
| 218 | `tests/test_memory_store.py` | Create | Testes: indexação, recuperação K vizinhos, TTL, dedup, namespace isolation por agente | @test-generator | 211–216 |

### Domínio: ML Judge — Agente Auditor Independente (Adições v4.4)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 219 | `judge/__init__.py` | Create | Package init — isolado dos demais módulos; sem imports de mlops/ | @python-developer | None |
| 220 | `judge/ml_judge.py` | Create | Agente auditor: Gemini 2.5 Pro; acesso somente leitura a spepe_mlops.*; metodologia independente de backtest | @genai-architect | 221, 222, 223 |
| 221 | `judge/independent_backtester.py` | Create | Backtest independente: carrega predições de `fact_predictions`, cruza com ground truth TSE, calcula métricas próprias (Brier, calibration, coverage) | @python-developer | None |
| 222 | `judge/fairness_auditor.py` | Create | Auditoria de fairness: equidade por quintil de renda, sg_uf, pct_zona_rural; metodologia Equalized Odds | @python-developer | None |
| 223 | `judge/technical_report.py` | Create | Gera parecer técnico formal (Markdown + PDF): metodologia, métricas auditadas, achados, limitações, recomendação (Aprovado / Aprovado com ressalvas / Reprovado) | @python-developer | 220, 221, 222 |
| 224 | `judge/judge_config.yaml` | Create | Thresholds do Judge: Brier máx 0.25, calibration error máx 0.05, fairness gap máx 15%, modelo Gemini 2.5 Pro | @python-developer | None |
| 225 | `judge/promotion_gate.py` | Create | Integra com promote.py: bloqueia promoção se Judge retornar "Reprovado"; libera com parecer arquivado em spepe_mlops.audit_reports | @ai-data-engineer | 220, 223 |
| 226 | `infra/terraform/bigquery_judge.tf` | Create | Dataset spepe_mlops.audit_reports: tabela de pareceres com model_version, recommendation, report_path, auditor_model | @gcp-data-architect | None |
| 227 | `tests/test_ml_judge.py` | Create | Testes: backtest independente (dados sintéticos), fairness audit, geração de parecer, bloqueio de promoção em "Reprovado" | @test-generator | 219–225 |

**Total Arquivos v4.4: 227 (+17)**

### Domínio: Disclaimer Obrigatório — Enforcement Universal (Adições v4.5)

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 228 | `hooks/disclaimer_hook.py` | Create | Detecta tipo de output (4 triggers); injeta disclaimer ausente; registra em Cloud Logging; gate binário | @python-developer | 229 |
| 229 | `security/disclaimer_templates.yaml` | Create | 4 templates por tipo: tipo_a_previsao, tipo_b_dados, tipo_c_pesquisa, tipo_d_recomendacao | @python-developer | None |
| 230 | `security/output_validators.py` | Update | `disclaimer_present` como validação hard (bloqueia, não apenas métrica); integra com disclaimer_hook | @python-developer | 228 |
| 231 | `llmops/eval/metrics.py` | Update | `disclaimer_present_rate` = gate binário (100% obrigatório); alerta P1 se agente < 100% em 24h | @llm-specialist | None |
| 232 | `judge/technical_report.py` | Update | Inclui auditoria de `disclaimer_rate` no parecer; Reprovado automático se < 100% em outputs da amostra | @python-developer | None |
| 233 | `tests/test_disclaimer_hook.py` | Create | Testes: detecção correta de 4 tipos, injeção de template, outputs sem trigger não modificados, Cloud Logging | @test-generator | 228, 229 |

**Total Arquivos v4.5: 233 (+6)**

---

## Agent Assignment Rationale

| Agent | Files # | Why This Agent |
|-------|---------|----------------|
| @ai-data-engineer | 1–5, 29–31, 39–46, 47–52, 56, 74 | DataOps pipelines, Vertex AI, BigQuery, GCS |
| @python-developer | 6–14, 32, 57–59, 70, 74, 76, 78, 82, 102–110 | Core Python modules, configs, clustering code |
| @data-quality-analyst | 33–38 | Great Expectations, Cloud DQ, DQ gates |
| @llm-specialist | 61–73 | Prompt engineering, eval framework, registry |
| @gcp-data-architect | 79–81, 87–97, 103 | GCP Terraform, IAP, Cloud Armor, Dataplex |
| @test-generator | 15, 60, 111–116 | pytest fixtures, acceptance tests, integration |
| @ci-cd-specialist | 98–101 | GitHub Actions, Cloud Build |

---

## Code Patterns

### Pattern 1: HDBSCAN Clustering Pipeline

```python
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
import hdbscan
import umap
import numpy as np
import pandas as pd

def run_archetype_pipeline(
    df: pd.DataFrame,
    feature_cols: list[str],
    min_cluster_size: int = 30,
    n_pca_components: int = 50,
) -> tuple[np.ndarray, np.ndarray, float]:
    X = df[feature_cols].fillna(0).values
    X_scaled = StandardScaler().fit_transform(X)
    X_pca = PCA(n_components=min(n_pca_components, X_scaled.shape[1])).fit_transform(X_scaled)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=5,
        cluster_selection_method="eom",
        prediction_data=True,
    )
    labels = clusterer.fit_predict(X_pca)

    from sklearn.metrics import silhouette_score
    mask = labels != -1
    score = silhouette_score(X_pca[mask], labels[mask]) if mask.sum() > 1 else 0.0

    reducer = umap.UMAP(n_components=2, random_state=42)
    embedding_2d = reducer.fit_transform(X_pca)

    return labels, embedding_2d, score
```

### Pattern 2: Bootstrap IC 95% (statsmodels)

```python
import numpy as np
import statsmodels.api as sm
from dataclasses import dataclass

@dataclass
class Prediction:
    point_estimate: float
    ci_lower: float
    ci_upper: float
    n_bootstrap: int = 1000

def predict_with_ic(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_pred: np.ndarray,
    n_bootstrap: int = 1000,
    alpha: float = 0.05,
) -> Prediction:
    predictions = []
    n = len(X_train)
    for _ in range(n_bootstrap):
        idx = np.random.choice(n, n, replace=True)
        model = sm.Logit(y_train[idx], sm.add_constant(X_train[idx]))
        result = model.fit(disp=False, maxiter=100)
        predictions.append(result.predict(sm.add_constant(X_pred))[0])

    preds = np.array(predictions)
    return Prediction(
        point_estimate=float(np.mean(preds)),
        ci_lower=float(np.percentile(preds, alpha / 2 * 100)),
        ci_upper=float(np.percentile(preds, (1 - alpha / 2) * 100)),
        n_bootstrap=n_bootstrap,
    )
```

### Pattern 3: Folium Mapa Coroplético Brasil

```python
import folium
import geopandas as gpd
import pandas as pd

def build_brazil_map(
    df_archetypes: pd.DataFrame,
    geojson_path: str,
    archetype_colors: dict[int, str],
) -> folium.Map:
    gdf = gpd.read_file(geojson_path)
    merged = gdf.merge(df_archetypes, left_on="CD_MUN", right_on="cod_municipio_ibge")

    m = folium.Map(location=[-14.235, -51.925], zoom_start=4, tiles="CartoDB positron")
    folium.Choropleth(
        geo_data=merged.__geo_interface__,
        data=df_archetypes,
        columns=["cod_municipio_ibge", "archetype_id"],
        key_on="feature.properties.CD_MUN",
        fill_color="YlOrRd",
        fill_opacity=0.7,
        line_opacity=0.2,
        legend_name="Arquétipo do Eleitorado",
    ).add_to(m)

    folium.GeoJson(
        merged,
        tooltip=folium.GeoJsonTooltip(
            fields=["NM_MUN", "archetype_label", "archetype_id"],
            aliases=["Município", "Arquétipo", "ID"],
        ),
    ).add_to(m)
    return m

# Nota: cod_municipio_ibge = chave global de 7 dígitos IBGE (âncora territorial obrigatória)
```

### Pattern 4: Vertex AI KFP Component

```python
from kfp.v2 import dsl
from kfp.v2.dsl import component, Output, Dataset

@component(
    base_image="python:3.12-slim",
    packages_to_install=["google-cloud-bigquery", "pandas", "scikit-learn"],
)
def extract_features(
    project_id: str,
    dataset_id: str,
    table_name: str,
    output_dataset: Output[Dataset],
):
    from google.cloud import bigquery
    import pandas as pd

    client = bigquery.Client(project=project_id)
    query = f"SELECT * FROM `{project_id}.{dataset_id}.{table_name}`"
    df = client.query(query).to_dataframe()
    df.to_parquet(output_dataset.path)
```

### Pattern 5: SHAP Top-10 em Linguagem Natural

```python
import shap
import numpy as np
import pandas as pd

def get_shap_explanation(
    model,
    X: pd.DataFrame,
    feature_names: list[str],
    top_n: int = 10,
) -> list[dict]:
    explainer = shap.LinearExplainer(model, X)
    shap_values = explainer(X)
    mean_abs_shap = np.abs(shap_values.values).mean(axis=0)
    top_idx = np.argsort(mean_abs_shap)[::-1][:top_n]

    return [
        {
            "feature": feature_names[i],
            "importance": float(mean_abs_shap[i]),
            "direction": "positivo" if shap_values.values[:, i].mean() > 0 else "negativo",
        }
        for i in top_idx
    ]
```

### Pattern 6: Hook DLP (LGPD)

```python
import re
from claude_code_sdk import ClaudeCodeHookResult

CPF_PATTERN = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

def dlp_hook(output: str) -> ClaudeCodeHookResult:
    violations = []
    if CPF_PATTERN.search(output):
        violations.append("CPF detectado")
    if EMAIL_PATTERN.search(output):
        violations.append("email pessoal detectado")

    if violations:
        return ClaudeCodeHookResult(
            block=True,
            reason=f"LGPD: PII detectado no output — {', '.join(violations)}. "
                   "Dados devem estar em nível agregado (município/cluster).",
        )
    return ClaudeCodeHookResult(block=False)
```

### Pattern 7: Config GCP por Ambiente

```yaml
# config/gcp_config.yaml
environments:
  dev:
    project_id: spepe-dev
    region: southamerica-east1
    gcs_bucket: spepe-dev-data
    bigquery_dataset_silver: spepe_silver
    bigquery_dataset_gold: spepe_gold
    cloud_run_service: spepe-dev
    vertex_location: us-central1  # Vertex não disponível em southamerica-east1
    firestore_database: "(default)"
  prod:
    project_id: spepe-prod
    region: southamerica-east1
    gcs_bucket: spepe-prod-data
    bigquery_dataset_silver: spepe_silver
    bigquery_dataset_gold: spepe_gold
    cloud_run_service: spepe
    vertex_location: us-central1
    firestore_database: "(default)"
    budget_alert_usd: 50
```

---

## Data Flow

```text
INGESTÃO (14 fontes → Bronze)
1. Cloud Scheduler dispara Cloud Run job (tse_ingest_job.py)
   │
   ▼
2. Download CSVs TSE 2014/2018/2022 → convert Parquet → GCS gs://spepe-{env}-data/raw/tse/{ano}/
   ├── ibge_sync_job.py: IBGE SIDRA API + Censo 2022 CSV → GCS raw/ibge/
   └── digital_ingest_job.py: Meta Ads + Google Trends → GCS raw/digital/
   │
   ▼
PROCESSAMENTO (Bronze → Silver)
3. silver_transform_job.py:
   ├── Aplica schema registry por ano (TSE 2014 ≠ 2022)
   ├── Join TSE + IBGE via depara_municipios.py
   ├── Great Expectations DQ gate (score ≥ 95%)
   └── Escrita em BigQuery spepe_silver.*
   │
   ▼
MODELAGEM (Silver → Gold)
4. gold_build_job.py:
   ├── fact_municipio_eleicao: 5570 municípios × 2 eleições (2018/2022) × ~200 features
   ├── fato_social: sinal digital agregado (município × semana, LGPD-safe, 4 fontes)
   └── fact_pesquisa: tabela central — record_confidence_score + house_effect_adj por instituto
   │
   ▼
ML STAGE 1 — Arquétipos
5. Perfilador agent (/arquétipos):
   ├── Lê fact_municipio_eleicao do BigQuery
   ├── run_archetype_pipeline() → HDBSCAN → UMAP 2D
   ├── labels.py: Claude labela cada cluster sociologicamente
   ├── cards.py: gera ficha por arquétipo (top-10 features, histórico)
   └── visualizer.py: Folium mapa BR → HTML → Chainlit iframe
   │
   ▼
FEATURE MATRIX → STAGE 2 — Previsão Bayesiana (dados integrados)
6. Analista agent (/perfil):
   ├── Lê fato_social + fact_municipio_eleicao + fact_pesquisa
   ├── Cruzamento: perfil socioeconômico × comportamento histórico × sinal digital × intenção
   └── Agregação LGPD-safe (município mínimo, nunca individual)
   │
   ▼
STAGE 2 — Modelo Bayesiano (feature matrix completa)
7. Modelista agent (/prever):
   ├── predict_with_ic(): bootstrap logistic → P(X)=N% [IC 95%: A%–B%]
   ├── poll_aggregator.py: house effect adjustment × 5+ institutos
   ├── shap_explainer.py: top-10 SHAP values
   └── Premissas declaradas + disclaimers eleitorais automáticos
   │
   ▼
EXPLICABILIDADE + NARRATIVA
8. Explicador (/explicar): SHAP top-10 → linguagem natural
   Narrador (/relatorio): output técnico → texto para leigos
   │
   ▼
USUÁRIO: resposta conversacional com IC, mapa, fichas, disclaimer
```

---

## Pipeline Architecture

### DAG DataOps

```text
[TSE CSV]──────────────────┐
[IBGE SIDRA API]──────────►│ Bronze Layer (GCS raw/)
[IBGE Censo 2022 CSV]─────►│   Parquet, imutável, particionado por fonte/ano
[Digital Signal APIs]──────┘
         │                  job_tracker.py registra: job_id, timestamp, rows, checksum
         ▼
[Contract Validator] bronze_to_silver.yaml ── violação → BLOCK + Alert
         │ contrato OK
         ▼
[Great Expectations DQ Suite] ── score < 95% → ALERT + BLOCK
         │ score ≥ 95%
         ▼
[Silver Layer] (BigQuery spepe_silver)
  municipios_clean | candidatos_clean | ibge_indicadores | digital_signal | pesquisas_clean
         │                  contract_validator: silver_to_gold.yaml
         ▼
[Gold Builder] (BigQuery spepe_gold)
  fact_municipio_eleicao (~200 vars) | fato_social (~40 vars) | fact_pesquisa
         │
         ▼
[Dataplex Lineage Tag] → linhagem tabela→Bronze→Silver→Gold auditável
[Column Lineage YAML]  → por coluna: fonte_original → transformação → feature ML
```

### Lineage por Coluna (column_lineage.yaml)

Rastreia a origem de cada feature crítica até a fonte bruta:

| Feature Gold | Origem Bronze | Transformação Silver | Contrato |
|---|---|---|---|
| `resultado_2022` | `tse_2022/*.parquet → qt_votos_validos` | normalizado por `qt_votos_aptos` | `silver_to_gold.yaml` |
| `renda_media_domiciliar` | `ibge_sidra/*.parquet → V6527` | join por `cod_municipio_ibge` | `silver_to_gold.yaml` |
| `volume_mencoes` | `digital/*.parquet → tweet_count + yt_comment_count` | agregado por `semana_ref` | `bronze_to_silver.yaml` |
| `record_confidence_score` | `pesquisas/*.parquet → origem` | mapeado por tabela score (1.00→0.30) | `bronze_to_silver.yaml` |
| `house_effect_adj` | `pesquisas/*.parquet → instituto + acerto_historico` | `poll_aggregator.py` | `gold_to_model.yaml` |

### Partition Strategy — BigQuery

| Table | Partition Key | Granularity | Rationale |
|-------|---------------|-------------|-----------|
| `fact_municipio_eleicao` | `ano_eleicao` | Anual | 2 valores principais: 2018, 2022 (2014 = auxiliar) |
| `fato_social` | `semana_ref` | Semanal | Agregação município × semana, LGPD-safe |
| `fact_pesquisa` | `data_pesquisa` | Diária | Série de pesquisas por instituto |
| Silver TSE | `sg_uf` + `ano_eleicao` | Anual | Consulta por UF é o padrão |

### Data Quality Gates

| Gate | Tool | Threshold | Action on Failure |
|------|------|-----------|-------------------|
| Null em PKs (cod_municipio_ibge, ano) | Great Expectations | 0 nulls | Block + Alert |
| qt_votos ≥ 0 | Great Expectations | 100% | Block |
| Cobertura municípios por UF | GE row count | ≥ 95% dos municípios da UF | Alert + continue |
| DQ score geral Silver | GE suite | ≥ 95% | Block Gold build |
| Freshness Bronze | Dataplex | Arquivo presente por eleição | Alert |
| IBGE indicadores por município | GE | ≥ 3 indicadores | Alert |

---

## Integration Points

| External System | Integration Type | Authentication |
|-----------------|-----------------|----------------|
| TSE Repositório de Dados | HTTP download (CSV/zip) | Público — sem auth |
| IBGE SIDRA API | REST API | Público — sem auth, rate limit |
| IBGE Censo 2022 | HTTP download (CSV) | Público — sem auth |
| Meta Ad Library | REST API | App Token (Meta Developer) |
| Google Trends | pytrends (scraper) | Nenhuma (unofficial) |
| YouTube Data API v3 | REST API | API Key via Secret Manager |
| Anthropic API | SDK (anthropic Python) | API Key via Secret Manager |
| Google BigQuery | google-cloud-bigquery SDK | Service Account ADC |
| Google Cloud Storage | google-cloud-storage SDK | Service Account ADC |
| Vertex AI | google-cloud-aiplatform SDK | Service Account ADC |
| Vertex Feature Store | google-cloud-aiplatform SDK | Service Account ADC |
| Firestore | google-cloud-firestore SDK | Service Account ADC |
| Cloud Trace | google-cloud-trace SDK | Service Account ADC |
| Cloud DLP | google-cloud-dlp SDK | Service Account ADC |

---

## Testing Strategy

| Test Type | Scope | Files | Tools | Coverage Goal |
|-----------|-------|-------|-------|---------------|
| Unit | Clustering, IC bootstrap, SHAP, DLP hook | `tests/test_archetype_pipeline.py`, `tests/test_mlops_pipeline.py`, `tests/test_security_hooks.py` | pytest | 80% linhas nos módulos core |
| Integration | Bronze→Silver→Gold com fixtures Parquet | `tests/test_dataops_pipeline.py`, `tests/test_dq_gates.py` | pytest + fixtures locais | Happy path + DQ failures |
| Acceptance | AT-001 a AT-006 (slash commands reais) | `tests/test_agents_at.py` | pytest + Chainlit test client | 6/6 ATs passando |
| LLM Eval | 50 queries golden dataset | `llmops/eval/eval_runner.py` | Custom metrics (relevância, disclaimer) | Score ≥ 0.85 |
| CI Gate | Lint + unit + LLM-eval em todo PR | `.github/workflows/ci.yml` | GitHub Actions | 100% pass para merge |

---

## Error Handling

| Error Type | Handling Strategy | Retry? |
|------------|-------------------|--------|
| TSE URL mudou ou arquivo ausente | Log warning + usa cache Bronze existente; alerta operador | Não |
| IBGE SIDRA rate limit (429) | Exponential backoff 1s→2s→4s→8s (max 3 retries), depois cache | Sim (3x) |
| HDBSCAN silhouette < 0.45 | Fallback para K-means (k=8); log aviso de qualidade baixa | Não |
| Bootstrap IC diverge (NaN) | Aumenta n_bootstrap para 2000; se persistir, retorna IC ∅ com aviso | Sim (1x) |
| Budget guard atingido ($2.00) | Interrompe sessão, informa usuário, registra em audit log | Não |
| BigQuery query sem partition filter | security_hook bloqueia antes de executar | Não |
| PII detectado em output (DLP hook) | Bloqueia output, registra em Cloud Logging, envia alerta | Não |
| Cloud Run cold start timeout | Keepalive request a cada 5min via Cloud Scheduler (prod) | N/A |
| Vertex Pipeline falha em componente | KFP retry policy: 2 tentativas por componente | Sim (2x) |

---

## Configuration

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `GCP_PROJECT_ID` | string | `spepe-dev` | GCP project ID por ambiente |
| `GCS_BUCKET` | string | `spepe-dev-data` | Bucket Bronze + Silver Parquet |
| `BIGQUERY_DATASET_SILVER` | string | `spepe_silver` | Dataset Silver no BigQuery |
| `BIGQUERY_DATASET_GOLD` | string | `spepe_gold` | Dataset Gold no BigQuery |
| `VERTEX_LOCATION` | string | `us-central1` | Vertex AI region (≠ southamerica-east1) |
| `MAX_BUDGET_USD` | float | `2.0` | Budget guard por sessão |
| `HDBSCAN_MIN_CLUSTER_SIZE` | int | `30` | Tamanho mínimo de cluster (municípios) |
| `HDBSCAN_MIN_SAMPLES` | int | `5` | Amostras mínimas HDBSCAN |
| `N_BOOTSTRAP` | int | `1000` | Iterações bootstrap para IC |
| `BOOTSTRAP_ALPHA` | float | `0.05` | Nível de significância IC (95%) |
| `SILHOUETTE_THRESHOLD` | float | `0.45` | Gate de qualidade clustering |
| `DQ_SCORE_THRESHOLD` | float | `0.95` | Gate DQ Silver (Great Expectations) |
| `DRIFT_THRESHOLD` | float | `0.10` | Threshold drift para alertas MLOps |
| `LLMOPS_EVAL_SCORE_MIN` | float | `0.85` | Score mínimo LLM-eval para CI pass |
| `DEFAULT_UF` | string | `SP` | UF default para MVP |
| `LGPD_MIN_AGGREGATE_LEVEL` | string | `municipio` | Granularidade mínima para dados digitais |

---

## Governance

### Classificação de Dados

| Classe | Exemplos no SPEPE | Retenção | Acesso |
|--------|-------------------|----------|--------|
| **Público** | Resultados TSE, indicadores IBGE agregados | 4 anos (ciclo eleitoral) | spepe.viewer+ |
| **Interno** | fato_social município×semana, arquétipos | 2 anos | spepe.analyst+ |
| **Restrito** | `house_effect_adj`, `record_confidence_score`, `fact_predictions` | 2 anos | spepe.admin |
| **Sensível** | Qualquer dado individual (bloqueado por DLP) | Nunca armazena | Proibido |

### Papéis de Data Steward

| Domínio | Steward | Responsabilidades |
|---------|---------|-------------------|
| Eleições (TSE) | Admin do projeto | Aprovar schema changes, validar ciclos eleitorais |
| Pesquisas | Analista sênior | Validar house effect, aprovar novos institutos |
| Social | Analista sênior | Validar agregações LGPD, aprovar novas fontes |
| IBGE / Estrutural | Admin do projeto | Validar joins, aprovar novos indicadores |

### Política de Retenção

| Camada | Período | Mecanismo |
|--------|---------|-----------|
| Bronze (GCS) | Indefinido — dados históricos eleitorais são permanentes | GCS Lifecycle: nenhuma deleção automática |
| Silver (BigQuery) | 90 dias por partição | BigQuery partition expiry |
| Gold (BigQuery) | 4 anos (1 ciclo eleitoral completo) | BigQuery partition expiry |
| MLOps (fact_predictions, bias_metrics) | 2 anos | BigQuery partition expiry |
| Logs Cloud Logging | 90 dias | Log Sink retention |
| Sessões Firestore | 30 dias | Firestore TTL field |

### Processo de Schema Change

```
PR com mudança de schema
  → atualiza contrato YAML correspondente (obrigatório)
  → CI valida contrato vs. schema proposto
  → notifica consumidores registrados no contrato
  → aprovação do steward do domínio
  → merge → deploy com backward-compat por 1 sprint (soft deprecation)
```

---

## Data Contracts

### Contrato Bronze → Silver

```yaml
# dataops/contracts/bronze_to_silver.yaml
contract_version: "1.0"
producer: "tse_ingest_job / ibge_sync_job / digital_ingest_job"
consumer: "silver_transform_job"
sla:
  freshness_max_hours:
    tse: 24
    ibge: 72
    digital: 6
schema_requirements:
  tse:
    required_columns: [cod_municipio_ibge, sg_uf, ano_eleicao, nm_candidato, qt_votos_validos]
    types: {cod_municipio_ibge: int64, qt_votos_validos: int64}
    no_nulls: [cod_municipio_ibge, ano_eleicao]
  digital:
    required_columns: [cod_municipio_ibge, semana_ref, volume_mencoes, fonte]
    lgpd_level: municipio  # nunca individual
quality_gates:
  municipio_coverage_pct: 95   # % municípios esperados por UF
  null_pk_max: 0
```

### Contrato Silver → Gold

```yaml
# dataops/contracts/silver_to_gold.yaml
contract_version: "1.0"
producer: "silver_transform_job"
consumer: "gold_build_job"
gates:
  dq_score_min: 0.95            # Great Expectations suite
  cod_municipio_ibge: required  # chave global obrigatória
  eleicoes_required: [2018, 2022]
  ibge_indicadores_min: 3       # por município
on_violation: block             # bloqueia build Gold
```

### Contrato Gold → Modelo

```yaml
# dataops/contracts/gold_to_model.yaml
contract_version: "1.0"
producer: "gold_build_job"
consumer: "mlops/vertex_pipeline"
required_features:
  - {name: renda_media_domiciliar, type: float64, range: [0, 50000]}
  - {name: pct_zona_rural, type: float64, range: [0, 1]}
  - {name: taxa_desemprego, type: float64, range: [0, 1]}
  - {name: resultado_2018, type: float64, range: [0, 1]}
  - {name: resultado_2022, type: float64, range: [0, 1]}
pesquisas_filter:
  record_confidence_score_min: 0.80   # só dados de alta confiança
```

### Contrato Gold → API / Dashboard

```yaml
# dataops/contracts/gold_to_api.yaml
contract_version: "1.0"
producer: "gold_build_job"
consumer: "dashboard_api / chainlit_app"
sla:
  freshness_max_hours: 2        # Gold deve estar atualizado em 2h
  api_p99_ms: 3000
public_columns:                 # disponíveis para spepe.viewer
  - cod_municipio_ibge
  - sg_uf
  - nm_municipio
  - resultado_2022
  - resultado_2018
restricted_columns:             # somente spepe.admin
  - house_effect_adj
  - record_confidence_score
schema_version: semver          # breaking changes exigem major bump
```

---

## Access Control Model

### RBAC — Três Papéis Funcionais

| Papel | Google Group | O que pode fazer |
|-------|-------------|-----------------|
| `spepe.viewer` | spepe-viewers@{dominio} | Lê dashboards, outputs de agentes; sem acesso a dados brutos Gold |
| `spepe.analyst` | spepe-analysts@{dominio} | Executa slash commands; acessa Gold filtrado por UF atribuída; sem colunas restritas |
| `spepe.admin` | spepe-admins@{dominio} | Acesso irrestrito; executa jobs DataOps/MLOps; lê colunas restritas |

### Column-Level Security (BigQuery)

| Coluna | Tabela | Nível mínimo |
|--------|--------|-------------|
| `house_effect_adj` | `fact_pesquisa` | spepe.admin |
| `record_confidence_score` | `fact_pesquisa` | spepe.admin |
| `p_mean`, `p_lower`, `p_upper` | `fact_predictions` | spepe.admin |
| `bias_score_by_group` | `bias_metrics` | spepe.admin |
| Todos os outros campos Gold | `fact_municipio_eleicao`, `fato_social` | spepe.analyst |

### Row-Level Security (BigQuery Row Access Policies)

```sql
-- security/row_access_policies.sql
-- Analistas veem apenas a UF atribuída ao seu perfil
CREATE OR REPLACE ROW ACCESS POLICY rls_analyst_uf
ON spepe_gold.fact_municipio_eleicao
GRANT TO ("group:spepe-analysts@dominio.com")
FILTER USING (sg_uf IN UNNEST(SESSION_USER_ATTRIBUTES('uf_filter')));

-- Admins e viewers (via dashboards agregados) veem tudo
CREATE OR REPLACE ROW ACCESS POLICY rls_admin_full
ON spepe_gold.fact_municipio_eleicao
GRANT TO ("group:spepe-admins@dominio.com")
FILTER USING (TRUE);
```

### Auditoria de Acesso

- BigQuery Audit Logs habilitado: registra todo `jobCompleted` com `userEmail`, `query`, `tablesAccessed`, `bytesProcessed`
- Log Sink → Cloud Logging → export para bucket de auditoria (retenção 2 anos)
- Alerta: acesso a colunas restritas por spepe.analyst → Cloud Alerting imediato

---

## Política de Disclaimer — Obrigatório em Todos os Outputs

### Princípio

> **Todo output do SPEPE que contenha dado eleitoral, probabilidade, análise ou previsão DEVE incluir disclaimer explícito. Outputs sem disclaimer são bloqueados pelo `disclaimer_hook.py` antes de chegar ao usuário.**

`disclaimer_present` não é métrica de qualidade — é gate binário. Score de eval ≥ 0.85 sem disclaimer = output bloqueado.

### Triggers de Disclaimer (quando é obrigatório)

| Condição no output | Exemplos | Disclaimer obrigatório |
|---|---|---|
| Contém percentual eleitoral | "Lula 44%", "43,2% dos votos" | Tipo A — Previsão |
| Contém IC ou probabilidade | "P(X)=31% [IC 95%: 24–39%]" | Tipo A — Previsão |
| Contém análise socioeconômica | "IDHM 0.783, renda R$3.872" | Tipo B — Dados |
| Contém resultado de pesquisa | "Instituto X aponta 38%" | Tipo C — Pesquisa |
| Contém SHAP / features | "Renda média: +0.42 ↑" | Tipo B — Dados |
| Contém comparação histórica | "Em 2022, o padrão foi..." | Tipo B — Dados |
| Contém recomendação de ação | "Recomenda-se foco em..." | Tipo D — Recomendação |

### Templates de Disclaimer por Tipo

```yaml
# security/disclaimer_templates.yaml

tipo_a_previsao: |
  ⚠️ DISCLAIMER — Previsão Eleitoral
  Esta análise é baseada em modelos estatísticos com dados históricos (TSE 2018/2022),
  indicadores socioeconômicos (IBGE) e sinal digital agregado. Resultados são
  probabilísticos — o intervalo de confiança de 95% não garante certeza do resultado.
  Eleições são eventos complexos sujeitos a fatores não capturados pelo modelo.
  Uso para fins de estratégia e análise; não constitui afirmação de resultado.

tipo_b_dados: |
  ℹ️ DISCLAIMER — Dados Eleitorais e Socioeconômicos
  Dados eleitorais: TSE (repositório oficial). Dados socioeconômicos: IBGE SIDRA e
  Censo 2022. Todos os dados são agregados ao nível de município ou superior — nenhuma
  informação individual de eleitores é processada ou exibida (LGPD).

tipo_c_pesquisa: |
  📊 DISCLAIMER — Pesquisas Eleitorais
  Pesquisas exibidas são registradas no TSE PesqEle (sistema oficial obrigatório).
  O SPEPE aplica house effect adjustment por instituto com base em acerto histórico.
  Margem de erro e metodologia originais da pesquisa devem ser consultadas no TSE.
  record_confidence_score < 0.80 indica dado com menor confiabilidade.

tipo_d_recomendacao: |
  🔍 DISCLAIMER — Análise Estratégica
  Esta análise é produzida por sistema de IA e deve ser interpretada por profissionais
  qualificados. Não substitui julgamento humano especializado em ciência política,
  estratégia eleitoral ou consultoria jurídica. O sistema não tem acesso a informações
  confidenciais de campanha.
```

### Enforcement — 3 Camadas

```text
CAMADA 1 — Prompt (prevenção)
  Todos os prompts em agents/registry/*.md incluem instrução:
  "OBRIGATÓRIO: inclua o disclaimer do tipo [X] ao final de todo output
   que contenha [condição]. O output sem disclaimer será bloqueado."

CAMADA 2 — Hook (detecção e bloqueio)
  disclaimer_hook.py executa APÓS geração, ANTES de entregar ao usuário:
    ├── detecta tipo de output (regex + keyword matching)
    ├── verifica presença de disclaimer correspondente
    ├── BLOQUEIA se ausente → força re-geração com instrução explícita
    └── REGISTRA: {agente, tipo, presente/ausente} em Cloud Logging

CAMADA 3 — Eval (auditoria contínua)
  continuous_eval.py (5% sampling produção):
    ├── disclaimer_present_rate deve ser 100% por agente
    ├── alerta P1 se qualquer agente < 100% em janela de 24h
    └── ML Judge verifica disclaimer_rate nos pareceres de auditoria
```

### disclaimer_hook.py — Lógica de Detecção

```python
import re
from pathlib import Path
import yaml

DISCLAIMER_TEMPLATES = yaml.safe_load(
    (Path(__file__).parent.parent / "security" / "disclaimer_templates.yaml").read_text()
)

TRIGGERS = {
    "tipo_a_previsao": re.compile(
        r"P\(|IC\s*9[05]%|\d+[\.,]\d+\s*%.*(?:voto|eleição|candidato|turno)|"
        r"(?:probabilidade|chance|previsão)\s+de\s+\d", re.IGNORECASE
    ),
    "tipo_b_dados": re.compile(
        r"IDHM|renda\s+média|IBGE|SHAP|resultado\s+(?:de\s+)?20(?:18|22)|"
        r"municípios?\s+com", re.IGNORECASE
    ),
    "tipo_c_pesquisa": re.compile(
        r"pesquisa|instituto\s+\w+|PesqEle|margem\s+de\s+erro|"
        r"intenção\s+de\s+voto", re.IGNORECASE
    ),
    "tipo_d_recomendacao": re.compile(
        r"recomend[ao]|sugir[oa]|estratégi[ao]|foco\s+em|priorizar", re.IGNORECASE
    ),
}

def disclaimer_hook(output: str, agent_name: str) -> tuple[str, bool]:
    """Retorna (output_final, foi_modificado). Injeta disclaimer se ausente."""
    required_types = [t for t, pattern in TRIGGERS.items() if pattern.search(output)]
    if not required_types:
        return output, False

    missing = [t for t in required_types if DISCLAIMER_TEMPLATES[t][:30] not in output]
    if not missing:
        return output, False

    disclaimer_block = "\n\n---\n" + "\n\n".join(DISCLAIMER_TEMPLATES[t] for t in missing)
    return output + disclaimer_block, True
```

### Atualização dos Arquivos Existentes

| Arquivo | Mudança necessária |
|---------|-------------------|
| `hooks/disclaimer_hook.py` | **NOVO** — implementação acima |
| `security/disclaimer_templates.yaml` | **NOVO** — 4 templates |
| `agents/registry/*.md` | **UPDATE** — adicionar instrução de disclaimer em todos os 7 prompts |
| `security/output_validators.py` | **UPDATE** — `disclaimer_present` como validação hard (não soft) |
| `llmops/eval/metrics.py` | **UPDATE** — `disclaimer_present_rate` = gate binário, não só métrica |
| `judge/technical_report.py` | **UPDATE** — audita `disclaimer_rate` dos outputs; Reprovado se < 100% |
| `mlops/eval/golden_dataset.jsonl` | **UPDATE** — todos os 50 casos de teste devem ter expected_disclaimer |

---

## Security Considerations

- **LGPD — aggregate-only**: Todo dado de sinal digital (redes sociais, trends) deve ser agregado ao nível mínimo de município antes de qualquer output. Hook DLP bloqueia CPF, nome + data de nascimento, e-mail individual.
- **IAP authentication**: Cloud Run exposto apenas via Identity-Aware Proxy — sem acesso público. Todos os usuários devem ter conta Google autorizada no projeto GCP.
- **Secret Manager**: Zero secrets em variáveis de ambiente de produção. `ANTHROPIC_API_KEY`, `YOUTUBE_API_KEY`, `META_APP_TOKEN` obrigatoriamente via Secret Manager.
- **IAM least-privilege**: Cada service account tem apenas as permissões necessárias. Cloud Run SA: BigQuery Data Viewer + GCS Object Viewer. Jobs DataOps SA: BigQuery Data Editor + GCS Object Admin.
- **Cloud Armor WAF**: Rate limiting (100 req/min por IP), geo-restriction (Brasil + VPN conhecidas), SQL injection patterns bloqueados.
- **VPC Service Controls**: BigQuery e GCS dentro do perímetro VPC — acesso apenas de IPs autorizados e service accounts do projeto.
- **Audit logging**: Todos os eventos de agente (tool calls, outputs) registrados em Cloud Logging com estrutura JSON. Retenção 90 dias.
- **Data locality**: Todos os dados em `southamerica-east1` (BigQuery, GCS, Firestore). Vertex AI usa `us-central1` apenas para compute (dados não saem do projeto).
- **security_hook.py**: Bloqueia DDL (DROP/TRUNCATE/DELETE), queries BigQuery sem filtro de partição (custo proteção), SQL injection patterns adaptados para BigQuery.

---

## Observability

### Logging

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `session_id` | string | ID único por sessão Chainlit |
| `agent_name` | string | coletor / analista / modelista / ... |
| `token_count` | int | tokens consumidos na chamada |
| `cost_usd` | float | custo estimado da chamada |
| `duration_ms` | int | latência end-to-end |
| `job_id` | string | ID do Cloud Run Job (DataOps) |
| `contract_version` | string | versão do contrato validado |
| `dq_score` | float | score DQ da run (se DataOps) |

### SLOs / SLAs

| SLO | Target | Métrica | Alerta se |
|-----|--------|---------|-----------|
| Gold freshness | < 2h após Bronze | `MAX(timestamp)` por tabela Gold | > 2h sem atualização |
| DQ Silver | ≥ 95% score | Great Expectations suite result | < 95% |
| API response p99 | < 3s | Cloud Trace latência | > 3s por 5 min |
| Model Brier score | < 0.25 | `spepe_mlops.model_evaluations` | > 0.25 em avaliação |
| Uptime Chainlit | ≥ 99.5% | Cloud Run request success rate | < 99.5% em 1h |
| Budget mensal | ≤ $50 GCP | Cloud Billing | 50% / 90% / 100% |

### Pipeline Health Endpoint

`/health` retorna status real dos componentes (não apenas "up"):

```json
{
  "status": "healthy | degraded | down",
  "components": {
    "gold_freshness": {"status": "ok", "last_update": "2026-04-23T14:00Z", "lag_minutes": 45},
    "dq_silver": {"status": "ok", "score": 0.973, "last_run": "2026-04-23T13:30Z"},
    "model_champion": {"status": "ok", "version": "v2.3", "brier": 0.187},
    "drift_monitor": {"status": "ok", "last_js_score": 0.042},
    "budget": {"status": "ok", "spent_usd": 23.40, "cap_usd": 50.00}
  }
}
```

### Alertas Completos

| Condição | Severidade | Canal |
|----------|-----------|-------|
| DQ score < 95% | P1 | PagerDuty + Slack |
| Gold freshness > 2h | P2 | Slack |
| Drift > 10% | P2 | Slack + auto-retrain |
| Brier score > champion × 1.05 | P1 | PagerDuty + auto-rollback |
| Custo sessão > $1.50 | P3 | Log only |
| Erros > 5/min em Cloud Run | P2 | Slack |
| Acesso coluna restrita por analyst | P1 | PagerDuty + audit log |
| Budget GCP > 90% | P2 | Email + Slack |

### Dashboards Looker Studio

| Dashboard | Audiência | Métricas principais |
|-----------|-----------|---------------------|
| DataOps Health | Admins | DQ score, freshness por tabela, rows ingeridas, contratos violados |
| MLOps Performance | Admins | Brier score, drift, champion/challenger, HP tuning history |
| LLMOps Usage | Admins | tokens/sessão, custo, eval score, disclaimer rate |
| SLO Board | Todos | SLO status atual, error budget restante, incidentes abertos |

---

## Memória Vetorial de Longo Prazo

### Arquitetura

```text
FLUXO DE MEMÓRIA (por sessão)

Pré-sessão:
  retriever.py
    ├── embedding da query atual (Gecko 768d)
    ├── Vertex AI Vector Search ANN (K=5, cosine ≥ 0.75)
    └── injeta memórias recuperadas no contexto do agente
          "Memórias relevantes: [análise SP 2022, padrão polarização...]"

Durante sessão: agente responde com contexto enriquecido

Pós-sessão:
  session_memory.py
    ├── seleciona outputs com valor analítico (não respostas triviais)
    ├── classifica tipo: análise / padrão_eleitoral / alerta / decisão / político
    ├── calcula embedding Gecko 768d
    └── indexa no Vertex AI Vector Search com metadata:
          {agent_name, session_id, timestamp, uf, cargo, tipo, conteúdo}
```

### Tipos de Memória e Casos de Uso

| Tipo | Exemplo | Benefício |
|------|---------|-----------|
| `análise` | "SP 2022: polarização extrema, diff 0.8pp — Lula × Bolsonaro" | Contexto imediato para `/prever SP 2026` |
| `padrão_eleitoral` | "Interior paulista: voto rural tende +15pp direita vs. capital" | Melhora profundidade analítica do Analista |
| `alerta` | "Burst social de 3σ em GO precedeu drift de 12% no modelo" | Sentinel correlaciona com eventos futuros |
| `decisão_modelo` | "Shadow mode: challenger reprovado p=0.12 em 2026-03" | Evita reprocessar análise já feita |
| `contexto_político` | "Pré-eleição 2026: cenário de 3 candidatos competitivos" | Enriquece previsões do Modelista |

### Configuração

```yaml
# memory_store/memory_index_config.yaml
vertex_vector_search:
  location: us-central1
  dimensions: 768
  algorithm: ScaNN
  distance_measure: COSINE
  approximate_neighbors_count: 100
  leaf_node_embedding_count: 500

retrieval:
  k: 5
  min_similarity: 0.75
  max_age_days: 365

namespaces:
  - coletor
  - analista
  - modelista
  - perfilador
  - sentinel
  - global    # compartilhada entre todos os agentes
```

---

## ML Judge — Agente Auditor Independente

### Princípio de Isolamento

```text
╔══════════════════════════════════════════════════════════════════════╗
║  ML JUDGE — Zona de Isolamento                                       ║
║                                                                      ║
║  Modelo:   Gemini 2.5 Pro (≠ Claude dos agentes analíticos)         ║
║  Acesso:   somente leitura em spepe_mlops.* (sem spepe_gold)        ║
║  Código:   judge/ sem imports de mlops/ ou agents/                   ║
║  Ativação: automática antes de TODA promoção champion/challenger     ║
║  Output:   parecer técnico formal arquivado em audit_reports         ║
╚══════════════════════════════════════════════════════════════════════╝
```

### Pipeline de Auditoria

```text
promote.py ativa promotion_gate.py
  │
  ▼
ML Judge (judge/ml_judge.py — Gemini 2.5 Pro)
  │
  ├── independent_backtester.py
  │     ├── carrega fact_predictions (shadow=true, últimos 7 dias)
  │     ├── cruza com ground truth TSE (eleições passadas)
  │     └── calcula: Brier score, calibration error, IC coverage
  │
  ├── fairness_auditor.py
  │     ├── Equalized Odds por quintil de renda
  │     ├── Equalized Odds por sg_uf (27 grupos)
  │     └── Equalized Odds por pct_zona_rural > 50%
  │
  └── GenAI Interpretation (Gemini 2.5 Pro)
        ├── analisa métricas + achados
        ├── compara com thresholds do judge_config.yaml
        └── gera recomendação: Aprovado / Aprovado com ressalvas / Reprovado
  │
  ▼
technical_report.py → Parecer Técnico (Markdown + PDF)
  │
  ├── APROVADO → promoção liberada + parecer arquivado
  ├── APROVADO COM RESSALVAS → promoção liberada + alerta + revisão humana 30 dias
  └── REPROVADO → promoção bloqueada + notificação P1 + modelo descartado
```

### Estrutura do Parecer Técnico

```markdown
# Parecer Técnico — Auditoria de Modelo SPEPE
**Modelo auditado:** v{version} | **Data:** {date} | **Auditor:** ML Judge (Gemini 2.5 Pro)
**Recomendação:** APROVADO / APROVADO COM RESSALVAS / REPROVADO

## 1. Metodologia
- Backtest independente: {N} predições vs. ground truth TSE {eleições}
- Fairness: Equalized Odds por {grupos avaliados}
- Thresholds aplicados: Brier ≤ 0.25, calibration error ≤ 0.05, fairness gap ≤ 15%

## 2. Métricas Auditadas
| Métrica | Valor | Threshold | Status |
|---------|-------|-----------|--------|
| Brier Score | {X} | ≤ 0.25 | ✓ / ✗ |
| Calibration Error | {X} | ≤ 0.05 | ✓ / ✗ |
| IC 95% Coverage | {X} | ≥ 0.93 | ✓ / ✗ |
| Fairness Gap máx | {X} | ≤ 15% | ✓ / ✗ |

## 3. Achados
{texto gerado pelo Gemini 2.5 Pro com análise dos resultados}

## 4. Limitações
{limitações do backtest: tamanho amostral, janela temporal, etc.}

## 5. Recomendação Final
{APROVADO | APROVADO COM RESSALVAS | REPROVADO} — {justificativa}
```

---

## Maturidade Nível 5 — DataOps / MLOps / LLMOps

### Definição de Nível 5 por Domínio

| Nível | DataOps | MLOps | LLMOps |
|-------|---------|-------|--------|
| L1 | Manual, sem monitoramento | Treino manual, sem métricas | Prompts ad-hoc, sem versão |
| L2 | Pipelines automatizados, logging básico | Treino automatizado, métricas básicas | Templates de prompt, logging básico |
| L3 | DQ gates, lineage, scheduling | CI/CD para modelos, canary | Registry de prompts, eval em CI |
| L4 | Data contracts, observabilidade, self-service | Auto-retrain por drift, prediction store, bias | Eval contínuo, tracing, cost attribution parcial |
| **L5** | **CDC incremental, self-healing, data versioning, DQ real-time, data mesh, cost optimization** | **Experiment tracking, feature store online, shadow mode, significância estatística, treino contínuo** | **Semantic cache, eval produção 5%, hallucination detection, prompt A/B, context management, output drift** |

### DataOps L5 — Capacidades Implementadas

```text
PIPELINE SELF-HEALING (dataops/healing/)
  Bronze chega (GCS event → Pub/Sub bronze-new-file)
    │
    ▼
  incremental_loader.py
    ├── detecta delta (updated_at ou hash)
    ├── carrega apenas novos registros
    └── fallback: full-refresh se fonte sem timestamp
    │
    ▼
  auto_profiler.py → perfil automático: distribuição, nulls, outliers
    │
    ▼
  realtime_dq.py (Dataflow streaming) → alerta em < 60s
    │
  se falha → pipeline_healer.py
      ├── schema drift → schema_evolver.py (backward-compatible)
      ├── null explosion → quarentena + alerta
      └── arquivo corrompido → reprocessa última versão válida
    │
    ▼
  snapshot_manager.py → BigQuery Table Snapshot (Gold versioned, 7 dias)
    │
  slot_optimizer.py → recomenda slots, bloqueia queries sem partition filter
```

### MLOps L5 — Capacidades Implementadas

```text
CONTINUOUS TRAINING LOOP
  Gatilhos paralelos:
    ├── drift_detected (JS > 0.10) → retrain
    ├── new Gold snapshot (data-triggered) → retrain
    └── schedule semanal (garantia mínima)
          │
          ▼
  experiment_tracker.py → Vertex AI Experiments (params + métricas + artifacts)
          │
          ▼
  hptuning_component → otimiza hiperparâmetros
          │
          ▼
  train_bootstrap + continuous_train (feature store online)
          │
  ┌───── SHADOW MODE (7 dias) ─────────────────────────┐
  │  champion: serve usuário                            │
  │  challenger: roda em paralelo, grava shadow=true   │
  └─────────────────────────────────────────────────────┘
          │
  significance_tester.py → McNemar + permutation (p < 0.05)
          │
  promote (se p < 0.05 e Brier melhor) OU discard
          │
  auto_updater.py → model_card.md atualizado automaticamente
```

### LLMOps L5 — Capacidades Implementadas

```text
REQUEST LIFECYCLE COM L5
  Usuário envia query
    │
    ▼
  context_manager.py → verifica fill rate; se > 80% → sumariza sessão
    │
    ▼
  semantic_cache.py → embedding da query → cosine sim > 0.95?
    ├── HIT → retorna resposta cached (sem chamar LLM) → ~10ms
    └── MISS → continua para LLM
          │
          ▼
  prompt_ab_test.py → versão A ou B (50/50 por sessão) → chama LLM
          │
          ▼
  LLM (Claude Sonnet / Gemini)
          │
          ▼
  hallucination_detector.py → verifica claims numéricos vs. Gold
    ├── OK → output → usuário
    └── DIVERGÊNCIA > 5pp → bloqueia + reformula com dado correto
          │
          ▼
  cost_attributor.py → registra: agente, tokens, custo, latência
          │
  continuous_eval.py → amostra 5% → roda métricas → alerta se < 0.85
  output_drift.py → monitora distribuição disclaimer_rate, confidence, tokens
```

### Pattern 8: Semantic Cache (Redis + Embeddings)

```python
import hashlib
import numpy as np
import redis
from anthropic import Anthropic

class SemanticCache:
    def __init__(self, redis_url: str, similarity_threshold: float = 0.95):
        self.r = redis.from_url(redis_url)
        self.threshold = similarity_threshold
        self.client = Anthropic()

    def _embed(self, text: str) -> np.ndarray:
        # Usa embeddings do modelo — substituir por Vertex AI Embeddings em produção
        import hashlib
        seed = int(hashlib.md5(text.encode()).hexdigest(), 16) % (2**32)
        rng = np.random.default_rng(seed)
        return rng.standard_normal(1536)  # placeholder; use real embeddings

    def get_or_compute(self, query: str, compute_fn) -> tuple[str, bool]:
        q_emb = self._embed(query)
        for key in self.r.scan_iter("cache:emb:*"):
            cached_emb = np.frombuffer(self.r.get(key), dtype=np.float32)
            sim = np.dot(q_emb, cached_emb) / (np.linalg.norm(q_emb) * np.linalg.norm(cached_emb))
            if sim > self.threshold:
                response_key = key.decode().replace("emb:", "resp:")
                return self.r.get(response_key).decode(), True  # cache hit

        response = compute_fn(query)
        key_id = hashlib.sha256(query.encode()).hexdigest()[:16]
        self.r.setex(f"cache:emb:{key_id}", 86400, q_emb.astype(np.float32).tobytes())
        self.r.setex(f"cache:resp:{key_id}", 86400, response.encode())
        return response, False  # cache miss
```

### Pattern 9: Hallucination Detector

```python
from google.cloud import bigquery
import re

def check_electoral_claims(output: str, project_id: str) -> list[dict]:
    client = bigquery.Client(project=project_id)
    violations = []

    # Detecta padrão: "Candidato X% em UF"
    pattern = re.compile(r"(\w+)\s+(\d+[\.,]\d+)%\s+em\s+([A-Z]{2})", re.IGNORECASE)
    for match in pattern.finditer(output):
        candidato, pct_str, uf = match.group(1), match.group(2), match.group(3)
        pct_claimed = float(pct_str.replace(",", "."))

        row = client.query(f"""
            SELECT pct_votos_validos FROM `{project_id}.spepe_gold.fact_municipio_eleicao`
            WHERE LOWER(nm_candidato) LIKE LOWER('%{candidato}%')
              AND sg_uf = '{uf}' AND ano_eleicao = 2022
            LIMIT 1
        """).result()

        for r in row:
            diff = abs(r.pct_votos_validos - pct_claimed)
            if diff > 5.0:
                violations.append({
                    "claim": match.group(0),
                    "claimed": pct_claimed,
                    "actual": r.pct_votos_validos,
                    "diff_pp": diff,
                })

    return violations
```

### Pattern 10: Significance Tester (McNemar)

```python
import numpy as np
from scipy.stats import binom_test
from dataclasses import dataclass

@dataclass
class SignificanceResult:
    promote: bool
    p_value: float
    champion_brier: float
    challenger_brier: float
    n_samples: int

def mcnemar_significance(
    champion_preds: np.ndarray,
    challenger_preds: np.ndarray,
    ground_truth: np.ndarray,
    alpha: float = 0.05,
) -> SignificanceResult:
    champ_correct = (champion_preds.round() == ground_truth)
    chall_correct = (challenger_preds.round() == ground_truth)

    # McNemar: casos onde um acerta e outro erra
    b = ((champ_correct == 1) & (chall_correct == 0)).sum()  # champ wins
    c = ((champ_correct == 0) & (chall_correct == 1)).sum()  # chall wins

    p_value = float(binom_test(c, b + c, 0.5))

    champ_brier = float(np.mean((champion_preds - ground_truth) ** 2))
    chall_brier = float(np.mean((challenger_preds - ground_truth) ** 2))

    return SignificanceResult(
        promote=(p_value < alpha and chall_brier < champ_brier),
        p_value=p_value,
        champion_brier=champ_brier,
        challenger_brier=chall_brier,
        n_samples=len(ground_truth),
    )
```

---

## Sentinel Architecture

> "A Fleet of AI Agents. One Intelligent Crew."
> O Sentinel é ortogonal ao Supervisor — enquanto o Supervisor serve análises ao usuário, o Sentinel monitora o sistema 24/7 de forma autônoma.

### Visão Geral

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SENTINEL — Orquestrador de Monitoramento                 │
│                         Event-driven · Autonomous · KB-powered              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  FONTES DE EVENTOS (Pub/Sub → sentinel-events)                              │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐  │
│  │ DQ gate    │ │ Contract   │ │ Drift /    │ │ Freshness  │ │ Social   │  │
│  │ violation  │ │ breach     │ │ Brier deg. │ │ SLO breach │ │ burst    │  │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └────┬─────┘  │
│        └──────────────┴──────────────┴──────────────┴─────────────┘        │
│                                      ▼                                      │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    ORCHESTRATOR (sentinel/orchestrator.py)            │  │
│  │              Claude Sonnet · event router · crew coordinator         │  │
│  └──────────────────────┬────────────────────────────────────────────────┘  │
│                          │ roteia por tipo de evento                        │
│          ┌───────────────┼───────────────┬─────────────────┐               │
│          ▼               ▼               ▼                 ▼               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐  │
│  │  CREW 1      │ │  CREW 2      │ │  CREW 3      │ │  CREW 4          │  │
│  │ Observadores │ │ Analisadores │ │Interpretadores│ │  Despachantes    │  │
│  │              │ │              │ │              │ │                  │  │
│  │ DataOps Wtch │ │ Pattern Det. │ │ Context Bld. │ │ Reporter         │  │
│  │ MLOps Watcher│ │ Anomaly Det. │ │ GenAI Interp │ │ Dispatcher       │  │
│  │ Infra Watcher│ │ Cross-domain │ │ KB Updater   │ │ Action Executor  │  │
│  │ Social Watch │ │ correlation  │ │ (Firestore)  │ │                  │  │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └────────┬─────────┘  │
│         └────────────────┴────────────────┘                  │            │
│                                                               ▼            │
│                                              ┌────────────────────────────┐ │
│                                              │  OUTPUT                    │ │
│                                              │  Slack · Cloud Logging     │ │
│                                              │  sentinel-alerts (Pub/Sub) │ │
│                                              │  Dashboard SLO Board       │ │
│                                              │  Auto-action (retrain /    │ │
│                                              │  rollback / pipeline block)│ │
│                                              └────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tipos de Evento e Routing

| Evento | Origem | Crews acionadas | Ação autônoma possível |
|--------|--------|-----------------|------------------------|
| `dq_violation` | DQ runner | Observadores + Analisadores + Interpretadores + Despachantes | Bloqueia Gold build |
| `contract_breach` | contract_validator | Observadores + Analisadores + Despachantes | Bloqueia promoção de camada |
| `drift_detected` | drift_monitor | Todas (correlaciona com social_burst) | Dispara retrain pipeline |
| `bias_alert` | bias_monitor | Analisadores + Interpretadores + Despachantes | Alerta P1 — sem ação auto |
| `freshness_slo_breach` | freshness_monitor | Observadores + Despachantes | Escala Cloud Run job |
| `budget_warning` | Cloud Billing | Observadores + Despachantes | Throttle sessões ativas |
| `social_burst` | social_watcher | Analisadores + Interpretadores | Aumenta frequência de coleta |
| `pipeline_failure` | Cloud Run exit ≠ 0 | Observadores + Analisadores + Despachantes | Retry automático (max 3x) |
| `canary_degradation` | auto_rollback | Todas | Rollback imediato |

### Crew 3 — Interpretadores: Fluxo GenAI

```text
Evento + Correlações (Crew 1+2)
        │
        ▼
Context Builder
  ├── KB histórica (Firestore): padrões similares anteriores
  ├── Dados recentes: últimas 24h de métricas
  └── Evento atual: tipo, severidade, features afetadas
        │
        ▼
GenAI Interpreter (Claude Sonnet)
  prompt: "Dado este evento [X] correlacionado com [Y] e [Z],
           e padrões históricos [KB], qual a causa raiz provável
           e qual ação recomendada com que nível de confiança?"
        │
        ▼
Resposta estruturada:
  {
    "causa_raiz": "...",
    "confiança": 0.87,
    "ação_recomendada": "...",
    "severidade": "P1 | P2 | P3",
    "referências_kb": ["incidente_2026-03-15", ...],
    "requer_humano": true | false
  }
        │
        ▼
KB Updater → persiste padrão + ação + outcome para aprendizado futuro
```

### Knowledge Base (Firestore `sentinel_kb`)

```
sentinel_kb/
  incidents/          → histórico de incidentes com causa e resolução
    {incident_id}:
      event_type, timestamp, correlations, cause, action, outcome, resolved_by
  patterns/           → padrões de causa raiz aprendidos
    {pattern_id}:
      trigger_signature, probable_cause, recommended_action, confidence, occurrences
  playbooks/          → ações aprovadas por tipo de evento
    {event_type}:
      auto_actions: [...]      → executadas sem aprovação humana
      suggested_actions: [...] → apresentadas ao operador
      escalation_path: [...]
```

### Correlação Cross-Domain (exemplo real)

```
social_burst detectado (volume_mencoes SP > μ + 3σ)
  │
  ├─ Analisador correlaciona com: nenhum drift recente
  ├─ KB: padrão "burst_sem_drift" = evento político externo (não erro)
  │
  ├─ GenAI Interpreter: "Possível evento político em SP — monitorar por 6h.
  │   Recomendo aumentar frequência de coleta digital de 6h → 1h.
  │   Confiança: 0.82. Não requer ação de modelo."
  │
  └─ Action Executor: aumenta frequência digital_ingest_job para 1h (cooldown 6h)
     Reporter: notifica Slack com contexto eleitoral
```

### Configuração

```yaml
# config/sentinel_config.yaml
sentinel:
  run_mode: event_driven           # Cloud Run always-on + Pub/Sub
  genai_interpreter_model: claude-sonnet-4-6
  kb_collection: sentinel_kb
  cooldowns:                       # evita loops de ação
    retrain: 72h
    rollback: 24h
    pipeline_retry: 30min
    scale_job: 1h
  auto_actions_enabled:
    drift_detected: true
    pipeline_failure: true
    freshness_slo_breach: true
    bias_alert: false              # sempre requer humano
    contract_breach: true
  slack_webhook: ${SLACK_WEBHOOK}  # via Secret Manager
  alert_channel: "#spepe-sentinel"
```

---

## Slash Commands — Mapeamento Agente

| Comando | Agente | Modelo | Descrição |
|---------|--------|--------|-----------|
| `/coletar [uf] [ano]` | Coletor | sonnet-4-6 | Ingesta TSE + IBGE para UF/ano, Bronze→Silver→Gold |
| `/arquétipos [uf\|BR]` | Perfilador | sonnet-4-6 | Clustering HDBSCAN + mapa Folium + fichas |
| `/perfil [município] [ano] [cargo]` | Analista | sonnet-4-6 | Cruzamento socioeconômico × eleitoral |
| `/prever [candidato] [cenário]` | Modelista | sonnet-4-6 | P(X)=N% [IC 95%: A%–B%] + premissas |
| `/explicar` | Explicador | sonnet-4-6 | SHAP top-10 em linguagem natural |
| `/relatorio` | Narrador | haiku-4-5-20251001 | Output técnico → texto para leigos |
| `/health` | Supervisor | opus-4-6 | Status de todos os 7 agentes + GCP services |
| `/plan [objetivo]` | Supervisor | opus-4-6 | DOMA protocol: decompõe e roteia |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-17 | design-agent | MVP local: 4 agentes, DuckDB, sem cloud, sem ML pipeline |
| 2.0 | 2026-04-18 | design-agent | Rewrite completo: 7 agentes, 14 fontes, 3 tabelas Gold ~200 vars, ML 3 estágios, GCP full stack, MLOps/DataOps/LLMOps, LGPD, 116 arquivos |
| 3.0 | 2026-04-18 | iterate-agent | MLOps Nível 5: +6 decisões (drift→auto-retrain, canary, auto-rollback, HP tuning, prediction store, bias monitoring), +17 arquivos (133 total) |
| 4.0 | 2026-04-23 | iterate-agent | 9 decisões arquiteturais: feature matrix integrada (4 dims → modelo único), fato_social (município×semana LGPD-safe), fact_pesquisa central (record_confidence_score), LLM=suporte (ADR-13), nlp_social + pdf_parser components, cod_municipio_ibge como chave global, janela temporal 2018/2022/2026, 2014 auxiliar |
| 4.1 | 2026-04-23 | iterate-agent | 5 lacunas preenchidas: Governança (catálogo, stewards, retenção, schema change), Lineage (column-level, job_tracker), Observabilidade (SLOs, health endpoint, alertas completos, 4 dashboards), Access Control (RBAC 3 papéis, column-level ACL, Row Access Policies BigQuery), Data Contracts (4 contratos YAML ODCS-inspired, contract_validator); +2 ADRs (14, 15); +19 arquivos (152 total) |
| 4.2 | 2026-04-23 | iterate-agent | Sentinel: orquestrador multi-agent autônomo de monitoramento (4 crews: Observadores + Analisadores + Interpretadores + Despachantes), event-driven Pub/Sub, KB Firestore com auto-update, GenAI Interpreter (Claude Sonnet) para causa raiz + ação recomendada, Action Executor autônomo, correlação cross-domain; +1 ADR (16); +25 arquivos (177 total) |
| 4.3 | 2026-04-23 | iterate-agent | DataOps L5 (CDC incremental, self-healing, data versioning, real-time DQ, auto-profiler, slot optimizer, data mesh), MLOps L5 (experiment tracking, feature store online, shadow mode, McNemar significance, continuous training, auto model card), LLMOps L5 (semantic cache Redis, continuous eval 5%, hallucination detector, prompt A/B, context manager, output drift, cost attributor); +3 ADRs (17, 18, 19); +3 code patterns (8, 9, 10); +33 arquivos (210 total) |
| 4.4 | 2026-04-23 | iterate-agent | Memória vetorial de longo prazo (Vertex AI Vector Search 768d, ScaNN, K=5 por sessão, 5 tipos de memória, TTL 1 ano, namespaces por agente); ML Judge auditor independente (Gemini 2.5 Pro, isolamento total, backtest independente, Equalized Odds fairness, parecer técnico formal Aprovado/Reprovado, bloqueio de promoção); +2 ADRs (20, 21); +17 arquivos (227 total) |
| 4.5 | 2026-04-23 | iterate-agent | Disclaimer obrigatório universal: 4 tipos (previsão/dados/pesquisa/recomendação), enforcement em 3 camadas (prompt→hook→eval), disclaimer_hook.py como gate binário bloqueante, disclaimer_templates.yaml, disclaimer_rate = 100% gate no ML Judge; +6 arquivos (233 total) |

---

## Next Step

**Ready for:** `/build .claude/sdd/features/DESIGN_SPEPE.md`
