"""Pré-candidatos 2026 — single source of truth.

Dados estáticos pesquisados em 21/05/2026.
Fontes: Diário do Rio, Poder360, Agenda do Poder, JOTA.

Importado por:
  - ui/dashboard_api.py          → endpoint /api/precandidatos
  - dataops/jobs/social_ingest_job.py → nomes + handles para coleta
"""

from __future__ import annotations

PRE_CANDIDATOS_2026: dict[str, dict[str, list[dict]]] = {
    "RJ": {
        "governador": [
            {
                "nm": "Eduardo Paes",
                "partido": "PSD",
                "status": "confirmado",
                "instagram": "@eduardopaes",
                "youtube": "@EduardoPaes",
                "x": "@eduardopaes",
                "facebook": "eduardopaesoficial",
                "obs": "Favorito — 50% nas pesquisas. Prefeito do Rio; vice: Jane Reis (MDB).",
            },
            {
                "nm": "Douglas Ruas",
                "partido": "PL",
                "status": "confirmado",
                "instagram": "@douglasruasrj",
                "obs": "Dep. estadual; articulação Flávio Bolsonaro.",
            },
            {
                "nm": "Anthony Garotinho",
                "partido": "Republicanos",
                "status": "confirmado",
                "instagram": "@anthonygarotinhorj",
                "tiktok": "@anthonygarotinhorj",
                "x": "@anthonygarotinhorj",
                "obs": "Lançou pré-candidatura ao governo (mai/2026). Também testado para Dep. Federal.",
            },
            {
                "nm": "André Marinho",
                "partido": "Novo",
                "status": "confirmado",
                "obs": "Confirmado pelo Novo.",
            },
            {
                "nm": "André Português",
                "partido": "Republicanos",
                "status": "confirmado",
                "obs": "Ex-prefeito de Miguel Pereira.",
            },
            {
                "nm": "William Siri",
                "partido": "PSOL",
                "status": "confirmado",
                "instagram": "@williamsiri",
                "obs": "Vereador do Rio; lançado pelo PSOL.",
            },
            {
                "nm": "Washington Reis",
                "partido": "MDB",
                "status": "citado",
                "obs": "Pré-candidato; candidatura depende de questão jurídica no STF.",
            },
            {
                "nm": "Fabiano Horta",
                "partido": "PT",
                "status": "citado",
                "instagram": "@fabianohorta",
                "obs": "Ex-prefeito de Maricá; tendência do PT apoiar Eduardo Paes.",
            },
            {
                "nm": "Rodrigo Neves",
                "partido": "PDT",
                "status": "citado",
                "instagram": "@rodrigonevesniteroi",
                "obs": "Prefeito de Niterói; articulação estadual.",
            },
        ],
        "dep federal": [
            {
                "nm": "Wladimir Garotinho",
                "partido": "PL",
                "status": "confirmado",
                "youtube": "@wladimirgarotinhooficial",
                "facebook": "wladimirgarotinhoo",
                "obs": "Ex-prefeito de Campos; base Norte Fluminense; alinhado Flávio Bolsonaro.",
            },
            {
                "nm": "Anthony Garotinho",
                "partido": "Republicanos",
                "status": "confirmado",
                "instagram": "@anthonygarotinhorj",
                "tiktok": "@anthonygarotinhorj",
                "x": "@anthonygarotinhorj",
                "obs": "Ex-governador. Pode migrar para disputa ao governo.",
            },
            {
                "nm": "Juliana Benício",
                "partido": "Cidadania",
                "status": "confirmado",
                "instagram": "@julianabeniciooficial",
                "youtube": "@Juliana.Benicio",
                "facebook": "JulianaBenicioOficial",
                "obs": "Niterói; ligada ao grupo Rodrigo Neves/Cidadania.",
            },
            {
                "nm": "Gracyanne Barbosa",
                "partido": "Republicanos",
                "status": "confirmado",
                "instagram": "@graoficial",
                "obs": "Influenciadora fitness; alto engajamento digital.",
            },
            {
                "nm": "Felipe Curi",
                "partido": "PP",
                "status": "confirmado",
                "instagram": "@delegadofelipecuri",
                "obs": "Ex-secretário de Polícia Civil; apoio Flávio Bolsonaro.",
            },
            {
                "nm": "Cristina Mel",
                "partido": "PSDB",
                "status": "confirmado",
                "instagram": "@cristinamel",
                "obs": "Cantora gospel; pré-candidatura anunciada.",
            },
            {
                "nm": "Zé de Abreu",
                "partido": "PT",
                "status": "confirmado",
                "instagram": "@zehdeabreu",
                "x": "@zehdeabreu",
                "obs": "Ator; candidatura apoiada por Quaquá (PT-RJ).",
            },
            {
                "nm": "Elias Jabbour",
                "partido": "PCdoB",
                "status": "confirmado",
                "instagram": "@eliasmkjabbour",
                "obs": "Economista/professor; site eliasjabbour.com.br.",
            },
            {
                "nm": "José Camilo Zito",
                "partido": "Cidadania",
                "status": "confirmado",
                "obs": "Ex-prefeito de Duque de Caxias; filiou-se ao Cidadania.",
            },
            {
                "nm": "Haroldo Filho",
                "partido": "Podemos",
                "status": "confirmado",
                "instagram": "@haroldofilho",
                "obs": "Secretário de Valença; pré-candidatura anunciada.",
            },
            {
                "nm": "Dado Dolabella",
                "partido": "MDB",
                "status": "confirmado",
                "instagram": "@dadodolabella",
                "obs": "Ator; anúncio pelo MDB foi apagado — candidatura instável.",
            },
            {
                "nm": "Altineu Côrtes",
                "partido": "PL",
                "status": "citado",
                "instagram": "@altineucortes",
                "obs": "Citado como puxador competitivo.",
            },
            {
                "nm": "Áureo Ribeiro",
                "partido": "Solidariedade",
                "status": "citado",
                "instagram": "@aureoribeiro",
            },
            {
                "nm": "Carlos Jordy",
                "partido": "PL",
                "status": "citado",
                "instagram": "@carlosjordy",
                "x": "@carlosjordy",
            },
            {
                "nm": "Chico Alencar",
                "partido": "PSOL",
                "status": "citado",
                "instagram": "@chicoalencar",
                "x": "@chicoalencar",
            },
            {
                "nm": "Daniel Soranz",
                "partido": "PSD",
                "status": "citado",
                "instagram": "@danielsoranz",
                "x": "@danielsoranz",
            },
            {
                "nm": "Dr. Luizinho",
                "partido": "PP",
                "status": "citado",
                "instagram": "@drluizinho",
                "x": "@drluizinho",
            },
            {
                "nm": "General Pazuello",
                "partido": "PL",
                "status": "citado",
                "instagram": "@genpazuello",
            },
            {
                "nm": "Glauber Braga",
                "partido": "PSOL",
                "status": "citado",
                "instagram": "@glauber_braga",
                "x": "@glauber_braga",
            },
            {
                "nm": "Gutemberg Reis",
                "partido": "MDB",
                "status": "citado",
                "instagram": "@gutembergreis",
            },
            {
                "nm": "Lindbergh Farias",
                "partido": "PT",
                "status": "citado",
                "instagram": "@lindberghfarias",
                "x": "@lindberghfarias",
            },
            {
                "nm": "Marcelo Crivella",
                "partido": "Republicanos",
                "status": "citado",
                "x": "@mcrivella",
            },
            {
                "nm": "Marcelo Freixo",
                "partido": "PT",
                "status": "citado",
                "instagram": "@marcelofreixo",
                "x": "@marcelofreixo",
            },
            {
                "nm": "Otoni de Paula",
                "partido": "MDB",
                "status": "citado",
                "instagram": "@otonidepaula",
                "x": "@otonidepaula",
            },
            {
                "nm": "Pastor Henrique Vieira",
                "partido": "PSOL",
                "status": "citado",
                "instagram": "@pastorhenriquevieira",
            },
            {
                "nm": "Reimont",
                "partido": "PT",
                "status": "citado",
                "instagram": "@reimont",
                "x": "@reimont",
            },
            {
                "nm": "Soraya Santos",
                "partido": "PL",
                "status": "citado",
                "instagram": "@sorayasantos",
            },
            {
                "nm": "Talíria Petrone",
                "partido": "PSOL",
                "status": "citado",
                "instagram": "@taliriapetrone",
                "x": "@taliriapetrone",
            },
            {
                "nm": "Tarcísio Motta",
                "partido": "PSOL",
                "status": "citado",
                "instagram": "@tarcisiomotta",
                "x": "@tarcisiomotta",
            },
            {
                "nm": "Alessandro Molon",
                "partido": "PSB",
                "status": "citado",
                "instagram": "@alessandromolon",
                "x": "@alessandromolon",
            },
        ],
    }
}


# ── Helper functions ─────────────────────────────────────────────────────────


def get_candidatos(uf: str, cargo: str) -> list[dict]:
    """Retorna lista completa de pré-candidatos para UF/cargo."""
    return PRE_CANDIDATOS_2026.get(uf.upper(), {}).get(cargo.lower().strip(), [])


def get_nomes(uf: str, cargo: str | None = None) -> list[str]:
    """Retorna lista de nomes únicos. cargo=None retorna todos os cargos da UF."""
    uf_data = PRE_CANDIDATOS_2026.get(uf.upper(), {})
    if cargo:
        cands = uf_data.get(cargo.lower().strip(), [])
    else:
        seen: set[str] = set()
        cands = []
        for lst in uf_data.values():
            for c in lst:
                if c["nm"] not in seen:
                    seen.add(c["nm"])
                    cands.append(c)
    return [c["nm"] for c in cands]


def get_pages_dict(uf: str, cargo: str | None = None) -> dict[str, list[str]]:
    """Retorna {facebook: [...], instagram: [...], youtube: [...], x: [...]} com handles únicos.

    Strips '@' prefix so handles can be used directly in API calls.
    """
    uf_data = PRE_CANDIDATOS_2026.get(uf.upper(), {})
    cargos = [cargo.lower().strip()] if cargo else list(uf_data.keys())

    result: dict[str, list[str]] = {"facebook": [], "instagram": [], "youtube": [], "x": []}
    seen: dict[str, set[str]] = {k: set() for k in result}

    for c_key in cargos:
        for cand in uf_data.get(c_key, []):
            for field in ("facebook", "instagram", "youtube", "x"):
                val = cand.get(field)
                if val:
                    clean = val.lstrip("@")
                    if clean not in seen[field]:
                        seen[field].add(clean)
                        result[field].append(clean)

    return result


# ── BigQuery schema ───────────────────────────────────────────────────────────

# Table: spepe_gold.dim_precandidato_2026
# Partitioned/clustered by: sg_uf, cargo
PRECANDIDATOS_BQ_SCHEMA = [
    # identity
    {
        "name": "id_precandidato",
        "type": "STRING",
        "mode": "REQUIRED",
        "description": "SHA256(sg_uf+cargo+nm)[:12] — stable surrogate key",
    },
    {"name": "sg_uf", "type": "STRING", "mode": "REQUIRED"},
    {
        "name": "cargo",
        "type": "STRING",
        "mode": "REQUIRED",
        "description": "governador | dep federal | dep estadual | senador",
    },
    {"name": "nm", "type": "STRING", "mode": "REQUIRED"},
    {"name": "partido", "type": "STRING", "mode": "NULLABLE"},
    {
        "name": "status",
        "type": "STRING",
        "mode": "NULLABLE",
        "description": "confirmado | citado | desistiu",
    },
    {"name": "obs", "type": "STRING", "mode": "NULLABLE"},
    # social handles (sem @)
    {"name": "instagram", "type": "STRING", "mode": "NULLABLE"},
    {"name": "youtube", "type": "STRING", "mode": "NULLABLE"},
    {"name": "x", "type": "STRING", "mode": "NULLABLE"},
    {"name": "facebook", "type": "STRING", "mode": "NULLABLE"},
    {"name": "tiktok", "type": "STRING", "mode": "NULLABLE"},
    # profile metrics (updated by profile_scraper job)
    {"name": "x_followers", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "x_following", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "x_tweets", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "x_engagement_rate", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "ig_followers", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "ig_posts", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "yt_subscribers", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "yt_videos", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "fb_fans", "type": "INTEGER", "mode": "NULLABLE"},
    # territory / alliances
    {
        "name": "regiao_base",
        "type": "STRING",
        "mode": "NULLABLE",
        "description": "Região eleitoral principal (e.g. Norte Fluminense)",
    },
    {
        "name": "prefeituras_apoio",
        "type": "STRING",
        "mode": "NULLABLE",
        "description": "JSON array: [{nm_municipio, nm_prefeito, partido}]",
    },
    {
        "name": "aliancas",
        "type": "STRING",
        "mode": "NULLABLE",
        "description": "JSON array: [nm_candidato_aliado]",
    },
    {
        "name": "adversarios_diretos",
        "type": "STRING",
        "mode": "NULLABLE",
        "description": "JSON array: [nm_candidato_adversario]",
    },
    # timestamps
    {
        "name": "dt_pesquisa",
        "type": "DATE",
        "mode": "NULLABLE",
        "description": "Data do levantamento estático",
    },
    {
        "name": "dt_atualizacao_metricas",
        "type": "TIMESTAMP",
        "mode": "NULLABLE",
        "description": "Última coleta de métricas de perfil",
    },
]

# Table: spepe_gold.fact_precandidato_profile_history
# Tracks metric evolution over time (one row per scrape per handle per platform)
PROFILE_HISTORY_BQ_SCHEMA = [
    {"name": "id_precandidato", "type": "STRING", "mode": "REQUIRED"},
    {"name": "platform", "type": "STRING", "mode": "REQUIRED"},
    {"name": "handle", "type": "STRING", "mode": "REQUIRED"},
    {"name": "scrape_ts", "type": "TIMESTAMP", "mode": "REQUIRED"},
    {"name": "followers", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "following", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "posts_count", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "avg_likes", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "avg_comments", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "engagement_rate", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "verified", "type": "BOOLEAN", "mode": "NULLABLE"},
    {"name": "error", "type": "STRING", "mode": "NULLABLE"},
]


def to_bq_rows(uf: str = "RJ") -> list[dict]:
    """Convert static PRE_CANDIDATOS_2026 data to BQ-insertable rows for dim_precandidato_2026."""
    import hashlib
    import json

    rows = []
    for cargo, cands in PRE_CANDIDATOS_2026.get(uf.upper(), {}).items():
        for c in cands:
            key = f"{uf.upper()}|{cargo}|{c['nm']}"
            id_pc = hashlib.sha256(key.encode()).hexdigest()[:12]
            rows.append(
                {
                    "id_precandidato": id_pc,
                    "sg_uf": uf.upper(),
                    "cargo": cargo,
                    "nm": c["nm"],
                    "partido": c.get("partido"),
                    "status": c.get("status"),
                    "obs": c.get("obs"),
                    "instagram": c.get("instagram", "").lstrip("@") or None,
                    "youtube": c.get("youtube", "").lstrip("@") or None,
                    "x": c.get("x", "").lstrip("@") or None,
                    "facebook": c.get("facebook", "").lstrip("@") or None,
                    "tiktok": c.get("tiktok", "").lstrip("@") or None,
                    "dt_pesquisa": "2026-05-21",
                    "prefeituras_apoio": json.dumps(
                        c.get("prefeituras_apoio", []), ensure_ascii=False
                    ),
                    "aliancas": json.dumps(c.get("aliancas", []), ensure_ascii=False),
                    "adversarios_diretos": json.dumps(
                        c.get("adversarios_diretos", []), ensure_ascii=False
                    ),
                }
            )
    return rows
