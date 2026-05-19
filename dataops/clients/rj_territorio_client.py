"""RJ território client — mapeamento de municípios do RJ para regiões geográficas."""

from __future__ import annotations

import pandas as pd

MUNICIPIOS_RJ: dict[int, dict] = {
    # Capital (1)
    3304557: {"nm_municipio": "Rio de Janeiro", "regiao_rj": "Capital"},
    # Baixada Fluminense (13)
    3300506: {"nm_municipio": "Belford Roxo", "regiao_rj": "Baixada Fluminense"},
    3301850: {"nm_municipio": "Duque de Caxias", "regiao_rj": "Baixada Fluminense"},
    3302007: {"nm_municipio": "Guapimirim", "regiao_rj": "Baixada Fluminense"},
    3302205: {"nm_municipio": "Itaguaí", "regiao_rj": "Baixada Fluminense"},
    3302502: {"nm_municipio": "Japeri", "regiao_rj": "Baixada Fluminense"},
    3302900: {"nm_municipio": "Magé", "regiao_rj": "Baixada Fluminense"},
    3305554: {"nm_municipio": "Mesquita", "regiao_rj": "Baixada Fluminense"},
    3303601: {"nm_municipio": "Nilópolis", "regiao_rj": "Baixada Fluminense"},
    3303908: {"nm_municipio": "Nova Iguaçu", "regiao_rj": "Baixada Fluminense"},
    3304005: {"nm_municipio": "Paracambi", "regiao_rj": "Baixada Fluminense"},
    3304409: {"nm_municipio": "Queimados", "regiao_rj": "Baixada Fluminense"},
    3305703: {"nm_municipio": "São João de Meriti", "regiao_rj": "Baixada Fluminense"},
    3306304: {"nm_municipio": "Seropédica", "regiao_rj": "Baixada Fluminense"},
    # São Gonçalo / Niterói (4)
    3302106: {"nm_municipio": "Itaboraí", "regiao_rj": "São Gonçalo / Niterói"},
    3303106: {"nm_municipio": "Maricá", "regiao_rj": "São Gonçalo / Niterói"},
    3303700: {"nm_municipio": "Niterói", "regiao_rj": "São Gonçalo / Niterói"},
    3305505: {"nm_municipio": "São Gonçalo", "regiao_rj": "São Gonçalo / Niterói"},
    # Região dos Lagos (8)
    3300209: {"nm_municipio": "Araruama", "regiao_rj": "Região dos Lagos"},
    3300258: {"nm_municipio": "Armação dos Búzios", "regiao_rj": "Região dos Lagos"},
    3300308: {"nm_municipio": "Arraial do Cabo", "regiao_rj": "Região dos Lagos"},
    3300803: {"nm_municipio": "Cabo Frio", "regiao_rj": "Região dos Lagos"},
    3302056: {"nm_municipio": "Iguaba Grande", "regiao_rj": "Região dos Lagos"},
    3304953: {"nm_municipio": "Rio das Ostras", "regiao_rj": "Região dos Lagos"},
    3305900: {"nm_municipio": "São Pedro da Aldeia", "regiao_rj": "Região dos Lagos"},
    3306205: {"nm_municipio": "Saquarema", "regiao_rj": "Região dos Lagos"},
    # Norte Fluminense (9)
    3301108: {"nm_municipio": "Carapebus", "regiao_rj": "Norte Fluminense"},
    3301157: {"nm_municipio": "Cardoso Moreira", "regiao_rj": "Norte Fluminense"},
    3301306: {"nm_municipio": "Campos dos Goytacazes", "regiao_rj": "Norte Fluminense"},
    3301603: {"nm_municipio": "Conceição de Macabu", "regiao_rj": "Norte Fluminense"},
    3302700: {"nm_municipio": "Macaé", "regiao_rj": "Norte Fluminense"},
    3304508: {"nm_municipio": "Quissamã", "regiao_rj": "Norte Fluminense"},
    3305307: {"nm_municipio": "São Fidélis", "regiao_rj": "Norte Fluminense"},
    3305406: {"nm_municipio": "São Francisco de Itabapoana", "regiao_rj": "Norte Fluminense"},
    3305604: {"nm_municipio": "São João da Barra", "regiao_rj": "Norte Fluminense"},
    # Sul Fluminense (8)
    3300407: {"nm_municipio": "Barra do Piraí", "regiao_rj": "Sul Fluminense"},
    3300456: {"nm_municipio": "Barra Mansa", "regiao_rj": "Sul Fluminense"},
    3302452: {"nm_municipio": "Itatiaia", "regiao_rj": "Sul Fluminense"},
    3304151: {"nm_municipio": "Pinheiral", "regiao_rj": "Sul Fluminense"},
    3304201: {"nm_municipio": "Piraí", "regiao_rj": "Sul Fluminense"},
    3304383: {"nm_municipio": "Quatis", "regiao_rj": "Sul Fluminense"},
    3304607: {"nm_municipio": "Resende", "regiao_rj": "Sul Fluminense"},
    3307106: {"nm_municipio": "Volta Redonda", "regiao_rj": "Sul Fluminense"},
    # Serrana (12)
    3300902: {"nm_municipio": "Cachoeiras de Macacu", "regiao_rj": "Serrana"},
    3301405: {"nm_municipio": "Cantagalo", "regiao_rj": "Serrana"},
    3301702: {"nm_municipio": "Cordeiro", "regiao_rj": "Serrana"},
    3301801: {"nm_municipio": "Duas Barras", "regiao_rj": "Serrana"},
    3302800: {"nm_municipio": "Macuco", "regiao_rj": "Serrana"},
    3303809: {"nm_municipio": "Nova Friburgo", "regiao_rj": "Serrana"},
    3304144: {"nm_municipio": "Petrópolis", "regiao_rj": "Serrana"},
    3305109: {"nm_municipio": "Santa Maria Madalena", "regiao_rj": "Serrana"},
    3306502: {"nm_municipio": "Sumidouro", "regiao_rj": "Serrana"},
    3306601: {"nm_municipio": "Teresópolis", "regiao_rj": "Serrana"},
    3306700: {"nm_municipio": "Trajano de Moraes", "regiao_rj": "Serrana"},
    3305801: {"nm_municipio": "São José do Vale do Rio Preto", "regiao_rj": "Serrana"},
    # Médio Paraíba / Interior (restante — 37 municípios)
    3300100: {"nm_municipio": "Angra dos Reis", "regiao_rj": "Médio Paraíba / Interior"},
    3300159: {"nm_municipio": "Aperibé", "regiao_rj": "Médio Paraíba / Interior"},
    3300233: {"nm_municipio": "Areal", "regiao_rj": "Médio Paraíba / Interior"},
    3300605: {"nm_municipio": "Bom Jardim", "regiao_rj": "Médio Paraíba / Interior"},
    3300704: {"nm_municipio": "Bom Jesus do Itabapoana", "regiao_rj": "Médio Paraíba / Interior"},
    3301009: {"nm_municipio": "Cambuci", "regiao_rj": "Médio Paraíba / Interior"},
    3301207: {"nm_municipio": "Carmo", "regiao_rj": "Médio Paraíba / Interior"},
    3301504: {"nm_municipio": "Casimiro de Abreu", "regiao_rj": "Médio Paraíba / Interior"},
    3301876: {"nm_municipio": "Comendador Levy Gasparian", "regiao_rj": "Médio Paraíba / Interior"},
    3301900: {
        "nm_municipio": "Engenheiro Paulo de Frontin",
        "regiao_rj": "Médio Paraíba / Interior",
    },
    3302254: {"nm_municipio": "Italva", "regiao_rj": "Médio Paraíba / Interior"},
    3302304: {"nm_municipio": "Itaocara", "regiao_rj": "Médio Paraíba / Interior"},
    3302403: {"nm_municipio": "Itaperuna", "regiao_rj": "Médio Paraíba / Interior"},
    3302601: {"nm_municipio": "Laje do Muriaé", "regiao_rj": "Médio Paraíba / Interior"},
    3303007: {"nm_municipio": "Mangaratiba", "regiao_rj": "Médio Paraíba / Interior"},
    3303205: {"nm_municipio": "Mendes", "regiao_rj": "Médio Paraíba / Interior"},
    3303304: {"nm_municipio": "Miguel Pereira", "regiao_rj": "Médio Paraíba / Interior"},
    3303403: {"nm_municipio": "Miracema", "regiao_rj": "Médio Paraíba / Interior"},
    3303502: {"nm_municipio": "Natividade", "regiao_rj": "Médio Paraíba / Interior"},
    3304104: {"nm_municipio": "Paraíba do Sul", "regiao_rj": "Médio Paraíba / Interior"},
    3304110: {"nm_municipio": "Paraty", "regiao_rj": "Médio Paraíba / Interior"},
    3304128: {"nm_municipio": "Paty do Alferes", "regiao_rj": "Médio Paraíba / Interior"},
    3304300: {"nm_municipio": "Porciúncula", "regiao_rj": "Médio Paraíba / Interior"},
    3304359: {"nm_municipio": "Porto Real", "regiao_rj": "Médio Paraíba / Interior"},
    3304706: {"nm_municipio": "Rio Bonito", "regiao_rj": "Médio Paraíba / Interior"},
    3304805: {"nm_municipio": "Rio Claro", "regiao_rj": "Médio Paraíba / Interior"},
    3304904: {"nm_municipio": "Rio das Flores", "regiao_rj": "Médio Paraíba / Interior"},
    3305208: {"nm_municipio": "Santo Antônio de Pádua", "regiao_rj": "Médio Paraíba / Interior"},
    3305752: {"nm_municipio": "São José de Ubá", "regiao_rj": "Médio Paraíba / Interior"},
    3306007: {"nm_municipio": "São Sebastião do Alto", "regiao_rj": "Médio Paraíba / Interior"},
    3306106: {"nm_municipio": "Sapucaia", "regiao_rj": "Médio Paraíba / Interior"},
    3306403: {"nm_municipio": "Silva Jardim", "regiao_rj": "Médio Paraíba / Interior"},
    3306551: {"nm_municipio": "Tanguá", "regiao_rj": "Médio Paraíba / Interior"},
    3306800: {"nm_municipio": "Três Rios", "regiao_rj": "Médio Paraíba / Interior"},
    3306909: {"nm_municipio": "Valença", "regiao_rj": "Médio Paraíba / Interior"},
    3306958: {"nm_municipio": "Varre-Sai", "regiao_rj": "Médio Paraíba / Interior"},
    3307007: {"nm_municipio": "Vassouras", "regiao_rj": "Médio Paraíba / Interior"},
}


def get_regiao_rj(cd_municipio_ibge: int) -> str:
    entry = MUNICIPIOS_RJ.get(cd_municipio_ibge)
    if entry is None:
        return "Interior"
    return entry["regiao_rj"]


def build_dim_regiao_rj() -> pd.DataFrame:
    rows = [
        {
            "cd_municipio_ibge": code,
            "nm_municipio": info["nm_municipio"],
            "regiao_rj": info["regiao_rj"],
        }
        for code, info in MUNICIPIOS_RJ.items()
    ]
    return pd.DataFrame(rows, columns=["cd_municipio_ibge", "nm_municipio", "regiao_rj"])


def get_dim_regiao_rj_sql(project_id: str, gold_dataset: str) -> str:
    gold = f"{project_id}.{gold_dataset}"

    def _row(code: int, info: dict) -> str:
        nm = info["nm_municipio"]
        rg = info["regiao_rj"]
        return f"({code}, '{nm}', '{rg}')"

    rows = ",\n    ".join(_row(code, info) for code, info in MUNICIPIOS_RJ.items())
    return (
        f"CREATE OR REPLACE TABLE `{gold}.dim_regiao_rj` AS\n"
        "SELECT\n"
        "    cd_municipio_ibge,\n"
        "    nm_municipio,\n"
        "    regiao_rj,\n"
        "    CURRENT_TIMESTAMP() AS ingested_at\n"
        "FROM UNNEST([\n"
        "    STRUCT<cd_municipio_ibge INT64, nm_municipio STRING, regiao_rj STRING>\n"
        f"    {rows}\n"
        "])"
    )
