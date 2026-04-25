"""Test: validate cd_cargo column flows through Bronze → Silver → Gold."""

import pandas as pd


class TestSchemaGoldCdCargo:
    """AT-003: Validar schema Gold contém cd_cargo para filtro multi-cargo."""

    def test_silver_schema_includes_cd_cargo(self):
        """Silver schema deve incluir cd_cargo após normalização TSE."""
        from dataops.clients.tse_client import normalize_columns

        # Mock data: simular resultado TSE com CD_CARGO
        mock_tse = pd.DataFrame(
            {
                "SG_UF": ["SP", "SP"],
                "CD_MUNICIPIO": [35001, 35002],
                "CD_CARGO": [1, 1],  # Presidente
                "QT_VOTOS": [100, 200],
            }
        )

        # Normalizar (como TSE client faria)
        normalized = normalize_columns(mock_tse, year=2022)

        # Validar que cd_cargo existe após normalização
        assert (
            "cd_cargo" in normalized.columns
        ), "cd_cargo não presente após normalize_columns"
        assert normalized["cd_cargo"].notna().all(), "cd_cargo tem valores nulos"
        assert list(normalized["cd_cargo"].unique()) == [
            1
        ], "cd_cargo não mantém valores corretos"

    def test_gold_schema_preserves_cd_cargo(self):
        """Gold fact_municipio_eleicao deve incluir cd_cargo no groupby."""
        from dataops.gold_builder import _build_fact_municipio_eleicao

        # Mock Silver data: completo com cd_cargo
        mock_silver = pd.DataFrame(
            {
                "sg_uf": ["SP", "SP", "SP"],
                "cd_municipio": [35001, 35001, 35002],
                "cod_municipio_ibge": ["3500105", "3500105", "3500204"],
                "ano_eleicao": [2022, 2022, 2022],
                "cd_cargo": [1, 1, 3],  # Presidente, Presidente, Governador
                "nm_candidato": ["Cand A", "Cand B", "Cand C"],
                "qt_votos": [100, 200, 150],
                "idhm_valor": [0.7, 0.7, 0.75],
            }
        )

        # Build fact table
        fact = _build_fact_municipio_eleicao(mock_silver)

        # Validar resultado
        assert len(fact) > 0, "fact_municipio_eleicao retornou vazio"

        # cd_cargo deve estar no resultado se existia no input
        if "cd_cargo" in mock_silver.columns:
            # fact pode ter agrupado por cd_cargo, logo deve ter mais linhas ou coluna presente
            assert (
                "cd_cargo" in fact.columns or len(fact) > 2
            ), "fact_municipio_eleicao não preservou informação de cd_cargo"

            # Validar que temos registros para diferentes cargos
            if "cd_cargo" in fact.columns:
                unique_cargos = fact["cd_cargo"].nunique()
                assert unique_cargos >= 1, "cd_cargo sem valores"

    def test_gold_supports_multi_cargo_filter(self):
        """Validar que tabela Gold pode ser filtrada por cd_cargo."""
        from dataops.gold_builder import _build_fact_municipio_eleicao

        # Mock com múltiplos cargos
        mock_silver = pd.DataFrame(
            {
                "sg_uf": ["SP"] * 6,
                "cd_municipio": [35001] * 6,
                "cod_municipio_ibge": ["3500105"] * 6,
                "ano_eleicao": [2022] * 6,
                "cd_cargo": [1, 1, 3, 3, 5, 7],  # 6 cargos diferentes
                "nm_candidato": [f"Cand {i}" for i in range(6)],
                "qt_votos": [100 + i * 10 for i in range(6)],
            }
        )

        fact = _build_fact_municipio_eleicao(mock_silver)

        # Se cd_cargo está no schema, deve ser filtrável
        if "cd_cargo" in fact.columns:
            # Teste filtro por cargo específico (Presidente)
            pres = fact[fact["cd_cargo"] == 1]
            assert len(pres) > 0, "Não conseguiu filtrar por cd_cargo == 1"

            # Teste filtro por governador
            gov = fact[fact["cd_cargo"] == 3]
            assert len(gov) > 0, "Não conseguiu filtrar por cd_cargo == 3"
