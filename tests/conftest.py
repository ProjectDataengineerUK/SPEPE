"""Fixtures compartilhadas para testes do SPEPE."""

import sys
from unittest.mock import MagicMock

import pytest


def pytest_configure(config):
    """Registra stubs do namespace google antes da coleta de testes.

    Evita ModuleNotFoundError para google-cloud-* em CI sem GCP SDK instalado.
    Testes que precisam de comportamento específico substituem via patch.dict.
    """
    if "google" not in sys.modules:
        _google = MagicMock(name="google")
        _google_cloud = MagicMock(name="google.cloud")
        _google.cloud = _google_cloud

        _stubs = {
            "google": _google,
            "google.cloud": _google_cloud,
            "google.cloud.bigquery": MagicMock(name="google.cloud.bigquery"),
            "google.cloud.language_v2": MagicMock(name="google.cloud.language_v2"),
            "google.cloud.storage": MagicMock(name="google.cloud.storage"),
            "google.cloud.firestore": MagicMock(name="google.cloud.firestore"),
            "google.cloud.pubsub_v1": MagicMock(name="google.cloud.pubsub_v1"),
            "google.cloud.run_v2": MagicMock(name="google.cloud.run_v2"),
            "google.auth": MagicMock(name="google.auth"),
            "google.oauth2": MagicMock(name="google.oauth2"),
            "google.oauth2.credentials": MagicMock(name="google.oauth2.credentials"),
        }
        for _name, _mod in _stubs.items():
            sys.modules.setdefault(_name, _mod)

        # Garante atributo language_v2 no google.cloud stub
        _google_cloud.language_v2 = sys.modules["google.cloud.language_v2"]
        _google_cloud.bigquery = sys.modules["google.cloud.bigquery"]


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
    df = pd.DataFrame(
        {
            "sg_uf": ["SP"] * n,
            "cd_municipio": rng.integers(10000, 99999, n),
            "nr_zona": rng.integers(1, 50, n),
            "nr_secao": rng.integers(1, 500, n),
            "nm_candidato": rng.choice(["LULA", "BOLSONARO", "CIRO"], n),
            "qt_votos": rng.integers(0, 500, n),
            "ds_cargo": ["PRESIDENTE"] * n,
        }
    )
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
    df = pd.DataFrame(
        {
            "cd_municipio_ibge": [str(i) for i in range(3500000, 3500000 + n)],
            "nm_municipio": [f"Município {i}" for i in range(n)],
            "indicador": rng.choice(["idhm", "renda_media", "anos_estudo"], n),
            "valor": rng.uniform(0.5, 0.9, n).round(3).astype(str),
            "periodo": ["2022"] * n,
        }
    )
    path = temp_data_dir / "ibge" / "idhm_SP.parquet"
    df.to_parquet(path, index=False)
    return path
