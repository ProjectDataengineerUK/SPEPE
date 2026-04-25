import logging
import re

logger = logging.getLogger("spepe.hooks.security")

_DESTRUCTIVE_PATTERNS = [
    r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b",
    r"\bTRUNCATE\b",
    r"\bDELETE\s+FROM\b",
    r"rm\s+-rf",
    r"git\s+reset\s+--hard",
    r"git\s+push\s+--force",
    r"chmod\s+777",
    r"sudo\s+rm",
    r"mkfs\.",
    r":(){:|:&};:",
]

_SQL_COST_PATTERNS = [
    r"SELECT\s+\*\s+FROM\b(?!.*\bWHERE\b)(?!.*\bLIMIT\b)",
]

_BIGQUERY_COST_PATTERNS = [
    r"SELECT\s+\*\s+FROM\s+`[^`]+`(?!\s+WHERE)(?!\s+LIMIT)",
    r"FROM\s+`[^`]+`(?!\s*(WHERE|LIMIT|PARTITION))",
]

_SQL_INJECTION_PATTERNS = [
    r";\s*DROP\s+",
    r";\s*DELETE\s+FROM",
    r"UNION\s+ALL\s+SELECT",
    r"--\s*$",
    r"'\s*OR\s*'1'\s*=\s*'1",
    r"1\s*=\s*1\s*--",
    r";\s*CREATE\s+OR\s+REPLACE",
    r"\bEXPORT\s+DATA\b",
    r";\s*CALL\s+",
    r"\bINTO\s+OUTFILE\b",
    r"INFORMATION_SCHEMA\s*\.\s*TABLES",
]

_COMPILED_DESTRUCTIVE = [re.compile(p, re.IGNORECASE) for p in _DESTRUCTIVE_PATTERNS]
_COMPILED_SQL_COST = [
    re.compile(p, re.IGNORECASE | re.DOTALL) for p in _SQL_COST_PATTERNS
]
_COMPILED_BQ_COST = [
    re.compile(p, re.IGNORECASE | re.DOTALL) for p in _BIGQUERY_COST_PATTERNS
]
_COMPILED_SQL_INJECT = [re.compile(p, re.IGNORECASE) for p in _SQL_INJECTION_PATTERNS]


def block_destructive_commands(tool_name: str, tool_input: dict) -> str | None:
    text = str(tool_input)
    for pattern in _COMPILED_DESTRUCTIVE:
        if pattern.search(text):
            msg = f"BLOQUEADO: comando destrutivo detectado em {tool_name}: {pattern.pattern}"
            logger.warning(msg)
            return msg
    return None


def check_sql_cost(tool_name: str, tool_input: dict) -> str | None:
    if "sql" not in tool_name.lower() and "query" not in tool_name.lower():
        return None
    text = str(tool_input.get("sql", tool_input.get("query", "")))
    for pattern in _COMPILED_SQL_COST:
        if pattern.search(text):
            msg = "BLOQUEADO: SELECT * sem WHERE ou LIMIT. Especifique colunas ou adicione filtro."
            logger.warning(f"SQL cost block: {tool_name}")
            return msg
    return None


def check_bigquery_cost(tool_name: str, tool_input: dict) -> str | None:
    """Block BigQuery queries without partition filter (high cost risk)."""
    text = str(tool_input.get("sql", tool_input.get("query", "")))
    if (
        not text
        or "bigquery" not in tool_name.lower()
        and "bq" not in tool_name.lower()
    ):
        return None
    if "spepe_gold" in text or "spepe_silver" in text:
        has_partition_filter = any(
            p in text.upper()
            for p in ["WHERE", "PARTITION", "LIMIT", "_PARTITIONTIME", "ANO_ELEICAO"]
        )
        if not has_partition_filter:
            msg = "BLOQUEADO: Query BigQuery em tabela SPEPE sem filtro de partição. Adicione WHERE ano_eleicao = ... ou LIMIT."
            logger.warning(f"BigQuery cost block: {tool_name}")
            return msg
    return None


def check_sql_injection(tool_name: str, tool_input: dict) -> str | None:
    """Block SQL injection patterns in any tool input."""
    text = str(tool_input)
    for pattern in _COMPILED_SQL_INJECT:
        if pattern.search(text):
            msg = f"BLOQUEADO: possível SQL injection detectado em {tool_name}."
            logger.warning(msg)
            return msg
    return None
