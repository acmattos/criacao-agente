"""API minima da Etapa 1: so as quatro rotas necessarias para a prova de
confirmacao persistida (sessao, mensagem, confirmacao, eventos). Visitantes,
regulamento, cancelamento, agenda completa e concorrencia ficam para a
Etapa 2.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import confirmation_service, runner_service, storage
from . import config

app = FastAPI(title="Aurora - Etapa 1 (prova de confirmacao persistida)")

# Indirecao deliberada: os testes deterministicos trocam este modulo por um
# fake (ver tests/test_api.py), sem precisar de Gemini. Em producao e o
# proprio runner_service, que fala com o ADK de verdade.
EXECUTOR = runner_service


class CriarSessaoRequest(BaseModel):
    apartamento: str


class MensagemRequest(BaseModel):
    texto: str


class ConfirmacaoRequest(BaseModel):
    id: str
    confirmado: bool


def _conn():
    return storage.connect(config.DB_PATH)


def _require_session(conn, session_id: str):
    session = storage.get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="sessao nao encontrada")
    return session


@app.post("/sessoes", status_code=201)
async def criar_sessao(body: CriarSessaoRequest):
    session_id = str(uuid.uuid4())
    await EXECUTOR.create_session(apartment=body.apartamento, session_id=session_id)
    conn = _conn()
    try:
        storage.create_session(conn, session_id, body.apartamento)
    finally:
        conn.close()
    return {"session_id": session_id}


@app.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, body: MensagemRequest):
    conn = _conn()
    try:
        session = _require_session(conn, session_id)
        apartment = session["apartment"]
        await confirmation_service.reconcile_stuck(
            conn, EXECUTOR, session_id=session_id, apartment=apartment
        )
        return await confirmation_service.handle_message(
            conn, EXECUTOR, session_id=session_id, apartment=apartment, texto=body.texto
        )
    finally:
        conn.close()


@app.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, body: ConfirmacaoRequest):
    conn = _conn()
    try:
        session = _require_session(conn, session_id)
        try:
            return await confirmation_service.handle_confirmation(
                conn,
                EXECUTOR,
                session_id=session_id,
                apartment=session["apartment"],
                confirmation_id=body.id,
                confirmado=body.confirmado,
            )
        except confirmation_service.ConfirmationConflict:
            raise HTTPException(
                status_code=409, detail="confirmacao inexistente, de outra sessao ou ja respondida"
            )
    finally:
        conn.close()


@app.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str):
    conn = _conn()
    try:
        session = _require_session(conn, session_id)
        apartment = session["apartment"]
    finally:
        conn.close()

    events = await EXECUTOR.get_raw_events(apartment=apartment, session_id=session_id)
    if events is None:
        raise HTTPException(status_code=404, detail="sessao nao encontrada")
    return [_dump_event(event) for event in events]


def _dump_event(event):
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json", exclude_none=True)
    return event
