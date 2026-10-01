"""Orquestra mensagens e confirmacoes sem nunca deixar o texto da conversa
decidir uma aprovacao, e sem jamais chamar o Runner do ADK para um id de
confirmacao que nao esteja 'pending' no ledger desta API.

Este modulo e deliberadamente independente do ADK: recebe um `executor` (ver
`ExecutorProtocol`) com `send_message` e `resume`, e so enxerga
`NormalizedEvent`. Em producao o executor e `runner_service` (fala com o
Gemini de verdade); nos testes e um fake determinístico.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional, Protocol

from . import areas, config, storage
from .events import NormalizedEvent


class ConfirmationConflict(Exception):
    """Levantado quando um id de confirmacao nao esta pendente nesta sessao.

    A rota HTTP deve traduzir isso para 409 sem jamais ter chamado o
    executor/Runner antes de levantar esta excecao.
    """


class ExecutorProtocol(Protocol):
    async def send_message(
        self, *, apartment: str, session_id: str, texto: str
    ) -> list[NormalizedEvent]: ...

    async def resume(
        self,
        *,
        apartment: str,
        session_id: str,
        confirmation_id: str,
        confirmado: bool,
    ) -> list[NormalizedEvent]: ...


def _extract_text(events: list[NormalizedEvent]) -> str:
    textos = [e.text for e in events if e.text]
    return textos[-1] if textos else ""


def _ingest_new_confirmations(
    conn: sqlite3.Connection, session_id: str, events: list[NormalizedEvent]
) -> None:
    for event in events:
        for fc in event.function_calls:
            if fc.get("name") != config.CONFIRMATION_FUNCTION_NAME:
                continue
            original = (fc.get("args") or {}).get("originalFunctionCall") or {}
            original_args = original.get("args") or {}
            storage.insert_pending_confirmation(
                conn,
                confirmation_id=fc["id"],
                session_id=session_id,
                original_function_call_id=original.get("id", fc["id"]),
                tool_name=original.get("name", ""),
                args=original_args,
            )


def _build_pending_list(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """Monta `confirmacoes_pendentes` a partir dos argumentos reais da tool
    (`args_json`) - genericamente, sem assumir que toda tool tem `area`/
    `data`: a autorizacao de visitante, por exemplo, tem `name`/`date`.

    `taxa` so e adicionada quando `args` tem uma chave `area` que bate com
    uma area cadastrada - nunca extraida de um argumento do modelo."""
    pendentes = []
    for row in storage.list_pending_confirmations(conn, session_id):
        args = json.loads(row["args_json"])
        detalhes: dict[str, Any] = dict(args)
        area = args.get("area")
        if area:
            taxa = areas.lookup_taxa(area)
            if taxa is not None:
                detalhes["taxa"] = taxa
        pendentes.append({"id": row["id"], "acao": row["tool_name"], "detalhes": detalhes})
    return pendentes


async def handle_message(
    conn: sqlite3.Connection,
    executor: ExecutorProtocol,
    *,
    session_id: str,
    apartment: str,
    texto: str,
) -> dict[str, Any]:
    events = await executor.send_message(apartment=apartment, session_id=session_id, texto=texto)
    _ingest_new_confirmations(conn, session_id, events)
    return {
        "resposta": _extract_text(events),
        "confirmacoes_pendentes": _build_pending_list(conn, session_id),
    }


async def handle_confirmation(
    conn: sqlite3.Connection,
    executor: ExecutorProtocol,
    *,
    session_id: str,
    apartment: str,
    confirmation_id: str,
    confirmado: bool,
) -> dict[str, Any]:
    """Resolve uma confirmacao pendente.

    So chama o executor (e portanto o ADK/Gemini) depois que a transicao
    atomica 'pending' -> 'approved'/'denied' no ledger tiver sucesso. Se a
    transicao falhar (id inexistente, de outra sessao, ou ja respondido),
    levanta ConfirmationConflict sem tocar no executor.
    """
    if not storage.try_resolve(conn, confirmation_id, session_id, approve=confirmado):
        raise ConfirmationConflict(confirmation_id)

    if not confirmado:
        events = await executor.resume(
            apartment=apartment,
            session_id=session_id,
            confirmation_id=confirmation_id,
            confirmado=False,
        )
        _ingest_new_confirmations(conn, session_id, events)
        return {
            "resposta": _extract_text(events),
            "confirmacoes_pendentes": _build_pending_list(conn, session_id),
        }

    return await _execute_approved(
        conn, executor, session_id=session_id, apartment=apartment, confirmation_id=confirmation_id
    )


async def _execute_approved(
    conn: sqlite3.Connection,
    executor: ExecutorProtocol,
    *,
    session_id: str,
    apartment: str,
    confirmation_id: str,
) -> dict[str, Any]:
    """Executa uma confirmacao ja aprovada no ledger.

    Usado tanto pelo caminho normal (logo apos try_resolve) quanto pela
    reconciliacao de confirmacoes travadas em 'executing'/'failed'.
    Pressupoe que o status no ledger ja e 'approved', 'executing' ou
    'failed' - nunca 'pending' (quem decide pending->approved e
    handle_confirmation via try_resolve).
    """
    storage.set_status(conn, confirmation_id, "executing")
    try:
        events = await executor.resume(
            apartment=apartment,
            session_id=session_id,
            confirmation_id=confirmation_id,
            confirmado=True,
        )
    except Exception:
        storage.set_status(conn, confirmation_id, "failed")
        raise

    _ingest_new_confirmations(conn, session_id, events)

    confirmation = storage.get_confirmation(conn, confirmation_id)
    effect = storage.get_effect(conn, session_id, confirmation["original_function_call_id"])
    if effect is None:
        # A tool nao persistiu nenhum efeito (ex.: o modelo nao chegou a
        # chamar a tool de novo). Nao marcamos como executado - fica
        # 'failed' para uma reconciliacao futura tentar de novo.
        storage.set_status(conn, confirmation_id, "failed")
        raise RuntimeError(
            f"execucao da confirmacao {confirmation_id} nao produziu efeito persistido"
        )

    storage.set_status(conn, confirmation_id, "executed")
    return {
        "resposta": _extract_text(events),
        "confirmacoes_pendentes": _build_pending_list(conn, session_id),
    }


async def reconcile_stuck(
    conn: sqlite3.Connection,
    executor: ExecutorProtocol,
    *,
    session_id: str,
    apartment: str,
) -> None:
    """Repara confirmacoes aprovadas cuja execucao nao terminou.

    Cobre as duas janelas de falha pedidas na revisao:
      - decisao persistida ('approved'), processo caiu antes de chamar o
        executor: status fica 'executing' so quando _execute_approved roda;
        se o processo caiu ANTES disso (entre try_resolve e
        _execute_approved), o status fica em 'approved' puro - tambem
        coberto aqui.
      - efeito persistido pela tool, processo caiu antes de finalizar a
        resposta: o efeito ja existe: so promove 'executing'/'failed' para
        'executed' sem chamar o executor de novo.
    Nunca duplica o efeito: ou o efeito ja existe (so promove o status), ou
    chama o executor de novo, que por sua vez so persiste o efeito se ele
    ainda nao existir (garantido por storage.record_effect_if_absent,
    chamado de dentro da tool).
    """
    stuck_statuses = ("approved", "executing", "failed")
    rows = conn.execute(
        "SELECT * FROM confirmations WHERE session_id = ? AND status IN (?, ?, ?)",
        (session_id, *stuck_statuses),
    ).fetchall()

    for row in rows:
        effect = storage.get_effect(conn, session_id, row["original_function_call_id"])
        if effect is not None:
            storage.set_status(conn, row["id"], "executed")
            continue
        try:
            await _execute_approved(
                conn,
                executor,
                session_id=session_id,
                apartment=apartment,
                confirmation_id=row["id"],
            )
        except Exception:
            # ja registrado como 'failed' dentro de _execute_approved;
            # segue tentando reconciliar as outras pendencias travadas.
            continue
