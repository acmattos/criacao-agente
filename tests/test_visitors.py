from __future__ import annotations

import pytest

from aurora_assistant import storage, visitors


@pytest.fixture
def conn(tmp_path):
    connection = storage.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def test_authorize_visitor_succeeds(conn):
    result = visitors.authorize_visitor(
        conn, apartment="101", name="Joana Ribeiro", date="2030-04-21",
        session_id="s1", function_call_id="fc1",
    )
    assert result == {"status": "autorizado", "nome": "Joana Ribeiro", "data": "2030-04-21"}


def test_authorize_visitor_is_idempotent(conn):
    first = visitors.authorize_visitor(
        conn, apartment="101", name="Joana Ribeiro", date="2030-04-21", session_id="s1", function_call_id="fc1"
    )
    second = visitors.authorize_visitor(
        conn, apartment="101", name="Joana Ribeiro", date="2030-04-21", session_id="s1", function_call_id="fc1"
    )
    assert first == second
    count = conn.execute("SELECT COUNT(*) c FROM visitor_authorizations").fetchone()["c"]
    assert count == 1


def test_list_authorized_visitors_filters_by_apartment(conn):
    visitors.authorize_visitor(
        conn, apartment="101", name="Joana Ribeiro", date="2030-04-21", session_id="s1", function_call_id="fc1"
    )
    visitors.authorize_visitor(
        conn, apartment="302", name="Marina Duarte", date="2030-03-16", session_id="s2", function_call_id="fc2"
    )
    assert visitors.list_authorized_visitors(conn, apartment="101") == [
        {"nome": "Joana Ribeiro", "data": "2030-04-21"}
    ]


# -- regressao: idempotencia e atomicidade (mesmas garantias de reservations) --


def test_reusing_identity_with_different_params_is_rejected_before_any_change(conn):
    visitors.authorize_visitor(
        conn, apartment="101", name="Joana Ribeiro", date="2030-04-21", session_id="s1", function_call_id="fc1"
    )
    with pytest.raises(storage.EffectParamsMismatch):
        visitors.authorize_visitor(
            conn, apartment="101", name="Outra Pessoa", date="2030-04-22",
            session_id="s1", function_call_id="fc1",
        )
    rows = conn.execute("SELECT name, date FROM visitor_authorizations").fetchall()
    assert [dict(r) for r in rows] == [{"name": "Joana Ribeiro", "date": "2030-04-21"}]


def test_failure_between_business_write_and_effect_rolls_back_completely(conn, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("falha simulada entre a escrita de negocio e o efeito")

    monkeypatch.setattr(storage, "insert_effect_row", boom)
    with pytest.raises(RuntimeError):
        visitors.authorize_visitor(
            conn, apartment="101", name="Joana Ribeiro", date="2030-04-21",
            session_id="s1", function_call_id="fc1",
        )
    assert conn.execute("SELECT COUNT(*) c FROM visitor_authorizations").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM effects").fetchone()["c"] == 0


def test_recovery_after_commit_does_not_duplicate_effect(conn):
    first = visitors.authorize_visitor(
        conn, apartment="101", name="Joana Ribeiro", date="2030-04-21", session_id="s1", function_call_id="fc1"
    )
    recovered = visitors.authorize_visitor(
        conn, apartment="101", name="Joana Ribeiro", date="2030-04-21", session_id="s1", function_call_id="fc1"
    )
    assert recovered == first
    assert conn.execute("SELECT COUNT(*) c FROM visitor_authorizations").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM effects").fetchone()["c"] == 1
