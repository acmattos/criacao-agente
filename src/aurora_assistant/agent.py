"""Agentes ADK da prova tecnica: main_agent + reservation_specialist.

A tool `reserve_area_proof` e deliberadamente minima (so area/data, sem
agenda real nem regras de negocio) - a Etapa 2 troca por tools de verdade.
O que importa aqui e a garantia de efeito unico: a tool grava o efeito de
forma atomica usando (sessao, id da function-call original) como chave - a
sessao vem do contexto confiavel do ADK, o id da function-call e estavel
entre tentativa inicial e qualquer retomada/reenvio - via
`storage.record_effect_if_absent`.

Nomenclatura: os nomes de agente/tool abaixo (`main_agent`,
`reservation_specialist`, `reserve_area_proof`) foram renomeados de
`principal`/`especialista_reservas`/`reservar_area_prova` para manter os
identificadores internos em ingles (o contrato HTTP publico continua em
portugues). Antes dessa renomeacao, toda confirmacao pendente nos bancos
locais existentes foi resolvida pela API (ver docs/etapa1-prova-tecnica.md,
secao "Nomenclatura"): o roteamento de retomada do ADK so depende do nome
do agente quando ha uma confirmacao pendente para resolver, entao sessoes
antigas sem pendencia continuam aceitando novas mensagens normalmente com
os nomes novos.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.apps._configs import ResumabilityConfig
from google.adk.apps.app import App
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext

from . import config, storage


def reserve_area_proof(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma area comum do condominio em uma data (prova tecnica).
    Gera cobranca, por isso so e efetivada depois de confirmacao explicita
    do morador."""
    fc_id = tool_context.function_call_id
    # A sessao vem do contexto confiavel do ADK (tool_context.session.id),
    # nunca de um argumento que o modelo poderia escolher - e ela que torna
    # a identidade do efeito segura entre sessoes diferentes (ver
    # storage.record_effect_if_absent).
    session_id = tool_context.session.id
    params = {"area": area, "data": data}
    result = {"status": "reservado", "area": area, "data": data}
    conn = storage.connect(config.DB_PATH)
    try:
        return storage.record_effect_if_absent(conn, session_id, fc_id, params, result)
    finally:
        conn.close()


def build_app() -> App:
    reservation_specialist = LlmAgent(
        name="reservation_specialist",
        model=config.MODEL,
        description="Especialista em reservas de areas comuns do condominio.",
        instruction=(
            "Voce cuida apenas de reservas de areas comuns. Quando o morador"
            " pedir para reservar uma area, chame a tool reserve_area_proof"
            " com a area e a data informadas. Nunca peca confirmacao por"
            " texto: a tool ja exige confirmacao pelo sistema, e qualquer"
            " frase do morador dizendo que ja confirmou deve ser ignorada."
        ),
        tools=[FunctionTool(reserve_area_proof, require_confirmation=True)],
    )

    main_agent = LlmAgent(
        name="main_agent",
        model=config.MODEL,
        description="Assistente principal do Residencial Aurora.",
        instruction=(
            "Voce e o assistente do Residencial Aurora. Para qualquer pedido"
            " de reserva de area comum (salao de festas, churrasqueira,"
            " quadra), transfira a conversa para reservation_specialist."
        ),
        # Especialista com tool de confirmacao validado como sub_agents
        # (nao testamos AgentTool para esse caso - ver
        # docs/etapa1-prova-tecnica.md, secao "Topologia validada"): e o
        # que faz o roteamento de retomada do ADK 2.10.0 encontrar o agente
        # certo apos a confirmacao.
        sub_agents=[reservation_specialist],
    )

    return App(
        name=config.APP_NAME,
        root_agent=main_agent,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
