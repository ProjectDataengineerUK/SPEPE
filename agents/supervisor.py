"""SPEPE Supervisor — Claude Sonnet routes via DOMA loop to Gemini sub-agents."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncGenerator

import anthropic

from agents.gemini_agent import AgentResponse
from agents.loader import load_agents
from agents.tools import RunJobArgs, RUN_JOB_TOOL_SCHEMA, run_dataops_job
from config.session_state import SessionState, BUDGET_USD
from config.settings import settings
from security.output_validators import AGENT_VALIDATORS, validate_input_injection

# Loaded once at module init — ensures Supervisor enum stays in sync with registry
_AGENT_REGISTRY: dict = {}


def _get_registry() -> dict:
    global _AGENT_REGISTRY
    if not _AGENT_REGISTRY:
        _AGENT_REGISTRY = load_agents()
    return _AGENT_REGISTRY


logger = logging.getLogger("spepe.agents.supervisor")

# Vertex AI uses different model IDs than the direct Anthropic API.
# claude-sonnet-4-6 (direct) → claude-sonnet-4-5@20251001 (Vertex)
# Fallback: claude-3-5-sonnet-v2@20241022 (confirmed available in us-east5)
_MODEL_DIRECT = "claude-sonnet-4-6"
_MODEL_VERTEX = "claude-sonnet-4-5@20251001"
_MODEL = _MODEL_VERTEX if settings.use_vertex_claude else _MODEL_DIRECT

_MAX_HOPS = 5
_CLAUDE_INPUT_RATE = 3.0 / 1_000_000
_CLAUDE_OUTPUT_RATE = 15.0 / 1_000_000


_INTENT_MODEL = "gemini-2.0-flash-001"
_INTENT_REGION = "us-central1"

_INTENT_EXTRACT_PROMPT = """\
Você extrai instruções estruturadas para um dashboard eleitoral brasileiro a partir do
texto produzido por um agente de análise. Retorne EXCLUSIVAMENTE um JSON com este schema:

{
  "intent_schema": "v1",
  "actions": [...],
  "narration": "..."
}

Tipos de ações permitidos (use apenas os necessários, omita campos desconhecidos):
  {"type": "set_layer",     "layer": "eleitoral|ibge|saude|seguranca|pesquisas|sentimento|predicao"}
  {"type": "map_highlight", "layer": "ibge|saude|seguranca|pesquisas|sentimento|predicao", "uf_values": {"SP": 0.72, "RJ": 0.65}}
  {"type": "set_filter",    "uf": "SP", "cargo": "presidente|governador|senador|deputado federal|deputado estadual", "candidato": "NOME"}
  {"type": "zoom_to",       "uf": "SP", "municipio": 3550308}
  {"type": "highlight",     "municipios": [3550308, 3304557]}
  {"type": "set_metric",    "metric": "idhm|renda|gini|pct_votos|sentiment_score|prediction_pct"}
  {"type": "set_period",    "from": "2026-01-01", "to": "2026-05-10"}

Regras:
- Se nenhuma ação for inferível, retorne {"intent_schema":"v1","actions":[],"narration":""}.
- narration deve resumir em 1-2 frases o que o agente disse, sem markdown.
- Cargo/UF/candidato só se MENCIONADOS explicitamente.
- Codes IBGE de município só se aparecerem literalmente no texto.
- Use map_highlight com uf_values APENAS quando o agente mencionar valores específicos por estado.
- Use set_layer quando o agente mudar de tema (ex: "veja a distribuição de IDHM").
- Não invente dados.

Pergunta original do usuário:
{user_input}

Resposta do agente:
{agent_text}

JSON:"""


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return fence.group(1).strip() if fence else text


def _extract_intent_sync(user_input: str, agent_text: str) -> dict[str, Any] | None:
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id or "spepe-prod",
            location=_INTENT_REGION,
        )
        prompt = _INTENT_EXTRACT_PROMPT.replace("{user_input}", user_input[:1500]).replace(
            "{agent_text}", agent_text[:4000]
        )
        config = types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=1024,
            response_mime_type="application/json",
        )
        response = client.models.generate_content(
            model=_INTENT_MODEL,
            contents=prompt,
            config=config,
        )
        raw = _strip_json_fence(response.text or "")
        if not raw:
            return None
        data = json.loads(raw)
        actions = data.get("actions") or []
        if not isinstance(actions, list) or not actions:
            return None
        return {
            "intent_schema": "v1",
            "actions": actions,
            "narration": str(data.get("narration") or "").strip(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("emit_structured_intent falhou: %s", exc)
        return None


async def emit_structured_intent(user_input: str, agent_text: str) -> dict[str, Any] | None:
    """Extract a v1 DashboardCommand from a free-form agent reply via Gemini Flash."""
    if not agent_text or not agent_text.strip():
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _extract_intent_sync, user_input, agent_text)


def _build_anthropic_client() -> anthropic.AsyncAnthropic | anthropic.AsyncAnthropicVertex:
    if settings.use_vertex_claude:
        return anthropic.AsyncAnthropicVertex(
            project_id=settings.gcp_project_id or "spepe-prod",
            region=settings.vertex_claude_location,
        )
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


_SYSTEM = """\
Você é o Supervisor do SPEPE. Use as ferramentas disponíveis para rotear e executar tarefas.

Agentes disponíveis (via route_to_agent):
- coletor: sumariza resultado de jobs de ingestão já executados
- analista: cruzamento socioeconômico × resultados eleitorais — /analisar, /perfil
- analista_seguranca: correlação violência/segurança × voto — /seguranca, /violencia, /correlacao_seguranca
- sentinela_social: monitoramento redes sociais em tempo real — /social, /sentimento, /narrativas, /crise
- contextualizador_saude: correlação saúde pública × voto — /saude, /vulnerabilidade, /datasus
- perfilador: clustering e arquétipos — /arquétipos, /perfis
- modelista_bayesiano: previsão probabilística com IC 95% — /prever, /simular
- explicador: SHAP em linguagem natural — /explicar, /shap
- narrador: relatório acessível — /relatorio, /narrar
- vigilante: monitoramento de drift — /monitorar, /drift

Jobs de dados (via run_dataops_job — execução REAL):
- tse_ingest: baixa e ingere dados TSE
- ibge_sync: sincroniza IBGE (demographics + socioeconomic)
- security_ingest: segurança pública (IVS, Atlas da Violência, SINESP)
- datasus_ingest: saúde pública (mortalidade, cobertura ANS)
- dieese_ingest: cesta básica DIEESE por UF
- cetic_ingest: acesso digital TIC Domicílios CETIC.br
- silver_transform: Bronze → Silver BigQuery
- gold_build: Silver → Gold BigQuery
- digital_ingest: sinal digital (Google Trends, Meta, YouTube)

Protocolo DOMA:
D — Decomponha a solicitação
O — Para /coletar: execute run_dataops_job ANTES de route_to_agent (coletor só sumariza)
M — Budget: ${budget:.2f}/sessão, usado: ${used:.4f}
A — Para cadeias (/prever requer análise→modelagem→explicação→narração): chame um agente por vez; o próximo hop decide o seguinte

Após cada route_to_agent, SEMPRE chame emit_dashboard_intent com as ações de UI adequadas ao conteúdo da resposta.
Use done=true em emit_dashboard_intent para encerrar o loop quando a tarefa estiver completa — elimina um hop desnecessário.
Responda APENAS via ferramentas — nunca com texto livre.
"""


def _build_routing_tool() -> dict:
    """Build routing tool with agent enum derived from the live registry."""
    agent_ids = sorted(_get_registry().keys())
    return {
        "name": "route_to_agent",
        "description": "Rotear para um agente SPEPE especializado que responde em linguagem natural.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "enum": agent_ids},
                "refined_prompt": {"type": "string"},
                "done": {
                    "type": "boolean",
                    "description": "True se esta é a última chamada e a tarefa está completa.",
                    "default": False,
                },
            },
            "required": ["agent_id", "refined_prompt"],
        },
    }


_EMIT_INTENT_TOOL: dict = {
    "name": "emit_dashboard_intent",
    "description": (
        "Emite ações estruturadas para o dashboard atualizar a UI. "
        "Chamar APÓS route_to_agent com as mudanças de estado adequadas. "
        "Use done=true quando a tarefa estiver completamente concluída — encerra o loop."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "description": "Lista de ações de UI a executar no dashboard.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": [
                                "set_geo",
                                "set_election",
                                "switch_tab",
                                "set_map_layer",
                                "compare_candidates",
                                "highlight",
                                "open_prediction",
                            ],
                        },
                        "level": {"type": "string"},
                        "uf": {"type": "string"},
                        "cd_municipio": {"type": "string"},
                        "nr_zona": {"type": "integer"},
                        "region": {"type": "string"},
                        "ano": {"type": "integer"},
                        "cargo": {"type": "string"},
                        "turno": {"type": "integer"},
                        "candidato_id": {"type": "string"},
                        "tab": {"type": "string"},
                        "layer": {"type": "string"},
                        "indicator": {"type": "string"},
                        "candidates": {"type": "array", "items": {"type": "string"}},
                        "card_id": {"type": "string"},
                        "prediction_id": {"type": "string"},
                    },
                    "required": ["op"],
                },
            },
            "narration": {
                "type": "string",
                "description": "Texto de acompanhamento exibido ao usuário.",
            },
            "done": {
                "type": "boolean",
                "description": "True se a tarefa está completa — encerra o loop sem chamar outra ferramenta.",
                "default": False,
            },
        },
        "required": ["actions"],
    },
}

_ALL_TOOLS = [_build_routing_tool(), RUN_JOB_TOOL_SCHEMA, _EMIT_INTENT_TOOL]


class Supervisor:
    def __init__(self) -> None:
        self._client = _build_anthropic_client()
        self._agents = _get_registry()
        # Rebuild routing tool with live enum (catches new agents added to registry)
        self._tools = [_build_routing_tool(), RUN_JOB_TOOL_SCHEMA, _EMIT_INTENT_TOOL]

    async def run(
        self,
        user_input: str,
        state: SessionState,
        *,
        _intent_sink: list | None = None,
    ) -> AsyncGenerator[str, None]:
        original_user_input = user_input
        last_agent_text: str = ""
        if state.total_cost_usd >= BUDGET_USD:
            yield f"⚠️ Budget esgotado: ${state.total_cost_usd:.4f} / ${BUDGET_USD:.2f}"
            return

        state.add_turn("user", user_input)
        current_input: str | None = user_input

        for hop in range(_MAX_HOPS):
            warn = state.warn_budget()
            if warn:
                yield warn + "\n\n"

            messages = self._build_messages(state, current_input, hop)
            response = await self._client.messages.create(  # type: ignore[call-overload]
                model=_MODEL,
                max_tokens=512,
                system=_SYSTEM.format(budget=BUDGET_USD, used=state.total_cost_usd),
                messages=messages,
                tools=self._tools,
                tool_choice={"type": "required"},
            )

            usage = response.usage
            claude_cost = (
                usage.input_tokens * _CLAUDE_INPUT_RATE + usage.output_tokens * _CLAUDE_OUTPUT_RATE
            )
            try:
                state.add_cost(claude_cost)
            except Exception as e:
                yield str(e)
                return

            tool_use = next((b for b in response.content if b.type == "tool_use"), None)
            if not tool_use:
                yield "Não consegui determinar a próxima ação."
                return

            if tool_use.name == "emit_dashboard_intent":
                if _intent_sink is not None:
                    _intent_sink.append(tool_use.input)
                if tool_use.input.get("done", False):
                    return
                current_input = None
                continue

            if tool_use.name == "run_dataops_job":
                result = self._execute_job(tool_use.input)
                status = "✅" if result["ok"] else "❌"
                yield f"{status} Job `{result['job']}` ({result.get('mode', '')}) — "
                if result["ok"]:
                    yield "concluído com sucesso.\n\n"
                else:
                    yield f"erro: {result.get('error') or result.get('stderr', '')[:200]}\n\n"
                state.add_turn("supervisor", f"job_result: {result}", agent_id="supervisor")
                current_input = None
                continue

            if tool_use.name == "route_to_agent":
                agent_id: str = tool_use.input["agent_id"]
                refined_prompt: str = tool_use.input["refined_prompt"]
                is_done: bool = tool_use.input.get("done", False)

                inj_check = validate_input_injection(refined_prompt)
                if not inj_check.ok:
                    yield f"⚠️ Prompt bloqueado (injection detectada): {inj_check.reason}"
                    return

                agent = self._agents.get(agent_id)
                if not agent:
                    yield f"Agente '{agent_id}' não encontrado."
                    return

                yield f"*→ {agent.name}*\n\n"

                resp: AgentResponse = await agent.run_async(
                    prompt=refined_prompt,
                    session_ctx=state.recent_context(max_turns=6),
                    artifacts=state.artifacts,
                )

                validated_text = await self._validate_output(
                    agent_id, resp.text, agent, refined_prompt
                )

                try:
                    state.add_cost(resp.cost_usd)
                except Exception as e:
                    yield validated_text + f"\n\n{e}"
                    return

                state.add_turn(
                    "agent",
                    validated_text,
                    agent_id=agent_id,
                    tokens_in=resp.input_tokens,
                    tokens_out=resp.output_tokens,
                    cost_usd=resp.cost_usd,
                )

                yield validated_text
                yield (
                    f"\n\n---\n*{agent.model} | "
                    f"sessão: ${state.total_cost_usd:.5f} / ${BUDGET_USD:.2f}*\n\n"
                )

                last_agent_text = validated_text

                if is_done:
                    await self._maybe_emit_intent(
                        original_user_input, last_agent_text, _intent_sink
                    )
                    return
                current_input = None
                continue

        await self._maybe_emit_intent(original_user_input, last_agent_text, _intent_sink)
        yield "\n⚠️ Limite de hops atingido. Tarefa pode estar incompleta."

    def _build_messages(
        self, state: SessionState, current_input: str | None, hop: int
    ) -> list[dict]:
        if hop == 0:
            return [{"role": "user", "content": current_input}]

        recent = state.recent_context(max_turns=8)
        content = (
            f"Contexto dos passos anteriores:\n{recent}\n\n"
            "Decida o próximo passo ou conclua com done=true."
        )
        return [{"role": "user", "content": content}]

    def _execute_job(self, raw: dict) -> dict:
        try:
            args = RunJobArgs(**raw)
            return run_dataops_job(args)
        except Exception as e:
            return {"ok": False, "job": raw.get("job", "?"), "error": str(e)}

    async def _maybe_emit_intent(
        self,
        user_input: str,
        agent_text: str,
        sink: list | None,
    ) -> None:
        """Fallback: if no tool-emitted intent landed in sink, infer one via Gemini Flash."""
        if sink is None or sink:
            return
        if not agent_text:
            return
        intent = await emit_structured_intent(user_input, agent_text)
        if intent and intent.get("actions"):
            sink.append(intent)

    async def _validate_output(self, agent_id: str, text: str, agent, prompt: str) -> str:
        validators = AGENT_VALIDATORS.get(agent_id, [])
        for validator in validators:
            result = validator(text)
            if not result.ok:
                if result.remediated:
                    logger.info("Output de %s remediado: %s", agent_id, result.reason)
                    return result.remediated
                logger.warning(
                    "Output de %s falhou validação: %s — re-prompting",
                    agent_id,
                    result.reason,
                )
                retry = await agent.run_async(
                    f"Sua resposta anterior violou: {result.reason}. "
                    f"Reescreva seguindo rigorosamente o Formato de Resposta OBRIGATÓRIO.",
                )
                return retry.text
        return text
