from __future__ import annotations

import json

import pytest

from aurora_assistant import config, restore, storage


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Isola completamente o teste: dados/ fake e bancos num diretorio
    temporario, nunca tocando nos arquivos reais do projeto."""
    dados_dir = tmp_path / "dados"
    dados_dir.mkdir()
    (dados_dir / "reservas.json").write_text(
        json.dumps(
            [{"codigo": "RSV-1377", "apartamento": "101", "area": "quadra", "data": "2030-03-09"}]
        ),
        encoding="utf-8",
    )
    (dados_dir / "visitantes.json").write_text(
        json.dumps([{"apartamento": "302", "nome": "Marina Duarte", "data": "2030-03-16"}]),
        encoding="utf-8",
    )

    db_path = tmp_path / "aurora.db"
    adk_db_path = tmp_path / "aurora_adk.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "ADK_SESSIONS_DB_PATH", adk_db_path)
    monkeypatch.setattr(restore, "DADOS_DIR", dados_dir)
    return db_path, adk_db_path, dados_dir


def test_restore_loads_initial_reservations_and_visitors(isolated_env):
    db_path, _, _ = isolated_env
    restore.restore()

    conn = storage.connect(db_path)
    try:
        reservation = conn.execute("SELECT * FROM reservations").fetchone()
        assert reservation["code"] == "RSV-1377"
        assert reservation["apartment"] == "101"
        assert reservation["area_id"] == "quadra"
        assert reservation["status"] == "active"

        visitor = conn.execute("SELECT * FROM visitor_authorizations").fetchone()
        assert visitor["apartment"] == "302"
        assert visitor["name"] == "Marina Duarte"
    finally:
        conn.close()


def test_restore_wipes_previous_state(isolated_env):
    db_path, adk_db_path, _ = isolated_env
    restore.restore()

    conn = storage.connect(db_path)
    try:
        # simula uso: cria uma reserva extra e uma sessao.
        conn.execute(
            """
            INSERT INTO reservations
                (code, apartment, area_id, date, status, session_id, function_call_id, created_at)
            VALUES ('RSV-9999', '999', 'churrasqueira', '2030-12-25', 'active', 's', 'fc', ?)
            """,
            (storage.now_iso(),),
        )
        storage.create_session(conn, "sessao-de-teste", "999")
        conn.commit()
    finally:
        conn.close()

    restore.restore()

    conn = storage.connect(db_path)
    try:
        codes = {row["code"] for row in conn.execute("SELECT code FROM reservations").fetchall()}
        assert codes == {"RSV-1377"}
        assert storage.get_session(conn, "sessao-de-teste") is None
    finally:
        conn.close()


def test_restore_does_not_modify_dados_files(isolated_env):
    _, _, dados_dir = isolated_env
    before = (dados_dir / "reservas.json").read_bytes()
    restore.restore()
    after = (dados_dir / "reservas.json").read_bytes()
    assert before == after
