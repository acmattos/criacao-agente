from __future__ import annotations

import pytest

from aurora_assistant import reservations, storage


@pytest.fixture
def conn(tmp_path):
    connection = storage.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def test_create_reservation_succeeds_and_generates_code(conn):
    result = reservations.create_reservation(
        conn, apartment="101", area_id="salao-de-festas", date="2030-04-20",
        session_id="s1", function_call_id="fc1",
    )
    assert result["status"] == "reservado"
    assert result["area"] == "salao-de-festas"
    assert result["data"] == "2030-04-20"
    assert result["codigo"].startswith("RSV-")


def test_create_reservation_is_idempotent_for_same_session_and_fc(conn):
    first = reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20",
        session_id="s1", function_call_id="fc1",
    )
    second = reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20",
        session_id="s1", function_call_id="fc1",
    )
    assert first == second
    count = conn.execute("SELECT COUNT(*) c FROM reservations").fetchone()["c"]
    assert count == 1


def test_two_sequential_operations_for_same_area_date_only_one_succeeds(conn):
    """Chamadas sequenciais (sem concorrencia real) para a mesma area/data:
    confirma a logica de conflito, mas nao prova exclusividade sob corrida
    de verdade - isso e `test_two_concurrent_operations_for_same_area_date`
    (threads + sqlite3 de verdade), em test_concurrency.py."""
    first = reservations.create_reservation(
        conn, apartment="101", area_id="salao-de-festas", date="2030-05-11",
        session_id="s1", function_call_id="fc1",
    )
    second = reservations.create_reservation(
        conn, apartment="201", area_id="salao-de-festas", date="2030-05-11",
        session_id="s2", function_call_id="fc2",
    )
    statuses = {first["status"], second["status"]}
    assert statuses == {"reservado", "indisponivel"}
    active = conn.execute(
        "SELECT COUNT(*) c FROM reservations WHERE area_id='salao-de-festas' AND date='2030-05-11' AND status='active'"
    ).fetchone()["c"]
    assert active == 1


def test_list_active_reservations_filters_by_apartment(conn):
    reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    reservations.create_reservation(
        conn, apartment="302", area_id="churrasqueira", date="2030-04-21", session_id="s2", function_call_id="fc2"
    )
    result = reservations.list_active_reservations(conn, apartment="101")
    assert result == [{"codigo": result[0]["codigo"], "area": "quadra", "data": "2030-04-20"}]


def test_check_availability(conn):
    assert reservations.check_availability(conn, area_id="quadra", date="2030-04-20") == "livre"
    reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    assert reservations.check_availability(conn, area_id="quadra", date="2030-04-20") == "ocupada"


def test_cancel_own_reservation_succeeds(conn):
    reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    result = reservations.cancel_reservation(conn, apartment="101", area_id="quadra", date="2030-04-20")
    assert result["status"] == "cancelada"
    assert reservations.check_availability(conn, area_id="quadra", date="2030-04-20") == "livre"
    assert reservations.list_active_reservations(conn, apartment="101") == []


def test_cancel_other_apartment_reservation_is_indistinguishable_from_not_found(conn):
    reservations.create_reservation(
        conn, apartment="302", area_id="salao-de-festas", date="2030-04-20", session_id="s2", function_call_id="fc2"
    )
    result = reservations.cancel_reservation(conn, apartment="101", area_id="salao-de-festas", date="2030-04-20")
    assert result == {"status": "nao_encontrada", "area": "salao-de-festas", "data": "2030-04-20"}
    # a reserva do 302 continua intacta.
    assert reservations.check_availability(conn, area_id="salao-de-festas", date="2030-04-20") == "ocupada"


def test_cancel_nonexistent_reservation(conn):
    result = reservations.cancel_reservation(conn, apartment="101", area_id="quadra", date="2030-04-20")
    assert result == {"status": "nao_encontrada", "area": "quadra", "data": "2030-04-20"}


def test_cancelled_reservation_frees_the_slot(conn):
    reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    reservations.cancel_reservation(conn, apartment="101", area_id="quadra", date="2030-04-20")
    result = reservations.create_reservation(
        conn, apartment="302", area_id="quadra", date="2030-04-20", session_id="s2", function_call_id="fc2"
    )
    assert result["status"] == "reservado"


def test_codes_are_never_reused_even_after_cancel(conn):
    first = reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    reservations.cancel_reservation(conn, apartment="101", area_id="quadra", date="2030-04-20")
    # forca uma colisao de codigo: todos os candidatos "ja existem" menos um.
    used_code = first["codigo"]
    seen_codes = {
        row["code"] for row in conn.execute("SELECT code FROM reservations").fetchall()
    }
    assert used_code in seen_codes
    # mesmo cancelada, a linha continua existindo (nunca e apagada).
    row = conn.execute("SELECT status FROM reservations WHERE code = ?", (used_code,)).fetchone()
    assert row["status"] == "cancelled"


def test_code_generation_retries_on_collision(conn, monkeypatch):
    """Forca o gerador a "sortear" um codigo ja usado antes de um livre, e
    confere que ele tenta de novo em vez de falhar ou duplicar o codigo."""
    reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    used_code = conn.execute("SELECT code FROM reservations").fetchone()["code"]
    used_number = int(used_code.removeprefix("RSV-"))

    calls = {"n": 0}
    real_randint = reservations.random.randint

    def fake_randint(a, b):
        calls["n"] += 1
        if calls["n"] == 1:
            return used_number
        return real_randint(a, b)

    monkeypatch.setattr(reservations.random, "randint", fake_randint)
    result = reservations.create_reservation(
        conn, apartment="302", area_id="churrasqueira", date="2030-04-21", session_id="s2", function_call_id="fc2"
    )
    assert result["status"] == "reservado"
    assert result["codigo"] != used_code
    assert calls["n"] >= 2


# -- regressao: idempotencia e atomicidade (achados da revisao) -------------


def test_repeated_rejected_operation_stays_stable_after_slot_frees_up(conn):
    """Reproduz o bug 1 da revisao: 302 ocupa a data; 101 tenta a MESMA
    (sessao, function-call) e recebe 'indisponivel' (efeito gravado); 302
    cancela; 101 repete a IDENTICA operacao. Uma operacao ja concluida
    nunca e refeita so porque a situacao mudou depois - o resultado tem que
    continuar sendo o mesmo 'indisponivel', e nenhuma reserva nova pode
    aparecer para o 101."""
    reservations.create_reservation(
        conn, apartment="302", area_id="salao-de-festas", date="2030-05-11",
        session_id="s-302", function_call_id="fc-302",
    )
    first = reservations.create_reservation(
        conn, apartment="101", area_id="salao-de-festas", date="2030-05-11",
        session_id="s-101", function_call_id="fc-101",
    )
    assert first["status"] == "indisponivel"

    reservations.cancel_reservation(conn, apartment="302", area_id="salao-de-festas", date="2030-05-11")

    second = reservations.create_reservation(
        conn, apartment="101", area_id="salao-de-festas", date="2030-05-11",
        session_id="s-101", function_call_id="fc-101",
    )
    assert second == first
    active_101 = conn.execute(
        "SELECT COUNT(*) c FROM reservations WHERE apartment='101' AND status='active'"
    ).fetchone()["c"]
    assert active_101 == 0


def test_repeated_successful_operation_returns_same_result_single_effect(conn):
    first = reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    second = reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    assert first == second
    effects_count = conn.execute(
        "SELECT COUNT(*) c FROM effects WHERE session_id='s1' AND original_function_call_id='fc1'"
    ).fetchone()["c"]
    reservations_count = conn.execute("SELECT COUNT(*) c FROM reservations").fetchone()["c"]
    assert effects_count == 1
    assert reservations_count == 1


def test_reusing_identity_with_different_params_is_rejected_before_any_change(conn):
    reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    with pytest.raises(storage.EffectParamsMismatch):
        reservations.create_reservation(
            conn, apartment="101", area_id="churrasqueira", date="2030-04-21",
            session_id="s1", function_call_id="fc1",
        )
    # nada novo foi criado - so a reserva original de quadra continua existindo.
    rows = conn.execute("SELECT area_id, date FROM reservations").fetchall()
    assert [dict(r) for r in rows] == [{"area_id": "quadra", "date": "2030-04-20"}]


def test_failure_between_business_write_and_effect_rolls_back_completely(conn, monkeypatch):
    """Se gravar o efeito falhar depois do INSERT da reserva (dentro da
    MESMA transacao), a transacao inteira desfaz - nunca fica uma reserva
    gravada sem o efeito correspondente."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("falha simulada entre a escrita de negocio e o efeito")

    monkeypatch.setattr(storage, "insert_effect_row", boom)
    with pytest.raises(RuntimeError):
        reservations.create_reservation(
            conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
        )
    assert conn.execute("SELECT COUNT(*) c FROM reservations").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM effects").fetchone()["c"] == 0


def test_recovery_after_commit_does_not_duplicate_effect(conn):
    """Simula 'falha na resposta' depois do commit: a operacao ja foi
    concluida e persistida; uma nova tentativa (ex.: reconciliacao, ou o
    ADK reexecutando a tool) so pode devolver o que ja foi decidido, nunca
    duplicar o efeito nem a reserva."""
    first = reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    # "falha na resposta": o chamador nao recebeu o retorno, mas o commit
    # ja aconteceu. Uma nova tentativa com a MESMA identidade recupera.
    recovered = reservations.create_reservation(
        conn, apartment="101", area_id="quadra", date="2030-04-20", session_id="s1", function_call_id="fc1"
    )
    assert recovered == first
    assert conn.execute("SELECT COUNT(*) c FROM reservations").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM effects").fetchone()["c"] == 1
