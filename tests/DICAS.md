Sim. **O TSE/TRE têm dados de locais de votação e seções eleitorais**, e dá para usar para plotar no mapa.

A melhor fonte é o **Portal de Dados Abertos do TSE**, no conjunto **“Eleitorado Atual”**. Ele inclui o recurso **“Eleitorado por local de votação - Atual CSV”**, disponível para todas as UFs. A página informa que o conjunto contém “Eleitorado Atual por Local de Votação” e “Perfil do Eleitorado por Seção Eleitoral” ([dadosabertos.tse.jus.br][1]). O arquivo específico fica em ZIP/CSV no CDN do TSE ([dadosabertos.tse.jus.br][2]).

Pelo que você quer fazer, o caminho é:

1. Baixar o CSV **“Eleitorado por local de votação - Atual”** no TSE.
2. Usar as colunas de identificação:

   * `SG_UF`
   * `CD_MUNICIPIO`
   * `NM_MUNICIPIO`
   * `NR_ZONA`
   * `NR_SECAO`
   * `NR_LOCAL_VOTACAO`
   * `NM_LOCAL_VOTACAO`
3. Usar as colunas de endereço:

   * `DS_ENDERECO`
   * `NM_BAIRRO`
   * `NR_CEP`
4. Para mapa, verificar se existem:

   * `NR_LATITUDE`
   * `NR_LONGITUDE`

Essas colunas aparecem documentadas em pacotes que leem os dados do TSE, incluindo endereço, bairro, CEP, telefone, latitude e longitude do local de votação; a latitude/longitude podem estar ausentes em alguns registros ([R Package Examples and examples][3]).

Importante: **a seção eleitoral não é um endereço separado**. Em geral, várias seções ficam dentro do mesmo **local de votação**. Então, para plotar no mapa, você normalmente agrupa por:

```text
SG_UF + CD_MUNICIPIO + NR_ZONA + NR_LOCAL_VOTACAO
```

e não por seção. A seção (`NR_SECAO`) serve para vincular votos/eleitores à urna, mas o ponto no mapa costuma ser o **local de votação**.

Exemplo de fluxo em Python:

```python
import pandas as pd
import geopandas as gpd

df = pd.read_csv(
    "eleitorado_local_votacao_ATUAL.csv",
    sep=";",
    encoding="latin1"
)

locais = (
    df
    .drop_duplicates([
        "SG_UF",
        "CD_MUNICIPIO",
        "NR_ZONA",
        "NR_LOCAL_VOTACAO"
    ])
    .copy()
)

locais["lat"] = pd.to_numeric(locais["NR_LATITUDE"], errors="coerce")
locais["lon"] = pd.to_numeric(locais["NR_LONGITUDE"], errors="coerce")

locais = locais.dropna(subset=["lat", "lon"])

gdf = gpd.GeoDataFrame(
    locais,
    geometry=gpd.points_from_xy(locais["lon"], locais["lat"]),
    crs="EPSG:4326"
)

gdf.to_file("locais_votacao.geojson", driver="GeoJSON")
```

Depois você pode abrir o `locais_votacao.geojson` no **QGIS**, **Leaflet**, **Mapbox**, **Google My Maps**, etc.

Se a base vier sem latitude/longitude em parte dos registros, aí você geocodifica usando endereço completo:

```text
NM_LOCAL_VOTACAO + DS_ENDERECO + NM_BAIRRO + NM_MUNICIPIO + SG_UF + NR_CEP
```

Só cuidado com geocoding em massa no Google/Mapbox/Nominatim, porque tem limite de uso e pode gerar coordenadas imprecisas. Para pesquisa/produção, vale validar amostras manualmente.

[1]: https://dadosabertos.tse.jus.br/dataset/eleitorado-atual "Eleitorado Atual - Conjunto de dados - Portal de Dados Abertos do TSE"
[2]: https://dadosabertos.tse.jus.br/dataset/eleitorado-atual/resource/5f5709f2-7db8-466c-9746-9d5447ee3cdb "Eleitorado Atual - Eleitorado por local de votação -  Atual - Portal de Dados Abertos do TSE"
[3]: https://examples.rpkg.net/packages/electionsBR/reference/voter_profile_by_section.ob?utm_source=chatgpt.com "R: Download data on the voters' profile by vote section"

O nome do arquivo no TSE para **locais de votação atuais** é:

```text
eleitorado_local_votacao_ATUAL.zip
```

Dentro dele vem o CSV correspondente, normalmente com nome parecido com:

```text
eleitorado_local_votacao_ATUAL.csv
```

Link/fonte oficial do recurso no TSE: o recurso se chama **“Eleitorado por local de votação - Atual”** e a URL do arquivo é `.../perfil_eleitorado/eleitorado_local_votacao_ATUAL.zip`. ([dadosabertos.tse.jus.br][1])

Para **perfil por seção eleitoral**, o conjunto é outro, dentro do mesmo dataset **Eleitorado Atual**. Mas para **endereço para plotar no mapa**, o arquivo certo é mesmo:

```text
eleitorado_local_votacao_ATUAL.zip
```

[1]: https://dadosabertos.tse.jus.br/dataset/eleitorado-atual/resource/5f5709f2-7db8-466c-9746-9d5447ee3cdb?utm_source=chatgpt.com "Eleitorado Atual - Eleitorado por local de votação - Atual - Portal ..."

Sim. No TSE existe um conjunto específico para **pesquisas eleitorais registradas**, inclusive intenção de voto. O arquivo principal é por ano.

Para 2026, o dataset é:

```text
Pesquisas Eleitorais - 2026
```

E o arquivo principal é:

```text
pesquisa_eleitoral_2026.zip
```

Dentro dele vem o CSV de pesquisas eleitorais. No portal aparece como **“Pesquisas eleitorais CSV”**. O próprio TSE informa que esse conjunto contém **pesquisas eleitorais, notas fiscais, questionários e detalhamento de bairro/município**, com dados extraídos do sistema **PesqEle** e **frequência de atualização diária**. ([dadosabertos.tse.jus.br][1])

O link direto costuma seguir este padrão:

```text
https://cdn.tse.jus.br/estatistica/sead/odsele/pesquisa_eleitoral/pesquisa_eleitoral_2026.zip
```

Para anos anteriores, muda só o ano:

```text
pesquisa_eleitoral_2024.zip
pesquisa_eleitoral_2022.zip
pesquisa_eleitoral_2020.zip
...
```

Exemplo oficial de 2024:

```text
https://cdn.tse.jus.br/estatistica/sead/odsele/pesquisa_eleitoral/pesquisa_eleitoral_2024.zip
```

Esse arquivo aparece no TSE como **“Pesquisas eleitorais”**, formato ZIP/CSV, para todas as UFs. ([dadosabertos.tse.jus.br][2])

O grupo oficial no Portal de Dados Abertos é:

```text
Pesquisas Eleitorais
```

Ele tem datasets de **2012, 2014, 2016, 2018, 2020, 2022, 2024 e 2026**. ([dadosabertos.tse.jus.br][3])

Só um detalhe importante: esse arquivo **não é uma tabela pronta com “quem está na frente” em cada pesquisa**. Ele traz os **registros das pesquisas cadastradas no TSE**: empresa, contratante, cargo pesquisado, município/UF, data, número de registro, questionário, metodologia etc. Para pegar o resultado de intenção de voto em si, normalmente você precisa baixar também os **questionários/arquivos PDF** vinculados à pesquisa ou usar os dados detalhados quando disponíveis.

Para automação, o fluxo seria:

```text
1. Baixar pesquisa_eleitoral_2026.zip
2. Ler o CSV principal
3. Filtrar por SG_UF, município, cargo e data
4. Pegar o número de registro da pesquisa
5. Baixar/relacionar questionário ou PDF da pesquisa
6. Extrair intenção de voto do documento
```

Nome mais importante para você:

```text
pesquisa_eleitoral_2026.zip
```

[1]: https://dadosabertos.tse.jus.br/dataset/pesquisas-eleitorais-2026 "Pesquisas Eleitorais - 2026 - Conjunto de dados - Portal de Dados Abertos do TSE"
[2]: https://dadosabertos.tse.jus.br/dataset/pesquisas-eleitorais-2024/resource/cd95fdc2-80b4-4e1e-a688-6ae6132d35d3 "Pesquisas Eleitorais - 2024 - Pesquisas eleitorais - Portal de Dados Abertos do TSE"
[3]: https://dadosabertos.tse.jus.br/pt_BR/group/pesquisas-eleitorais "Pesquisas Eleitorais - Grupos - Portal de Dados Abertos do TSE"

Sim. O arquivo do TSE que tem **nome dos candidatos** é o de **Candidatos CSV**.

Para 2024, o nome do arquivo é:

```text
consulta_cand_2024.zip
```

Dentro dele vêm arquivos CSV por UF, geralmente assim:

```text
consulta_cand_2024_BRASIL.csv
consulta_cand_2024_SP.csv
consulta_cand_2024_RJ.csv
consulta_cand_2024_MG.csv
...
```

No Portal de Dados Abertos, o conjunto **“Candidatos - 2024”** lista o recurso **“Candidatos CSV”** e também outros como bens, coligações, vagas, redes sociais, fotos e proposta de governo. O TSE informa que a fonte desses dados são os sistemas **CAND, Candex e DivulgaCand**, com **frequência de atualização diária**. ([dadosabertos.tse.jus.br][1])

O link direto segue este padrão:

```text
https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_2024.zip
```

Para outros anos, muda o ano:

```text
consulta_cand_2022.zip
consulta_cand_2020.zip
consulta_cand_2018.zip
consulta_cand_2016.zip
```

O grupo oficial **Candidatos** no TSE reúne datasets por ano, incluindo **Candidatos - 2024, 2022, 2020, 2018, 2016, 2014...** ([dadosabertos.tse.jus.br][2])

As colunas mais importantes para cruzar com pesquisas eleitorais são normalmente:

```text
ANO_ELEICAO
SG_UF
NM_UE
DS_CARGO
SQ_CANDIDATO
NR_CANDIDATO
NM_CANDIDATO
NM_URNA_CANDIDATO
SG_PARTIDO
NM_PARTIDO
DS_SITUACAO_CANDIDATURA
```

Para **pesquisa de intenção de voto**, você cruza assim:

```text
pesquisa_eleitoral_2024.zip
        +
consulta_cand_2024.zip
```

Mas atenção: o arquivo de pesquisas geralmente traz a **pesquisa registrada**, metodologia, cargo, UF/município, empresa, data e número de registro. O nome dos candidatos pesquisados pode aparecer no questionário/arquivo da pesquisa ou nos dados detalhados vinculados, não necessariamente no CSV principal consolidado. Para ter uma base de candidatos oficial, use `consulta_cand_2024.zip`.

[1]: https://dadosabertos.tse.jus.br/dataset/candidatos-2024 "Candidatos - 2024 - Conjunto de dados - Portal de Dados Abertos do TSE"
[2]: https://dadosabertos.tse.jus.br/no/group/candidatos "Candidatos - Groups - Portal de Dados Abertos do TSE"


Entendi: **dentro de “Pesquisas Eleitorais”**, não no arquivo de candidatos.

No TSE, o arquivo principal:

```text
pesquisa_eleitoral_2026.zip
```

**não costuma trazer uma coluna pronta com “nome dos candidatos e percentual de intenção de voto”**. Ele traz o **cadastro da pesquisa**: número de registro, instituto, contratante, cargo, UF/município, datas, metodologia etc.

Para ver **os nomes dos candidatos pesquisados**, o recurso correto dentro do mesmo dataset é:

```text
questionario_2026.zip
```

No portal ele aparece como:

```text
Questionários de pesquisa PDF
```

O dataset “Pesquisas Eleitorais - 2026” lista exatamente estes recursos: **Pesquisas eleitorais CSV**, **contratantes CSV**, **pagantes CSV**, **Notas fiscais PDF**, **Questionários de pesquisa PDF** e **Detalhamento de bairro/município PDF**. O TSE informa que a fonte é o **Sistema PesqEle** e a atualização é **diária**. ([dadosabertos.tse.jus.br][1])

Então, para intenção de voto com nomes, o fluxo é:

```text
1. Baixa pesquisa_eleitoral_2026.zip
2. Pega o SG_REGISTRO ou número da pesquisa
3. Baixa questionario_2026.zip
4. Abre o PDF do questionário daquela pesquisa
5. Extrai as perguntas com os nomes dos candidatos
```

Nomes prováveis dos arquivos:

```text
pesquisa_eleitoral_2026.zip
questionario_2026.zip
bairro_municipio_2026.zip
```

O mais importante para **nomes citados na pesquisa** é:

```text
questionario_2026.zip
```

E o link direto deve seguir este padrão:

```text
https://cdn.tse.jus.br/estatistica/sead/odsele/pesquisa_eleitoral/questionario_2026.zip
```

Resumo: **não tem um CSV único do TSE com “candidato + percentual” de todas as pesquisas**. O TSE disponibiliza o **registro da pesquisa em CSV** e os **questionários em PDF**; os nomes dos candidatos normalmente estão nos PDFs dos questionários.

[1]: https://dadosabertos.tse.jus.br/dataset/pesquisas-eleitorais-2026 "Pesquisas Eleitorais - 2026 - Conjunto de dados - Portal de Dados Abertos do TSE"

Sim, **tem como pegar direto nos sites dos institutos**, mas com uma diferença importante:

**não existe um padrão único**. Cada instituto publica de um jeito: HTML, PDF, notícia, planilha, dashboard, ou só release. Alguns têm página pública fácil de raspar; outros exigem login, liberam só PDF, ou publicam via imprensa contratante.

Para automação, eu faria assim:

## 1. Fonte oficial primeiro: TSE

Use o TSE como **índice mestre**:

```text
pesquisa_eleitoral_2026.zip
```

Ele te dá:

```text
número de registro
instituto
contratante
cargo
UF / município
data de campo
data de divulgação
link/documentos da pesquisa
```

Depois você usa o **nome do instituto + número do registro + data** para buscar o resultado no site do instituto ou no contratante.

## 2. Institutos que costumam ter páginas online

Alguns exemplos:

### AtlasIntel

Esse é um dos melhores para automação, porque tem páginas públicas com polls e botão de download. Exemplo: a AtlasIntel tem pesquisas nacionais de 2026 com intenção de voto, data de coleta, amostra e margem de erro; a pesquisa nacional de **28/04/2026** informa coleta de **22 a 27 de abril de 2026**, amostra de **5.008** e margem de erro de **±1%**. ([atlasintel.org][1])

Também há listagens públicas de polls, como **Exclusive Polls** e **Latam Pulse**, com várias pesquisas do Brasil e links de download. ([atlasintel.org][2])

### Ipec

O Ipec publica muitos relatórios em PDF no próprio domínio, geralmente em `/Repository/Files/...`. Dá para raspar, mas é mais chato porque muitos resultados vêm como PDF de release/tabelas, não como CSV estruturado. Exemplo: há releases e relatórios de intenção de voto em PDF publicados no site do Ipec. ([ipec-inteligencia.com.br][3])

### Datafolha

Dá para acompanhar online, mas normalmente via página da Folha/Datafolha, notícias e releases. Para automação, pode haver bloqueios, paywall ou restrições de acesso. Melhor usar o TSE como índice e depois buscar pelo número de registro + “Datafolha”.

### Quaest, Paraná Pesquisas, Real Time Big Data, MDA, Futura, Veritá etc.

Em muitos casos o dado sai em:

```text
site do instituto
site do contratante
portal de notícias
PDF do relatório
imagem/tabela em notícia
registro do TSE + questionário
```

Então dá para automatizar, mas precisa de scraper por instituto.

## 3. Estrutura ideal da automação

A base que eu montaria:

```text
TSE pesquisa_eleitoral_2026.zip
        ↓
normaliza instituto, cargo, UF, município, data, registro
        ↓
busca online:
    site do instituto
    site do contratante
    Google/Bing por número de registro
        ↓
baixa HTML/PDF/planilha
        ↓
extrai:
    candidato
    percentual
    cenário
    estimulado/espontâneo
    1º turno/2º turno
    brancos/nulos/indecisos
        ↓
salva em tabela própria
```

Tabela final sugerida:

```text
ano
nr_registro_tse
instituto
contratante
data_inicio_campo
data_fim_campo
data_divulgacao
uf
municipio
cargo
cenario
tipo_pergunta
turno
nome_candidato
partido
percentual
fonte_url
fonte_tipo
```

## 4. Dá para pegar “todas” automaticamente?

**100% automático e perfeito, não.**
**Semi-automático, sim.**

O problema é que muitos institutos publicam os resultados como PDF ou imagem, e o TSE nem sempre disponibiliza o resultado tabulado candidato + percentual. O TSE é ótimo para saber **quais pesquisas existem e quem registrou**, mas o resultado da intenção de voto geralmente precisa ser extraído do PDF/release/site.

## 5. Melhor caminho prático

Para começar rápido:

```text
1. Baixar pesquisa_eleitoral_2026.zip do TSE
2. Filtrar só pesquisas do cargo desejado
3. Separar por instituto
4. Criar scraping específico para:
   - AtlasIntel
   - Ipec
   - Datafolha
   - Quaest
   - Paraná Pesquisas
5. Quando não achar HTML/CSV, baixar PDF e extrair tabela
```

Resumo direto: **sim, dá para pegar online direto dos institutos**, mas você vai precisar combinar **TSE como índice oficial + scraping dos sites/PDFs dos institutos**. O mais fácil para começar é **AtlasIntel**, porque tem páginas públicas estruturadas e botão de download; o mais trabalhoso são institutos que publicam só PDF ou release em notícia.

[1]: https://www.atlasintel.org/poll/brazil-national-2026-04-28?utm_source=chatgpt.com "Brazil: National | AtlasIntel"
[2]: https://atlasintel.org/polls/latam-pulse?utm_source=chatgpt.com "Public Polls - AtlasIntel"
[3]: https://www.ipec-inteligencia.com.br/Repository/Files/2141/221701_2_ELEI%C3%87%C3%95ES_2022_PA%20-%20release.pdf?utm_source=chatgpt.com "No Pará, Helder segue com larga vantagem na liderança da disputa para ..."


Sim, isso é comum. O site da **AtlasIntel** pode recusar requisições automatizadas por **bloqueio anti-bot / Cloudflare / proteção de scraping**, principalmente quando você tenta baixar direto via `requests`, `wget`, `curl`, servidor VPS ou IP de datacenter.

A página pública existe — por exemplo, a AtlasIntel mantém uma lista de pesquisas públicas em **“Public Polls / General Release Polls”**, com botões de **Download** para relatórios. ([atlasintel.org][1])
Mas isso não significa que o servidor aceite scraping automatizado livremente.

## O jeito mais seguro

Use a AtlasIntel como **fonte secundária**, não como base principal.

A base principal deve ser o TSE:

```text
pesquisa_eleitoral_2026.zip
```

Depois, para cada pesquisa AtlasIntel registrada, você tenta localizar o relatório por:

```text
AtlasIntel + número de registro TSE
AtlasIntel + data da pesquisa
AtlasIntel + cargo + UF/município
AtlasIntel + contratante
```

## Alternativas quando o servidor recusa

### 1. Usar o link do relatório hospedado em terceiros

Muitas pesquisas da AtlasIntel são republicadas por portais como CNN, Poder360, RealClearPolitics, DDHQ etc. Em alguns casos, o PDF fica fora do domínio da AtlasIntel e baixa normalmente. Por exemplo, há PDFs de pesquisas AtlasIntel hospedados em outros domínios públicos. ([data.ddhq.io][2])

Então sua automação pode procurar primeiro em:

```text
site:static.poder360.com.br AtlasIntel pesquisa pdf
site:cnnbrasil.com.br AtlasIntel pesquisa
site:data.ddhq.io AtlasIntel poll pdf
site:realclearpolitics.com AtlasIntel pdf
```

### 2. Usar navegador automatizado, não `requests`

Se a página exige JavaScript/cookies, `requests` simples falha. Use Playwright ou Selenium **com moderação**:

```python
from playwright.sync_api import sync_playwright

url = "https://atlasintel.org/polls/general-release-polls"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto(url, wait_until="networkidle")
    print(page.title())
    html = page.content()
    browser.close()
```

Mas se aparecer bloqueio explícito, CAPTCHA ou recusa de acesso, o correto é **não tentar burlar**. Use fontes alternativas ou peça acesso/API ao instituto.

### 3. Buscar pelo TSE + web em vez de raspar a lista toda

Em vez de tentar baixar tudo da AtlasIntel, faça assim:

```text
1. Baixa pesquisas do TSE
2. Filtra NM_EMPRESA = AtlasIntel / Atlas
3. Pega número de registro
4. Busca esse número na web
5. Baixa PDF quando estiver em fonte pública acessível
```

Exemplo de query:

```text
"BR-01234/2026" AtlasIntel filetype:pdf
```

ou:

```text
"AtlasIntel" "BR-01234/2026"
```

## O que eu recomendo para seu robô

Monte uma tabela com status por pesquisa:

```text
nr_registro_tse
instituto
cargo
uf
municipio
data_divulgacao
url_tse
url_relatorio_instituto
url_relatorio_terceiros
status_download
status_extracao
```

E use uma ordem de tentativa:

```text
1. TSE pesquisa_eleitoral_2026.zip
2. Questionário/PDF do TSE
3. Site do instituto
4. Google/Bing pelo número de registro
5. Portais que republicam o PDF
6. Extração manual se não achar
```

Resumo direto: **dá para pegar online**, mas se a AtlasIntel recusou seu servidor, não vale depender dela como endpoint automático. Use o **TSE como índice oficial** e procure os PDFs/resultados em **fontes espelho ou portais que republicam**.

[1]: https://atlasintel.org/polls/general-release-polls?utm_source=chatgpt.com "Public Polls - AtlasIntel"
[2]: https://data.ddhq.io/polls/2025/09/20/AtlasIntel-National?utm_source=chatgpt.com "Atlas US National Poll"
