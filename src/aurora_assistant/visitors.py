"""Regras de negocio de autorizacao de visitantes (Etapa 2)."""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from . import storage


def authorize_visitor(
    conn: sqlite3.Connection,
    *,
    apartment: str,
    name: str,
    date: str,
    session_id: str,
    function_call_id: str,
) -> dict[str, Any]:
    """Autoriza um visitante, ou devolve o resultado ja decidido se essa
    mesma (sessao, function-call) ja tiver sido processada antes.

    A escrita de negocio (INSERT em `visitor_authorizations`) e o registro
    do efeito acontecem na MESMA transacao: uma falha entre as duas deixa a
    transacao inteira sem efeito; depois do commit, os dois ficam
    persistidos juntos. Nao ha regra de exclusividade por data aqui (ao
    contrario de reservas), entao o unico conflito de integridade possivel
    e a mesma (sessao, function-call) inserida por uma execucao concorrente
    - tratado como corrida verdadeira, nunca mascarado.
    """
    params = {"name": name, "date": date}

    existing = storage.get_effect_record(conn, session_id, function_call_id)
    if existing is not None:
        return _validated_result(existing, params, session_id, function_call_id)

    conn.execute("BEGIN IMMEDIATE")
    committed = False
    try:
        # Reconfirma sob o lock de escrita: fecha a janela entre a checagem
        # acima (sem lock) e aqui.
        existing = storage.get_effect_record(conn, session_id, function_call_id)
        if existing is not None:
            return _validated_result(existing, params, session_id, function_call_id)

        try:
            conn.execute(
                """
                INSERT INTO visitor_authorizations
                    (id, apartment, name, date, session_id, function_call_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), apartment, name, date, session_id, function_call_id, storage.now_iso()),
            )
        except sqlite3.IntegrityError as e:
            message = str(e)
            if "visitor_authorizations.session_id, visitor_authorizations.function_call_id" in message:
                existing = storage.get_effect_record(conn, session_id, function_call_id)
                if existing is not None:
                    return _validated_result(existing, params, session_id, function_call_id)
            raise

        # Efeito gravado na MESMA transacao da escrita de negocio: uma
        # falha aqui (ou qualquer excecao inesperada antes do commit) tem
        # que desfazer os dois juntos - o `finally` abaixo cobre qualquer
        # saida que nao seja este commit explicito.
        result = {"status": "autorizado", "nome": name, "data": date}
        storage.insert_effect_row(conn, session_id, function_call_id, params, result)
        conn.commit()
        committed = True
        return result
    finally:
        if not committed:
            conn.rollback()


def _validated_result(
    effect: dict[str, Any], params: dict[str, Any], session_id: str, function_call_id: str
) -> dict[str, Any]:
    if not storage.effect_params_match(effect, params):
        raise storage.EffectParamsMismatch(
            f"a operacao {function_call_id} da sessao {session_id} ja foi executada"
            " com parametros diferentes dos agora apresentados"
        )
    return effect["result"]


def list_authorized_visitors(conn: sqlite3.Connection, *, apartment: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT name, date FROM visitor_authorizations WHERE apartment = ? ORDER BY date",
        (apartment,),
    ).fetchall()
    return [{"nome": r["name"], "data": r["date"]} for r in rows]
