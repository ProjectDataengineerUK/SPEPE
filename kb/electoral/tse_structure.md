# Estrutura dos Dados TSE

## Fonte
Repositório de Dados Eleitorais do TSE: `https://dados.tse.jus.br/dataset/`
Arquivos CSV por UF, ano e cargo. Encoding: `latin-1` ou `utf-8-sig`.

## Cargos e Códigos (`cd_cargo`)

| cd_cargo | Descrição | Turnos |
|----------|-----------|--------|
| 1 | Presidente | 1 e 2 |
| 3 | Governador | 1 e 2 |
| 5 | Senador | 1 apenas |
| 6 | Deputado Federal | 1 apenas |
| 7 | Deputado Estadual | 1 apenas |
| 8 | Deputado Distrital (DF) | 1 apenas |
| 11 | Prefeito | 1 e 2 |
| 13 | Vereador | 1 apenas |

## Colunas Principais (resultados por seção)

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `sg_uf` | str | Sigla UF (ex: SP, RJ) |
| `cd_municipio` | int | Código TSE do município (diferente do IBGE) |
| `nm_municipio` | str | Nome do município |
| `nr_zona` | int | Número da zona eleitoral |
| `nr_secao` | int | Número da seção |
| `nr_turno` | int | 1 ou 2 |
| `ds_cargo` | str | Descrição do cargo |
| `cd_cargo` | int | Código do cargo |
| `nr_candidato` | int | Número do candidato na urna |
| `nm_candidato` | str | Nome completo do candidato |
| `nm_urna_candidato` | str | Nome na urna |
| `sg_partido` | str | Sigla do partido |
| `nr_partido` | int | Número do partido |
| `qt_votos` | int | Votos válidos para este candidato nesta seção |
| `qt_comparecimento` | int | Eleitores que compareceram |
| `qt_abstencoes` | int | Abstenções |
| `qt_votos_brancos` | int | Votos em branco |
| `qt_votos_nulos` | int | Votos nulos |

## Observações Críticas

- `cd_municipio` TSE ≠ `cd_municipio` IBGE — exige tabela de depara (`dataops/depara_municipios.py`)
- Votos de legenda (partidos) têm `nr_candidato = 0` — filtrar para análise por candidato
- Seções com menos de 50 votos podem ser pontos de votação especiais (hospitais, presídios)
- Candidatos com `nm_candidato = "BRANCOS"` ou `"NULOS"` são registros agregados

## Partidos Principais (2022)

| Partido | Nº | Espectro |
|---------|----|---------|
| PT | 13 | Esquerda |
| PL | 22 | Direita |
| União Brasil | 44 | Centro-direita |
| MDB | 15 | Centro |
| PSDB | 45 | Centro |
| Republicanos | 10 | Centro-direita |
| PDT | 12 | Centro-esquerda |
| PP | 11 | Centro-direita |
| PSD | 55 | Centro |
| PSOL | 50 | Esquerda |
