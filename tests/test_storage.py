from __future__ import annotations

import sqlite3

import pytest

from aurora_assistant import storage


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    connection = storage.connect(db_path)
    yield connection
    connection.close()


def test_create_and_get_session(conn):
    storage.create_session(conn, "s1", "101")
    row = storage.get_session(conn, "s1")
    assert row["apartment"] == "101"


def test_get_session_missing_returns_none(conn):
    assert storage.get_session(conn, "nao-existe") is None


def test_insert_pending_confirmation_is_idempotent(conn):
    storage.create_session(conn, "s1", "101")
    storage.insert_pending_confirmation(
        conn,
        confirmation_id="c1",
        session_id="s1",
        original_function_call_id="fc1",
        tool_name="reservar_area_prova",
        area="salao-de-festas",
        data="2030-04-20",
    )
    # mesma confirmacao "vista" de novo (ex.: reconsulta de eventos) nao deve
    # recriar nem resetar o status.
    storage.try_resolve(conn, "c1", "s1", approve=True)
    storage.insert_pending_confirmation(
        conn,
        confirmation_id="c1",
        session_id="s1",
        original_function_call_id="fc1",
        tool_name="reservar_area_prova",
        area="salao-de-festas",
        data="2030-04-20",
    )
    row = storage.get_confirmation(conn, "c1")
    assert row["status"] == "approved"


def test_list_pending_confirmations_only_lists_pending(conn):
    storage.create_session(conn, "s1", "101")
    storage.insert_pending_confirmation(
        conn,
        confirmation_id="c1",
        session_id="s1",
        original_function_call_id="fc1",
        tool_name="reservar_area_prova",
        area="salao-de-festas",
        data="2030-04-20",
    )
    storage.insert_pending_confirmation(
        conn,
        confirmation_id="c2",
        session_id="s1",
        original_function_call_id="fc2",
        tool_name="reservar_area_prova",
        area="quadra",
        data="2030-04-21",
    )
    storage.try_resolve(conn, "c1", "s1", approve=True)

    pending = storage.list_pending_confirmations(conn, "s1")
    assert [row["id"] for row in pending] == ["c2"]


class TestTryResolve:
    def test_approve_pending_succeeds_once(self, conn):
        storage.create_session(conn, "s1", "101")
        storage.insert_pending_confirmation(
            conn,
            confirmation_id="c1",
            session_id="s1",
            original_function_call_id="fc1",
            tool_name="reservar_area_prova",
            area="salao-de-festas",
            data="2030-04-20",
        )
        assert storage.try_resolve(conn, "c1", "s1", approve=True) is True
        assert storage.get_confirmation(conn, "c1")["status"] == "approved"

    def test_resolving_twice_fails_second_time(self, conn):
        storage.create_session(conn, "s1", "101")
        storage.insert_pending_confirmation(
            conn,
            confirmation_id="c1",
            session_id="s1",
            original_function_call_id="fc1",
            tool_name="reservar_area_prova",
            area="salao-de-festas",
            data="2030-04-20",
        )
        assert storage.try_resolve(conn, "c1", "s1", approve=True) is True
        assert storage.try_resolve(conn, "c1", "s1", approve=True) is False
        assert storage.try_resolve(conn, "c1", "s1", approve=False) is False

    def test_denying_pending_succeeds(self, conn):
        storage.create_session(conn, "s1", "101")
        storage.insert_pending_confirmation(
            conn,
            confirmation_id="c1",
            session_id="s1",
            original_function_call_id="fc1",
            tool_name="reservar_area_prova",
            area="salao-de-festas",
            data="2030-04-20",
        )
        assert storage.try_resolve(conn, "c1", "s1", approve=False) is True
        assert storage.get_confirmation(conn, "c1")["status"] == "denied"

    def test_wrong_session_fails(self, conn):
        storage.create_session(conn, "s1", "101")
        storage.create_session(conn, "s2", "302")
        storage.insert_pending_confirmation(
            conn,
            confirmation_id="c1",
            session_id="s1",
            original_function_call_id="fc1",
            tool_name="reservar_area_prova",
            area="salao-de-festas",
            data="2030-04-20",
        )
        assert storage.try_resolve(conn, "c1", "s2", approve=True) is False
        assert storage.get_confirmation(conn, "c1")["status"] == "pending"

    def test_unknown_id_fails(self, conn):
        storage.create_session(conn, "s1", "101")
        assert storage.try_resolve(conn, "id-inexistente", "s1", approve=True) is False


PARAMS = {"area": "salao-de-festas", "data": "2030-04-20"}


def test_record_effect_if_absent_persists_once(conn):
    first = storage.record_effect_if_absent(conn, "s1", "fc1", PARAMS, {"status": "reservado", "n": 1})
    second = storage.record_effect_if_absent(conn, "s1", "fc1", PARAMS, {"status": "reservado", "n": 2})
    assert first == {"status": "reservado", "n": 1}
    # a segunda tentativa com os MESMOS parametros nao sobrescreve: devolve o que ja estava persistido.
    assert second == {"status": "reservado", "n": 1}
    assert storage.get_effect(conn, "s1", "fc1") == {"status": "reservado", "n": 1}


def test_get_effect_missing_returns_none(conn):
    assert storage.get_effect(conn, "s1", "nao-existe") is None


def test_two_sessions_with_same_function_call_id_stay_independent(conn):
    """O id da function-call vem do ADK/modelo e nao tem unicidade global
    garantida: duas sessoes podem receber o mesmo id para operacoes
    completamente diferentes. A identidade do efeito inclui a sessao
    exatamente para que isso nao vire um vazamento de resultado."""
    result_s1 = storage.record_effect_if_absent(
        conn, "s1", "fc-compartilhado", {"area": "salao-de-festas", "data": "2030-04-20"}, {"status": "reservado", "dono": "s1"}
    )
    result_s2 = storage.record_effect_if_absent(
        conn, "s2", "fc-compartilhado", {"area": "quadra", "data": "2030-04-21"}, {"status": "reservado", "dono": "s2"}
    )
    assert result_s1 == {"status": "reservado", "dono": "s1"}
    assert result_s2 == {"status": "reservado", "dono": "s2"}
    assert storage.get_effect(conn, "s1", "fc-compartilhado") == {"status": "reservado", "dono": "s1"}
    assert storage.get_effect(conn, "s2", "fc-compartilhado") == {"status": "reservado", "dono": "s2"}


def test_reusing_identity_with_incompatible_params_is_rejected(conn):
    storage.record_effect_if_absent(conn, "s1", "fc1", PARAMS, {"status": "reservado"})
    outros_params = {"area": "quadra", "data": "2030-04-21"}
    with pytest.raises(storage.EffectParamsMismatch):
        storage.record_effect_if_absent(conn, "s1", "fc1", outros_params, {"status": "reservado"})
    # nada foi sobrescrito nem duplicado: o efeito original continua intacto.
    assert storage.get_effect(conn, "s1", "fc1") == {"status": "reservado"}


def test_reusing_identity_with_same_params_is_idempotent(conn):
    first = storage.record_effect_if_absent(conn, "s1", "fc1", PARAMS, {"status": "reservado", "n": 1})
    second = storage.record_effect_if_absent(conn, "s1", "fc1", PARAMS, {"status": "reservado", "n": 2})
    assert first == second == {"status": "reservado", "n": 1}


class TestLegacyEffectsMigration:
    """A tabela `effects` ja existiu num formato antigo (chave so por
    original_function_call_id). A migracao tem que preservar os dados
    antigos (nunca apagar o banco) e so associar uma linha a uma sessao
    quando isso puder ser determinado sem ambiguidade a partir de
    `confirmations`."""

    def _make_legacy_db(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        raw = sqlite3.connect(db_path)
        raw.execute(
            """
            CREATE TABLE effects (
                original_function_call_id TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        raw.execute(
            """
            CREATE TABLE confirmations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                original_function_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                area TEXT,
                data TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )
        raw.commit()
        return db_path, raw

    def test_migrates_row_with_single_matching_session(self, tmp_path):
        db_path, raw = self._make_legacy_db(tmp_path)
        raw.execute(
            "INSERT INTO effects VALUES ('fc-unico', '{\"status\": \"reservado\"}', '2026-01-01T00:00:00+00:00')"
        )
        raw.execute(
            "INSERT INTO confirmations VALUES ('c1', 's1', 'fc-unico', 'reservar_area_prova', 'salao-de-festas', '2030-04-20', 'executed', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        raw.commit()
        raw.close()

        conn = storage.connect(db_path)
        try:
            assert storage.get_effect(conn, "s1", "fc-unico") == {"status": "reservado"}
            assert conn.execute("SELECT COUNT(*) c FROM effects_legacy_unmigrated").fetchone()["c"] == 0
            # a tabela antiga continua no banco, preservada.
            assert conn.execute(
                "SELECT COUNT(*) c FROM effects_legacy_v1_backup"
            ).fetchone()["c"] == 1
        finally:
            conn.close()

    def test_preserves_ambiguous_row_without_guessing(self, tmp_path):
        db_path, raw = self._make_legacy_db(tmp_path)
        raw.execute(
            "INSERT INTO effects VALUES ('fc-ambiguo', '{\"status\": \"reservado\"}', '2026-01-01T00:00:00+00:00')"
        )
        # duas sessoes diferentes referenciam o mesmo original_function_call_id - ambiguo de proposito.
        raw.execute(
            "INSERT INTO confirmations VALUES ('c1', 's1', 'fc-ambiguo', 'reservar_area_prova', 'salao-de-festas', '2030-04-20', 'executed', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        raw.execute(
            "INSERT INTO confirmations VALUES ('c2', 's2', 'fc-ambiguo', 'reservar_area_prova', 'quadra', '2030-04-21', 'executed', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        raw.commit()
        raw.close()

        conn = storage.connect(db_path)
        try:
            assert storage.get_effect(conn, "s1", "fc-ambiguo") is None
            assert storage.get_effect(conn, "s2", "fc-ambiguo") is None
            unmigrated = conn.execute(
                "SELECT original_function_call_id, reason FROM effects_legacy_unmigrated"
            ).fetchall()
            assert len(unmigrated) == 1
            assert unmigrated[0]["original_function_call_id"] == "fc-ambiguo"
            assert "2 sessoes" in unmigrated[0]["reason"]
        finally:
            conn.close()

    def test_preserves_row_with_no_matching_confirmation(self, tmp_path):
        db_path, raw = self._make_legacy_db(tmp_path)
        raw.execute(
            "INSERT INTO effects VALUES ('fc-orfao', '{\"status\": \"reservado\"}', '2026-01-01T00:00:00+00:00')"
        )
        raw.commit()
        raw.close()

        conn = storage.connect(db_path)
        try:
            unmigrated = conn.execute(
                "SELECT original_function_call_id, reason FROM effects_legacy_unmigrated"
            ).fetchall()
            assert len(unmigrated) == 1
            assert unmigrated[0]["original_function_call_id"] == "fc-orfao"
            assert "nenhuma confirmacao" in unmigrated[0]["reason"]
        finally:
            conn.close()

    def test_migration_runs_once(self, tmp_path):
        db_path, raw = self._make_legacy_db(tmp_path)
        raw.execute(
            "INSERT INTO effects VALUES ('fc-unico', '{\"status\": \"reservado\"}', '2026-01-01T00:00:00+00:00')"
        )
        raw.execute(
            "INSERT INTO confirmations VALUES ('c1', 's1', 'fc-unico', 'reservar_area_prova', 'salao-de-festas', '2030-04-20', 'executed', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        raw.commit()
        raw.close()

        conn1 = storage.connect(db_path)
        conn1.close()
        # reabrir o banco ja migrado nao deve reprocessar nem duplicar.
        conn2 = storage.connect(db_path)
        try:
            assert conn2.execute("SELECT COUNT(*) c FROM effects").fetchone()["c"] == 1
        finally:
            conn2.close()
