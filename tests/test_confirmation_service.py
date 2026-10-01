"""Testes deterministicos (sem Gemini, sem ADK) das protecoes de
confirmacao: 409 em id invalido, efeito unico, e recuperacao das duas
janelas de falha pedidas na revisao da Etapa 1.
"""

from __future__ import annotations

import json

import pytest

from aurora_assistant import confirmation_service, storage
from aurora_assistant.events import NormalizedEvent

CONFIRMATION_EVENT = NormalizedEvent(
    author="reservation_specialist",
    function_calls=[
        {
            "id": "adk-confirm-1",
            "name": "adk_request_confirmation",
            "args": {
                "originalFunctionCall": {
                    "id": "adk-fc-1",
                    "name": "reserve_area_proof",
                    "args": {"area": "salao-de-festas", "data": "2030-04-20"},
                },
                "toolConfirmation": {"hint": "...", "confirmed": False},
            },
        }
    ],
)


SECOND_CONFIRMATION_EVENT = NormalizedEvent(
    author="reservation_specialist",
    function_calls=[
        {
            "id": "adk-confirm-2",
            "name": "adk_request_confirmation",
            "args": {
                "originalFunctionCall": {
                    "id": "adk-fc-2",
                    "name": "reserve_area_proof",
                    "args": {"area": "churrasqueira", "data": "2030-05-01"},
                },
                "toolConfirmation": {"hint": "...", "confirmed": False},
            },
        }
    ],
)


class FakeExecutor:
    """Executor fake: send_message devolve uma lista fixa; resume delega
    para uma funcao configuravel por teste, permitindo simular sucesso,
    excecao, e persistencia de efeito "pela tool" (como na vida real)."""

    def __init__(self):
        self.send_message_events: list[NormalizedEvent] = []
        self.resume_calls: list[tuple] = []
        self.resume_behavior = None  # callable(call_index) -> list[NormalizedEvent]

    async def send_message(self, *, apartment, session_id, texto):
        return self.send_message_events

    async def resume(self, *, apartment, session_id, confirmation_id, confirmado):
        index = len(self.resume_calls)
        self.resume_calls.append((apartment, session_id, confirmation_id, confirmado))
        return self.resume_behavior(index)


@pytest.fixture
def conn(tmp_path):
    connection = storage.connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def executor():
    return FakeExecutor()


async def _setup_pending(conn, executor):
    storage.create_session(conn, "s1", "101")
    executor.send_message_events = [CONFIRMATION_EVENT]
    result = await confirmation_service.handle_message(
        conn, executor, session_id="s1", apartment="101", texto="reserve o salao"
    )
    assert len(result["confirmacoes_pendentes"]) == 1
    return result["confirmacoes_pendentes"][0]["id"]


@pytest.mark.asyncio
async def test_handle_message_ingests_pending_confirmation(conn, executor):
    confirmation_id = await _setup_pending(conn, executor)
    assert confirmation_id == "adk-confirm-1"
    row = storage.get_confirmation(conn, confirmation_id)
    assert row["status"] == "pending"
    assert json.loads(row["args_json"]) == {"area": "salao-de-festas", "data": "2030-04-20"}
    assert storage.get_effect(conn, "s1", "adk-fc-1") is None


@pytest.mark.asyncio
async def test_deny_does_not_execute(conn, executor):
    confirmation_id = await _setup_pending(conn, executor)
    executor.resume_behavior = lambda i: [
        NormalizedEvent(author="reservation_specialist", text="nao foi possivel reservar")
    ]
    result = await confirmation_service.handle_confirmation(
        conn,
        executor,
        session_id="s1",
        apartment="101",
        confirmation_id=confirmation_id,
        confirmado=False,
    )
    assert result["confirmacoes_pendentes"] == []
    assert storage.get_confirmation(conn, confirmation_id)["status"] == "denied"
    assert storage.get_effect(conn, "s1", "adk-fc-1") is None


@pytest.mark.asyncio
async def test_approve_executes_exactly_once(conn, executor):
    confirmation_id = await _setup_pending(conn, executor)

    def behavior(_index):
        storage.record_effect_if_absent(
            conn,
            "s1",
            "adk-fc-1",
            {"area": "salao-de-festas", "data": "2030-04-20"},
            {"status": "reservado", "area": "salao-de-festas"},
        )
        return [NormalizedEvent(author="reservation_specialist", text="reservado!")]

    executor.resume_behavior = behavior
    result = await confirmation_service.handle_confirmation(
        conn,
        executor,
        session_id="s1",
        apartment="101",
        confirmation_id=confirmation_id,
        confirmado=True,
    )
    assert result["resposta"] == "reservado!"
    assert result["confirmacoes_pendentes"] == []
    assert storage.get_confirmation(conn, confirmation_id)["status"] == "executed"
    assert storage.get_effect(conn, "s1", "adk-fc-1") == {
        "status": "reservado",
        "area": "salao-de-festas",
    }
    assert len(executor.resume_calls) == 1


@pytest.mark.asyncio
async def test_resend_after_approved_is_conflict_and_does_not_call_executor_again(conn, executor):
    confirmation_id = await _setup_pending(conn, executor)
    calls = {"n": 0}

    def behavior(_index):
        calls["n"] += 1
        storage.record_effect_if_absent(
            conn, "s1", "adk-fc-1", {"area": "salao-de-festas", "data": "2030-04-20"}, {"status": "reservado"}
        )
        return [NormalizedEvent(author="reservation_specialist", text="reservado!")]

    executor.resume_behavior = behavior
    await confirmation_service.handle_confirmation(
        conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=True
    )
    assert calls["n"] == 1

    with pytest.raises(confirmation_service.ConfirmationConflict):
        await confirmation_service.handle_confirmation(
            conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=True
        )
    # o executor (e portanto o ADK/Gemini) nunca foi chamado para o reenvio.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_resend_after_denied_is_conflict(conn, executor):
    confirmation_id = await _setup_pending(conn, executor)
    executor.resume_behavior = lambda i: [NormalizedEvent(author="x", text="negado")]
    await confirmation_service.handle_confirmation(
        conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=False
    )
    with pytest.raises(confirmation_service.ConfirmationConflict):
        await confirmation_service.handle_confirmation(
            conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=True
        )


@pytest.mark.asyncio
async def test_unknown_confirmation_id_is_conflict(conn, executor):
    storage.create_session(conn, "s1", "101")
    with pytest.raises(confirmation_service.ConfirmationConflict):
        await confirmation_service.handle_confirmation(
            conn, executor, session_id="s1", apartment="101", confirmation_id="id-inexistente", confirmado=True
        )
    assert executor.resume_calls == []


@pytest.mark.asyncio
async def test_confirmation_from_another_session_is_conflict(conn, executor):
    confirmation_id = await _setup_pending(conn, executor)
    storage.create_session(conn, "s2", "302")
    with pytest.raises(confirmation_service.ConfirmationConflict):
        await confirmation_service.handle_confirmation(
            conn, executor, session_id="s2", apartment="302", confirmation_id=confirmation_id, confirmado=True
        )
    # a pendencia continua intacta na sessao correta.
    assert storage.get_confirmation(conn, confirmation_id)["status"] == "pending"


@pytest.mark.asyncio
async def test_failure_after_decision_before_execution_recovers_via_reconcile(conn, executor):
    """Falha 1: decisao persistida ('approved'), processo cai antes do
    executor rodar. Simulado chamando o executor direto para falhar, e
    confirmando que o reenvio publico continua 409 enquanto isso, e que a
    reconciliacao completa a operacao sem duplicar o efeito."""
    confirmation_id = await _setup_pending(conn, executor)

    def failing_behavior(_index):
        raise RuntimeError("processo caiu durante a chamada ao Gemini")

    executor.resume_behavior = failing_behavior
    with pytest.raises(RuntimeError):
        await confirmation_service.handle_confirmation(
            conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=True
        )
    # decisao ja foi persistida antes da falha: reenvio publico e 409, nao recomeca do zero.
    with pytest.raises(confirmation_service.ConfirmationConflict):
        await confirmation_service.handle_confirmation(
            conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=True
        )
    assert storage.get_confirmation(conn, confirmation_id)["status"] == "failed"
    assert storage.get_effect(conn, "s1", "adk-fc-1") is None

    # reconciliacao com um executor que agora funciona completa a operacao.
    calls = {"n": 0}

    def working_behavior(_index):
        calls["n"] += 1
        storage.record_effect_if_absent(
            conn, "s1", "adk-fc-1", {"area": "salao-de-festas", "data": "2030-04-20"}, {"status": "reservado"}
        )
        return [NormalizedEvent(author="x", text="reservado apos recuperacao")]

    executor.resume_behavior = working_behavior
    await confirmation_service.reconcile_stuck(conn, executor, session_id="s1", apartment="101")

    assert storage.get_confirmation(conn, confirmation_id)["status"] == "executed"
    assert storage.get_effect(conn, "s1", "adk-fc-1") == {"status": "reservado"}
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_failure_after_effect_before_response_recovers_without_calling_executor_again(
    conn, executor
):
    """Falha 2: efeito ja persistido pela tool, processo cai antes de
    terminar a resposta HTTP. Simulado fazendo o executor gravar o efeito e
    so depois levantar excecao. A reconciliacao deve promover o status para
    'executed' SEM chamar o executor de novo (o efeito ja existe)."""
    confirmation_id = await _setup_pending(conn, executor)

    def behavior_crashes_after_effect(_index):
        storage.record_effect_if_absent(
            conn, "s1", "adk-fc-1", {"area": "salao-de-festas", "data": "2030-04-20"}, {"status": "reservado"}
        )
        raise RuntimeError("processo caiu depois do efeito, antes de montar a resposta")

    executor.resume_behavior = behavior_crashes_after_effect
    with pytest.raises(RuntimeError):
        await confirmation_service.handle_confirmation(
            conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=True
        )
    assert storage.get_confirmation(conn, confirmation_id)["status"] == "failed"
    assert storage.get_effect(conn, "s1", "adk-fc-1") == {"status": "reservado"}
    calls_before_reconcile = len(executor.resume_calls)

    def must_not_be_called(_index):
        raise AssertionError("reconciliacao nao deveria chamar o executor: o efeito ja existe")

    executor.resume_behavior = must_not_be_called
    await confirmation_service.reconcile_stuck(conn, executor, session_id="s1", apartment="101")

    assert storage.get_confirmation(conn, confirmation_id)["status"] == "executed"
    # a reconciliacao nao fez nenhuma chamada nova ao executor (so promoveu o status).
    assert len(executor.resume_calls) == calls_before_reconcile


# -- regressao: confirmacoes geradas durante a retomada (item 1 da revisao) --


@pytest.mark.asyncio
async def test_deny_can_surface_a_new_pending_confirmation(conn, executor):
    """Negar uma pendencia pode, na mesma resposta do ADK, trazer um novo
    pedido de confirmacao (o especialista propoe outra coisa). Isso tem que
    ser registrado e aparecer em confirmacoes_pendentes, nao descartado."""
    confirmation_id = await _setup_pending(conn, executor)
    executor.resume_behavior = lambda i: [SECOND_CONFIRMATION_EVENT]

    result = await confirmation_service.handle_confirmation(
        conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=False
    )

    assert [p["id"] for p in result["confirmacoes_pendentes"]] == ["adk-confirm-2"]
    row = storage.get_confirmation(conn, "adk-confirm-2")
    assert row is not None and row["status"] == "pending"
    assert storage.get_effect(conn, "s1", "adk-fc-2") is None


@pytest.mark.asyncio
async def test_approve_can_surface_a_new_pending_confirmation(conn, executor):
    """Mesma coisa no caminho de aprovacao: a retomada pode gerar uma nova
    pendencia (ex.: o especialista pede confirmacao de outra reserva em
    seguida)."""
    confirmation_id = await _setup_pending(conn, executor)

    def behavior(_index):
        storage.record_effect_if_absent(conn, "s1", "adk-fc-1", {"area": "salao-de-festas", "data": "2030-04-20"}, {"status": "reservado"})
        return [SECOND_CONFIRMATION_EVENT]

    executor.resume_behavior = behavior
    result = await confirmation_service.handle_confirmation(
        conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=True
    )

    assert [p["id"] for p in result["confirmacoes_pendentes"]] == ["adk-confirm-2"]
    row = storage.get_confirmation(conn, "adk-confirm-2")
    assert row is not None and row["status"] == "pending"

    # a nova pendencia pode ser respondida normalmente pela rota existente.
    executor.resume_behavior = lambda i: [NormalizedEvent(author="x", text="negado")]
    await confirmation_service.handle_confirmation(
        conn, executor, session_id="s1", apartment="101", confirmation_id="adk-confirm-2", confirmado=False
    )
    assert storage.get_confirmation(conn, "adk-confirm-2")["status"] == "denied"


@pytest.mark.asyncio
async def test_reconcile_can_surface_a_new_pending_confirmation(conn, executor):
    """A reconciliacao de uma confirmacao travada tambem precisa registrar
    qualquer nova pendencia que a retomada trouxer."""
    confirmation_id = await _setup_pending(conn, executor)

    def failing_behavior(_index):
        raise RuntimeError("falha simulada")

    executor.resume_behavior = failing_behavior
    with pytest.raises(RuntimeError):
        await confirmation_service.handle_confirmation(
            conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=True
        )

    def working_behavior(_index):
        storage.record_effect_if_absent(conn, "s1", "adk-fc-1", {"area": "salao-de-festas", "data": "2030-04-20"}, {"status": "reservado"})
        return [SECOND_CONFIRMATION_EVENT]

    executor.resume_behavior = working_behavior
    await confirmation_service.reconcile_stuck(conn, executor, session_id="s1", apartment="101")

    row = storage.get_confirmation(conn, "adk-confirm-2")
    assert row is not None and row["status"] == "pending"
    assert [p["id"] for p in storage.list_pending_confirmations(conn, "s1")] == ["adk-confirm-2"]


@pytest.mark.asyncio
async def test_reingesting_events_does_not_reopen_a_resolved_confirmation(conn, executor):
    """Reprocessar o mesmo evento de confirmacao (ex.: um reenvio de rede,
    ou o mesmo evento aparecendo em duas respostas do ADK) nao pode reabrir
    uma confirmacao ja respondida."""
    confirmation_id = await _setup_pending(conn, executor)
    executor.resume_behavior = lambda i: []
    await confirmation_service.handle_confirmation(
        conn, executor, session_id="s1", apartment="101", confirmation_id=confirmation_id, confirmado=False
    )
    assert storage.get_confirmation(conn, confirmation_id)["status"] == "denied"

    executor.send_message_events = [CONFIRMATION_EVENT]
    await confirmation_service.handle_message(
        conn, executor, session_id="s1", apartment="101", texto="qualquer coisa"
    )
    assert storage.get_confirmation(conn, confirmation_id)["status"] == "denied"
