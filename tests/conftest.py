"""Fixtures compartilhadas para testes do SPEPE."""

import pytest


@pytest.fixture
def temp_data_dir(tmp_path):
    """Diretório temporário para dados de teste."""
    (tmp_path / "tse").mkdir()
    (tmp_path / "ibge").mkdir()
    (tmp_path / "processed").mkdir()
    return tmp_path


@pytest.fixture
def sample_tse_parquet(temp_data_dir):
    """Parquet TSE sintético para testes."""
    import pandas as pd
    import numpy as np

    n = 500
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "sg_uf": ["SP"] * n,
        "cd_municipio": rng.integers(10000, 99999, n),
        "nr_zona": rng.integers(1, 50, n),
        "nr_secao": rng.integers(1, 500, n),
        "nm_candidato": rng.choice(["LULA", "BOLSONARO", "CIRO"], n),
        "qt_votos": rng.integers(0, 500, n),
        "ds_cargo": ["PRESIDENTE"] * n,
    })
    path = temp_data_dir / "tse" / "resultados_SP_2022.parquet"
    df.to_parquet(path, index=False)
    return path


@pytest.fixture
def sample_ibge_parquet(temp_data_dir):
    """Parquet IBGE sintético para testes."""
    import pandas as pd
    import numpy as np

    rng = np.random.default_rng(42)
    n = 100
    df = pd.DataFrame({
        "cd_municipio_ibge": [str(i) for i in range(3500000, 3500000 + n)],
        "nm_municipio": [f"Município {i}" for i in range(n)],
        "indicador": rng.choice(["idhm", "renda_media", "anos_estudo"], n),
        "valor": rng.uniform(0.5, 0.9, n).round(3).astype(str),
        "periodo": ["2022"] * n,
    })
    path = temp_data_dir / "ibge" / "idhm_SP.parquet"
    df.to_parquet(path, index=False)
    return path
