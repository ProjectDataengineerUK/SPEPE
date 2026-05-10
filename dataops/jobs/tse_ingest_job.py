"""Cloud Run Job: Download TSE data → Bronze layer."""

from __future__ import annotations

import logging
import os
import sys
from dataops.bronze_writer import write_bronze_from_file
from dataops.clients.tse_client import download_tse_resultados
from dataops.clients.tse_client import download_br_presidente

# Lista completa de UFs – usada tanto no modo script quanto nos jobs Cloud Run
_ALL_UFS = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR",
    "SC", "SP", "SE", "TO",
]

logbook = logging.getLogger("spepe.jobs.tse_ingest")


def main(uf: str, year: int) -> None:
    logbook.info("TSE ingest job: UF=%s ano=%d", uf, year)

    try:
        parquet_path = download_tse_resultados(uf, year)
    except Exception as exc:
        logbook.error("Download TSE falhou: %s", exc)
        sys.exit(1)

    # ---- NOVA FUNÇÃO: baixar e processar o arquivo BR (presidente) ----
    # Só faz download do arquivo nacional quando o ano é 2022 e o UF é "ALL"
    # (o arquivo BR não tem UF, ele contém cargos nacionais)
    if uf.upper() == "ALL" and year == 2022:
        try:
            br_parquet_path = download_br_presidente(year)
            # Escrita da camada Bronze para a tabela de presidente
            write_bronze_from_file(
                src_path=br_parquet_path,
                source="tse_presidente",
                year=year,
                uf="BR",               # código fictício para indicar "nacional"
                filename=f"presidente_{year}.parquet",
                use_gcs=bool(os.environ.get("GCS_BUCKET")),
            )
            logbook.info("Arquivo BR processado e Bronze escrito: %s", br_parquet_path)
        except Exception as exc:
            logbook.error("Falha ao processar arquivo BR (presidente): %s", exc)
            # Não abortamos a ingestão normal; apenas register o erro
            sys.exit(1)

    out_path = write_bronze_from_file(
        src_path=parquet_path,
        source="tse",
        year=year,
        uf=uf,
        filename=f"resultados_{uf.upper()}_{year}.parquet",
        use_gcs=bool(os.environ.get("GCS_BUCKET")),
    )

    logbook.info("TSE ingest concluído: %s", out_path)


if __name__ == "__main__":
    import argparse

    _ALL_UFS = [
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
        "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR",
        "SC", "SP", "SE", "TO"
    ]

    parser = argparse.ArgumentParser(description="TSE ingest job")
    parser.add_argument("--uf", default=os.environ.get("DEFAULT_UF", "SP"))
    parser.add_argument("--year", type=int, default=int(os.environ.get("DEFAULT_ANO", "2022")))
    args = parser.parse_args()
    ufs = _ALL_UFS if args.uf.upper() == "ALL" else [args.uf.upper()]
    for uf in ufs:
        main(uf, args.year)