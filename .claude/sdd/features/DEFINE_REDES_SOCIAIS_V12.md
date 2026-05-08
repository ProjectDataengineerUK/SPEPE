---
feature: REDES_SOCIAIS_V12
phase: define
version: 1.0
date: 2026-05-07
status: ready-for-design
clarity_score: 15/15
next_phase: /design .claude/sdd/features/DEFINE_REDES_SOCIAIS_V12.md
source_brainstorm: .claude/sdd/features/BRAINSTORM_REDES_SOCIAIS_V12.md
---

# Define — Redes Sociais v1.2

## Clarity Score: 15/15 ✅

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Problem | 3/3 | Gap quantificado: 2/9 plataformas ativas, sentimento rule-based, zero detecção de crise |
| Users | 3/3 | 3 personas com pain points específicos |
| Goals | 3/3 | MUST/SHOULD/COULD com métricas mensuráveis |
| Success | 3/3 | 18 Acceptance Tests com Given/When/Then |
| Scope | 3/3 | 12 itens explicitamente fora do escopo com justificativa |

---

## Problema

O módulo social do SPEPE v1.1 opera com apenas **2 de 9 plataformas ativas** (Bluesky + RSS), sentimento **rule-based categórico** (positivo/negativo/neutro sem score numérico), e **zero detecção de crise**. Para 2026, o produto precisa de cobertura multi-plataforma, sentimento via Vertex AI NLP com scores float, descoberta dinâmica de candidatos, scheduling híbrido por frequência, detecção de desinformação coordenada, e detecção de crise com < 4h de latência.

---

## Personas

### P1 — Analista Eleitoral
- **Contexto:** Acompanha percepção de candidatos por UF, compara narrativas entre regiões
- **Pain point:** Sem score de sentimento numérico, impossível comparar tendências semanais; sem YouTube e Facebook os candidatos mais relevantes ficam fora do radar
- **Necessidade:** `sentimento_score` float por candidato × UF × semana; cobertura das 6+ plataformas prioritárias

### P2 — Equipe de Crise
- **Contexto:** Precisa reagir a ataques coordenados ou conteúdo viral negativo em < 4 horas
- **Pain point:** Sem detecção automática de volume anômalo, a equipe monitora manualmente
- **Necessidade:** Alert automático via Pub/Sub `drift-detected` quando volume > 2× média histórica por UF

### P3 — Engenheiro de Dados
- **Contexto:** Mantém 20 clientes de ingestão e 19 Cloud Run Jobs em spepe-prod
- **Pain point:** Lista de páginas de candidatos hardcoded em código; GDELT desabilitado por rate limit; scheduling único para todas as fontes
- **Necessidade:** `dim_candidato_social_pages` no BigQuery; GDELT com backoff exponencial; Cloud Scheduler com múltiplas frequências

---

## Goals

| Prioridade | Goal | Métrica |
|-----------|------|---------|
| **MUST** | Ativar ≥ 6 plataformas simultâneas | COUNT(plataformas ativas) ≥ 6 |
| **MUST** | Sentimento Vertex AI em prod | `confianca_nlp` ≥ 0.70 para Gold |
| **MUST** | Descoberta dinâmica de candidatos | `dim_candidato_social_pages` populada no BQ |
| **MUST** | Detecção de crise < 4h | P95 latência desde evento até Pub/Sub |
| **MUST** | Custo incremental < $50/mês | Excluindo Twitter/X |
| **SHOULD** | Scheduling híbrido por fonte | ≥ 3 cron schedules distintos |
| **SHOULD** | Flagging de desinformação coordenada | `suspeito_coordenado` em todo Silver social |
| **SHOULD** | Instagram ativo | `fetch_instagram_posts()` retorna dados reais |
| **COULD** | TikTok Research API | Implementado mas desabilitado (pending approval) |
| **COULD** | Twitter/X 1×/dia | Apenas se `SOCIAL_X_ENABLED=true` e budget aprovado |

---

## Histórias de Usuário

### Sprint 1 — Ativar

| ID | História | Critério de Aceite |
|----|---------|-------------------|
| US-01 | Como analista, quero que o social_ingest carregue páginas de candidatos do BigQuery dinamicamente | `fetch_fb_page_posts()` aceita `bq_table=True` e faz query em `dim_candidato_social_pages` |
| US-02 | Como analista, quero posts do Instagram ingeridos | `fetch_instagram_posts()` retorna DataFrame com campos padrão Silver |
| US-03 | Como engenheiro, quero GDELT reabilitado com backoff | Tenacity max_retries=5, wait_exponential; cache GCS 30min |
| US-04 | Como engenheiro, quero Google Trends no social_ingest | `fetch_trends()` removido de digital_ingest; adicionado em social_ingest |
| US-05 | Como engenheiro, quero script para descobrir páginas de candidatos | `discover_candidate_pages.py` gera CSV com score ≥ threshold |

### Sprint 2 — Sentimento

| ID | História | Critério de Aceite |
|----|---------|-------------------|
| US-06 | Como analista, quero `sentimento_score` float no Silver | Campo FLOAT64 range -1 a +1; `enrich_sentiment_vertex()` retorna score numérico |
| US-07 | Como analista, quero `temas` array no Silver | Campo ARRAY<STRING> com temas NLP; `vw_social_temas_uf` retorna top 5 temas |
| US-08 | Como DQ engineer, quero flags de desinformação | `suspeito_coordenado` BOOL + `score_credibilidade_post` FLOAT64 em todo Silver social |

### Sprint 3 — Scheduling

| ID | História | Critério de Aceite |
|----|---------|-------------------|
| US-09 | Como engenheiro, quero RSS + Trends 1×/dia | Cloud Scheduler cron `0 23 * * *` → spepe-social-ingest com env SOURCE_FILTER=rss,trends |
| US-10 | Como engenheiro, quero YT + FB + IG 4×/dia | Cron `0 0,6,12,18 * * *` → spepe-social-ingest com SOURCE_FILTER=youtube,facebook,instagram |
| US-11 | Como engenheiro, quero Bluesky + Reddit 2×/dia | Cron `0 8,20 * * *` → spepe-social-ingest com SOURCE_FILTER=bluesky,reddit |
| US-12 | Como engenheiro, quero Twitter/X budget-gated | Cron `0 12 * * *` condicional a `SOCIAL_X_ENABLED=true` |

### Sprint 4 — Crise + TikTok

| ID | História | Critério de Aceite |
|----|---------|-------------------|
| US-13 | Como crise analyst, quero alerta automático de volume anômalo | Volume > 2× avg_7d por UF → mensagem em `drift-detected` dentro de 4h |
| US-14 | Como engenheiro, quero TikTok client pronto mas desabilitado | `tiktok_client.py` importável; desabilitado por ENV `TIKTOK_ENABLED=false` |

---

## Acceptance Tests (AT)

### Sprint 1

**AT-001 — dim_candidato_social_pages populada**
```
GIVEN: fact_pesquisa (spepe_silver) com candidatos 2026 ≥ 3 menções
WHEN: discover_candidate_pages.py executa com META_APP_TOKEN válido
THEN: dim_candidato_social_pages tem ≥ 1 row com facebook_page_id não-nulo
```

**AT-002 — social_ingest lê BQ dinamicamente**
```
GIVEN: dim_candidato_social_pages com 3 entries
WHEN: social_ingest_job executa com USE_BQ_CANDIDATES=true
THEN: fetch_fb_page_posts() é chamado com page_ids oriundos do BQ, não hardcoded
```

**AT-003 — Instagram ingerido**
```
GIVEN: META_APP_TOKEN válido no Secret Manager
WHEN: fetch_instagram_posts(instagram_handle) é chamado
THEN: retorna DataFrame com colunas: text, like_count, comment_count, created_at, fonte="instagram"
```

**AT-004 — GDELT backoff**
```
GIVEN: GDELT API retorna 429 rate limit
WHEN: gdelt_client.fetch_gdelt_events() é chamado
THEN: tenacity retenta 5 vezes com backoff exponencial; após retries, loga warning e retorna DataFrame vazio (não falha o job)
```

**AT-005 — GDELT cache GCS**
```
GIVEN: cache GCS gs://spepe-prod-spepe-data/cache/gdelt/{date}.parquet existe
WHEN: gdelt_client.fetch_gdelt_events(date) é chamado
THEN: retorna cache sem fazer chamada à API GDELT; log "cache hit"
```

**AT-006 — Google Trends em social_ingest**
```
GIVEN: digital_ingest_job executado sem Google Trends
AND: social_ingest_job executado com SOURCE_FILTER incluindo "trends"
THEN: fact_google_trends_uf atualizado pela execução do social_ingest
AND: digital_ingest_job NÃO atualiza fact_google_trends_uf
```

### Sprint 2

**AT-007 — sentimento_score float**
```
GIVEN: post com texto "Candidato X fez ótimo trabalho"
WHEN: enrich_sentiment_vertex() processa o post
THEN: row Silver tem sentimento_score FLOAT64 in [-1.0, 1.0] (não apenas "positivo")
AND: confianca_nlp FLOAT64 in [0.0, 1.0]
```

**AT-008 — Gold filtra NLP abaixo de threshold**
```
GIVEN: Silver social row com confianca_nlp = 0.45
WHEN: gold_build executa fact_social_municipio
THEN: row é excluída do Gold (confianca_nlp < 0.70)
```

**AT-009 — temas NLP**
```
GIVEN: post sobre "reforma da previdência e economia"
WHEN: Silver transform processa post com Vertex AI NLP
THEN: temas ARRAY<STRING> contém pelo menos um de ["economia", "previdencia", "social"]
```

**AT-010 — suspeito_coordenado flag**
```
GIVEN: 50 posts de contas criadas nas últimas 48h com texto idêntico em 1h
WHEN: transform_social_to_silver() processa batch
THEN: suspeito_coordenado = TRUE para esses posts
AND: score_credibilidade_post < 0.30
AND: posts aparecem no Gold com peso reduzido (peso = score_credibilidade_post)
```

### Sprint 3

**AT-011 — Scheduling RSS 1×/dia**
```
GIVEN: Cloud Scheduler trigger às 23h UTC
WHEN: spepe-social-ingest executa com SOURCE_FILTER=rss,trends
THEN: apenas fontes RSS e Google Trends são processadas; YouTube/Facebook/Instagram ignorados
```

**AT-012 — Scheduling YT+FB+IG 4×/dia**
```
GIVEN: Cloud Scheduler trigger às 0h, 6h, 12h, 18h UTC
WHEN: spepe-social-ingest executa com SOURCE_FILTER=youtube,facebook,instagram
THEN: apenas YouTube, Facebook, Instagram processados
```

**AT-013 — Twitter/X budget-gated**
```
GIVEN: SOCIAL_X_ENABLED=false (padrão)
WHEN: social_ingest_job executa
THEN: fetch_x_mentions() NÃO é chamado; log "X desabilitado — SOCIAL_X_ENABLED not set"
```

**AT-014 — Twitter/X ativo com flag**
```
GIVEN: SOCIAL_X_ENABLED=true
WHEN: social_ingest_job executa com SOURCE_FILTER=twitter
THEN: fetch_x_mentions() é chamado; posts ingeridos normalmente
```

### Sprint 4

**AT-015 — Crise detectada via Pub/Sub**
```
GIVEN: volume de posts UF=SP candidato=X nas últimas 4h = 500
AND: média histórica 7d UF=SP candidato=X = 200 (ratio = 2.5×)
WHEN: vigilante agent executa vw_social_crise_detector
THEN: mensagem publicada em drift-detected com payload: {uf, candidato, ratio, timestamp}
AND: latência desde primeiro post até publicação < 4 horas
```

**AT-016 — TikTok stub desabilitado**
```
GIVEN: TIKTOK_ENABLED não definido (padrão)
WHEN: social_ingest_job executa
THEN: tiktok_client não é chamado; log "TikTok desabilitado"
AND: import tiktok_client NÃO causa ImportError
```

**AT-017 — candidatos_discovery_job mensal**
```
GIVEN: Cloud Scheduler trigger dia 1 de cada mês às 3h UTC
WHEN: candidatos_discovery_job executa
THEN: dim_candidato_social_pages é atualizada com novos candidatos que apareceram em pesquisas 2026
AND: candidatos existentes NÃO são removidos (merge, não replace)
```

**AT-018 — Retenção Bronze 90 dias**
```
GIVEN: Bronze social row com ingested_at = 91 dias atrás
WHEN: BigQuery partition expiry aplicado (90d)
THEN: row expirada automaticamente pelo BigQuery TTL
```

---

## Critérios de Aceite por Plataforma

| Plataforma | Critério | Status v1.1 | Target v1.2 |
|-----------|---------|-------------|-------------|
| **YouTube** | Channel_id em dim_candidato_social_pages; ingerido 4×/dia | ✅ Em prod | Schedule 4×/dia |
| **Facebook** | Pages carregadas dinamicamente do BQ; 4×/dia | ⚠️ Hardcoded | ✅ Dinâmico via BQ |
| **Instagram** | `fetch_instagram_posts()` com META_APP_TOKEN; handle em dim_candidato | ❌ Não impl | ✅ Implementar |
| **Google Trends** | Movido de digital_ingest; 1×/dia | ✅ Em digital_ingest | ✅ Mover p/ social |
| **Reddit** | Schedule 2×/dia confirmado | ✅ Job separado | Schedule 2×/dia |
| **Bluesky** | Schedule 2×/dia confirmado | ✅ Ativo | Schedule 2×/dia |
| **GDELT** | Re-enabled; backoff tenacity; cache GCS 30min | ❌ Desabilitado | ✅ Reabilitar |
| **Twitter/X** | Apenas se SOCIAL_X_ENABLED=true; 1×/dia | ⚠️ Implementado | Budget-gated |
| **TikTok** | Stub importável; desabilitado por padrão | ❌ Não impl | ✅ Stub pronto |
| **RSS (8 feeds)** | 1×/dia; já ativo | ✅ Ativo | Schedule 1×/dia |

---

## Contrato de Dados — dim_candidato_social_pages

**Dataset:** `spepe_silver`
**Tabela:** `dim_candidato_social_pages`
**Partição:** `dt_atualizacao` (DATE)
**Atualização:** Mensal via `candidatos_discovery_job`

| Campo | Tipo | Modo | Descrição |
|-------|------|------|-----------|
| `candidato_id` | STRING | REQUIRED | ID interno SPEPE |
| `nome_candidato` | STRING | REQUIRED | Nome oficial TSE |
| `facebook_page_id` | STRING | NULLABLE | ID da página verificada |
| `instagram_handle` | STRING | NULLABLE | Handle Instagram |
| `youtube_channel_id` | STRING | NULLABLE | Channel ID YouTube |
| `twitter_handle` | STRING | NULLABLE | @handle (budget-gated) |
| `tiktok_handle` | STRING | NULLABLE | Handle TikTok (futuro) |
| `followers_fb` | INT64 | NULLABLE | Seguidores no momento da descoberta |
| `is_verified` | BOOL | NULLABLE | Página verificada pela plataforma |
| `dt_atualizacao` | DATE | REQUIRED | Última atualização |

**Constraint:** `candidato_id` é chave natural; INSERT OR UPDATE (MERGE) no job mensal.
**SLA:** Populada via script manual antes do Sprint 1 go-live; atualizada mensalmente.

---

## Contrato de Dados — Novos Campos Silver Social

**Tabela:** `spepe_silver.social_mencoes_br`
**Campos novos adicionados ao schema existente:**

| Campo | Tipo | Range | Fonte | Fallback |
|-------|------|-------|-------|---------|
| `sentimento_score` | FLOAT64 | -1.0 a +1.0 | Vertex AI NLP `document_sentiment.score` | 0.0 (neutro) sem GCP |
| `confianca_nlp` | FLOAT64 | 0.0 a 1.0 | Vertex AI NLP `document_sentiment.magnitude` normalizado | NULL sem GCP |
| `temas` | ARRAY<STRING> | — | Vertex AI `entities.type` + classificação customizada | [] |
| `suspeito_coordenado` | BOOL | — | Volume anômalo + age_account heuristic | FALSE |
| `score_credibilidade_post` | FLOAT64 | 0.0 a 1.0 | Função de `score_confiabilidade` fonte × `suspeito_coordenado` | 1.0 |

**Gate Gold:** `confianca_nlp >= 0.70` obrigatório para entrar em `fact_social_municipio` (posts com `confianca_nlp < 0.70` ficam no Silver mas não são agregados no Gold).

---

## Definição de "Crise Detectada"

```
crise = volume_4h(candidato, uf) > 2 × avg_daily_7d(candidato, uf)
```

| Parâmetro | Valor |
|-----------|-------|
| Janela de medição | 4 horas rolling |
| Baseline | Média diária dos últimos 7 dias |
| Threshold | 2× (200% da baseline) |
| Escopo geográfico | Por UF |
| Escopo de candidato | Por candidato individual |
| Avaliação | A cada execução do social_ingest (4×/dia) |
| Ação | Publicar em `drift-detected` Pub/Sub |

**Payload Pub/Sub:**
```json
{
  "event_type": "social_crisis_detected",
  "candidato": "string",
  "sg_uf": "string",
  "ratio": 2.5,
  "volume_4h": 500,
  "avg_daily_7d": 200,
  "fonte": "facebook|twitter|bluesky|...",
  "timestamp_utc": "2026-05-07T14:30:00Z"
}
```

---

## Janelas de Lookback por Fonte

| Fonte | Lookback | Justificativa |
|-------|---------|--------------|
| Twitter/X | 7 dias | Limite API Basic |
| Bluesky | 7 dias | Sem API histórica |
| RSS | 30 dias | Feed history limit |
| YouTube | Configurável (env `YT_LOOKBACK_DAYS`) | API Data v3 sem limite fixo |
| Facebook | Sem limite fixo (padrão 30d) | Graph API by page |
| Google Trends | Histórico completo | pytrends |
| GDELT | 30 dias (cache) | Rate limit |
| **Bronze social retention** | **Rolling 90 dias** | BigQuery partition TTL |

---

## Sprint Plan

| Sprint | Objetivo | Componentes Chave | Gate |
|--------|---------|------------------|------|
| **Sprint 1** | Ativar plataformas | Instagram + GDELT + dim_candidato + candidatos_discovery + social_ingest BQ-dinâmico + Trends move | AT-001 a AT-006 passando |
| **Sprint 2** | Elevar sentimento | sentimento_score float + temas NLP + suspeito_coordenado + score_credibilidade + Gold filter ≥ 0.70 | AT-007 a AT-010 passando |
| **Sprint 3** | Scheduling híbrido | Cloud Scheduler 3 crons + Twitter/X budget-gate | AT-011 a AT-014 passando |
| **Sprint 4** | Crise + TikTok | vigilante agent + Pub/Sub + tiktok_client.py stub | AT-015 a AT-018 passando |

---

## Requisitos Não-Funcionais

| Requisito | Meta | Medição |
|-----------|------|---------|
| Latência detecção de crise | < 4 horas | P95 desde evento até Pub/Sub |
| Plataformas simultâneas ativas | ≥ 6 | COUNT(fontes ativas em social_ingest) |
| Confiança NLP mínima para Gold | ≥ 0.70 | confianca_nlp threshold em gold_builder |
| Custo incremental mensal | < $50 (excl. X) | GCP Billing export |
| Disponibilidade social_ingest_job | > 99% | Cloud Run Job success rate |
| Retenção Bronze social | Rolling 90 dias | BigQuery partition TTL |
| Retry automático em falha | ≥ 3 tentativas | Cloud Run Job max_retries=3 |
| Vertex AI NLP throughput | ≤ 10k documentos/dia | Quota + cost guard |

---

## Freshness SLAs

| Camada | Fonte | SLA Freshness |
|--------|-------|--------------|
| Bronze | RSS + Trends | < 25h (1×/dia às 23h) |
| Bronze | YouTube + FB + IG | < 5h (4×/dia) |
| Bronze | Bluesky + Reddit | < 13h (2×/dia) |
| Silver | Todos os sociais | < 2h após Bronze (trigger automático) |
| Gold | fact_social_municipio | < 3h após Silver |
| dim | dim_candidato_social_pages | Mensal (< 30d) |

---

## Completeness Metrics

| Métrica | Threshold | Alerta |
|---------|----------|-------|
| Plataformas com dados nas últimas 24h | ≥ 5 | < 5 → alerta Pub/Sub |
| Posts processados por dia | ≥ 100 | < 100 → log WARNING |
| Posts com confianca_nlp not-null | ≥ 80% | < 80% → alerta (Vertex indisponível?) |
| dim_candidato_social_pages rows | ≥ 5 | < 5 → job falha (tabela não populada) |

---

## Lineage

```
fact_pesquisa (Silver)
  └── discover_candidate_pages.py (script manual)
        └── dim_candidato_social_pages (Silver)
              └── social_ingest_job (lê dinamicamente)
                    └── Bronze GCS raw/social/{source}/{date}/
                          └── silver_transformer.transform_social_to_silver()
                                ├── sentimento_score, confianca_nlp (Vertex AI NLP)
                                ├── temas (Vertex AI entities)
                                ├── suspeito_coordenado (heurística volume)
                                └── social_mencoes_br (Silver BQ)
                                      └── gold_builder (gate confianca ≥ 0.70)
                                            └── fact_social_municipio (Gold)
                                                  └── vw_social_candidato_sentimento
                                                  └── vw_social_crise_detector → Pub/Sub drift-detected
```

---

## Fora do Escopo v1.2

| Item | Justificativa |
|------|-------------|
| Telegram | Sem API oficial; risco ToS |
| Análise de imagens/vídeo | Vertex AI Vision — custo alto; defer v2.0 |
| Dataflow streaming completo | Cloud Scheduler 4×/dia suficiente; custo injustificado |
| Twitter/X como fonte primária | Basic API $100/mês proibitivo |
| Instagram Stories scraping avançado | Graph API avançada; ROI incerto |
| Moderação ou remoção de conteúdo | Fora da responsabilidade do sistema |
| Retroativo > 30 dias via API social | Limite das APIs (X: 7d; Bluesky: 7d; RSS: ~30d) |
| Análise de perfis individuais de eleitores | Não é missão do SPEPE; LGPD |
| NLP em vídeos YouTube (transcrição) | Vertex AI Speech — custo alto; defer v2.0 |
| Sentiment por tweet individual no Gold | Agregado por UF×dia é suficiente para produto |
| Webhook real-time Facebook | Infra adicional; Pub/Sub + Cloud Scheduler suficiente |
| Análise de grupos privados | Acesso impossível via API pública |

---

## Restrições

| Restrição | Impacto |
|-----------|---------|
| META_APP_TOKEN no Secret Manager (já existe) | Facebook + Instagram compartilham token |
| Vertex AI apenas em us-central1 | NLP latency +100ms vs southamerica-east1 |
| Twitter/X Basic API $100/mês | Budget-gated; default OFF |
| TikTok Research API pendente | Stub implementado; feature inacessível até aprovação |
| spepe-prod apenas para ingestão | Jobs Cloud Run em spepe-prod, não spepe-dev |
| Budget por sessão de agente: $2.00 | Vertex NLP batcheado; máx 10k docs/dia |
| Google Trends via pytrends (unofficial) | Sujeito a rate limit; cache local 24h |
| LGPD | Nenhum dado pessoal de eleitor individual no Silver/Gold |

---

## Assumptions

| # | Assumção | Se errada: |
|---|---------|-----------|
| A1 | META_APP_TOKEN permite busca de páginas públicas + posts Instagram | Precisa de token Instagram Graph API separado |
| A2 | `fact_pesquisa` 2026 tem ≥ 5 candidatos com ≥ 3 menções | Script não gera saída; dim_candidato vazia |
| A3 | Vertex AI NLP retorna `document_sentiment.score` como float | Precisar mudar parsing de resposta |
| A4 | Cloud Scheduler SOURCE_FILTER env var filtra fontes no social_ingest | Precisar refatorar job para aceitar parâmetro |
| A5 | `drift-detected` Pub/Sub topic já existe no Terraform | ✅ Confirmado em pubsub.tf |
| A6 | GDELT usa endpoint `api.gdeltproject.org/api/v2/doc/doc` | Se mudou, ajustar gdelt_client |
| A7 | TikTok Research API aprovação pode levar meses | Stub implementado; feature bloqueada |
| A8 | confianca_nlp de 0.70 é threshold adequado para qualidade Gold | Pode precisar ajuste em produção |

---

## Contexto de Engenharia de Dados

**Stack existente (reuso total):**
- `dataops/clients/social_client.py` — fetch_x_mentions, fetch_fb_page_posts, fetch_youtube_videos, enrich_sentiment_vertex
- `dataops/jobs/social_ingest_job.py` — Cloud Run Job já em prod
- `dataops/silver_transformer.py:transform_social_to_silver()` — Silver pipeline
- `dataops/gold_builder.py` — fact_social_municipio no Gold
- `dataops/source_registry.py` — score/tipo/viés para todas as fontes
- `infra/terraform/pubsub.tf` — drift-detected topic ✅
- `infra/terraform/cloud_run_jobs.tf` — spepe-social-ingest ✅

**KB Domains:** medallion, gcp, data-modeling, data-quality, ai-data-engineering, terraform, streaming

**IaC changes:**
1. `bigquery.tf` — adicionar `dim_candidato_social_pages`
2. `scheduler.tf` — adicionar 5 schedules (RSS 1×, YT+FB+IG 4×, Bluesky+Reddit 2×, X condicional, discovery mensal)
3. `cloud_run_jobs.tf` — adicionar `candidatos_discovery` (20º job)
4. `secrets.tf` — nenhuma nova secret (META_APP_TOKEN e YOUTUBE_API_KEY já existem)

---

## Revision History

| Versão | Data | Autor | Mudanças |
|--------|------|-------|---------|
| 1.0 | 2026-05-07 | define-agent (via Claude Sonnet 4.6) | Versão inicial — 18 ATs, 14 US, 4 sprints, contratos dim_candidato + Silver schema |
