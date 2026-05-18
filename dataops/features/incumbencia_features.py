"""Incumbency features: is_incumbent, cargo_anterior, troca_partido."""

from __future__ import annotations

INCUMBENCIA_COLS = [
    "is_incumbent_int",
    "trocou_partido_int",
]


def compute_incumbencia_sql(
    project_id: str,
    silver_dataset: str,
    gold_dataset: str,
) -> str:
    silver = f"`{project_id}.{silver_dataset}`"
    return f"""
CREATE OR REPLACE TABLE `{project_id}.spepe_mlops.dim_incumbencia` AS
WITH cands AS (
    SELECT
        sq_candidato,
        nm_candidato,
        sg_partido,
        cd_cargo,
        ds_cargo,
        sg_uf,
        ano_eleicao,
        ds_situacao,
        CASE WHEN UPPER(ds_situacao) LIKE '%ELEITO%'
              AND UPPER(ds_situacao) NOT LIKE '%NÃO%'
              AND UPPER(ds_situacao) NOT LIKE '%NAO%'
             THEN TRUE ELSE FALSE END AS foi_eleito
    FROM {silver}.tse_candidaturas_*
    WHERE sq_candidato IS NOT NULL
      AND nm_candidato IS NOT NULL
),
incumbencia AS (
    SELECT
        c.sq_candidato,
        c.nm_candidato,
        c.sg_partido                        AS sg_partido_atual,
        c.cd_cargo,
        c.sg_uf,
        c.ano_eleicao,
        p.sq_candidato IS NOT NULL          AS is_incumbent,
        p.cd_cargo                          AS cd_cargo_anterior,
        p.ds_cargo                          AS ds_cargo_anterior,
        p.sg_partido                        AS sg_partido_anterior,
        p.sq_candidato = c.sq_candidato
            AND p.sg_partido != c.sg_partido AS trocou_partido
    FROM cands c
    LEFT JOIN cands p
        ON  p.nm_candidato = c.nm_candidato
        AND p.sg_uf        = c.sg_uf
        AND p.cd_cargo     = c.cd_cargo
        AND p.ano_eleicao  = c.ano_eleicao - 4
        AND p.foi_eleito   = TRUE
)
SELECT
    sq_candidato,
    nm_candidato,
    sg_partido_atual                         AS sg_partido,
    cd_cargo,
    sg_uf,
    ano_eleicao,
    COALESCE(is_incumbent, FALSE)            AS is_incumbent,
    cd_cargo_anterior,
    ds_cargo_anterior,
    sg_partido_anterior,
    COALESCE(trocou_partido, FALSE)          AS trocou_partido,
    CURRENT_TIMESTAMP()                      AS ingested_at
FROM incumbencia
"""


def get_incumbencia_features(project_id: str) -> str:
    return f"""
SELECT
    sq_candidato,
    nm_candidato,
    sg_uf,
    cd_cargo,
    ano_eleicao,
    is_incumbent,
    CAST(is_incumbent AS INT64)              AS is_incumbent_int,
    trocou_partido,
    CAST(trocou_partido AS INT64)            AS trocou_partido_int,
    cd_cargo_anterior,
    sg_partido_anterior
FROM `{project_id}.spepe_mlops.dim_incumbencia`
"""
