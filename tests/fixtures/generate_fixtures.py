"""Generate test fixture parquet files."""
import numpy as np
import pandas as pd
from pathlib import Path

OUT = Path(__file__).parent


def generate_sample_tse_2022(n: int = 1000) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    municipios_sp = [
        (355030, "SAO PAULO"), (354890, "SANTOS"), (354980, "SOROCABA"),
        (352900, "GUARULHOS"), (350950, "CAMPINAS"), (354140, "RIBEIRAO PRETO"),
        (353440, "OSASCO"), (352620, "GUARUJA"), (354410, "SAO JOSE DOS CAMPOS"),
        (352590, "FRANCA"),
    ]
    choices = rng.choice(len(municipios_sp), size=n)

    return pd.DataFrame({
        "CD_MUNICIPIO": [str(municipios_sp[i][0] // 10) for i in choices],
        "NM_MUNICIPIO": [municipios_sp[i][1] for i in choices],
        "SG_UF": ["SP"] * n,
        "NR_ZONA": rng.integers(1, 300, n),
        "NR_SECAO": rng.integers(1, 500, n),
        "NR_CANDIDATO": rng.choice([13, 22, 40, 45], size=n),
        "NM_CANDIDATO": rng.choice(
            ["LULA", "BOLSONARO", "CIRO", "SIMONE TEBET"], size=n
        ),
        "DS_CARGO": ["PRESIDENTE"] * n,
        "QT_VOTOS_NOMINAIS": rng.integers(50, 800, n),
        "SG_PARTIDO": rng.choice(["PT", "PL", "PDT", "MDB"], size=n),
    })


def generate_sample_ibge(n: int = 10) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    municipios = [
        3550308, 3304557, 1501402, 2927408, 4106902,
        3106200, 2304400, 1302603, 5300108, 4314902,
    ][:n]

    return pd.DataFrame({
        "cd_municipio_ibge": municipios,
        "cd_municipio_tse": [m // 10 for m in municipios],
        "nm_municipio": [f"Municipio_{i}" for i in range(n)],
        "sg_uf": ["SP", "RJ", "PA", "BA", "PR", "MG", "CE", "AM", "DF", "RS"][:n],
        "idhm_2010": rng.uniform(0.6, 0.9, n),
        "renda_media_domiciliar": rng.uniform(800, 3000, n),
        "anos_estudo_medio": rng.uniform(6, 12, n),
        "populacao_2022": rng.integers(50000, 5000000, n),
        "taxa_desemprego": rng.uniform(5, 25, n),
        "pct_analfabetos": rng.uniform(1, 15, n),
        "gini": rng.uniform(0.4, 0.7, n),
        "pib_per_capita": rng.uniform(10000, 80000, n),
        "pct_zona_rural": rng.uniform(0, 60, n),
    })


if __name__ == "__main__":
    tse = generate_sample_tse_2022()
    tse.to_parquet(OUT / "sample_tse_2022.parquet", index=False, compression="zstd")
    print(f"Written sample_tse_2022.parquet: {len(tse)} rows")

    ibge = generate_sample_ibge()
    ibge.to_parquet(OUT / "sample_ibge.parquet", index=False, compression="zstd")
    print(f"Written sample_ibge.parquet: {len(ibge)} rows")
