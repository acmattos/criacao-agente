# Residencial Aurora — assistente de moradores (Google ADK)

Assistente de chat do aplicativo de moradores do Residencial Aurora: reserva de áreas comuns, cancelamento de reservas, autorização de visitantes e consulta ao regulamento interno. As regras críticas (confirmação de cobrança/acesso, isolamento por apartamento, exclusividade de reserva) estão garantidas em código, nunca dependentes do que o modelo decide — mesmo que o morador escreva "já confirmei" ou diga ser de outro apartamento.

O enunciado original deste desafio está preservado em [`docs/challenge.md`](docs/challenge.md). O histórico de decisões, achados e evidências de cada etapa da implementação está em [`docs/decisoes.md`](docs/decisoes.md) e nos arquivos `docs/etapaN-*.md`.

## Arquitetura

Um agente principal (`main_agent`) distribui a conversa entre três especialistas — todos declarados como `sub_agents` dele, não `AgentTool` (ver nota abaixo). Tudo em [`src/aurora_assistant/agent.py`](src/aurora_assistant/agent.py).

| Agente | Responsabilidade | Como é acionado |
|---|---|---|
| `main_agent` | Agente raiz do `App` (`root_agent=main_agent`). Encaminha o pedido ao especialista adequado; não tem lógica de negócio própria nem o regulamento nas instruções. | Ponto de entrada de uma conversa nova. Uma vez que a conversa transfere para um especialista, o ADK pode mantê-lo como agente ativo nas mensagens seguintes ou numa retomada de confirmação, sem necessariamente voltar a passar pelo `main_agent` a cada chamada (observado em [`docs/etapa1-prova-tecnica.md`](docs/etapa1-prova-tecnica.md) e [`docs/etapa4-regulamento.md`](docs/etapa4-regulamento.md)). |
| `reservation_specialist` | Reservas de áreas comuns: listar, checar disponibilidade, criar (com cobrança condicional) e cancelar. | Transferência do `main_agent` para pedidos sobre salão/churrasqueira/quadra. |
| `visitor_specialist` | Autorização e listagem de visitantes. | Transferência do `main_agent` para pedidos sobre visitantes. |
| `regulation_specialist` | Dúvidas sobre o regulamento interno, por busca seletiva. | Transferência do `main_agent` para dúvidas sobre regras/normas do condomínio. |

**Por que três especialistas, e por que `sub_agents`**: a Etapa 1 (prova técnica, [`docs/etapa1-prova-tecnica.md`](docs/etapa1-prova-tecnica.md)) testou, lendo o código-fonte do ADK 2.10.0 e validando contra o Gemini real, que com a topologia `sub_agents` a retomada de uma confirmação pendente roteia corretamente de volta para o agente dono da tool. Essa topologia foi testada e adotada neste projeto para os dois especialistas com tool de confirmação (`reservation_specialist`, `visitor_specialist`). A alternativa com `AgentTool` **não foi validada aqui** — não há evidência, neste projeto, de que funcione ou de que falhe para esse cenário; por isso não foi adotada para esses dois especialistas. `regulation_specialist` não tem tool de confirmação (não depende dessa topologia), mas fica no mesmo grupo por consistência.

**Tools** (reservas e visitantes nunca são lembrados ou inventados pelo modelo — sempre lidos/gravados por elas):
- `list_my_reservations`, `check_area_availability`, `create_reservation`, `cancel_reservation` (em `agent.py`) delegam para [`src/aurora_assistant/reservations.py`](src/aurora_assistant/reservations.py).
- `list_my_visitors`, `authorize_visitor` (em `agent.py`) delegam para [`src/aurora_assistant/visitors.py`](src/aurora_assistant/visitors.py).
- `consult_regulation` (em `agent.py`) delega para [`src/aurora_assistant/regulamento.py`](src/aurora_assistant/regulamento.py), que busca por palavra-chave em `dados/regulamento.md` (parseado em artigos/capítulos — ver Garantia 4).

**Persistência** (dois bancos SQLite separados, em `.data/`, fora do git):
- [`src/aurora_assistant/storage.py`](src/aurora_assistant/storage.py) — ledger próprio da API: sessões (apartamento), confirmações pendentes/resolvidas e efeitos de negócio.
- `SqliteSessionService` do próprio ADK, montado em `get_runner` ([`runner_service.py`](src/aurora_assistant/runner_service.py)) — eventos e estado da conversa.

**API** ([`src/aurora_assistant/api.py`](src/aurora_assistant/api.py), FastAPI) — nem toda rota aciona o agente:
- `POST /sessoes` (`criar_sessao`): cria a sessão no serviço de sessão do próprio ADK e no ledger da API. Não invoca o agente nem o Gemini.
- `POST /sessoes/{id}/mensagens` (`enviar_mensagem`) e `POST /sessoes/{id}/confirmacoes` (`responder_confirmacao`): processam ou retomam a execução pelo `Runner` do ADK — passam pelo agente e pelo Gemini.
- `GET /sessoes/{id}/eventos` (`listar_eventos`): lê os eventos já persistidos pelo serviço de sessão do ADK. Não invoca o agente.
- `GET /apartamentos/{id}/reservas` e `GET /apartamentos/{id}/visitantes` (`listar_reservas_do_apartamento`, `listar_visitantes_do_apartamento`): leem o SQLite próprio da API direto — nenhuma interação com o ADK.

## Garantias

### 1. Cobrança ou acesso só com confirmação

- `FunctionTool(create_reservation, require_confirmation=_reservation_requires_confirmation)`, em [`agent.py`](src/aurora_assistant/agent.py). `_reservation_requires_confirmation` consulta `areas.requires_confirmation` (em [`areas.py`](src/aurora_assistant/areas.py)), que só olha a taxa cadastrada em `dados/areas.json` — nunca um valor vindo do modelo.
- `FunctionTool(authorize_visitor, require_confirmation=True)`, em `agent.py`: autorizar visitante sempre libera acesso, então a confirmação é incondicional.
- A confirmação nunca vem do texto da conversa: `confirmation_service.handle_confirmation` (em [`confirmation_service.py`](src/aurora_assistant/confirmation_service.py)) só chama o Runner depois que `storage.try_resolve` (em [`storage.py`](src/aurora_assistant/storage.py)) tiver sucesso — um `UPDATE confirmations ... WHERE status='pending'` atômico. Sem afetar nenhuma linha (id inexistente, de outra sessão, ou já respondido), levanta `ConfirmationConflict`, que a rota `responder_confirmacao` (em `api.py`) traduz em `409` **sem nunca chamar o ADK**.

### 2. Cada sessão pertence a um apartamento

- `agent._identity()` (em `agent.py`) é o único jeito de uma tool de negócio obter o apartamento: sempre `tool_context.session.user_id`, nunca um argumento que o modelo escolhe — nenhuma tool em `agent.py` declara um parâmetro de apartamento.
- `reservations.list_active_reservations`/`cancel_reservation` (em [`reservations.py`](src/aurora_assistant/reservations.py)) filtram por esse apartamento; `check_availability` devolve só `"livre"`/`"ocupada"`, nunca de quem é a reserva.
- O apartamento é definido uma única vez em `criar_sessao` (em `api.py`) e gravado em `storage.sessions` — nunca alterado depois.

### 3. Nada se perde no reinício

- `SqliteSessionService(db_path=...)`, montado em `get_runner` (em [`runner_service.py`](src/aurora_assistant/runner_service.py)): eventos e estado do ADK persistidos em arquivo, não em memória do processo.
- O ledger próprio (`aurora_assistant.storage`) usa `sqlite3.connect` sobre um arquivo em `.data/` (`config.DB_PATH`, em [`config.py`](src/aurora_assistant/config.py)) — nunca uma estrutura em memória.
- Validado com reinícios reais de processo (`Stop-Process`/`Start-Process` de verdade, não simulado) nas Etapas 1, 2, 3 e 5 — ver [`docs/etapa1-prova-tecnica.md`](docs/etapa1-prova-tecnica.md) e [`docs/etapa5-validacao-integrada.md`](docs/etapa5-validacao-integrada.md).

### 4. O regulamento é consultado, não carregado

- O `main_agent` orienta a transferência para `regulation_specialist`; o especialista orienta o uso da tool `consult_regulation` (ambas as instruções em `agent.py`). Nenhuma dessas instruções contém o texto do regulamento.
- `consult_regulation` delega para `regulamento.search()` (em [`regulamento.py`](src/aurora_assistant/regulamento.py)): uma busca lexical ponderada (peso IDF; um artigo só concorre se a pergunta bater no título do capítulo ou em pelo menos duas palavras distintas do corpo) que seleciona os artigos mais pontuados e limita o retorno ao **capítulo de maior pontuação** — nunca o documento inteiro, e nunca mais de um capítulo na mesma resposta.
- Essa restrição a um capítulo é uma garantia do mecanismo (a resposta nunca mistura assuntos de capítulos diferentes), **não** uma prova de que o capítulo escolhido é sempre o pertinente para qualquer pergunta possível. Nos testes e nas execuções com Gemini real documentadas em [`docs/etapa4-regulamento.md`](docs/etapa4-regulamento.md), o capítulo escolhido foi o correto para as perguntas testadas (piscina, animais de estimação); o mesmo documento registra uma consulta ambígua que inicialmente escolheu o capítulo errado, corrigida para devolver "não encontrado" nesses casos em vez de arriscar um capítulo incorreto.

### 5. Dois moradores, uma reserva

- Índice único parcial `idx_reservations_active_area_date ON reservations(area_id, date) WHERE status='active'`, no schema de [`storage.py`](src/aurora_assistant/storage.py): o banco rejeita a segunda reserva ativa para a mesma área/data no instante do `INSERT`, nunca numa consulta prévia.
- `reservations.create_reservation` (em [`reservations.py`](src/aurora_assistant/reservations.py)) abre a transação com `BEGIN IMMEDIATE` e distingue o tipo de conflito pela mensagem do SQLite: colisão de código tenta outro código; conflito real de área/data vira resposta de negócio normal (`"indisponivel"`), nunca erro de servidor.
- Validado com concorrência real: threads + conexões SQLite separadas em [`tests/test_concurrency.py`](tests/test_concurrency.py), e duas requisições HTTP disparadas ao mesmo tempo nas Etapas 3 e 5 ([`docs/etapa3-api-completa.md`](docs/etapa3-api-completa.md), [`docs/etapa5-validacao-integrada.md`](docs/etapa5-validacao-integrada.md)).

## Como rodar

### Pré-requisitos

- Python 3.12 ou superior
- [uv](https://docs.astral.sh/uv/)
- Uma chave de API do [Google AI Studio](https://aistudio.google.com/) (Gemini)

### Instalação

```
cp .env.example .env
# edite .env e preencha GOOGLE_API_KEY com sua chave
uv sync --locked
```

`--locked` garante que a instalação usa exatamente o que está em `uv.lock`, sem atualizar silenciosamente nenhuma dependência.

### Variáveis do `.env`

| Variável | Valor |
|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | `FALSE` — usa o Google AI Studio, não o Vertex AI |
| `GOOGLE_API_KEY` | sua chave do Google AI Studio |

### Restaurar os dados iniciais

```
uv run aurora-restore
```

Apaga por completo o estado local — sessões, eventos do ADK, confirmações, reservas e visitantes — e recria reservas/visitantes a partir de `dados/*.json`. Só lê esses arquivos, nunca escreve neles. Rode com a API parada.

### Subir a API

```
uv run uvicorn aurora_assistant.api:app --port 8000
```

Responde em `http://localhost:8000`.

### Rodar os testes determinísticos (sem Gemini, sem subir a API)

```
uv run pytest tests/
```
