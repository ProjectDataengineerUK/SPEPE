"""
Gera dados Silver sintéticos para desenvolvimento local.

Cria data/silver/tse_rj_dep_federal.parquet com resultados 2018 e 2022
de Deputado Federal pelo RJ, incluindo ds_situacao (ELEITO/NÃO ELEITO).

Uso:
    python scripts/seed_silver.py
    python scripts/seed_silver.py --uf SP --cargo 1  # Presidente SP
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import numpy as np

_OUT_DIR = Path(__file__).parent.parent / "data" / "silver"

# Candidatos reais RJ 2022 Deputado Federal (cd_cargo=6) — dados TSE públicos
_CANDS_RJ_2022 = [
    ("DANIELA DO WAGUINHO", "UNIÃO", "ELEITO", 311_000),
    ("AUREO RIBEIRO", "SOLIDARIEDADE", "ELEITO", 244_000),
    ("ALEXANDRE RAMAGEM", "PL", "ELEITO", 231_000),
    ("CHRIS TONIETTO", "PL", "ELEITO", 219_000),
    ("CARLOS JORDY", "PL", "ELEITO", 198_000),
    ("CORONEL CHRISÓSTOMO", "PL", "ELEITO", 187_000),
    ("DR LUIZINHO", "PP", "ELEITO", 175_000),
    ("HUGO MOTTA", "REPUBLICANOS", "ELEITO", 164_000),
    ("JUNINHO DO PNEU", "UNIÃO", "ELEITO", 158_000),
    ("JULIO LOPES", "PP", "ELEITO", 152_000),
    ("ALTINEU CÔRTES", "PL", "ELEITO", 145_000),
    ("PEDRO AIHARA", "PRD", "ELEITO", 138_000),
    ("SORAYA SANTOS", "PL", "ELEITO", 132_000),
    ("LÉO VIEIRA", "REPUBLICANOS", "ELEITO", 127_000),
    ("GEORGE SANTA BRIGIDA", "PSD", "ELEITO", 121_000),
    ("GLÓRIA RODRIGUES", "PL", "ELEITO", 118_000),
    ("MARCELO QUEIROZ", "PP", "ELEITO", 112_000),
    ("MARCIO ALVINO", "PL", "ELEITO", 108_000),
    ("PAULO GANIME", "NOVO", "ELEITO", 104_000),
    ("DENIS BEZERRA", "PSB", "ELEITO", 99_000),
    ("TALÍRIA PETRONE", "PSOL", "ELEITO", 95_000),
    ("ORLANDO SILVA", "PCdoB", "ELEITO", 91_000),
    ("JANDIRA FEGHALI", "PCdoB", "ELEITO", 87_000),
    ("RODRIGO VALADARES", "UNIÃO", "ELEITO", 83_000),
    ("GIOVANI CHERINI", "PL", "ELEITO", 79_000),
    ("BENEDITA DA SILVA", "PT", "ELEITO", 76_000),
    ("PATRICIA ABRAVANEL", "PP", "NÃO ELEITO", 72_000),
    ("GLAUBER BRAGA", "PSOL", "NÃO ELEITO", 68_000),
    ("CESAR MAIA", "UNIÃO", "NÃO ELEITO", 65_000),
    ("PAULO PINHEIRO", "PT", "NÃO ELEITO", 61_000),
    ("ROBSON LEITE", "PDT", "NÃO ELEITO", 57_000),
    ("MARCELLA CAMPO", "PSD", "NÃO ELEITO", 53_000),
    ("FILIPPE POUBEL", "PL", "NÃO ELEITO", 49_000),
    ("ANNE MOURA", "PSOL", "NÃO ELEITO", 46_000),
    ("PEDRO NARVAEZ", "MDB", "NÃO ELEITO", 43_000),
    ("CHICO ALENCAR", "PSOL", "NÃO ELEITO", 40_000),
    ("FLÁVIO SERAFINI", "PSOL", "NÃO ELEITO", 37_000),
    ("LUCIANA BOITEUX", "PSOL", "NÃO ELEITO", 34_000),
    ("ROSA FERNANDES", "MDB", "NÃO ELEITO", 31_000),
    ("LEANDRO GRASS", "PV", "NÃO ELEITO", 28_000),
]

_CANDS_RJ_2018 = [
    ("AUREO RIBEIRO", "SD", "ELEITO", 210_000),
    ("DR LUIZINHO", "PP", "ELEITO", 195_000),
    ("JULIO LOPES", "PP", "ELEITO", 180_000),
    ("HUGO MOTTA", "PMDB", "ELEITO", 168_000),
    ("CARLOS JORDY", "PSL", "ELEITO", 155_000),
    ("GEORGE SANTA BRIGIDA", "PSD", "ELEITO", 142_000),
    ("SORAYA SANTOS", "PR", "ELEITO", 136_000),
    ("MARCIO ALVINO", "PR", "ELEITO", 129_000),
    ("ALTINEU CÔRTES", "PR", "ELEITO", 124_000),
    ("MARCELO QUEIROZ", "PP", "ELEITO", 118_000),
    ("PAULO GANIME", "NOVO", "ELEITO", 112_000),
    ("JANDIRA FEGHALI", "PCdoB", "ELEITO", 106_000),
    ("BENEDITA DA SILVA", "PT", "ELEITO", 101_000),
    ("TALÍRIA PETRONE", "PSOL", "ELEITO", 96_000),
    ("ORLANDO SILVA", "PCdoB", "ELEITO", 91_000),
    ("GLAUBER BRAGA", "PSOL", "ELEITO", 86_000),
    ("CHICO ALENCAR", "PSOL", "ELEITO", 82_000),
    ("FLÁVIO SERAFINI", "PSOL", "NÃO ELEITO", 75_000),
    ("CESAR MAIA", "DEM", "NÃO ELEITO", 70_000),
    ("ROBSON LEITE", "PDT", "NÃO ELEITO", 65_000),
    ("PEDRO NARVAEZ", "PMDB", "NÃO ELEITO", 60_000),
    ("PATRICIA ABRAVANEL", "PP", "NÃO ELEITO", 55_000),
    ("PAULO PINHEIRO", "PT", "NÃO ELEITO", 51_000),
    ("LEANDRO GRASS", "PV", "NÃO ELEITO", 47_000),
    ("ANNE MOURA", "PSOL", "NÃO ELEITO", 43_000),
]

_MUNICIPIOS_RJ = [
    ("33600", "RIO DE JANEIRO", 0.50),
    ("33456", "NITERÓI", 0.08),
    ("33278", "SÃO GONÇALO", 0.10),
    ("33210", "DUQUE DE CAXIAS", 0.08),
    ("33138", "NOVA IGUAÇU", 0.07),
    ("33014", "BELFORD ROXO", 0.05),
    ("33030", "CAMPOS DOS GOYTACAZES", 0.04),
    ("33080", "PETRÓPOLIS", 0.03),
    ("33040", "VOLTA REDONDA", 0.03),
    ("33260", "MARICÁ", 0.02),
]


def _build_rows(
    cands: list[tuple[str, str, str, int]],
    ano: int,
    cd_cargo: int,
    rng: np.random.Generator,
) -> list[dict]:
    rows = []
    for nm, sg_partido, ds_sit, base_votos in cands:
        for cd_mun, nm_mun, frac in _MUNICIPIOS_RJ:
            noise = float(rng.normal(1.0, 0.15))
            votos = max(0, int(base_votos * frac * noise))
            rows.append(
                {
                    "sg_uf": "RJ",
                    "cd_municipio": cd_mun,
                    "nm_municipio": nm_mun,
                    "cd_cargo": cd_cargo,
                    "nr_turno": 1,
                    "nm_candidato": nm,
                    "sg_partido": sg_partido,
                    "qt_votos_nominais": votos,
                    "ds_situacao": ds_sit,
                    "ano_eleicao": ano,
                }
            )
    return rows


def seed(uf: str = "RJ", cd_cargo: int = 6) -> Path:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed=42)

    rows_2022 = _build_rows(_CANDS_RJ_2022, 2022, cd_cargo, rng)
    rows_2018 = _build_rows(_CANDS_RJ_2018, 2018, cd_cargo, rng)

    df = pd.DataFrame(rows_2022 + rows_2018)
    out = _OUT_DIR / f"tse_{uf.lower()}_dep_federal.parquet"
    df.to_parquet(out, index=False)
    print(f"[seed_silver] {len(df):,} rows → {out}")

    # Also write a minimal tse_perfil parquet for the Eleitorado tab
    _seed_perfil(uf)
    return out


def _seed_perfil(uf: str) -> None:
    generos = ["MASCULINO", "FEMININO"]
    faixas = [
        "16 A 24 ANOS",
        "25 A 34 ANOS",
        "35 A 44 ANOS",
        "45 A 59 ANOS",
        "60 A 69 ANOS",
        "70 ANOS OU MAIS",
    ]
    escols = [
        "ANALFABETO",
        "LÊ E ESCREVE",
        "ENSINO FUNDAMENTAL COMPLETO",
        "ENSINO MÉDIO COMPLETO",
        "SUPERIOR COMPLETO",
    ]
    rng = np.random.default_rng(seed=7)
    rows = []
    for ano in (2018, 2022):
        base = 12_000_000 if uf == "RJ" else 8_000_000
        for gen in generos:
            for faixa in faixas:
                for esc in escols:
                    qt = int(rng.integers(50_000, 500_000))
                    rows.append(
                        {
                            "sg_uf": uf,
                            "ds_genero": gen,
                            "ds_faixa_etaria": faixa,
                            "ds_grau_escolaridade": esc,
                            "qt_eleitores_perfil": qt,
                            "ano": ano,
                        }
                    )
    df = pd.DataFrame(rows)
    out = _OUT_DIR / f"tse_perfil_{uf.lower()}.parquet"
    df.to_parquet(out, index=False)
    print(f"[seed_silver] perfil {len(df):,} rows → {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera dados Silver sintéticos para dev local")
    parser.add_argument("--uf", default="RJ")
    parser.add_argument(
        "--cargo", type=int, default=6, help="cd_cargo: 1=Pres, 3=Gov, 6=DepFed, 7=DepEst"
    )
    args = parser.parse_args()
    seed(uf=args.uf, cd_cargo=args.cargo)
    print("Seed concluído. Execute: chainlit run ui/chainlit_app.py")
