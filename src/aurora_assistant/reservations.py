"""Regras de negocio de reservas (Etapa 2).

Cada funcao recebe uma conexao sqlite ja aberta (de `storage.connect`) e o
apartamento/identidade ja resolvidos pelo chamador - nunca resolve
apartamento sozinha, e nunca confia num apartamento vindo de argumento de
tool (isso e responsabilidade de quem chama, em `agent.py`, que le
`tool_context.session.user_id`).
"""

from __future__ import annotations

import random
import sqlite3
from typing import Any, Optional

from . import storage

MAX_CODE_ATTEMPTS = 50
# Formato livre (o enunciado permite); espaco amplo so reduz a chance de
# colisao - quem garante correcao e o tratamento de IntegrityError abaixo,
# nao o tamanho do espaco.
_CODE_LOW, _CODE_HIGH = 100_000, 999_999


def _random_code() -> str:
    return f"RSV-{random.randint(_CODE_LOW, _CODE_HIGH)}"


class _CodeCollision(Exception):
    """Sinal interno: o codigo sorteado ja existe, tentar outro."""


class _ConcurrentWinner(Exception):
    """Sinal interno: outra execucao da MESMA (sessao, function-call)
    venceu a corrida e ja commitou. Carrega o resultado dela, ja definitivo
    - o chamador so precisa fazer rollback da propria transacao (que nao
    fez nenhuma escrita util) e devolver esse resultado, sem tentar gravar
    o efeito de novo."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__()
        self.result = result


def create_reservation(
    conn: sqlite3.Connection,
    *,
    apartment: str,
    area_id: str,
    date: str,
    session_id: str,
    function_call_id: str,
) -> dict[str, Any]:
    """Cria uma reserva, ou devolve o resultado ja decidido se essa mesma
    (sessao, function-call) ja tiver sido processada antes - inclusive
    quando o resultado anterior foi "indisponivel": uma operacao concluida
    nunca e refeita, mesmo que a area tenha ficado livre depois.

    A escrita de negocio (INSERT em `reservations`, quando a reserva e
    aceita) e o registro do efeito acontecem na MESMA transacao (`BEGIN
    IMMEDIATE` ... `COMMIT`): uma falha entre as duas deixa a transacao
    inteira sem efeito (nem reserva nem efeito gravados); depois do commit,
    os dois estao persistidos juntos, para sempre consistentes entre si.

    A exclusividade de area+data (Garantia 5) e garantida pelo indice unico
    parcial `idx_reservations_active_area_date`, que so permite uma reserva
    ATIVA por area+data - vale no INSERT, nao numa consulta previa. Os
    conflitos de integridade sao distinguidos pela mensagem do SQLite (cada
    constraint cita suas proprias colunas):
      - colisao de codigo (`reservations.code`): sorteia outro codigo e
        tenta de novo, nunca reaproveita um codigo existente;
      - area+data ja ocupada (`reservations.area_id, reservations.date`):
        resultado de negocio normal ("indisponivel"), gravado como efeito
        final dessa operacao;
      - mesma (sessao, function-call) inserida por outra execucao
        concorrente (`reservations.session_id, reservations.function_call_id`):
        corrida verdadeira - devolve o efeito que a outra execucao gravou;
      - qualquer outra IntegrityError: propagada, nunca mascarada como
        indisponibilidade.
    """
    params = {"area": area_id, "date": date}

    existing = storage.get_effect_record(conn, session_id, function_call_id)
    if existing is not None:
        return _validated_result(existing, params, session_id, function_call_id)

    for _ in range(MAX_CODE_ATTEMPTS):
        code = _random_code()
        conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            # Reconfirma sob o lock de escrita: fecha a janela entre a
            # checagem acima (sem lock) e aqui - se outra execucao
            # concorrente gravou o efeito enquanto esperavamos o lock, usa
            # o resultado dela em vez de prosseguir.
            existing = storage.get_effect_record(conn, session_id, function_call_id)
            if existing is not None:
                return _validated_result(existing, params, session_id, function_call_id)

            try:
                result = _try_insert_reservation(
                    conn, code=code, apartment=apartment, area_id=area_id, date=date,
                    session_id=session_id, function_call_id=function_call_id,
                )
            except _CodeCollision:
                continue
            except _ConcurrentWinner as winner:
                return winner.result

            # Efeito gravado na MESMA transacao da escrita de negocio: uma
            # falha aqui (ou em qualquer ponto antes do commit, inclusive
            # uma excecao inesperada) tem que desfazer os dois juntos - por
            # isso nao ha commit ate aqui, e o `finally` abaixo cobre
            # qualquer saida que nao seja este commit explicito.
            storage.insert_effect_row(conn, session_id, function_call_id, params, result)
            conn.commit()
            committed = True
            return result
        finally:
            if not committed:
                conn.rollback()

    raise RuntimeError("nao foi possivel gerar um codigo de reserva unico")


def _try_insert_reservation(
    conn: sqlite3.Connection,
    *,
    code: str,
    apartment: str,
    area_id: str,
    date: str,
    session_id: str,
    function_call_id: str,
) -> dict[str, Any]:
    """Tenta criar a reserva dentro da transacao ja aberta pelo chamador.

    Devolve o resultado de negocio (criada ou indisponivel) sem commitar -
    quem chama grava o efeito e commita. Levanta `_CodeCollision` para o
    chamador tentar outro codigo, ou deixa IntegrityError genuina subir
    (nunca mascarada)."""
    try:
        conn.execute(
            """
            INSERT INTO reservations
                (code, apartment, area_id, date, status, session_id, function_call_id, created_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (code, apartment, area_id, date, session_id, function_call_id, storage.now_iso()),
        )
        return {"status": "reservado", "codigo": code, "area": area_id, "data": date}
    except sqlite3.IntegrityError as e:
        message = str(e)
        if "reservations.code" in message:
            raise _CodeCollision() from None
        if "reservations.area_id, reservations.date" in message:
            return {"status": "indisponivel", "area": area_id, "data": date}
        if "reservations.session_id, reservations.function_call_id" in message:
            existing = storage.get_effect_record(conn, session_id, function_call_id)
            if existing is not None:
                raise _ConcurrentWinner(existing["result"]) from None
        raise


def _validated_result(
    effect: dict[str, Any], params: dict[str, Any], session_id: str, function_call_id: str
) -> dict[str, Any]:
    if not storage.effect_params_match(effect, params):
        raise storage.EffectParamsMismatch(
            f"a operacao {function_call_id} da sessao {session_id} ja foi executada"
            " com parametros diferentes dos agora apresentados"
        )
    return effect["result"]


def list_active_reservations(conn: sqlite3.Connection, *, apartment: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT code, area_id, date FROM reservations WHERE apartment = ? AND status = 'active' ORDER BY date",
        (apartment,),
    ).fetchall()
    return [{"codigo": r["code"], "area": r["area_id"], "data": r["date"]} for r in rows]


def check_availability(conn: sqlite3.Connection, *, area_id: str, date: str) -> str:
    """Devolve so 'livre'/'ocupada' - nunca de quem e a reserva (Garantia 2)."""
    taken = conn.execute(
        "SELECT 1 FROM reservations WHERE area_id = ? AND date = ? AND status = 'active'",
        (area_id, date),
    ).fetchone()
    return "ocupada" if taken else "livre"


def cancel_reservation(
    conn: sqlite3.Connection, *, apartment: str, area_id: str, date: str
) -> dict[str, Any]:
    """Cancela a reserva do PROPRIO apartamento para essa area/data.

    Se a reserva pertencer a outro apartamento (ou nao existir), devolve o
    mesmo resultado nos dois casos - nunca revela que a area/data esta
    ocupada por outra pessoa (Garantia 2).
    """
    row = conn.execute(
        "SELECT code, apartment FROM reservations WHERE area_id = ? AND date = ? AND status = 'active'",
        (area_id, date),
    ).fetchone()
    if row is None or row["apartment"] != apartment:
        return {"status": "nao_encontrada", "area": area_id, "data": date}

    conn.execute(
        "UPDATE reservations SET status = 'cancelled', cancelled_at = ? WHERE code = ?",
        (storage.now_iso(), row["code"]),
    )
    conn.commit()
    return {"status": "cancelada", "codigo": row["code"], "area": area_id, "data": date}
