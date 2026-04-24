# Indicadores IBGE usados como Features

## Fontes

| Fonte | API / Acesso | Granularidade |
|-------|-------------|---------------|
| IBGE SIDRA | `https://apisidra.ibge.gov.br/` | Município |
| IBGE Localidades | `https://servicodados.ibge.gov.br/api/v1/localidades/` | Município |
| Censo 2022 | Arquivos CSV no FTP IBGE | Setor censitário |
| PNAD Contínua | SIDRA tabelas 4099, 6318 | UF trimestral |

## Indicadores Principais (features do modelo)

| Feature SPEPE | Tabela SIDRA | Descrição | Interpretação eleitoral |
|---------------|-------------|-----------|------------------------|
| `populacao` | 6579 | População residente | Volume do eleitorado |
| `renda_media_domiciliar` | 6691 | Rendimento médio mensal domiciliar | Proxy de classe econômica |
| `taxa_desemprego` | 4099 | Taxa de desocupação | Insatisfação econômica |
| `pct_analfabetos` | 9543 | % pop. sem instrução ou fund. incompleto | Vulnerabilidade informacional |
| `idhm` | derivado | IDH Municipal (Censo 2010, PNUD) | Desenvolvimento humano |
| `pct_rural` | 6579 | % pop. em área rural | Perfil urbano/rural |
| `pib_per_capita` | 5938 | PIB per capita municipal | Capacidade econômica |
| `pct_evangelicos` | Censo 2010 | % declarados evangélicos | Alinhamento religioso |
| `gini` | derivado | Índice de Gini municipal | Desigualdade |

## Correlações Históricas Conhecidas (2022)

- Municípios com `renda_media > R$3.000` tenderam ao Bolsonaro no 1º turno
- Municípios com `pct_rural > 50%` e `renda_media < R$1.500` tenderam ao Lula
- `taxa_desemprego` alta correlaciona positivamente com voto de oposição ao governo vigente
- `pct_evangelicos > 40%` correlaciona com candidatos conservadores
- Nordeste tem padrão histórico distinto — modelos treinados apenas no Sudeste têm bias

## Códigos UF (IBGE)

| UF | Código | UF | Código | UF | Código |
|----|--------|----|--------|----|--------|
| AC | 12 | MA | 21 | RJ | 33 |
| AL | 27 | MT | 51 | RN | 24 |
| AP | 16 | MS | 50 | RS | 43 |
| AM | 13 | MG | 31 | RO | 11 |
| BA | 29 | PA | 15 | RR | 14 |
| CE | 23 | PB | 25 | SC | 42 |
| DF | 53 | PR | 41 | SP | 35 |
| ES | 32 | PE | 26 | SE | 28 |
| GO | 52 | PI | 22 | TO | 17 |

## Observações

- Código IBGE município tem 7 dígitos; código TSE tem 5 — nunca confundir
- PNAD é amostral e disponível apenas por UF, não por município
- Censo 2022 ainda em divulgação parcial — alguns indicadores ainda usam Censo 2010
