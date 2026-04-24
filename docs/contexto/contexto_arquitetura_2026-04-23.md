# Contexto de Arquitetura SPEPE — 2026-04-23

Registro de todas as decisões, insights e inputs entregues nesta sessão.
Fonte de verdade para futuras conversas sobre o projeto.

---

## 1. Síntese Final do Sistema

> **O SPEPE passa a ser:**
> Um sistema modular de dados + ML + sinais digitais, capaz de integrar comportamento histórico, contexto estrutural, intenção declarada e narrativa em tempo real para prever cenários eleitorais com explicabilidade.

### Regra Final de Integração

| Camada | Papel |
|--------|-------|
| Dados estruturais | Explicam |
| Histórico eleitoral | Valida |
| Pesquisas | Calibram |
| Sinal digital | Antecipa |
| Modelo bayesiano | Integra |

---

## 2. Nova Estrutura Alinhada

### Módulos de dados (separados)
- social
- pesquisas
- dados públicos (IBGE + DATASUS)
- eleições

### Núcleo analítico
- BigQuery (core)
- camada semântica
- feature layer

### ML / MLOps
- Vertex AI
- modelos bayesianos
- pipelines

### GCP — Projetos
- core-analytics
- data-platform
- ml-platform

---

## 3. Pipeline Global (sequência confirmada)

```
Ingestão (módulos separados)
  → Cloud Storage (raw — zona de pouso, fora do Medallion)
  → Processamento (Cloud Run + Dataflow + Vertex AI)
  → BigQuery (Bronze / Silver / Gold — Medallion completo dentro do BQ)
  → Camada Semântica (views BigQuery)
  → ML (Vertex AI Pipelines)
  → Dashboards / Chainlit / APIs / Alertas
```

**Regra arquitetural:**
> Ingestão separada, dados padronizados, análise unificada.

---

## 4. Decisão: GCS vs BigQuery no Medallion

**Confirmado pelo usuário:** o diagrama `spepe-gcp-architecture.html` é o correto.

| Camada | Tecnologia |
|--------|-----------|
| **Zona de pouso raw** | Cloud Storage (GCS) — 4 buckets por módulo, imutável |
| **Bronze** | BigQuery — staging, dados carregados do GCS como vieram |
| **Silver** | BigQuery — curated, normalizado, chave `cod_municipio_ibge` |
| **Gold** | BigQuery — analytics, `fact_*`, particionado por `ano_eleicao` |

GCS **não é** camada do Medallion — é zona de pouso antes do pipeline.

---

## 5. Orquestração (decisão confirmada)

**Usar:** Cloud Scheduler + Workflows
**Não usar:** Cloud Composer / Airflow (descartado)

---

## 6. Módulo Social — Contexto Completo

### Fontes e papéis

| Fonte | Papel | Sinal |
|-------|-------|-------|
| Twitter/X | Reação imediata | Pico de menções, hashtags |
| YouTube | Opinião real | Comentários, narrativa sustentada |
| Google Trends | Interesse geral | Volume de busca por UF × semana |
| Google Alertas | Narrativa da mídia | O que a imprensa amplifica |
| TikTok | Tendência | Viralização, discurso jovem |
| Meta Ads | Performance | Ad spend, alcance por candidato × dia |

### Insight central (muito importante)

> ❝ O valor não está na coleta — está na **correlação entre fontes**. ❞

**Exemplo de crise real:**
```
Google Alertas → notícia negativa
       ↓ (lag ~2h)
Twitter        → explosão de menções negativas
       ↓ (lag ~6h)
YouTube        → comentários reforçando a narrativa
→ CRISE REAL confirmada → alerta disparado
```

Sem correlação, cada fonte parece ruído. Com correlação, é um evento real.

### Princípio

> O módulo social é **radar narrativo**, não medidor de sentimento.

### MVP por fases

| Semana | Fontes | Entrega |
|--------|--------|---------|
| 1 | Twitter + Google Trends | Baseline de menções e interesse |
| 2 | + YouTube | Opinião qualitativa + narrativa |
| 3 | + Google Alertas + dashboard | Radar narrativo completo |
| depois | + TikTok + Meta Ads | Tendência jovem + performance |

### Nível avançado (campanha profissional)
- Detecção de bots (Vector Search, similaridade > 0.92 em janela de 1h)
- Análise de influenciadores (amplificação por tema)
- Clusterização de discurso (HDBSCAN sobre embeddings 768d)
- Previsão de tendência (série temporal sentimento → curva futura)

---

## 7. Módulo Pesquisas — TSE PesqEle como Backbone

O TSE mantém o **Sistema de Gerenciamento de Pesquisas Eleitorais (PesqEle)** — registro legal obrigatório de toda pesquisa eleitoral no Brasil.

**Estratégia:**
- TSE PesqEle como backbone (CSV bulk + portal)
- PDFs como evidência complementar
- Parser tradicional primeiro (camelot/pdfplumber)
- LLM (Gemini) só como fallback quando parser falha > 30% células

**House effect:** peso por tamanho de amostra, acerto histórico, recência e metodologia → prior para o Modelista Bayesiano.

---

## 8. Reunião de Arquitetura (input completo)

Ver arquivo original: `reuniao.md` na raiz do projeto.

### Decisões extraídas da reunião

| Decisão | Escolha |
|---------|---------|
| Número de projetos GCP | 3 (core-analytics, data-platform, ml-platform) → evoluir para 6 |
| Terraform | Estrutura bootstrap/modules/envs desde o dia 1 |
| Chave territorial | `cod_municipio_ibge` (7 dígitos) — âncora obrigatória |
| BigQuery | Datasets separados por domínio: social_*, pesquisas_*, eleicoes_* |
| Camada semântica | Views prontas: 6 `vw_*` para consumo direto |
| MLOps | 4 blocos: NLP social, PDF parser, score eleitoral, previsão |
| LLM no pipeline | Apoio seletivo — parser tradicional primeiro, LLM como fallback |

### Naming convention

| Recurso | Padrão |
|---------|--------|
| Projetos | `prj-{env}-{dominio}` |
| Buckets | `{env}-{dominio}-{camada}` |
| Datasets BQ | `{dominio}_{camada}` |
| Service Accounts | `sa-{env}-{dominio}-{funcao}` |
| Pub/Sub Topics | `{env}-{dominio}-{evento}` |

### Frase-resumo (da reunião)
> *"Terraform organiza a fundação, GCP hospeda os módulos, BigQuery centraliza a inteligência, Vertex AI amplia a capacidade analítica, e os cinco domínios de dados alimentam um único núcleo de decisão eleitoral."*

### Riscos identificados na reunião
- Tudo em um projeto só → separar em 3 mínimo
- LLM para tudo → custo + latência → usar como fallback
- Não padronizar território → âncora `cod_municipio_ibge` obrigatória
- Misturar raw com curated → camadas estritamente separadas
- BigQuery sem particionamento → custo explode
- State Terraform não remoto → backend GCS desde o dia 1

---

## 9. Diagrama de Arquitetura

**Arquivo:** `docs/diagrams/spepe-gcp-architecture.html`

Diagrama enterprise estilo GCP, fluxo esquerda → direita, 8 colunas:

```
Orquestração → Ingestão → Eventos → Storage Raw → Processamento → BigQuery → Semântica → Consumo
```

4 swim lanes verticais: Social / Pesquisas / Dados Públicos / Eleições

**Este diagrama foi confirmado pelo usuário como a arquitetura correta.**

---

## 10. Configuração de Modelos (desenvolvimento)

Para economizar tokens no desenvolvimento:

| Agente/Contexto | Modelo |
|-----------------|--------|
| Padrão projeto (`.claude/settings.json`) | `claude-haiku-4-5-20251001` |
| Tarefas complexas (arquitetura, debugging) | trocar via `/model claude-sonnet-4-6` |
| `modelista-bayesiano` | `gemini-2.5-pro` |
| `analista-eleitoral` | `gemini-2.5-pro` |
| `explicador` | `gemini-2.5-flash` (downgrade de pro) |
| `coletor` | `gemini-2.0-flash` (downgrade de flash) |
| `narrador`, `vigilante`, `perfilador` | `gemini-2.0-flash` |

---

---

## 11. Módulo Pesquisas — Detalhamento Adicional

### Contexto histórico
| Eleição | Papel |
|---------|-------|
| 2018 | Contexto (ruptura) |
| 2022 | Padrão (polarização consolidada) |
| 2026 | Realidade atual (incremental) |

**TSE = base. Atlas = complemento.**

### Novas fontes adicionadas
- **Questionários** — perguntas exatas por pesquisa registrada
- **Detalhamento por bairro/município** — granularidade abaixo de UF
- **Anexos complementares do Atlas** — dados adicionais por eleição/UF

### record_confidence_score (campo obrigatório)

Campo de qualidade em todo registro da `fato_pesquisa`:

| Score | Origem |
|-------|--------|
| **1.00** | CSV TSE íntegro |
| **0.95** | TSE + anexo validado |
| **0.80** | Atlas conciliado com TSE |
| **0.50** | Atlas ainda não conciliado |
| **0.30** | Extraído só de PDF |

Uso: filtrar `score >= 0.80` para análises de alta confiança. `score < 0.50` vai para fila de revisão manual.

### Stack recomendada para o módulo

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python + FastAPI + SQLAlchemy + Pydantic |
| Banco | PostgreSQL |
| Filas | Redis + RQ/Celery **ou** RabbitMQ + workers |
| Extração | httpx/requests + pandas + PyMuPDF/pdfplumber + OCR fallback |
| Storage | GCS / S3 / Cloudflare R2 |
| Orquestração | cron (início) → Airflow/Prefect se crescer |

### Painéis obrigatórios desde o início

**Operacional:**
- Pesquisas novas por dia
- PDFs baixados vs falhos
- Taxa de conciliação Atlas ↔ TSE
- Distribuição de `record_confidence_score`

**Produto:**
- Últimas pesquisas por cargo
- Cobertura por UF
- Evolução de intenção por candidato × instituto

---

*Gerado em: 2026-04-23 | Sessão de arquitetura SPEPE*
