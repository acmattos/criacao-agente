"""Persistencia da API: sessoes (apartamento), ledger de confirmacoes e
efeitos (resultado de negocio executado).

O ledger de confirmacoes e a unica fonte de verdade para o contrato publico
de confirmacoes (pendente / aprovada / negada / em execucao / executada /
falhou). Ele nao deve ser deduzido a partir dos eventos do ADK a cada
request: e escrito uma vez quando uma nova pendencia aparece e transicionado
de forma atomica pela rota de confirmacoes.

A tabela de efeitos e a garantia de "executa uma unica vez": a tool grava
nela de forma atomica (INSERT, nunca UPDATE), chaveada por
(session_id, original_function_call_id) - nunca so pelo id da function-call,
porque esse id vem do ADK/modelo e nao tem unicidade global garantida (duas
sessoes podem receber o mesmo id). O session_id vem sempre do contexto
confiavel do ADK (tool_context.session.id), nunca de um argumento que o
modelo escolhe. A chave composta e estavel entre a tentativa inicial e
qualquer retomada ou reenvio da MESMA operacao, mas nao permite que duas
sessoes diferentes compartilhem um efeito por coincidencia de id.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    apartment TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS confirmations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    original_function_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    area TEXT,
    data TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'approved', 'denied', 'executing', 'executed', 'failed')
    ),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS effects (
    session_id TEXT NOT NULL,
    original_function_call_id TEXT NOT NULL,
    params_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, original_function_call_id)
);

-- Linhas de um esquema antigo de `effects` (chave so por
-- original_function_call_id) que a migracao nao conseguiu associar com
-- seguranca a uma unica sessao. Preservadas, nunca descartadas nem
-- associadas por suposicao - ver _migrate_legacy_effects_if_needed.
CREATE TABLE IF NOT EXISTS effects_legacy_unmigrated (
    original_function_call_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_legacy_effects_if_needed(conn: sqlite3.Connection) -> None:
    """Migra uma tabela `effects` do esquema antigo (chave so por
    original_function_call_id) para o esquema atual (chave composta por
    sessao + original_function_call_id).

    Nunca apaga o banco nem a tabela antiga: ela e renomeada para
    `effects_legacy_v1_backup` e fica no banco. Para cada linha antiga,
    tenta achar a sessao atraves de `confirmations.original_function_call_id`
    (que ja guardava essa associacao). Se exatamente uma sessao bate,
    migra; se nenhuma ou mais de uma batem, a associacao e ambigua e a
    linha vai para `effects_legacy_unmigrated` com o motivo registrado -
    nunca inventamos qual sessao era a dona.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(effects)").fetchall()]
    if not cols or "session_id" in cols:
        return  # tabela nova, ou ainda nao existe (sera criada do zero)

    old_rows = conn.execute(
        "SELECT original_function_call_id, result_json, created_at FROM effects"
    ).fetchall()
    conn.execute("ALTER TABLE effects RENAME TO effects_legacy_v1_backup")
    conn.executescript(SCHEMA)

    migrated = 0
    unmigrated = 0
    for row in old_rows:
        fc_id = row["original_function_call_id"]
        matches = conn.execute(
            "SELECT DISTINCT session_id FROM confirmations WHERE original_function_call_id = ?",
            (fc_id,),
        ).fetchall()
        if len(matches) == 1:
            session_id = matches[0]["session_id"]
            confirmation = conn.execute(
                "SELECT area, data FROM confirmations WHERE original_function_call_id = ? AND session_id = ?",
                (fc_id, session_id),
            ).fetchone()
            params = {"area": confirmation["area"], "data": confirmation["data"]}
            conn.execute(
                """
                INSERT INTO effects
                    (session_id, original_function_call_id, params_json, result_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, fc_id, json.dumps(params), row["result_json"], row["created_at"]),
            )
            migrated += 1
        else:
            reason = (
                "nenhuma confirmacao correspondente encontrada em confirmations"
                if not matches
                else f"{len(matches)} sessoes diferentes referenciam este original_function_call_id"
            )
            conn.execute(
                """
                INSERT INTO effects_legacy_unmigrated
                    (original_function_call_id, result_json, created_at, reason, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fc_id, row["result_json"], row["created_at"], reason, now_iso()),
            )
            unmigrated += 1
    conn.commit()
    if migrated or unmigrated:
        logger.warning(
            "Migracao de effects (esquema antigo -> chave por sessao): "
            "%d linha(s) migrada(s), %d preservada(s) sem associacao segura "
            "em effects_legacy_unmigrated (motivo registrado por linha).",
            migrated,
            unmigrated,
        )


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate_legacy_effects_if_needed(conn)
    conn.executescript(SCHEMA)
    return conn


# -- sessoes -----------------------------------------------------------------


def create_session(conn: sqlite3.Connection, session_id: str, apartment: str) -> None:
    conn.execute(
        "INSERT INTO sessions (session_id, apartment, created_at) VALUES (?, ?, ?)",
        (session_id, apartment, now_iso()),
    )
    conn.commit()


def get_session(conn: sqlite3.Connection, session_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()


# -- confirmacoes --------------------------------------------------------------


def insert_pending_confirmation(
    conn: sqlite3.Connection,
    *,
    confirmation_id: str,
    session_id: str,
    original_function_call_id: str,
    tool_name: str,
    area: Optional[str],
    data: Optional[str],
) -> None:
    """Registra uma nova pendencia. Idempotente: se o id ja existe (mesma
    function-call de confirmacao vista de novo), nao faz nada - nunca reseta
    uma confirmacao ja resolvida de volta para 'pending'."""
    conn.execute(
        """
        INSERT OR IGNORE INTO confirmations
            (id, session_id, original_function_call_id, tool_name, area, data, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            confirmation_id,
            session_id,
            original_function_call_id,
            tool_name,
            area,
            data,
            now_iso(),
        ),
    )
    conn.commit()


def get_confirmation(conn: sqlite3.Connection, confirmation_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM confirmations WHERE id = ?", (confirmation_id,)
    ).fetchone()


def list_pending_confirmations(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM confirmations WHERE session_id = ? AND status = 'pending' ORDER BY created_at",
        (session_id,),
    ).fetchall()


def list_stuck_confirmations(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    """Confirmacoes aprovadas cuja execucao nao terminou (ficaram em
    'executing' ou 'failed' - ver confirmation_service.reconcile_executing)."""
    return conn.execute(
        "SELECT * FROM confirmations WHERE session_id = ? AND status IN ('executing', 'failed')",
        (session_id,),
    ).fetchall()


def try_resolve(
    conn: sqlite3.Connection, confirmation_id: str, session_id: str, *, approve: bool
) -> bool:
    """Transicao atomica e guardada: 'pending' -> 'approved'/'denied'.

    So afeta uma linha se o id existir, pertencer a essa sessao e ainda
    estiver pendente - cobre de uma vez so os tres motivos de 409 (id
    inexistente, de outra sessao, ou ja respondido). Retorna True sse a
    transicao realmente aconteceu.
    """
    new_status = "approved" if approve else "denied"
    cur = conn.execute(
        """
        UPDATE confirmations
           SET status = ?, resolved_at = ?
         WHERE id = ? AND session_id = ? AND status = 'pending'
        """,
        (new_status, now_iso(), confirmation_id, session_id),
    )
    conn.commit()
    return cur.rowcount == 1


def set_status(conn: sqlite3.Connection, confirmation_id: str, status: str) -> None:
    conn.execute(
        "UPDATE confirmations SET status = ? WHERE id = ?", (status, confirmation_id)
    )
    conn.commit()


# -- efeitos -------------------------------------------------------------------


class EffectParamsMismatch(Exception):
    """A identidade (sessao + function-call original) ja tem um efeito
    gravado, mas com parametros diferentes dos agora apresentados.

    Isso nunca deve acontecer em operacao normal (o ADK reexecuta a mesma
    function-call com os mesmos argumentos originais), mas se acontecer -
    por exemplo por um bug de roteamento ou uma tentativa de reusar a
    identidade de outra operacao - a resposta e rejeitar explicitamente.
    Nunca executamos de novo nem devolvemos silenciosamente um resultado
    que pertence a outra operacao.
    """


def get_effect(
    conn: sqlite3.Connection, session_id: str, original_function_call_id: str
) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT result_json FROM effects WHERE session_id = ? AND original_function_call_id = ?",
        (session_id, original_function_call_id),
    ).fetchone()
    return json.loads(row["result_json"]) if row else None


def record_effect_if_absent(
    conn: sqlite3.Connection,
    session_id: str,
    original_function_call_id: str,
    params: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Grava o efeito uma unica vez, identificado por (session_id,
    original_function_call_id) - nunca so pelo id da function-call, que vem
    do ADK/modelo e nao tem unicidade global garantida entre sessoes.

    Se a mesma identidade ja tiver um efeito com os MESMOS `params`, devolve
    o resultado ja persistido (idempotente: cobre reexecucao da mesma
    function-call original pelo ADK). Se os parametros forem diferentes,
    levanta EffectParamsMismatch - a identidade nao pode ser reaproveitada
    para uma operacao diferente.
    """
    params_json = json.dumps(params, sort_keys=True)
    try:
        conn.execute(
            """
            INSERT INTO effects
                (session_id, original_function_call_id, params_json, result_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, original_function_call_id, params_json, json.dumps(result), now_iso()),
        )
        conn.commit()
        return result
    except sqlite3.IntegrityError:
        existing = conn.execute(
            """
            SELECT params_json, result_json FROM effects
             WHERE session_id = ? AND original_function_call_id = ?
            """,
            (session_id, original_function_call_id),
        ).fetchone()
        assert existing is not None
        if existing["params_json"] != params_json:
            raise EffectParamsMismatch(
                f"a operacao {original_function_call_id} da sessao {session_id} ja foi"
                " executada com parametros diferentes dos agora apresentados"
            )
        return json.loads(existing["result_json"])
