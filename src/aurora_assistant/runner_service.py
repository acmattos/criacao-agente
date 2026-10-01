"""Unica camada que fala com o Runner/ADK de verdade. Implementa o mesmo
"formato" que `confirmation_service.ExecutorProtocol` espera (send_message e
resume), convertendo eventos reais do ADK em `NormalizedEvent`.
"""

from __future__ import annotations

from typing import Optional

from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from . import config
from .agent import build_app
from .events import NormalizedEvent

_runner: Optional[Runner] = None


def get_runner() -> Runner:
    global _runner
    if _runner is None:
        session_service = SqliteSessionService(db_path=str(config.ADK_SESSIONS_DB_PATH))
        _runner = Runner(app=build_app(), session_service=session_service)
    return _runner


def _normalize(event) -> NormalizedEvent:
    normalized = NormalizedEvent(author=event.author)
    if event.content and event.content.parts:
        for part in event.content.parts:
            if part.text:
                normalized.text = part.text
            if part.function_call:
                normalized.function_calls.append({
                    "id": part.function_call.id,
                    "name": part.function_call.name,
                    "args": part.function_call.args or {},
                })
            if part.function_response:
                normalized.function_responses.append({
                    "id": part.function_response.id,
                    "name": part.function_response.name,
                })
    return normalized


async def create_session(*, apartment: str, session_id: str) -> None:
    runner = get_runner()
    await runner.session_service.create_session(
        app_name=config.APP_NAME, user_id=apartment, session_id=session_id
    )


async def send_message(*, apartment: str, session_id: str, texto: str) -> list[NormalizedEvent]:
    runner = get_runner()
    message = types.Content(role="user", parts=[types.Part(text=texto)])
    events: list[NormalizedEvent] = []
    async for event in runner.run_async(
        user_id=apartment, session_id=session_id, new_message=message
    ):
        events.append(_normalize(event))
    return events


async def resume(
    *, apartment: str, session_id: str, confirmation_id: str, confirmado: bool
) -> list[NormalizedEvent]:
    runner = get_runner()
    message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=confirmation_id,
                    name=config.CONFIRMATION_FUNCTION_NAME,
                    response={"confirmed": confirmado},
                )
            )
        ],
    )
    events: list[NormalizedEvent] = []
    async for event in runner.run_async(
        user_id=apartment, session_id=session_id, new_message=message
    ):
        events.append(_normalize(event))
    return events


async def get_raw_events(*, apartment: str, session_id: str):
    runner = get_runner()
    session = await runner.session_service.get_session(
        app_name=config.APP_NAME, user_id=apartment, session_id=session_id
    )
    return session.events if session else None
