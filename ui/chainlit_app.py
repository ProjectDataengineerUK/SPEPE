"""SPEPE — Chainlit UI (Claude Sonnet routing + Gemini agents + session state)."""

import uuid

import chainlit as cl
from fastapi import Response
from chainlit.server import app as _fastapi_app


@_fastapi_app.get("/healthz")
async def healthz() -> Response:
    return Response(content="ok", status_code=200)


import ui.dashboard_api  # noqa: E402, F401
from agents.supervisor import Supervisor  # noqa: E402

# Chainlit registers /{full_path:path} as a catch-all when imported.
# Routes added after that import (dashboard_api) are appended after the catch-all
# and are never reached. Move /dash, /admin, /healthz before the catch-all.
_r = _fastapi_app.router.routes
_our_paths = {"/dash", "/admin", "/healthz"}
_ours = [r for r in _r if getattr(r, "path", "") in _our_paths]
for _route in _ours:
    _r.remove(_route)
_catchall_idx = next(
    (i for i, r in enumerate(_r) if getattr(r, "path", "") == "/{full_path:path}"),
    len(_r),
)
for i, _route in enumerate(_ours):
    _r.insert(_catchall_idx + i, _route)
from config.logging_config import setup_logging  # noqa: E402
from config.session_state import SessionState  # noqa: E402
from config.settings import settings  # noqa: E402
from security.output_validators import validate_input_injection  # noqa: E402

setup_logging(log_level=settings.log_level, console_log_level="ERROR")

_supervisor: Supervisor | None = None


def _get_supervisor() -> Supervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = Supervisor()
    return _supervisor


_HELP = """\
## SPEPE — Comandos disponíveis

| Comando | Agente | Modelo |
|---------|--------|--------|
| `/coletar SP 2022` | Coletor | Gemini Flash |
| `/perfil São Paulo 2022` | Analista | Gemini Pro |
| `/arquétipos BR` | Perfilador | Gemini Flash |
| `/prever Lula 2026` | Modelista → Explicador → Narrador | Gemini Pro |
| `/explicar` | Explicador | Gemini Pro |
| `/relatorio` | Narrador | Gemini Flash |
| `/monitorar` | Vigilante | Gemini Flash |
| `/help` | — | — |

*Arquitetura: Claude Sonnet 4.6 (roteamento + tool use) + Google Gemini (execução) via Vertex AI*
*Budget por sessão: $2.00*
"""


@cl.on_chat_start
async def on_start() -> None:
    session_id = str(uuid.uuid4())
    cl.user_session.set("state", SessionState(session_id=session_id))
    await cl.Message(
        content=(
            "## SPEPE — Sistema de Perfilamento do Eleitorado e Previsão Eleitoral\n\n"
            "Digite `/help` para ver os comandos disponíveis.\n\n"
            f"*Sessão `{session_id[:8]}` iniciada | Budget: $2.00*"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    user_input = message.content.strip()

    if user_input.lower() in ("/help", "help", "ajuda"):
        await cl.Message(content=_HELP).send()
        return

    injection_check = validate_input_injection(user_input)
    if not injection_check.ok:
        await cl.Message(content=f"⚠️ Input bloqueado: {injection_check.reason}").send()
        return

    state: SessionState = cl.user_session.get("state")
    msg = cl.Message(content="")
    await msg.send()

    supervisor = _get_supervisor()
    try:
        async for chunk in supervisor.run(user_input, state):
            msg.content += chunk
            await msg.update()
    except Exception as e:
        msg.content += f"\n\n❌ Erro interno: {e}"
        await msg.update()
