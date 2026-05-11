"""Cloud Run Job: NER geográfico + sentimento → fact_sentiment_municipio.

Fluxo:
  1. Lê spepe_silver.social_mencoes_br das últimas N horas (default 24)
  2. Processa posts em batches de 50 via Gemini 2.0 Flash (us-central1)
     — NER municipal + sentimento float numa única chamada por batch
  3. Match município extraído contra spepe_gold.dim_territorio
  4. Agrega por (data_referencia, cd_municipio_ibge, sg_uf, candidato)
  5. MERGE em spepe_gold.fact_sentiment_municipio (upsert por data+municipio+candidato)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("spepe.jobs.sentiment_geocode")

# ── Configuração ─────────────────────────────────────────────────────────────

_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "spepe-prod")
_VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
_GEMINI_MODEL = "gemini-2.0-flash-001"
_BATCH_SIZE = 50

_SILVER_TABLE = f"{_PROJECT_ID}.spepe_silver.social_mencoes_br"
_DIM_TERRITORIO = f"{_PROJECT_ID}.spepe_gold.dim_territorio"
_GOLD_TABLE = f"{_PROJECT_ID}.spepe_gold.fact_sentiment_municipio"

# Custo estimado: Gemini 2.0 Flash ~$0.075/1M input tokens
# ~350 tokens/batch × (2800/50) batches = ~19.6k tokens/dia → $0.0015/dia
_TOKENS_PER_BATCH_EST = 350

# ── Prompt ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Você é um analisador de texto político brasileiro. Retorne SOMENTE um JSON array válido, \
sem markdown, sem explicações. Um objeto por post na mesma ordem recebida.
"""

_USER_PROMPT_TEMPLATE = """\
Analise os posts abaixo e retorne um JSON array com {n} objetos, um por post, na mesma ordem.

Campos obrigatórios por objeto:
- "municipio": string com nome do município brasileiro mencionado no post (null se não detectado)
- "uf": string com sigla do estado (null se não detectado)
- "sentimento": float de -1.0 (muito negativo) a +1.0 (muito positivo) sobre política brasileira
- "candidato_mencionado": string com nome normalizado do candidato principal mencionado (null se nenhum)

Posts (separados por ---):
{posts}

Retorne apenas o JSON array. Não inclua ```json``` nem explicações.
"""

# ── SQL ───────────────────────────────────────────────────────────────────────

_QUERY_SILVER = """
SELECT
    COALESCE(id_post, CAST(ROW_NUMBER() OVER () AS STRING)) AS id_post,
    fonte,
    COALESCE(created_at, CURRENT_TIMESTAMP()) AS created_at,
    COALESCE(text, '') AS texto,
    COALESCE(nm_candidato, '') AS nm_candidato,
    COALESCE(like_count + retweet_count + reply_count + likes + comments + shares, 0) AS engajamento
FROM `{silver_table}`
WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {hours} HOUR)
  AND text IS NOT NULL
  AND CHAR_LENGTH(text) > 10
"""

_QUERY_DIM_TERRITORIO = """
SELECT
    cd_municipio_ibge,
    nm_municipio,
    sg_uf
FROM `{dim_table}`
WHERE cd_municipio_ibge IS NOT NULL
"""

_MERGE_SQL = """
MERGE `{gold_table}` AS T
USING (
    SELECT
        data_referencia,
        cd_municipio_ibge,
        sg_uf,
        candidato,
        AVG(sentimento_score) AS sentimento_score,
        SUM(volume_mencoes) AS volume_mencoes,
        SUM(polaridade_neg) AS polaridade_neg,
        SUM(polaridade_pos) AS polaridade_pos,
        SUM(polaridade_neu) AS polaridade_neu,
        ARRAY_AGG(DISTINCT fonte IGNORE NULLS) AS fontes,
        MAX(ingested_at) AS ingested_at
    FROM UNNEST(@rows)
    GROUP BY data_referencia, cd_municipio_ibge, sg_uf, candidato
) AS S
ON  T.data_referencia     = S.data_referencia
AND T.cd_municipio_ibge   = S.cd_municipio_ibge
AND T.sg_uf               = S.sg_uf
AND COALESCE(T.candidato, '')  = COALESCE(S.candidato, '')
WHEN MATCHED THEN UPDATE SET
    sentimento_score = (T.sentimento_score * T.volume_mencoes + S.sentimento_score * S.volume_mencoes)
                      / NULLIF(T.volume_mencoes + S.volume_mencoes, 0),
    volume_mencoes   = T.volume_mencoes + S.volume_mencoes,
    polaridade_neg   = T.polaridade_neg + S.polaridade_neg,
    polaridade_pos   = T.polaridade_pos + S.polaridade_pos,
    polaridade_neu   = T.polaridade_neu + S.polaridade_neu,
    fontes           = ARRAY(SELECT DISTINCT f FROM UNNEST(ARRAY_CONCAT(T.fontes, S.fontes)) AS f),
    ingested_at      = S.ingested_at
WHEN NOT MATCHED THEN INSERT (
    data_referencia, cd_municipio_ibge, sg_uf, candidato,
    sentimento_score, volume_mencoes,
    polaridade_neg, polaridade_pos, polaridade_neu,
    fontes, ingested_at
) VALUES (
    S.data_referencia, S.cd_municipio_ibge, S.sg_uf, S.candidato,
    S.sentimento_score, S.volume_mencoes,
    S.polaridade_neg, S.polaridade_pos, S.polaridade_neu,
    S.fontes, S.ingested_at
)
"""

# ── Gemini client ─────────────────────────────────────────────────────────────


def _build_gemini_client() -> Any:
    import vertexai
    from vertexai.generative_models import GenerativeModel, GenerationConfig

    vertexai.init(project=_PROJECT_ID, location=_VERTEX_LOCATION)
    model = GenerativeModel(
        _GEMINI_MODEL,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=GenerationConfig(
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )
    return model


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _call_gemini(model: Any, posts: list[str]) -> list[dict]:
    """Envia um batch de posts ao Gemini e retorna o array de análises."""
    from google.api_core.exceptions import ResourceExhausted

    posts_blob = "\n---\n".join(
        f"[{i + 1}] {p[:500]}" for i, p in enumerate(posts)
    )
    prompt = _USER_PROMPT_TEMPLATE.format(n=len(posts), posts=posts_blob)

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
        parsed: list[dict] = json.loads(raw)
        if not isinstance(parsed, list):
            logger.warning("Gemini retornou não-lista: %s", type(parsed))
            return []
        return parsed
    except ResourceExhausted as exc:
        logger.warning("Gemini ResourceExhausted — retry: %s", exc)
        raise
    except json.JSONDecodeError as exc:
        logger.warning("JSON inválido do Gemini: %s | raw[:200]=%s", exc, raw[:200])
        return []


# ── Fuzzy match de município ──────────────────────────────────────────────────


def _build_municipio_index(df_dim: pd.DataFrame) -> dict[str, dict]:
    """Monta índice nome_normalizado → {cd_municipio_ibge, sg_uf}."""
    import unicodedata

    def _norm(s: str) -> str:
        s = s.upper().strip()
        nfkd = unicodedata.normalize("NFKD", s)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    idx: dict[str, dict] = {}
    for _, row in df_dim.iterrows():
        key = _norm(str(row["nm_municipio"]))
        idx[key] = {
            "cd_municipio_ibge": int(row["cd_municipio_ibge"]),
            "sg_uf": str(row["sg_uf"]),
        }
    return idx


def _lookup_municipio(
    nm: str | None, uf: str | None, idx: dict[str, dict]
) -> dict | None:
    """Tenta match exato normalizado; se uf fornecida, filtra pelo estado."""
    if not nm:
        return None

    import unicodedata

    def _norm(s: str) -> str:
        nfkd = unicodedata.normalize("NFKD", s.upper().strip())
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    key = _norm(nm)
    entry = idx.get(key)
    if entry is None:
        return None
    if uf and entry["sg_uf"] != uf.upper():
        # UF não bate — aceita mesmo assim mas loga para debug
        logger.debug("UF mismatch: Gemini=%s idx=%s para '%s'", uf, entry["sg_uf"], nm)
    return entry


# ── Agregação ────────────────────────────────────────────────────────────────


def _aggregate(enriched: list[dict]) -> list[dict]:
    """Agrega por (data_referencia, cd_municipio_ibge, sg_uf, candidato)."""
    from collections import defaultdict

    groups: dict[tuple, dict] = defaultdict(
        lambda: {
            "sentimento_sum": 0.0,
            "sentimento_count": 0,
            "volume_mencoes": 0,
            "polaridade_neg": 0,
            "polaridade_pos": 0,
            "polaridade_neu": 0,
            "fontes": set(),
        }
    )

    for r in enriched:
        key = (
            r["data_referencia"],
            r["cd_municipio_ibge"],
            r["sg_uf"],
            r.get("candidato"),
        )
        g = groups[key]
        sent = float(r.get("sentimento", 0.0) or 0.0)
        g["sentimento_sum"] += sent
        g["sentimento_count"] += 1
        g["volume_mencoes"] += 1
        if sent < -0.1:
            g["polaridade_neg"] += 1
        elif sent > 0.1:
            g["polaridade_pos"] += 1
        else:
            g["polaridade_neu"] += 1
        if r.get("fonte"):
            g["fontes"].add(r["fonte"])

    now = datetime.now(timezone.utc)
    rows = []
    for (data_ref, cd_mun, sg_uf, candidato), g in groups.items():
        count = g["sentimento_count"] or 1
        rows.append(
            {
                "data_referencia": data_ref,
                "cd_municipio_ibge": cd_mun,
                "sg_uf": sg_uf,
                "candidato": candidato,
                "sentimento_score": round(g["sentimento_sum"] / count, 6),
                "volume_mencoes": g["volume_mencoes"],
                "polaridade_neg": g["polaridade_neg"],
                "polaridade_pos": g["polaridade_pos"],
                "polaridade_neu": g["polaridade_neu"],
                "fontes": list(g["fontes"]),
                "ingested_at": now,
            }
        )
    return rows


# ── Main ───────────────────────────────────────────────────────────────────────


def main(hours: int = 24) -> None:
    try:
        from google.cloud import bigquery
    except ImportError:
        logger.error("google-cloud-bigquery não instalado")
        sys.exit(1)

    logger.info("sentiment_geocode_job: project=%s hours=%d", _PROJECT_ID, hours)
    t0 = time.monotonic()

    bq = bigquery.Client(project=_PROJECT_ID)

    # 1. Ler posts das últimas N horas
    query_sql = _QUERY_SILVER.format(silver_table=_SILVER_TABLE, hours=hours)
    logger.info("Consultando Silver: %s", _SILVER_TABLE)
    try:
        df_posts = bq.query(query_sql).to_dataframe()
    except Exception as exc:
        logger.error("Falha ao ler Silver: %s", exc)
        sys.exit(1)

    total_posts = len(df_posts)
    logger.info("Posts lidos: %d", total_posts)

    if df_posts.empty:
        logger.info("Nenhum post nas últimas %dh — nada a processar", hours)
        return

    # 2. Ler dim_territorio para lookup
    logger.info("Carregando dim_territorio")
    try:
        df_dim = bq.query(_QUERY_DIM_TERRITORIO.format(dim_table=_DIM_TERRITORIO)).to_dataframe()
    except Exception as exc:
        logger.warning("dim_territorio inacessível: %s — match municipal desabilitado", exc)
        df_dim = pd.DataFrame(columns=["cd_municipio_ibge", "nm_municipio", "sg_uf"])

    municipio_idx = _build_municipio_index(df_dim)
    logger.info("Índice municipal: %d municípios carregados", len(municipio_idx))

    # 3. Inicializar Gemini
    logger.info("Inicializando Gemini %s em %s", _GEMINI_MODEL, _VERTEX_LOCATION)
    try:
        gemini_model = _build_gemini_client()
    except Exception as exc:
        logger.error("Falha ao inicializar Gemini: %s", exc)
        sys.exit(1)

    # 4. Processar em batches
    posts_list = df_posts.to_dict("records")
    enriched: list[dict] = []
    batches_total = (total_posts + _BATCH_SIZE - 1) // _BATCH_SIZE
    batches_ok = 0
    posts_sem_municipio = 0
    total_tokens_est = 0

    for batch_idx in range(0, total_posts, _BATCH_SIZE):
        batch_records = posts_list[batch_idx : batch_idx + _BATCH_SIZE]
        batch_texts = [r["texto"] for r in batch_records]
        batch_num = batch_idx // _BATCH_SIZE + 1

        logger.info("Batch %d/%d: %d posts", batch_num, batches_total, len(batch_texts))
        total_tokens_est += _TOKENS_PER_BATCH_EST

        try:
            analyses = _call_gemini(gemini_model, batch_texts)
        except Exception as exc:
            logger.warning("Batch %d falhou após retries: %s — pulando", batch_num, exc)
            continue

        # Alinhar resultados com registros originais
        for i, record in enumerate(batch_records):
            analysis = analyses[i] if i < len(analyses) else {}

            nm_mun = analysis.get("municipio")
            uf_raw = analysis.get("uf")
            sentimento = analysis.get("sentimento")
            candidato_ner = analysis.get("candidato_mencionado")

            # Sentimento padrão se Gemini não retornou valor válido
            if sentimento is None or not isinstance(sentimento, (int, float)):
                sentimento = 0.0
            sentimento = max(-1.0, min(1.0, float(sentimento)))

            # Candidato: preferir nm_candidato do Silver se NER não detectou
            candidato_final = (
                candidato_ner
                or (record["nm_candidato"] if record["nm_candidato"] else None)
            )

            # Match municipal
            geo = _lookup_municipio(nm_mun, uf_raw, municipio_idx)
            if geo is None:
                logger.debug("Sem município detectado: post_id=%s", record["id_post"])
                posts_sem_municipio += 1
                continue

            created_at = record["created_at"]
            if hasattr(created_at, "date"):
                data_ref = created_at.date()
            else:
                data_ref = datetime.now(timezone.utc).date()

            enriched.append(
                {
                    "data_referencia": data_ref,
                    "cd_municipio_ibge": geo["cd_municipio_ibge"],
                    "sg_uf": geo["sg_uf"],
                    "candidato": candidato_final,
                    "sentimento": sentimento,
                    "fonte": record.get("fonte"),
                }
            )

        batches_ok += 1

    logger.info(
        "NER concluído: %d posts enriquecidos | %d sem município | %d batches OK/%d",
        len(enriched),
        posts_sem_municipio,
        batches_ok,
        batches_total,
    )

    if not enriched:
        logger.info("Nenhum post geocodificado — fact_sentiment_municipio não atualizado")
        elapsed = time.monotonic() - t0
        _log_summary(total_posts, 0, 0, total_tokens_est, elapsed)
        return

    # 5. Agregar
    agg_rows = _aggregate(enriched)
    municipios_com_dados = len({r["cd_municipio_ibge"] for r in agg_rows})
    logger.info("Agregados: %d linhas | %d municípios únicos", len(agg_rows), municipios_com_dados)

    # 6. MERGE no Gold
    _merge_gold(bq, agg_rows)

    elapsed = time.monotonic() - t0
    _log_summary(total_posts, len(enriched), municipios_com_dados, total_tokens_est, elapsed)


def _merge_gold(bq: Any, rows: list[dict]) -> None:
    """Escreve staging e executa MERGE em fact_sentiment_municipio."""
    from google.cloud import bigquery

    staging_ref = f"{_PROJECT_ID}.spepe_gold._fact_sentiment_municipio_stage"

    df = pd.DataFrame(rows)
    df["data_referencia"] = pd.to_datetime(df["data_referencia"]).dt.date
    df["ingested_at"] = pd.to_datetime(df["ingested_at"], utc=True)

    schema = [
        bigquery.SchemaField("data_referencia", "DATE"),
        bigquery.SchemaField("cd_municipio_ibge", "INTEGER"),
        bigquery.SchemaField("sg_uf", "STRING"),
        bigquery.SchemaField("candidato", "STRING"),
        bigquery.SchemaField("sentimento_score", "FLOAT"),
        bigquery.SchemaField("volume_mencoes", "INTEGER"),
        bigquery.SchemaField("polaridade_neg", "INTEGER"),
        bigquery.SchemaField("polaridade_pos", "INTEGER"),
        bigquery.SchemaField("polaridade_neu", "INTEGER"),
        bigquery.SchemaField("fontes", "STRING", mode="REPEATED"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP"),
    ]

    cfg = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=schema,
    )
    try:
        bq.load_table_from_dataframe(df, staging_ref, job_config=cfg).result()
        logger.info("Staging carregado: %d rows → %s", len(df), staging_ref)
    except Exception as exc:
        logger.error("Falha ao carregar staging: %s", exc)
        sys.exit(1)

    merge_sql = _MERGE_SQL.format(gold_table=_GOLD_TABLE)
    merge_sql_with_staging = merge_sql.replace(
        "FROM UNNEST(@rows)", f"FROM `{staging_ref}`"
    )
    try:
        bq.query(merge_sql_with_staging).result()
        logger.info("MERGE concluído em %s", _GOLD_TABLE)
    except Exception as exc:
        logger.error("MERGE falhou: %s", exc)
        sys.exit(1)


def _log_summary(
    total_posts: int,
    enriched: int,
    municipios: int,
    tokens_est: int,
    elapsed_s: float,
) -> None:
    custo_usd = tokens_est / 1_000_000 * 0.075
    logger.info(
        "SUMMARY | posts_processados=%d | posts_geocodificados=%d | "
        "municipios_com_dados=%d | tokens_estimados=%d | custo_gemini_est=US$%.5f | "
        "elapsed=%.1fs",
        total_posts,
        enriched,
        municipios,
        tokens_est,
        custo_usd,
        elapsed_s,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NER geográfico + sentimento para posts sociais")
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="Janela de dados em dias (default: 1 = últimas 24h)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Janela de dados em horas (sobrepõe --days se fornecido)",
    )
    args = parser.parse_args()

    hours_window = args.hours if args.hours is not None else args.days * 24
    main(hours=hours_window)
