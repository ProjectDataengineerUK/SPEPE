"""MCP Server: Digital Signal — Meta Ads, Google Trends, YouTube (LGPD-safe aggregate).

Tools:
  digital_trends     — Google Trends por candidato × UF
  digital_meta_ads   — Meta Ad Library por candidato (agregado por UF)
  digital_youtube    — YouTube views do canal oficial (agregado)
  digital_status     — Cache local disponível
"""
from __future__ import annotations

import json
import logging
import os
import sys

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("spepe.mcp.digital")

TOOLS = [
    {
        "name": "digital_trends",
        "description": "Busca Google Trends para palavras-chave (candidatos) no período eleitoral. LGPD: agregado por UF, nunca individual.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}, "description": "Lista de candidatos/termos (max 5)"},
                "timeframe": {"type": "string", "default": "2022-06-01 2022-10-30"},
                "geo": {"type": "string", "default": "BR"},
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "digital_meta_ads",
        "description": "Busca Meta Ad Library por candidato. Retorna ad spend agregado por UF. Requer META_APP_TOKEN.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_name": {"type": "string"},
                "country": {"type": "string", "default": "BR"},
            },
            "required": ["candidate_name"],
        },
    },
    {
        "name": "digital_youtube",
        "description": "Busca views totais do canal oficial de um candidato no período eleitoral. Requer YOUTUBE_API_KEY.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "published_after": {"type": "string", "default": "2022-06-01T00:00:00Z"},
                "published_before": {"type": "string", "default": "2022-10-31T23:59:59Z"},
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "digital_status",
        "description": "Verifica cache de sinal digital disponível localmente.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _trends(keywords, timeframe, geo):
    from mcp_servers.digital.google_trends import fetch_trends
    df = fetch_trends(keywords, timeframe=timeframe, geo=geo)
    if df.empty:
        return "Nenhum dado de Trends disponível (verifique conectividade ou limites da API)."
    return df.to_markdown()


def _meta_ads(candidate_name, country):
    from mcp_servers.digital.meta_ads import fetch_ad_spend_by_candidate
    token = os.environ.get("META_APP_TOKEN", "")
    df = fetch_ad_spend_by_candidate(candidate_name, country=country, access_token=token)
    if df.empty:
        return f"Nenhum dado Meta Ads para '{candidate_name}'. Verifique META_APP_TOKEN."
    return df.to_markdown(index=False)


def _youtube(channel_id, published_after, published_before):
    from mcp_servers.digital.youtube import fetch_video_views_aggregate
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    result = fetch_video_views_aggregate(channel_id, api_key, published_after, published_before)
    return json.dumps(result, ensure_ascii=False, indent=2)


def _status():
    from pathlib import Path
    cache_dirs = [
        Path("data/bronze/digital/google_trends"),
        Path("data/bronze/digital/meta_ads"),
        Path("data/bronze/digital/youtube"),
    ]
    lines = ["Cache de sinal digital:"]
    for d in cache_dirs:
        files = list(d.glob("*.parquet")) if d.exists() else []
        lines.append(f"  {d.name}: {len(files)} arquivos")
    return "\n".join(lines)


def handle_request(request: dict) -> dict:
    method = request.get("method", "")

    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "spepe-digital", "version": "1.0.0"}}

    if method == "tools/list":
        return {"tools": TOOLS}

    if method == "tools/call":
        name = request.get("params", {}).get("name", "")
        args = request.get("params", {}).get("arguments", {})

        if name == "digital_trends":
            text = _trends(args.get("keywords", []), args.get("timeframe", "2022-06-01 2022-10-30"), args.get("geo", "BR"))
            return {"content": [{"type": "text", "text": text}]}
        if name == "digital_meta_ads":
            text = _meta_ads(args.get("candidate_name", ""), args.get("country", "BR"))
            return {"content": [{"type": "text", "text": text}]}
        if name == "digital_youtube":
            text = _youtube(args.get("channel_id", ""), args.get("published_after", "2022-06-01T00:00:00Z"), args.get("published_before", "2022-10-31T23:59:59Z"))
            return {"content": [{"type": "text", "text": text}]}
        if name == "digital_status":
            return {"content": [{"type": "text", "text": _status()}]}
        return {"content": [{"type": "text", "text": f"Tool desconhecida: {name}"}]}

    return {"result": {}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            result = handle_request(request)
            response = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        except Exception as e:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
