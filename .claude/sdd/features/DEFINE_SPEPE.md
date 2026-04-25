# DEFINE: SPEPE — Sistema de Perfilamento do Eleitorado e Previsão Eleitoral

> Plataforma real de inteligência eleitoral: pipeline Medallion Bronze→Silver→Gold com 9 fontes de dados, feature matrix integrada com 4 dimensões estruturais (histórico eleitoral + IBGE + sinal digital + pesquisas), modelo bayesiano hierárquico com IC 95%, SHAP explainability, chave global `cod_municipio_ibge`, deploy GCP southamerica-east1 com MLOps/DataOps/LLMOps e compliance LGPD. LLM é suporte analítico — nunca etapa primária de processamento.

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | SPEPE |
| **Date** | 2026-04-17 |
| **Author** | define-agent |
| **Updated** | 2026-04-25 (iterate-agent — v4.1: +4 módulos Group B: DataSUS/DIEESE/CETIC/Segurança; +IBGE expandido 10 domínios; +3 tabelas Gold; +3 Cloud Run Jobs; Source Inventory atualizado; Open Question 2 resolvida) |
| **Status** | Ready for Build |
| **Clarity Score** | 15/15 |

---

## Problem Statement

Analistas eleitorais, pesquisadores, jornalistas e consultores políticos não têm acesso a uma **plataforma integrada de inteligência eleitoral** que: (1) descubra automaticamente os arquétipos sociológicos do eleitorado sem rótulo prévio; (2) cruce perfil socioeconômico com comportamento eleitoral histórico (2018 como baseline, 2022 como referência recente, 2026 como alvo); (3) incorpore sinal digital como dimensão **estrutural** do modelo — não como extra opcional; (4) calibre previsões com pesquisas eleitorais ponderadas por confiança e house effect de instituto; (5) emita previsões probabilísticas com IC 95% e explicabilidade por variável — em linguagem natural, com transparência metodológica e compliance LGPD.

As ferramentas existentes são dashboards estáticos (sem ML), modelos caixa-preta (sem IC) ou pesquisas pagas (sem dados abertos e sem integração com histórico). O SPEPE resolve isso com pipeline Medallion + feature matrix de 4 dimensões + 7 agentes especializados + ML end-to-end — onde LLM é intérprete de outputs, nunca processador de dados.

---

## Target Users

| User | Role | Pain Point |
|------|------|------------|
| Cientista de dados eleitoral | Pesquisador / desenvolvedor | Pipeline reproduzível, controle total, acesso a dados brutos Gold |
| Consultor político | Analista de campanhas | Arquétipos + previsões por UF via slash commands, sem código |
| Jornalista / comunicador | Não-técnico | `/relatorio` sem jargão, mapa visual, resultado claro |
| Cientista político | Acadêmico | API + Jupyter, reprodutibilidade, SHAP para publicação |

---

## Goals

| Priority | Goal |
|----------|------|
| **MUST** | Pipeline Medallion Bronze→Silver→Gold para 13 fontes de dados, 5.570 municípios |
| **MUST** | `fact_municipio_eleicao` com ≥ 200 features por município × eleição (2018/2022) — inclui IBGE expandido + CETIC + DIEESE |
| **MUST** | `fact_saude_municipio` (DataSUS SIM + ANS) e `fact_economico_municipio` (DIEESE + PIB) como tabelas Gold dedicadas |
| **MUST** | `fact_seguranca_municipio` (IVS + Atlas da Violência + SINESP) com agente dedicado `analista_seguranca` |
| **MUST** | `fato_social` como tabela Gold: sinal digital agregado (município × semana, LGPD-safe) |
| **MUST** | `fact_pesquisa` como tabela central: pesquisas com `record_confidence_score` (1.00→0.30) e house effect |
| **MUST** | `cod_municipio_ibge` (7 dígitos IBGE) como chave global em todo o pipeline Gold |
| **MUST** | Feature matrix integrada: histórico + IBGE + sinal digital + pesquisas → modelo bayesiano único |
| **MUST** | `/arquétipos` produz 6–12 clusters estáveis com mapa interativo BR + fichas sociológicas |
| **MUST** | `/prever` emite `P(X) = N% [IC 95%: A%–B%]` com premissas declaradas em < 30s |
| **MUST** | `/explicar` retorna SHAP values top-10 em linguagem natural |
| **MUST** | Sistema inicia com `./start.sh --chainlit` sem erros, todos os 7 agentes respondem |
| **MUST** | Disclaimers eleitorais automáticos em 100% dos outputs de previsão |
| **MUST** | LGPD: dados sempre em nível agregado (município mínimo), nunca individual |
| **MUST** | Deploy GCP Cloud Run southamerica-east1, IAP autenticando, Secret Manager |
| **MUST** | LLM como suporte analítico: pdfplumber/camelot primeiro; LLM só se parser_fail_rate > 30% |
| **SHOULD** | Backtesting documentado: modelo treinado em 2018 vs resultado real 2022 |
| **SHOULD** | CI/CD completo: GitHub Actions + Cloud Build, LLM-eval gates nos prompts |
| **SHOULD** | `nlp_social_component` e `pdf_parser_component` como etapas no Vertex AI Pipeline |
| **COULD** | PyMC para modelo hierárquico real (pós-statsmodels MVP) |
| **COULD** | Jupyter notebook de reprodutibilidade para uso acadêmico |
| **MUST** | **Sentinel**: orquestrador autônomo de monitoramento com 4 crews (Observadores, Analisadores, Interpretadores, Despachantes); event-driven Pub/Sub; KB institucional Firestore; GenAI Interpreter para causa raiz; Action Executor para ações autônomas |
| **MUST** | **Disclaimer enforcement obrigatório**: 3 camadas (prompt → hook → eval); 4 tipos de template (previsão/dados/pesquisa/recomendação); `disclaimer_hook.py` como gate binário bloqueante; `disclaimer_present_rate` = 100% em produção |
| **MUST** | **ML Judge**: agente auditor independente (Gemini 2.5 Pro, isolado de mlops/); backtest independente; Equalized Odds fairness por UF + quintil renda; parecer técnico formal (Aprovado / Aprovado com ressalvas / Reprovado); bloqueia promoção se Reprovado |
| **MUST** | **DataOps Nível 5**: CDC incremental (GCS event-triggered), self-healing (pipeline_healer + schema_evolver), data versioning (BigQuery Table Snapshots), DQ em tempo real (Dataflow streaming < 60s), auto-profiler, slot optimizer, data mesh por domínio |
| **MUST** | **MLOps Nível 5**: experiment tracking (Vertex AI Experiments), feature store online (< 50ms), shadow mode (7 dias sem servir usuário), significância estatística (McNemar p < 0.05), treino contínuo data-triggered, model card auto-atualizado |
| **MUST** | **LLMOps Nível 5**: semantic cache (Redis cosine ≥ 0.95, -40% custo), eval contínuo 5% produção, hallucination detector (claims numéricos vs. Gold, bloqueia se > 5pp), prompt A/B com poder estatístico, context manager (sumariza a 80% fill), output drift monitor |
| **SHOULD** | **Memória vetorial de longo prazo**: Vertex AI Vector Search 768d, K=5 por sessão (cosine ≥ 0.75), 5 tipos de memória (análise/padrão/alerta/decisão/político), TTL 1 ano, namespaces por agente |
| **SHOULD** | **Governança**: catálogo de dados (4 classes: público/interno/restrito/sensível), stewards por domínio, política de retenção por camada, processo de schema change com aprovação |
| **SHOULD** | **Data Contracts**: 4 contratos YAML ODCS-inspired (Bronze→Silver, Silver→Gold, Gold→Modelo, Gold→API), `contract_validator.py` integrado no pipeline, freshness SLAs por contrato |
| **SHOULD** | **RBAC**: 3 papéis (spepe.viewer / spepe.analyst / spepe.admin), column-level ACL BigQuery (house_effect_adj, record_confidence_score = admin only), Row Access Policies por UF para analysts, BigQuery Audit Logs habilitado |

---

## Success Criteria

### Dados e Pipeline
- [ ] `fact_municipio_eleicao` com ≥ 150 features para todos os 5.570 municípios, eleições 2018 e 2022
- [ ] `fato_social` com sinal digital agregado por município × semana, DQ score ≥ 95%
- [ ] `fact_pesquisa` com `record_confidence_score` ≥ 0.80 para ≥ 80% dos registros usados em análises
- [ ] `cod_municipio_ibge` presente e não-nulo em 100% dos registros Gold
- [ ] DQ score ≥ 95% validado pelo Great Expectations em todas as partições Silver
- [ ] Linhagem completa fonte → Bronze → Silver → Gold no Dataplex

### Arquétipos
- [ ] Clusterização produz 6–12 arquétipos com silhouette score ≥ 0.45
- [ ] Mapa interativo BR funcional com Folium, colorido por arquétipo
- [ ] Fichas de cada arquétipo com top-10 features + histórico 2018/2022
- [ ] `/arquétipos BR` retorna mapa + fichas em < 60s

### Previsão e Explicabilidade
- [ ] `/prever` retorna `P(X) = 67% [IC 95%: 58%–74%]` com premissas em < 30s
- [ ] `/explicar` retorna top-10 SHAP values em linguagem natural
- [ ] Backtesting 2018→2022: erro médio documentado (baseline honesto)
- [ ] Disclaimers eleitorais em 100% dos outputs de previsão

### Sentinel e Auditoria
- [ ] Sentinel detecta evento de drift e emite parecer de causa raiz via GenAI Interpreter em < 5 min
- [ ] ML Judge executa automaticamente antes de toda promoção e emite parecer técnico formal arquivado
- [ ] `disclaimer_hook.py` bloqueia 100% dos outputs de previsão sem disclaimer antes de chegar ao usuário
- [ ] `disclaimer_present_rate` = 100% validado pelo `continuous_eval.py` em amostra de produção

### Maturidade L5
- [ ] CDC incremental ativo: apenas novos registros Bronze processados (não full-refresh)
- [ ] Self-healing: pipeline_healer corrige schema drift sem intervenção humana em casos rotineiros
- [ ] BigQuery Table Snapshot gerado por build Gold (rollback de dados disponível em 7 dias)
- [ ] Semantic cache: hit_rate > 30% em sessões típicas (queries similares reutilizadas)
- [ ] Hallucination detector: zero claims numéricos eleitorais divergentes > 5pp em produção
- [ ] Experiment tracking: cada run MLOps registrado no Vertex AI Experiments com params + métricas

### Sistema Conversacional
- [ ] Sistema sobe em < 60s, todos os 7 agentes OK em `/health`
- [ ] Budget guard $2.00/sessão funcional
- [ ] Custo por sessão ≤ USD 0,80
- [ ] `/relatorio` gera texto sem jargão, compreensível por leigo

### Plataforma GCP
- [ ] Cloud Run southamerica-east1 deploy funcional
- [ ] IAP + Secret Manager + Cloud Armor ativos
- [ ] Vertex AI Pipeline de treino end-to-end executando (feature_extract → train → evaluate → promote)
- [ ] Looker Studio com métricas DataOps + MLOps + LLMOps

---

## Acceptance Tests

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AT-001 | Perfil de município existente | Dados TSE 2022 SP carregados | Usuário: `/perfil São Paulo zona 001 2022 presidente` | Retorna breakdown de votos por candidato + 3+ indicadores socioeconômicos do IBGE |
| AT-002 | Previsão com intervalo de confiança | Perfil de AT-001 em contexto | Usuário: `/prever candidato X 2026` | Retorna `P(X) = XX% [IC 95%: a%–b%]` + lista de premissas declaradas |
| AT-003 | Região sem dados disponíveis | Dados TSE SP carregados | Usuário: `/perfil Amazonas zona 005 2022` | Retorna mensagem clara de dado não disponível, sem crash |
| AT-004 | Relatório para não-técnico | Previsão de AT-002 em contexto | Usuário: `/relatorio` | Texto corrido sem fórmulas, com conclusão clara, sem jargão estatístico |
| AT-005 | Disclaimer automático em previsão | Qualquer previsão gerada | Agente emite resultado | Texto inclui disclaimer de sensibilidade política e limitações metodológicas |
| AT-006 | Budget guard ativo | Sessão com múltiplas consultas | Custo acumulado atinge limite configurado | Sistema alerta e interrompe antes de ultrapassar MAX_BUDGET_USD |
| AT-007 | Sinal digital integrado | `fato_social` carregado para SP 2022 | Usuário: `/perfil São Paulo 2022` | Resposta inclui dado de sentimento/volume digital além de TSE+IBGE |
| AT-008 | record_confidence_score aplicado | `fact_pesquisa` com scores mistos | Usuário: `/prever X 2026` | Modelo usa apenas registros com score ≥ 0.80; baixo score mencionado no disclaimer |
| AT-009 | Sentinel correlaciona eventos | drift_detected + social_burst simultâneos em SP | Sentinel recebe ambos eventos via Pub/Sub | GenAI Interpreter emite causa raiz correlacionada em < 5 min; Action Executor inicia retrain |
| AT-010 | ML Judge bloqueia promoção | Challenger com Brier degradado e fairness gap > 15% no Norte | promotion_gate.py ativado | ML Judge emite "Reprovado"; promoção bloqueada; parecer arquivado em spepe_mlops.audit_reports |
| AT-011 | Disclaimer hook bloqueia output | Agente Modelista gera previsão sem disclaimer | Output enviado ao hook | disclaimer_hook.py detecta tipo_a_previsao; injeta template correto; evento logado; usuário recebe output com disclaimer |
| AT-012 | Memória vetorial recupera contexto | Sessão anterior analisou polarização em SP | Nova sessão: `/prever SP 2026` | retriever.py recupera ≥ 1 memória relevante (sim ≥ 0.75); agente usa como contexto sem o usuário re-contextualizar |

---

## Out of Scope

- Previsões em tempo real no dia da eleição — produto comercial futuro
- Interface web standalone além do Chainlit — não necessário para MVP
- Integração com Databricks ou Microsoft Fabric — não aplicável
- Cobertura multi-estado simultânea — após validar 1 UF com sucesso
- Dados eleitorais anteriores a 2018 como features principais (2014 pode ser contexto histórico adicional, não entra na feature matrix)
- Análise individual de eleitores — proibido por LGPD; granularidade mínima: município

---

## Constraints

| Type | Constraint | Impact |
|------|------------|--------|
| Technical | Python 3.12, Claude SDK, Chainlit, GCP como plataforma-alvo | Design deve seguir stack GCP southamerica-east1 |
| Technical | Dados TSE são CSV/zip públicos sem SLA | Coletor resiliente a mudanças de URL e schema |
| Technical | IBGE SIDRA é API pública sem autenticação para indicadores básicos | Cache local obrigatório; backoff exponencial em rate limits |
| Technical | Vertex AI não disponível em southamerica-east1 | Vertex em us-central1; dados em southamerica-east1 |
| **Arquitetural** | **LLM = suporte analítico, nunca etapa primária de processamento** | Parser tradicional (pdfplumber/camelot) obrigatório como 1ª etapa; LLM apenas se parser_fail_rate > 30% |
| **Temporal** | **Janela: 2018 = baseline, 2022 = referência recente, 2026 = alvo** | Features treinadas sobre 2018+2022; 2026 é variável-alvo de previsão |
| **Chave global** | **`cod_municipio_ibge` (7 dígitos IBGE) como âncora territorial obrigatória** | Todo join entre fontes passa pela tabela de-para TSE↔IBGE |
| Legal | Análises preditivas eleitorais têm sensibilidade política | Disclaimers obrigatórios em 100% dos outputs de previsão — enforcement via `disclaimer_hook.py` (gate binário, não soft) |
| **Auditoria** | **Promoção de modelo requer parecer técnico independente** | ML Judge (Gemini 2.5 Pro, isolado) executa antes de toda promoção; Reprovado = bloqueio automático |
| **Monitoramento** | **Sistema deve ser auto-monitorado sem intervenção humana rotineira** | Sentinel (4 crews, event-driven) monitora 24/7; ações autônomas com cooldown configurável |
| **Memória** | **Agentes acumulam inteligência entre sessões** | Vertex AI Vector Search como memória de longo prazo; TTL 1 ano; K=5 por sessão |
| IaC | Terraform completo em `infra/terraform/` — GCP como plataforma | 15 arquivos `.tf` já existentes; bootstrap via `terraform init` |
| Resource | Budget por sessão: $2.00 USD | `cost_guard_hook.py` bloqueia automaticamente |

---

## Technical Context

| Aspect | Value | Notes |
|--------|-------|-------|
| **Deployment** | GCP Cloud Run southamerica-east1 | Chainlit/FastAPI containerizado |
| **ML Platform** | Vertex AI Pipelines (us-central1) | KFP v2; training jobs desacoplados do serving |
| **Data Platform** | BigQuery spepe_silver + spepe_gold | Particionado por `ano_eleicao`; clusterizado por `sg_uf` |
| **IaC** | Terraform completo | `infra/terraform/` — 15 arquivos, backend GCS |
| **Orquestração** | Cloud Scheduler + Workflows | Cloud Composer descartado (custo) |
| **KB Domains** | `electoral`, `bayesian-modeling`, `brazil-geography` | Domínios a manter |

---

## Data Contract

### Source Inventory

| Source | Tipo | Camada | Freshness | Owner |
|--------|------|--------|-----------|-------|
| TSE Repositório de Dados Eleitorais | CSV/zip download | Bronze raw | Anual (por ciclo eleitoral) | TSE (público) |
| TSE PesqEle | CSV bulk + portal | Bronze raw | Contínuo (por registro de pesquisa) | TSE (público/gratuito) |
| IBGE SIDRA | API REST | Bronze raw | Anual (PNAD) / quinquenal (Censo) | IBGE (público) |
| IBGE Censo 2022 | API SIDRA (tabelas 9514, 9543, 9662, 9714, 2094) | Bronze raw | Estático | IBGE (público) |
| IPEADATA | API OData4 | Bronze raw | Anual | IPEA (público) |
| Atlas da Violência (IPEA/FBSP) | CSV download | Bronze raw | Anual | IPEA/FBSP (público) |
| IVS — Índice de Vulnerabilidade Social | API IPEADATA | Bronze raw | Censo | IPEA (público) |
| SINESP / dados.gov.br | CSV download | Bronze raw | Mensal | MJ (público) |
| DataSUS SIM / ANS | IPEADATA API + ANS CSV | Bronze raw | Anual | DataSUS/ANS (público) |
| DIEESE Cesta Básica | IPEADATA API | Bronze raw | Mensal | DIEESE/IPEA (público) |
| CETIC TIC Domicílios | API REST | Bronze raw | Anual | CETIC.br (público) |
| Google Trends (pytrends) | API não-oficial | Bronze raw | Semanal | Google (público) |
| Meta Ad Library | API oficial | Bronze raw | Diário | Meta (público, token necessário) |
| Twitter/X | API v2 (ou scraping) | Bronze raw | Diário | Twitter/X (token necessário) |
| YouTube Data API v3 | API oficial | Bronze raw | Diário | Google (token necessário) |
| Google Alertas | RSS/scraping | Bronze raw | Diário | Google (público) |

### Schema Contract — TSE (eleições, 1º turno)

| Coluna | Tipo | Constraints | PII? |
|--------|------|-------------|------|
| `sg_uf` | VARCHAR(2) | NOT NULL | Não |
| `cd_municipio` | INT | NOT NULL (TSE code — mapear para `cod_municipio_ibge`) | Não |
| `cod_municipio_ibge` | INT(7) | NOT NULL após de-para | Não |
| `nr_zona` | INT | NOT NULL | Não |
| `nr_secao` | INT | NOT NULL | Não |
| `nm_candidato` | VARCHAR | NOT NULL | Não |
| `qt_votos` | INT | NOT NULL, >= 0 | Não |
| `ds_cargo` | VARCHAR | NOT NULL | Não |
| `ano_eleicao` | INT | NOT NULL, IN (2018, 2022) | Não |

### Schema Contract — fato_social (sinal digital agregado)

| Coluna | Tipo | Constraints | Notas |
|--------|------|-------------|-------|
| `cod_municipio_ibge` | INT(7) | NOT NULL, FK | Chave global |
| `sg_uf` | VARCHAR(2) | NOT NULL | |
| `semana_ref` | DATE | NOT NULL | Início da semana ISO |
| `nm_candidato` | VARCHAR | NOT NULL | |
| `volume_mencoes` | INT | >= 0 | Twitter + YouTube agregado |
| `sentimento_score` | FLOAT | -1.0 a 1.0 | NLP agregado |
| `trends_index` | FLOAT | 0–100 | Google Trends normalizado |
| `ad_spend_r` | FLOAT | >= 0 | Meta Ads em R$ |
| `fonte_flags` | VARCHAR | NOT NULL | Fontes presentes nesta linha |
| `lgpd_nivel` | VARCHAR | 'municipio' | Granularidade mínima garantida |

### Schema Contract — fact_pesquisa (pesquisas eleitorais)

| Coluna | Tipo | Constraints | Notas |
|--------|------|-------------|-------|
| `id_pesquisa` | VARCHAR | NOT NULL, PK | Registro TSE PesqEle |
| `instituto` | VARCHAR | NOT NULL | |
| `metodologia` | VARCHAR | NOT NULL | Presencial/telefone/online |
| `tamanho_amostra` | INT | > 0 | |
| `data_pesquisa` | DATE | NOT NULL | |
| `sg_uf` | VARCHAR(2) | NOT NULL | |
| `nm_candidato` | VARCHAR | NOT NULL | |
| `intencao_voto_pct` | FLOAT | 0–100 | |
| `margem_erro_pp` | FLOAT | >= 0 | |
| `house_effect_adj` | FLOAT | | Ajuste por viés histórico do instituto |
| `record_confidence_score` | FLOAT | 0.0–1.0 | 1.00=TSE CSV íntegro; 0.30=só PDF |
| `origem` | VARCHAR | NOT NULL | 'tse_csv' / 'atlas' / 'pdf' |

### Regra de uso do `record_confidence_score`

| Score | Origem | Uso no modelo |
|-------|--------|---------------|
| ≥ 0.80 | TSE CSV íntegro ou Atlas conciliado | Features principais do bayesiano |
| 0.50–0.79 | Atlas não conciliado | Análises exploratórias apenas |
| < 0.50 | Somente PDF | Fila de revisão manual — não entra no modelo |

### Chave Global de Lineage

Todos os joins entre fontes são feitos via `cod_municipio_ibge` (7 dígitos IBGE).
A tabela `dataops/depara_municipios.py` mapeia `cd_municipio` (TSE 5 dígitos) → `cod_municipio_ibge`.
Nenhum dado chega ao Gold sem `cod_municipio_ibge` validado e não-nulo.

### Freshness SLAs

| Layer | Target | Measurement |
|-------|--------|-------------|
| Raw TSE histórico | Download único por eleição | Presença do arquivo Bronze |
| Raw IBGE | Download único por Censo/PNAD | Presença do arquivo Bronze |
| fato_social | Atualização semanal em campanha | Log de conclusão do job digital_ingest |
| fact_pesquisa | Atualização ao detectar nova pesquisa no PesqEle | Monitor de RSS/API PesqEle |

---

## Assumptions

| ID | Assumption | If Wrong, Impact | Validated? |
|----|------------|------------------|------------|
| A-001 | TSE mantém schema estável de CSV para eleições 2022 e 2018 | Coletor quebra — parser customizado | [ ] |
| A-002 | IBGE SIDRA API acessível sem autenticação para indicadores básicos | Requer cadastro ou scraping alternativo | [ ] |
| A-004 | BigQuery + GCP resolve escala de 5.570 municípios × múltiplas eleições × 9 fontes | Custo pode escalar — mitigado por particionamento e clustering | [x] |
| A-005 | Regressão logística bayesiana com bootstrap é suficiente para MVP; PyMC em produção | Requer modelo mais sofisticado antes do lançamento | [ ] |
| A-006 | Tabela de-para TSE ↔ IBGE é construída via IBGE Localidades API (cd_municipio_tse ≈ ibge_code // 10) | Requer mapeamento manual para municípios com exceções | [x] (implementado em depara_municipios.py) |
| A-007 | APIs de sinal digital (Twitter, Meta, YouTube) têm tokens disponíveis para coleta | Fallback: Google Trends (pytrends) sem token cobre o sinal mínimo | [ ] |
| A-008 | TSE PesqEle disponibiliza bulk CSV com dados históricos de pesquisas | Fallback: scraping do portal + parsing de PDFs individuais | [ ] |

---

## Risks

| Risk | Probabilidade | Impacto | Mitigação |
|------|--------------|---------|-----------|
| Custo LLM excede budget | Médio | Alto | `cost_guard_hook.py` + LLM só como fallback |
| Volume de dados sociais em burst (campanha) | Médio | Médio | Rate limiting + LGPD aggregate semanal |
| Falha no parsing de PDFs de pesquisas | Alto | Médio | pdfplumber/camelot first; LLM fallback; `record_confidence_score` baixo para PDF |
| Qualidade de dados heterogênea entre fontes | Alto | Alto | Great Expectations DQ ≥ 95%; `record_confidence_score` por registro |
| Viés de instituto nas pesquisas | Médio | Alto | House effect ajustado por acerto histórico; `bias_monitor.py` por UF e quintil de renda |

---

## Clarity Score Breakdown

| Element | Score (0-3) | Notes |
|---------|-------------|-------|
| Problem | 3 | Reframado como plataforma real, diferencial claro vs. pipeline acadêmico |
| Users | 2 | 4 personas identificadas |
| Goals | 3 | MUST/SHOULD/COULD priorizados com chave global e regras explícitas |
| Success | 3 | Critérios testáveis com números, scores e comandos concretos |
| Scope | 3 | In/out explícitos — sinal digital e pesquisas agora IN SCOPE |
| **Total** | **14/15** | |

---

## Open Questions

1. ~~**2014 como feature**~~ — **RESOLVIDO**: 2014 = contexto histórico auxiliar; não entra na feature matrix principal (2018+2022). Ver DESIGN ADR 2026-04-23.
2. ~~**DATASUS**~~ — **RESOLVIDO**: DataSUS integrado em Fase 1 com SIM mortalidade (via IPEADATA) + ANS cobertura de planos. `fact_saude_municipio` adicionada ao Gold. DIEESE (cesta básica) e CETIC (internet domiciliar) também implementados. Ver DESIGN v4.3.
3. **UF do MVP**: SP confirmado — confirmar cobertura mínima de municípios SP para primeira entrega.
4. ~~**Nomenclatura**~~ — **RESOLVIDO**: `cod_municipio_ibge` (com prefixo `cod_`) é o padrão em todo o pipeline Gold. Ver DESIGN v4.0.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-17 | define-agent | Gerado a partir de BRAINSTORM_SPEPE.md |
| 2.0 | 2026-04-18 | iterate-agent | Expansão completa: 7 agentes, 14 fontes, GCP full stack |
| 3.0 | 2026-04-23 | iterate-agent | Reframing plataforma real; sinal digital estrutural (MUST); fato_social tabela Gold; fact_pesquisa central com record_confidence_score; cod_municipio_ibge chave global; regra LLM=suporte; Out of Scope corrigido; Data Contract com 9 fontes; Constraints GCP; Risks atualizados |
| 4.0 | 2026-04-23 | iterate-agent | Alinhamento com DESIGN v4.5: +9 MUST/SHOULD goals (Sentinel, ML Judge, Disclaimer enforcement, DataOps/MLOps/LLMOps L5, Vector Memory, Governance, Data Contracts, RBAC); +6 Success Criteria; +4 ATs (AT-009 a AT-012); +3 Constraints; 2 Open Questions resolvidas |
| 4.1 | 2026-04-25 | iterate-agent | **Group B implementado**: +4 módulos (DataSUS/DIEESE/CETIC/Segurança) + IBGE expandido (10 domínios, ~30 indicadores novos) · Source Inventory: 9→16 fontes · Goals: 9 fontes→13, `fact_municipio_eleicao` ≥150→≥200 features, +3 novas tabelas Gold MUST · Open Question 2 DATASUS resolvida |

---

## Next Step

**Ready for:** `/iterate .claude/sdd/features/DESIGN_SPEPE.md` — aplicar 9 decisões arquiteturais desta sessão
