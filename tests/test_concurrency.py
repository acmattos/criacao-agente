"""Testes de concorrencia real para as garantias de exclusividade e
idempotencia das tools de negocio: threads separadas, cada uma com sua
PROPRIA conexao sqlite para o MESMO arquivo, sincronizadas por
`threading.Barrier` - nao chamadas sequenciais simulando concorrencia (ver
`test_reservations.py::test_two_sequential_operations_for_same_area_date_only_one_succeeds`
para o equivalente sequencial, que so testa a logica, nao a corrida real).

A barreira fica ANTES de qualquer `BEGIN IMMEDIATE` (dentro de
`reservations.create_reservation`), nunca depois: colocar sincronizacao
dentro da regiao que o SQLite ja serializa faria uma thread segurar o lock
de escrita enquanto espera a outra, que por sua vez so progride depois de
conseguir o lock - um deadlock do proprio teste, nao do codigo sob teste.
"""

from __future__ import annotations

import random
import threading

from aurora_assistant import reservations, storage


def _run_in_threads(targets: list, *, timeout: float = 10.0) -> None:
    threads = [threading.Thread(target=t) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)
    for t in threads:
        assert not t.is_alive(), "thread nao terminou a tempo - possivel deadlock"


def test_two_concurrent_operations_for_same_area_date_one_succeeds_one_unavailable(tmp_path):
    """Garantia 5 sob concorrencia real: duas threads, duas conexoes, uma
    barreira sincronizando o inicio. Uma reserva, a outra recebe
    'indisponivel' de forma normal - nenhuma excecao vaza, e nunca existem
    duas reservas ativas para a mesma area/data."""
    db_path = tmp_path / "concurrency.db"
    storage.connect(db_path).close()  # cria o schema antes de abrir conexoes paralelas

    barrier = threading.Barrier(2)
    results: dict[str, dict] = {}
    errors: dict[str, Exception] = {}

    def worker(name: str, apartment: str, session_id: str, function_call_id: str) -> None:
        conn = storage.connect(db_path)
        try:
            barrier.wait(timeout=5)
            results[name] = reservations.create_reservation(
                conn,
                apartment=apartment,
                area_id="salao-de-festas",
                date="2030-05-11",
                session_id=session_id,
                function_call_id=function_call_id,
            )
        except Exception as exc:  # pragma: no cover - diagnostico se o teste falhar
            errors[name] = exc
        finally:
            conn.close()

    _run_in_threads([
        lambda: worker("a", "101", "s-101", "fc-101"),
        lambda: worker("b", "201", "s-201", "fc-201"),
    ])

    assert not errors, errors
    statuses = {results["a"]["status"], results["b"]["status"]}
    assert statuses == {"reservado", "indisponivel"}

    conn = storage.connect(db_path)
    try:
        active = conn.execute(
            "SELECT COUNT(*) c FROM reservations"
            " WHERE area_id='salao-de-festas' AND date='2030-05-11' AND status='active'"
        ).fetchone()["c"]
        assert active == 1
    finally:
        conn.close()


def test_two_concurrent_operations_different_dates_with_forced_code_collision_both_succeed(
    tmp_path,
):
    """Reproduz o bug 2 da revisao: duas operacoes para datas DIFERENTES e
    livres, forcadas a sortear o mesmo codigo antes de qualquer INSERT.
    Nenhuma das duas pode receber 'indisponivel' - a colisao e so de
    codigo, nao de area/data - e as duas tem que terminar com codigos
    distintos."""
    db_path = tmp_path / "concurrency_codes.db"
    storage.connect(db_path).close()

    barrier = threading.Barrier(2)
    results: dict[str, dict] = {}
    errors: dict[str, Exception] = {}
    real_randint = random.randint
    thread_local = threading.local()

    def forced_randint(a: int, b: int) -> int:
        count = getattr(thread_local, "count", 0)
        thread_local.count = count + 1
        if count == 0:
            return 555555  # mesmo "codigo sorteado" na primeira tentativa das duas threads
        return real_randint(a, b)

    def worker(name: str, apartment: str, area_id: str, date: str, session_id: str, fc_id: str) -> None:
        conn = storage.connect(db_path)
        try:
            barrier.wait(timeout=5)
            results[name] = reservations.create_reservation(
                conn,
                apartment=apartment,
                area_id=area_id,
                date=date,
                session_id=session_id,
                function_call_id=fc_id,
            )
        except Exception as exc:  # pragma: no cover
            errors[name] = exc
        finally:
            conn.close()

    original_randint = reservations.random.randint
    reservations.random.randint = forced_randint
    try:
        _run_in_threads([
            lambda: worker("a", "101", "quadra", "2030-06-01", "s1", "fc1"),
            lambda: worker("b", "302", "churrasqueira", "2030-06-02", "s2", "fc2"),
        ])
    finally:
        reservations.random.randint = original_randint

    assert not errors, errors
    assert results["a"]["status"] == "reservado"
    assert results["b"]["status"] == "reservado"
    assert results["a"]["codigo"] != results["b"]["codigo"]
