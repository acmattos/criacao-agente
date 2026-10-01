"""HISTÓRICO — experimento isolado, FORA do fluxo válido da aplicação.

Este script foi o primeiro experimento da Etapa 1 e permanece aqui só como
registro: foi ele que revelou, de propósito, que o ADK reexecuta a tool
quando a mesma confirmação já respondida é reenviada (ver "Achado crítico"
em docs/etapa1-prova-tecnica.md). Ele chama o Runner do ADK DIRETO, sem
passar pelo ledger de confirmações (`aurora_assistant.confirmation_service`
+ `aurora_assistant.storage`) que a API real usa para bloquear isso com
409. Rodar este script contra `.data/etapa1_proof.db` NÃO passa pelas
proteções da aplicação — não é um jeito alternativo de operar a Aurora, é
só a evidência de como o ADK se comporta sem elas.

A implementação válida é `aurora_assistant.api` (FastAPI), subida com
`uv run uvicorn aurora_assistant.api:app --port 8000`. Use este script
apenas para reproduzir o achado histórico, nunca como substituto da API.

Cada subcomando roda em um processo Python novo e isolado, usando
`SqliteSessionService` apontando para `.data/etapa1_proof.db`. Isso simula
um reinício de processo entre o pedido de confirmação e a resposta do
morador: nada fica em memória entre uma chamada e outra deste script, só o
que está persistido no SQLite.

Uso:
    uv run python scripts/etapa1_proof_legado.py create-session
    uv run python scripts/etapa1_proof_legado.py send <session_id> "<texto>"
    uv run python scripts/etapa1_proof_legado.py pending <session_id>
    uv run python scripts/etapa1_proof_legado.py confirm <session_id> <confirmation_id> <true|false>
    uv run python scripts/etapa1_proof_legado.py events <session_id>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import LlmAgent
from google.adk.apps.app import App
from google.adk.apps._configs import ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

APP_NAME = "aurora-proof"
USER_ID = "101"
MODEL = "gemini-2.5-flash"
CONFIRMATION_FUNCTION_NAME = "adk_request_confirmation"

DATA_DIR = Path(__file__).resolve().parent.parent / ".data"
DB_PATH = DATA_DIR / "etapa1_proof.db"
EXECUTION_LOG_PATH = DATA_DIR / "etapa1_proof_execucoes.log"


def _log_execution(area: str, data: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with EXECUTION_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} area={area} data={data}\n")


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum do condomínio em uma data. Gera cobrança, por
    isso só é efetivada depois de confirmação explícita do morador."""
    _log_execution(area, data)
    return {"status": "reservado", "area": area, "data": data}


def build_app() -> App:
    especialista_reservas = LlmAgent(
        name="especialista_reservas",
        model=MODEL,
        description="Especialista em reservas de áreas comuns do condomínio.",
        instruction=(
            "Você cuida apenas de reservas de áreas comuns. Quando o morador"
            " pedir para reservar uma área, chame a tool reservar_area com a"
            " área e a data informadas. Não peça confirmação por texto: a"
            " tool já exige confirmação pelo sistema."
        ),
        tools=[FunctionTool(reservar_area, require_confirmation=True)],
    )

    principal = LlmAgent(
        name="principal",
        model=MODEL,
        description="Assistente principal do Residencial Aurora.",
        instruction=(
            "Você é o assistente do Residencial Aurora. Para qualquer pedido"
            " de reserva de área comum (salão de festas, churrasqueira,"
            " quadra), transfira a conversa para especialista_reservas."
        ),
        sub_agents=[especialista_reservas],
    )

    return App(
        name=APP_NAME,
        root_agent=principal,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


def build_runner() -> Runner:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session_service = SqliteSessionService(db_path=str(DB_PATH))
    return Runner(app=build_app(), session_service=session_service)


def find_pending_confirmations(events) -> list[dict]:
    """Varre os eventos da sessão e devolve as confirmações ainda sem resposta.

    Uma confirmação (function_call `adk_request_confirmation`) é pendente
    quando não existe, depois dela, um evento de autor "user" com uma
    function_response do mesmo id.
    """
    pending: dict[str, dict] = {}
    for event in events:
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            fc = part.function_call
            if fc and fc.name == CONFIRMATION_FUNCTION_NAME:
                pending[fc.id] = {
                    "id": fc.id,
                    "args": fc.args,
                    "author": event.author,
                }
            fr = part.function_response
            if (
                fr
                and fr.name == CONFIRMATION_FUNCTION_NAME
                and fr.id in pending
            ):
                pending.pop(fr.id, None)
    return list(pending.values())


def print_event(event) -> None:
    parts_desc = []
    if event.content and event.content.parts:
        for part in event.content.parts:
            if part.text:
                parts_desc.append(f"text={part.text!r}")
            if part.function_call:
                parts_desc.append(
                    f"function_call(name={part.function_call.name},"
                    f" id={part.function_call.id}, args={part.function_call.args})"
                )
            if part.function_response:
                parts_desc.append(
                    f"function_response(name={part.function_response.name},"
                    f" id={part.function_response.id},"
                    f" response={part.function_response.response})"
                )
    print(f"  [{event.author}] invocation={event.invocation_id} id={event.id}")
    for desc in parts_desc:
        print(f"      {desc}")


async def cmd_create_session(_: argparse.Namespace) -> None:
    runner = build_runner()
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID
    )
    print(session.id)


async def cmd_send(args: argparse.Namespace) -> None:
    runner = build_runner()
    message = types.Content(role="user", parts=[types.Part(text=args.texto)])
    async for event in runner.run_async(
        user_id=USER_ID, session_id=args.session_id, new_message=message
    ):
        print_event(event)

    session = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=args.session_id
    )
    pending = find_pending_confirmations(session.events)
    print(f"\nconfirmacoes_pendentes ({len(pending)}):")
    for p in pending:
        print(f"  id={p['id']} args={p['args']}")


async def cmd_confirm(args: argparse.Namespace) -> None:
    runner = build_runner()
    confirmado = args.confirmado.lower() in ("true", "1", "sim", "yes")
    message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=args.confirmation_id,
                    name=CONFIRMATION_FUNCTION_NAME,
                    response={"confirmed": confirmado},
                )
            )
        ],
    )
    async for event in runner.run_async(
        user_id=USER_ID, session_id=args.session_id, new_message=message
    ):
        print_event(event)

    session = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=args.session_id
    )
    pending = find_pending_confirmations(session.events)
    print(f"\nconfirmacoes_pendentes ({len(pending)}):")
    for p in pending:
        print(f"  id={p['id']} args={p['args']}")


async def cmd_pending(args: argparse.Namespace) -> None:
    runner = build_runner()
    session = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=args.session_id
    )
    if session is None:
        print("sessao nao encontrada", file=sys.stderr)
        sys.exit(1)
    pending = find_pending_confirmations(session.events)
    print(f"confirmacoes_pendentes ({len(pending)}):")
    for p in pending:
        print(f"  id={p['id']} args={p['args']}")


async def cmd_events(args: argparse.Namespace) -> None:
    runner = build_runner()
    session = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=args.session_id
    )
    if session is None:
        print("sessao nao encontrada", file=sys.stderr)
        sys.exit(1)
    print(f"total de eventos: {len(session.events)}")
    for event in session.events:
        print_event(event)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("create-session")

    p_send = sub.add_parser("send")
    p_send.add_argument("session_id")
    p_send.add_argument("texto")

    p_confirm = sub.add_parser("confirm")
    p_confirm.add_argument("session_id")
    p_confirm.add_argument("confirmation_id")
    p_confirm.add_argument("confirmado")

    p_pending = sub.add_parser("pending")
    p_pending.add_argument("session_id")

    p_events = sub.add_parser("events")
    p_events.add_argument("session_id")

    args = parser.parse_args()
    handlers = {
        "create-session": cmd_create_session,
        "send": cmd_send,
        "confirm": cmd_confirm,
        "pending": cmd_pending,
        "events": cmd_events,
    }
    asyncio.run(handlers[args.cmd](args))


if __name__ == "__main__":
    main()
