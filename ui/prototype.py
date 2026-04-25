"""SPEPE — Protótipo UI (respostas mockadas, sem dependência de backend/GCP)."""

from __future__ import annotations

import asyncio
import uuid

import chainlit as cl

# ── Mock responses por comando ──────────────────────────────────────────────

_MOCK: dict[str, str] = {
    "/coletar": """\
✅ Job `tse_ingest` (SP / 2022) — concluído com sucesso.

*→ coletor*

## Coleta TSE — SP | 2022

| Métrica | Valor |
|---------|-------|
| Seções processadas | 78.432 |
| Municípios cobertos | 645 / 645 |
| Total de votos | 32.481.940 |
| Cargos ingeridos | Presidente, Governador, Senador, Dep. Federal, Dep. Estadual |
| Arquivo Bronze | `gs://spepe-dev-data/raw/tse/2022/SP/` |
| Tamanho | 187 MB |
| DQ Score | 98.5% |

✅ Dados prontos. Use `/social`, `/pesquisas` ou `/prever` para análise completa.

---
*gemini-2.5-flash | sessão: $0.00031 / $2.00*
""",
    "/social": """\
✅ Job `digital_ingest` (BR / 2022-2026) — concluído com sucesso.

*→ coletor*

## Sinal Digital — Redes Sociais | BR | 2022–2026

### Google Trends (últimos 90 dias)
| Candidato | Score médio | Pico | Tendência |
|-----------|------------|------|-----------|
| Lula | 68 | 94 (debate 08/abr) | ↗ +12% |
| Bolsonaro | 71 | 89 (convenção 02/abr) | → estável |
| Tarcísio | 44 | 67 (lançamento candidatura) | ↗ +28% |
| Marina Silva | 21 | 38 | ↘ -8% |

### Meta Ad Library — Gastos em Anúncios (abril/2026)
| Candidato / Partido | Gasto (R$) | Alcance estimado | CPM |
|--------------------|-----------|-----------------|-----|
| Bolsonaro / PL | 2.840.000 | 48,2M impressões | R$ 5,89 |
| Lula / PT | 1.920.000 | 31,7M impressões | R$ 6,05 |
| Tarcísio / Republicanos | 980.000 | 15,4M impressões | R$ 6,36 |

### YouTube — Visualizações de Conteúdo Político (30 dias)
| Canal / Candidato | Views | Engajamento | Sentimento |
|-------------------|-------|-------------|------------|
| Lula (canais aliados) | 42M | 8,2% | 62% positivo |
| Bolsonaro (canais aliados) | 58M | 11,4% | 58% positivo |
| Tarcísio (canais aliados) | 19M | 9,8% | 71% positivo |

### X (Twitter) — Menções e Sentimento (7 dias)
| Candidato | Menções | Sentimento + | Sentimento - | Bots estimados |
|-----------|---------|-------------|-------------|----------------|
| Lula | 2.1M | 41% | 38% | 18% |
| Bolsonaro | 2.8M | 44% | 35% | 23% |
| Tarcísio | 890k | 52% | 21% | 12% |

### Insights Automáticos
- **Tarcísio em ascensão**: Google Trends +28% em 30 dias — candidatura em lançamento
- **Bolsonaro lidera gasto em Meta**: 47% do total de anúncios políticos rastreados
- **Alto volume de bots**: X não é fonte confiável sem filtragem — desconto aplicado no modelo

---
*Fontes: Google Trends API · Meta Ad Library · YouTube Data API · X Academic API*
*gemini-2.5-flash | sessão: $0.00058 / $2.00*
""",
    "/pesquisas": """\
*→ analista-eleitoral*

## Agregação de Pesquisas Eleitorais — Presidente | BR | abril/2026

### Pesquisas recentes (últimos 30 dias)

| Data | Instituto | Registro TSE | Lula | Bolsonaro | Tarcísio | Outros | Margem |
|------|-----------|-------------|------|-----------|----------|--------|--------|
| 18/abr | Datafolha | BR-04892/2026 | 35% | 32% | 18% | 15% | ±2pp |
| 15/abr | Quaest | BR-04781/2026 | 34% | 33% | 17% | 16% | ±2pp |
| 12/abr | AtlasIntel | BR-04650/2026 | 33% | 31% | 20% | 16% | ±1,4pp |
| 08/abr | Ipespe | BR-04512/2026 | 36% | 30% | 16% | 18% | ±3,2pp |
| 02/abr | PoderData | BR-04380/2026 | 34% | 34% | 15% | 17% | ±2,5pp |

### Agregado com House Effect ajustado

| Candidato | Intenção agregada | IC 95% | House effect corrigido |
|-----------|------------------|--------|----------------------|
| **Lula** | **34,6%** | ±1,8pp | Datafolha +1,2pp vs. média |
| **Bolsonaro** | **32,1%** | ±1,8pp | PoderData +2,1pp vs. média |
| **Tarcísio** | **17,2%** | ±1,4pp | AtlasIntel +1,8pp vs. média |
| Outros/Indecisos | 16,1% | — | — |

### Evolução temporal (últimos 6 meses)

```
out/25  nov/25  dez/25  jan/26  fev/26  mar/26  abr/26
Lula:    38%     37%     36%     35%     34%     34%     34,6%
Bolso:   30%     31%     31%     32%     32%     33%     32,1%
Tarcís:   8%     10%     12%     14%     16%     17%     17,2%
```

### Análise de House Effect por Instituto
| Instituto | Viés histórico | Metodologia | Peso no agregado |
|-----------|---------------|-------------|-----------------|
| Datafolha | +1,2pp Lula | Presencial + online | 0.28 |
| Quaest | Neutro | Online painel | 0.25 |
| AtlasIntel | +1,8pp Tarcísio | Big data + painel | 0.22 |
| Ipespe | +2pp oposição | Telefônica | 0.14 |
| PoderData | +2,1pp Bolsonaro | Online | 0.11 |

### Alerta de Registro TSE
⚠️ Apenas pesquisas com registro TSE válido são incluídas no agregado.
Pesquisas não registradas são sinalizadas e excluídas do modelo.

---
*Fontes: TSE Sistema de Gerenciamento de Pesquisas Eleitorais · Datafolha · Quaest · AtlasIntel · Ipespe · PoderData*
*gemini-2.5-pro | sessão: $0.00142 / $2.00*
""",
    "/perfil": """\
*→ analista-eleitoral*

## Perfil Eleitoral — São Paulo | 2022 | Presidente

### Resultado Eleitoral (1º Turno)
| Candidato | Votos | % |
|-----------|-------|---|
| Luiz Inácio Lula da Silva | 14.228.401 | 43,8% |
| Jair Messias Bolsonaro | 13.972.616 | 43,0% |
| Simone Tebet | 2.406.012 | 7,4% |
| Ciro Gomes | 1.211.033 | 3,7% |
| Outros | 663.878 | 2,1% |

### Perfil Socioeconômico (IBGE)
| Indicador | São Paulo | Média BR |
|-----------|-----------|----------|
| IDHM (2010) | 0,783 | 0,727 |
| Renda média domiciliar | R$ 3.872 | R$ 2.311 |
| Taxa de analfabetismo | 4,2% | 11,3% |
| % Zona rural | 3,1% | 26,8% |
| Taxa de desemprego | 11,4% | 14,7% |

### Sinal Digital na Campanha 2022 (Google Trends + Meta)
| Candidato | Trends pico | Meta spend | YouTube views | Sentimento X |
|-----------|------------|-----------|--------------|-------------|
| Bolsonaro | 91 (out/22) | R$ 42M total | 180M views | 52% positivo |
| Lula | 88 (out/22) | R$ 31M total | 145M views | 48% positivo |
| Tebet | 34 | R$ 6M total | 22M views | 61% positivo |

### Correlação Sinal Digital × Resultado Real
- Google Trends explicou **73%** da variância dos votos em municípios acima de 100k hab.
- Meta Ad spend correlacionado com voto em 68% dos municípios paulistas
- Sentimento X apresentou viés de +8pp para candidatos de direita (desconto aplicado)

### Padrões Identificados
- **Polarização alta**: diferença de 0,8pp entre 1º e 2º colocados
- **Correlação renda × voto**: renda > R$4.500 → 58% Bolsonaro no interior
- **Efeito capital**: SP capital 49,2% Lula vs. 39,1% Bolsonaro
- **Efeito digital**: municípios com alto engajamento no Meta votaram mais Bolsonaro

Fonte: TSE 2022 · IBGE Censo 2022 · Google Trends · Meta Ad Library

---
*gemini-2.5-pro | sessão: $0.00134 / $2.00*
""",
    "/prever": """\
*→ analista-eleitoral*

Carregando features históricas + sinal digital + pesquisas...

*→ modelista-bayesiano*

## Previsão Eleitoral — Lula | Presidente 2026

P(Lula vence 1º turno) = 31% [IC 95%: 24%–39%]
P(Lula vence 2º turno vs. Bolsonaro) = 58% [IC 95%: 51%–65%]
P(Lula vence 2º turno vs. Tarcísio) = 52% [IC 95%: 44%–60%]

### Fontes integradas no modelo
| Fonte | Peso | Última atualização |
|-------|------|-------------------|
| TSE 2018 + 2022 (dados históricos) | 35% | Estático |
| IBGE socioeconômico | 20% | PNAD 2024 |
| Agregado de pesquisas (5 institutos) | 30% | 18/abr/2026 |
| Google Trends (90 dias) | 8% | Tempo real |
| Meta Ad Library | 4% | Semanal |
| Sentimento X (filtrado bots) | 3% | Diário |

### Agregado de pesquisas (house effect ajustado)
| Candidato | Agregado | IC 95% |
|-----------|---------|--------|
| Lula | 34,6% | ±1,8pp |
| Bolsonaro | 32,1% | ±1,8pp |
| Tarcísio | 17,2% | ±1,4pp |
| Indecisos | 16,1% | — |

### Top features do modelo (SHAP values)
| Feature | Impacto | Fonte |
|---------|---------|-------|
| Renda média domiciliar | +0.42 | IBGE |
| Intenção de voto agregada | +0.39 | Pesquisas |
| % Zona rural | +0.38 | IBGE |
| Google Trends (30d) | +0.29 | Digital |
| Taxa de desemprego | +0.31 | IBGE |
| Meta Ad spend (oposição) | -0.24 | Digital |
| % Evangélicos | -0.28 | IBGE |

### Cenário Base (abril/2026)
| Indicador | Valor | Fonte |
|-----------|-------|-------|
| Aprovação governo | 38% | Datafolha |
| Intenção de voto agregada | 34,6% ± 1,8pp | 5 institutos |
| Google Trends score | 68/100 | Google |
| Meta Ad spend relativo | 32% do total | Meta |
| JS Divergence features | 0.07 | Vigilante |

---
*→ explicador*

## Por que este resultado? (SHAP em linguagem natural)

Pesquisas eleitorais explicam 39% do poder preditivo do modelo — a maior fonte individual. O sinal digital (Google Trends + Meta) adiciona 37% quando combinado. Dados históricos do TSE calibram o modelo mas têm menor peso porque o cenário de 2026 difere de 2022 pela entrada de Tarcísio.

O modelo detecta que **Bolsonaro está recuperando terreno** nas pesquisas (+5pp desde outubro/2025) enquanto Lula estabilizou. O crescimento de Tarcísio (+9pp no mesmo período) é o maior fator de incerteza — qualquer consolidação dele no 2º turno muda os cenários.

---
*→ narrador*

## Análise Eleitoral — Lula | Presidencial 2026

Os dados combinados de pesquisas eleitorais, redes sociais e histórico eleitoral indicam uma disputa muito acirrada para 2026. Com aproximadamente 35% das intenções de voto nas pesquisas e uma tendência estável nos últimos meses, Lula seria o candidato com mais chance de avançar ao segundo turno — mas sem garantia de vitória.

O que chama atenção nos dados digitais é o crescimento acelerado de interesse por Tarcísio de Freitas, com alta de 28% no Google Trends em 30 dias. Se ele consolidar sua candidatura, pode redistribuir votos que hoje oscilam entre os dois primeiros colocados, tornando o segundo turno imprevisível.

Esta análise integra cinco institutos de pesquisa com house effect ajustado, sinal digital de quatro plataformas e dados históricos do TSE. Para fins educacionais e de pesquisa — não substitui análise eleitoral profissional e não constitui previsão definitiva de resultado eleitoral.

---
*gemini-2.5-pro | sessão: $0.00521 / $2.00*
""",
    "/arquétipos": """\
*→ perfilador*

## Arquétipos do Eleitorado Brasileiro — 2022

Identificados **7 clusters** via K-Means (k=7, silhouette=0.68) sobre 5.570 municípios.
Features: IBGE + TSE + Google Trends + Meta Ads por município.

---

### Cluster 1 — "Nordeste Popular" (28% dos municípios)
**Perfil:** Alta pobreza, forte dependência de transferências, baixo sinal digital
| Indicador | Valor médio |
|-----------|-------------|
| IDHM | 0,612 |
| Renda média | R$ 1.240 |
| % Bolsa Família | 38,4% |
| Google Trends (candidato vencedor) | 34/100 |
| Voto Lula 2022 | 71,3% |

---

### Cluster 2 — "Interior Agro Digital" (19% dos municípios)
**Perfil:** Rural, evangélico, alto engajamento no Meta Ads conservador
| Indicador | Valor médio |
|-----------|-------------|
| % zona rural | 61,2% |
| % evangélicos | 34,1% |
| Meta Ad spend conservador (per capita) | R$ 4,80 |
| Voto Bolsonaro 2022 | 64,8% |

---

### Cluster 3 — "Metrópole Polarizada" (8% dos municípios)
**Perfil:** Grandes cidades, alto engajamento digital, voto dividido
| Indicador | Valor médio |
|-----------|-------------|
| IDHM | 0,798 |
| Google Trends (volume total) | 87/100 |
| Meta Ad impressões per capita | 142 |
| Sentimento X neutro | 55% |
| Voto Lula 2022 | 51,2% |

---

*+ 4 clusters adicionais disponíveis com `/perfil {municipio}`*

Fonte: TSE 2022 · IBGE Censo 2022 · Google Trends · Meta Ad Library
Modelo: K-Means + PCA (18 features — 12 socioeconômicas + 6 digitais)

---
*gemini-2.5-flash | sessão: $0.00201 / $2.00*
""",
    "/monitorar": """\
*→ vigilante*

## Status de Monitoramento SPEPE — 20/04/2026 09:14 UTC

### Drift de Features Socioeconômicas (JS Divergence vs. baseline 2022)
| Feature | JS Divergence | Status |
|---------|---------------|--------|
| idhm_2010 | 0.03 | ✅ OK |
| renda_media_domiciliar | 0.07 | ✅ OK |
| taxa_desemprego | 0.09 | ⚠️ Atenção |
| pct_evangelicos | 0.04 | ✅ OK |
| pct_zona_rural | 0.02 | ✅ OK |

### Drift de Features Digitais (JS Divergence vs. baseline out/2022)
| Feature | JS Divergence | Status |
|---------|---------------|--------|
| google_trends_score | 0.11 | 🔴 ALERTA |
| meta_ad_spend_relativo | 0.08 | ⚠️ Atenção |
| youtube_views_index | 0.06 | ✅ OK |
| sentimento_x_filtrado | 0.13 | 🔴 ALERTA |

Threshold configurado: **0.10**
2 features digitais ultrapassaram threshold — retrain automático agendado.

### Drift de Pesquisas (vs. média histórica 2018–2022)
| Indicador | Desvio | Status |
|-----------|--------|--------|
| Spread 1º–2º colocado | -8pp (menor polarização) | ⚠️ Atenção |
| Volatilidade semanal | 1,2pp | ✅ OK |
| Indecisos | +4pp vs. histórico | ⚠️ Atenção |
| Novos candidatos emergentes | Tarcísio +9pp em 6 meses | 🔴 ALERTA |

### Bias por Grupo
| Grupo | Brier Score | vs. Média | Status |
|-------|-------------|-----------|--------|
| Nordeste (renda < R$1.500) | 0.18 | +0.03 | ⚠️ Atenção |
| Sul/Sudeste (renda > R$4.000) | 0.14 | -0.01 | ✅ OK |
| Municípios < 10k hab. | 0.21 | +0.06 | 🔴 ALERTA |
| Alta penetração digital | 0.15 | 0.00 | ✅ OK |

### Canário
Sem deployment de canário ativo. Último deploy: `v2.3.1` em 15/04/2026.

### Recomendação
Google Trends, sentimento X e emergência de Tarcísio requerem atenção urgente.
Retrain automático via Pub/Sub → Vertex AI Pipeline disparado em < 2h.

---
*gemini-2.0-flash | sessão: $0.00031 / $2.00*
""",
    "/relatorio": """\
*→ narrador*

## Análise Eleitoral — Brasil | Panorama abril/2026

O cenário eleitoral brasileiro de 2026 é o mais complexo desde a redemocratização em termos de fragmentação de informação. Integrando dados de pesquisas eleitorais, redes sociais, publicidade política e histórico do TSE, o SPEPE identifica três forças em disputa: a base consolidada de Lula no Nordeste e entre os mais pobres, a base conservadora de Bolsonaro no agronegócio e nas igrejas evangélicas, e o crescimento acelerado de Tarcísio entre eleitores que buscam uma alternativa ao ciclo PT-Bolsonaro.

Os dados digitais revelam algo que as pesquisas tradicionais ainda não capturaram completamente: o interesse por Tarcísio nas buscas do Google cresceu 28% em abril, e seu conteúdo no YouTube já ultrapassa 19 milhões de visualizações mensais com sentimento mais positivo que os demais candidatos. Isso não garante votos — mas historicamente, candidatos com esse padrão de crescimento digital tendem a superar as pesquisas no dia da eleição.

As pesquisas, com house effect ajustado para cinco institutos, mostram Lula estável em torno de 35% e Bolsonaro recuperando terreno, chegando a 32%. A margem entre os dois é menor do que a margem de erro combinada — o que significa que qualquer previsão hoje carrega incerteza muito alta. Esta análise é gerada pelo SPEPE para fins educacionais e de pesquisa, não substitui análise eleitoral profissional.

---
*Análise gerada pelo SPEPE para fins educacionais e de pesquisa.*
*Fontes: TSE · IBGE · Datafolha · Quaest · AtlasIntel · Google Trends · Meta Ad Library · YouTube*

*gemini-2.0-flash | sessão: $0.00024 / $2.00*
""",
}

_WELCOME = """\
## SPEPE — Sistema de Perfilamento do Eleitorado e Previsão Eleitoral

Bem-vindo ao protótipo interativo. Use os botões abaixo ou digite um comando.

**Fontes integradas:** TSE · IBGE · Pesquisas eleitorais · Google Trends · Meta Ads · YouTube · X

**Modo:** Protótipo (respostas demonstrativas)
"""

_HELP = """\
## Comandos disponíveis

| Comando | O que faz | Agente |
|---------|-----------|--------|
| `/coletar SP 2022` | Ingere dados TSE de uma UF/ano | Coletor |
| `/social BR 2026` | Coleta sinal digital: Trends, Meta, YouTube, X | Coletor |
| `/pesquisas presidente 2026` | Agrega pesquisas com house effect ajustado | Analista |
| `/perfil São Paulo 2022` | Perfil eleitoral + socioeconômico + digital | Analista |
| `/arquétipos BR` | Clusters do eleitorado (IBGE + TSE + digital) | Perfilador |
| `/prever Lula 2026` | Previsão integrada: pesquisas + digital + histórico | Modelista → Explicador → Narrador |
| `/relatorio` | Resumo executivo com todas as fontes | Narrador |
| `/monitorar` | Drift de features socioeconômicas e digitais | Vigilante |
| `/help` | Esta mensagem | — |

*Arquitetura: Claude Sonnet 4.6 (roteamento) + Google Gemini (execução) via Vertex AI*
*Budget por sessão: $2.00*
"""


def _match_command(text: str) -> str | None:
    t = text.strip().lower()
    pairs = [
        ("/social", ["social"]),
        ("/pesquisas", ["pesquisas", "polls", "pesquisa"]),
        ("/coletar", ["coletar"]),
        ("/perfil", ["perfil"]),
        ("/prever", ["prever", "previsão", "previsao"]),
        ("/arquétipos", ["arquétipos", "arquetipos", "arquétipos", "clusters"]),
        ("/monitorar", ["monitorar", "monitor", "drift"]),
        ("/relatorio", ["relatorio", "relatório", "report"]),
    ]
    for cmd, aliases in pairs:
        if t.startswith(cmd) or any(t.startswith(a) for a in aliases):
            return cmd
    return None


async def _stream(msg: cl.Message, text: str, delay: float = 0.010) -> None:
    for char in text:
        msg.content += char
        await msg.update()
        if char in (".", "\n", "|", "-"):
            await asyncio.sleep(delay * 3)
        else:
            await asyncio.sleep(delay)


@cl.on_chat_start
async def on_start() -> None:
    session_id = str(uuid.uuid4())
    cl.user_session.set("session_id", session_id)

    actions = [
        cl.Action(name="cmd", payload={"value": "/coletar SP 2022"}, label="📥 /coletar SP 2022"),
        cl.Action(name="cmd", payload={"value": "/social BR 2026"}, label="📱 /social BR 2026"),
        cl.Action(
            name="cmd", payload={"value": "/pesquisas presidente 2026"}, label="📊 /pesquisas 2026"
        ),
        cl.Action(
            name="cmd", payload={"value": "/perfil São Paulo 2022"}, label="🔍 /perfil SP 2022"
        ),
        cl.Action(name="cmd", payload={"value": "/prever Lula 2026"}, label="🎯 /prever Lula 2026"),
        cl.Action(name="cmd", payload={"value": "/arquétipos BR"}, label="🗂️ /arquétipos BR"),
        cl.Action(name="cmd", payload={"value": "/monitorar"}, label="👁️ /monitorar"),
        cl.Action(name="cmd", payload={"value": "/relatorio"}, label="📄 /relatorio"),
    ]

    await cl.Message(
        content=_WELCOME + f"\n*Sessão `{session_id[:8]}` | Budget: $2.00*",
        actions=actions,
    ).send()


@cl.action_callback("cmd")
async def on_action(action: cl.Action) -> None:
    cmd = action.payload.get("value", "") if action.payload else ""
    await cl.Message(content=cmd, author="Usuário").send()
    await _handle(cmd)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    await _handle(message.content.strip())


async def _handle(user_input: str) -> None:
    if user_input.lower() in ("/help", "help", "ajuda"):
        await cl.Message(content=_HELP).send()
        return

    cmd = _match_command(user_input)
    if not cmd:
        await cl.Message(
            content=(
                "Comando não reconhecido. Use `/help` para ver os comandos.\n\n"
                "*Dica: experimente `/social BR 2026`, `/pesquisas presidente 2026` ou `/prever Lula 2026`*"
            )
        ).send()
        return

    msg = cl.Message(content="")
    await msg.send()
    await _stream(msg, _MOCK[cmd])
