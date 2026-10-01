"""Testes HTTP deterministicos (sem Gemini): contrato da API, 404/409 e o
formato da resposta. A integracao real com o ADK/Gemini e validada
separadamente (ver docs/etapa1-prova-tecnica.md, secao "Execucao real").
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aurora_assistant import api, config
from aurora_assistant.events import NormalizedEvent


class FakeExecutor:
    def __init__(self):
        self.created_sessions = []
        self.send_message_events = []
        self.resume_behavior = lambda confirmation_id, confirmado: [
            NormalizedEvent(author="x", text="ok")
        ]
        self.raw_events = []

    async def create_session(self, *, apartment, session_id):
        self.created_sessions.append((apartment, session_id))

    async def send_message(self, *, apartment, session_id, texto):
        return self.send_message_events

    async def resume(self, *, apartment, session_id, confirmation_id, confirmado):
        return self.resume_behavior(confirmation_id, confirmado)

    async def get_raw_events(self, *, apartment, session_id):
        return self.raw_events


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


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "api_test.db")
    fake = FakeExecutor()
    monkeypatch.setattr(api, "EXECUTOR", fake)
    with TestClient(api.app) as test_client:
        test_client.fake_executor = fake
        yield test_client


def test_criar_sessao_retorna_201_e_session_id(client):
    resp = client.post("/sessoes", json={"apartamento": "101"})
    assert resp.status_code == 201
    assert "session_id" in resp.json()
    assert client.fake_executor.created_sessions == [("101", resp.json()["session_id"])]


def test_rotas_com_sessao_inexistente_retornam_404(client):
    assert client.post("/sessoes/nao-existe/mensagens", json={"texto": "oi"}).status_code == 404
    assert (
        client.post("/sessoes/nao-existe/confirmacoes", json={"id": "x", "confirmado": True}).status_code
        == 404
    )
    assert client.get("/sessoes/nao-existe/eventos").status_code == 404


def test_mensagem_sem_pendencia(client):
    session_id = client.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    client.fake_executor.send_message_events = [NormalizedEvent(author="main_agent", text="oi!")]
    resp = client.post(f"/sessoes/{session_id}/mensagens", json={"texto": "oi"})
    assert resp.status_code == 200
    assert resp.json() == {"resposta": "oi!", "confirmacoes_pendentes": []}


def test_mensagem_gera_pendencia_com_detalhes(client):
    session_id = client.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    client.fake_executor.send_message_events = [CONFIRMATION_EVENT]
    resp = client.post(f"/sessoes/{session_id}/mensagens", json={"texto": "reserve o salao"})
    body = resp.json()
    assert resp.status_code == 200
    assert len(body["confirmacoes_pendentes"]) == 1
    pendencia = body["confirmacoes_pendentes"][0]
    assert pendencia["id"] == "adk-confirm-1"
    assert pendencia["detalhes"]["area"] == "salao-de-festas"
    assert pendencia["detalhes"]["data"] == "2030-04-20"
    assert pendencia["detalhes"]["taxa"] == 150.0


def test_confirmacao_com_id_invalido_retorna_409(client):
    session_id = client.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    resp = client.post(
        f"/sessoes/{session_id}/confirmacoes", json={"id": "id-inexistente", "confirmado": True}
    )
    assert resp.status_code == 409


def test_confirmacao_de_outra_sessao_retorna_409(client):
    s1 = client.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    s2 = client.post("/sessoes", json={"apartamento": "302"}).json()["session_id"]
    client.fake_executor.send_message_events = [CONFIRMATION_EVENT]
    client.post(f"/sessoes/{s1}/mensagens", json={"texto": "reserve o salao"})

    resp = client.post(f"/sessoes/{s2}/confirmacoes", json={"id": "adk-confirm-1", "confirmado": True})
    assert resp.status_code == 409


def test_aprovar_confirmacao_executa_e_reenvio_vira_409(client):
    session_id = client.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    client.fake_executor.send_message_events = [CONFIRMATION_EVENT]
    client.post(f"/sessoes/{session_id}/mensagens", json={"texto": "reserve o salao"})

    def behavior(confirmation_id, confirmado):
        from aurora_assistant import storage

        conn = storage.connect(config.DB_PATH)
        storage.record_effect_if_absent(
            conn, session_id, "adk-fc-1", {"area": "salao-de-festas", "data": "2030-04-20"}, {"status": "reservado"}
        )
        conn.close()
        return [NormalizedEvent(author="reservation_specialist", text="reservado!")]

    client.fake_executor.resume_behavior = behavior

    resp = client.post(
        f"/sessoes/{session_id}/confirmacoes", json={"id": "adk-confirm-1", "confirmado": True}
    )
    assert resp.status_code == 200
    assert resp.json()["confirmacoes_pendentes"] == []

    resp2 = client.post(
        f"/sessoes/{session_id}/confirmacoes", json={"id": "adk-confirm-1", "confirmado": True}
    )
    assert resp2.status_code == 409


def test_eventos_lista_o_que_o_executor_devolve(client):
    session_id = client.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    client.fake_executor.raw_events = [{"author": "user", "content": {"parts": []}}]
    resp = client.get(f"/sessoes/{session_id}/eventos")
    assert resp.status_code == 200
    assert resp.json() == [{"author": "user", "content": {"parts": []}}]
