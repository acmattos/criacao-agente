# Etapa 1 — Prova técnica de confirmação persistida

Esta etapa tem duas partes com status diferentes:

1. **Histórico** (`scripts/etapa1_proof_legado.py`): o primeiro experimento, que chamava o Runner do ADK direto, sem nenhuma proteção própria. Serviu para descobrir como o mecanismo de confirmação do ADK funciona e revelou um problema real (ver "Achado histórico" abaixo). Fica preservado como registro, mas **não faz parte do fluxo válido da aplicação** — não tem o ledger de confirmações, não dá 409, não garante efeito único.
2. **Implementação corrigida** (`aurora_assistant.api`, uma API FastAPI de verdade): resolve o problema encontrado no histórico, com um ledger de confirmações próprio e persistido, identidade de efeito por sessão, testes determinísticos das proteções, e execuções HTTP completas com Gemini real atravessando reinícios de processo de verdade. Esta é a entrega da Etapa 1.

Este documento já passou por uma revisão anterior; esta versão incorpora as correções pedidas nela (ingestão de confirmações durante a retomada, identidade de efeito por sessão, nomenclatura e as imprecisões apontadas abaixo).

## Topologia validada

Especialista com tool de confirmação precisa estar em `sub_agents`, não em `AgentTool` — isso foi o que validamos e o que o projeto usa (`reservation_specialist` como `sub_agents` de `main_agent`, em `src/aurora_assistant/agent.py`). A prova não testou a topologia `AgentTool` para esse caso, então não há evidência de que ela funcione ou não. O que sustenta a escolha por `sub_agents` é a leitura do código do ADK 2.10.0 (`agents/_agent_router.py`: `find_agent_to_run` só localiza um agente através de `root_agent.find_agent`, que só percorre `sub_agents`) combinada com a validação empírica de que, com essa topologia, a retomada roteou corretamente em múltiplas execuções reais.

## Achado histórico (script legado, fora do fluxo válido)

Rodando `scripts/etapa1_proof_legado.py` — que chama `runner.run_async` direto, sem ledger — reenviar a mesma resposta de confirmação já respondida fez o ADK **executar a tool de novo** (log de execuções foi de 1 para 2 linhas). Essa reexecução é do ADK quando usado sem proteção alguma; não é o comportamento da API. A implementação corrigida nunca deixa esse reenvio chegar ao ADK (ver abaixo).

## Implementação corrigida: arquitetura

- `aurora_assistant.storage`: sessões (vínculo sessão→apartamento), ledger de confirmações (`pending` → `approved`/`denied` → `executing` → `executed`/`failed`) e tabela de efeitos (`effects`), chave composta `(session_id, original_function_call_id)`.
- `aurora_assistant.agent`: agentes ADK (`main_agent` + `reservation_specialist`) e a tool `reserve_area_proof`. A tool obtém a sessão do contexto confiável do ADK (`tool_context.session.id`) e o id da function-call original (`tool_context.function_call_id`), e grava o efeito de forma atômica usando essa dupla como chave (`storage.record_effect_if_absent`).
- `aurora_assistant.confirmation_service`: orquestra mensagens e confirmações. Nunca chama o executor (ADK) para um id que não esteja `pending` no ledger — a transição `pending → approved/denied` é atômica (`storage.try_resolve`) e acontece **antes** de qualquer chamada ao Runner. Também é responsável por registrar qualquer confirmação nova que apareça nos eventos devolvidos pelo executor, em **qualquer** caminho (mensagem nova, aprovação, negativa ou reconciliação) — ver "Correção: confirmações geradas durante a retomada".
- `aurora_assistant.runner_service`: única camada que fala com o ADK/Gemini de verdade; converte eventos do ADK em `NormalizedEvent` (`aurora_assistant.events`), para que `confirmation_service` seja testável sem Gemini.
- `aurora_assistant.api`: FastAPI com as 4 rotas da prova.

### Por que o reenvio dá 409 de verdade

`POST /sessoes/{id}/confirmacoes` primeiro tenta `storage.try_resolve(id, session_id, approve=...)`, um único `UPDATE` SQL guardado por `WHERE id=? AND session_id=? AND status='pending'`. Isso cobre de uma vez os três motivos de 409: id inexistente, id de outra sessão, ou id já respondido — e só chama o ADK se essa transição teve sucesso. O ADK nunca vê um reenvio.

### Efeito único e os três estados (decisão / execução / resultado)

O ledger distingue:
- **decisão recebida**: `pending → approved`/`denied` (antes de tocar no ADK);
- **execução em andamento**: `approved → executing` (logo antes de chamar `runner.run_async`);
- **resultado persistido**: `executing → executed`, só depois de confirmar que a tabela `effects` tem uma linha para `(session_id, original_function_call_id)` daquela confirmação.

Duas janelas de falha têm teste determinístico dedicado (`tests/test_confirmation_service.py`):

1. **Falha depois de persistir a decisão, antes de executar a tool** (`test_failure_after_decision_before_execution_recovers_via_reconcile`): confirmação fica `failed`, nenhum efeito é criado, reenvio público continua `409`, e `confirmation_service.reconcile_stuck` completa a operação numa tentativa seguinte, reaproveitando a mesma confirmação.
2. **Falha depois de persistir o efeito, antes de terminar a resposta** (`test_failure_after_effect_before_response_recovers_without_calling_executor_again`): `reconcile_stuck` detecta que o efeito já existe e promove o status para `executed` **sem chamar o executor de novo**.

### Quando a reconciliação (`reconcile_stuck`) de fato roda — correção de imprecisão

A versão anterior deste documento afirmava que `reconcile_stuck` era chamado no início das rotas `mensagens`, `confirmacoes` e `eventos`. **Isso nunca foi verdade no código**: `src/aurora_assistant/api.py` só chama `confirmation_service.reconcile_stuck` dentro de `enviar_mensagem` (rota `POST /mensagens`). As rotas `POST /confirmacoes` e `GET /eventos` não acionam reconciliação.

Implicação prática: se uma confirmação travar em `executing`/`failed` (as duas janelas de falha acima), ela só se autorrepara na **próxima mensagem de texto** enviada àquela sessão — não ao consultar eventos, nem ao tentar responder outra confirmação. Deliberadamente não adicionamos reconciliação a `GET /eventos` só para a rotina bater com o texto: é uma rota de leitura, e adicionar um efeito colateral nela para "corrigir a documentação" seria o inverso do que faz sentido. A correção certa foi documentar o comportamento real, que é este.

## Correção: identidade do efeito por sessão

**Problema encontrado na revisão**: a tabela `effects` usava só `original_function_call_id` como chave primária. Esse id vem do ADK/modelo (ex.: `call_549289`) e não tem unicidade global garantida — duas sessões diferentes podem, em princípio, receber o mesmo id para operações completamente diferentes, o que permitiria uma sessão "herdar" o efeito de outra.

**Correção**: a chave agora é composta, `(session_id, original_function_call_id)`. O `session_id` usado pela tool vem de `tool_context.session.id` — o contexto confiável do ADK, nunca de um argumento que o modelo escolhe (checado na fonte instalada: `google/adk/agents/context.py`, propriedade `session`). Isso está aplicado de ponta a ponta:
- **execução da tool** (`agent.reserve_area_proof`): grava com `(tool_context.session.id, tool_context.function_call_id)`.
- **registro/consulta da confirmação**: `confirmations.session_id` + `confirmations.original_function_call_id` já eram gravados assim; `confirmation_service` agora sempre passa `session_id` para `storage.get_effect`.
- **verificação de conclusão** (`_execute_approved`): `storage.get_effect(conn, session_id, confirmation["original_function_call_id"])`.
- **recuperação após falhas** (`reconcile_stuck`): mesma chamada, usando o `session_id` da sessão sendo reconciliada.

**Vínculo aos parâmetros aprovados**: `record_effect_if_absent` agora recebe também `params` (área/data) e grava `params_json`. Se a mesma identidade `(session_id, original_function_call_id)` for reaproveitada com parâmetros **diferentes**, a função levanta `EffectParamsMismatch` em vez de executar de novo ou devolver silenciosamente o resultado persistido — ver `tests/test_storage.py::test_reusing_identity_with_incompatible_params_is_rejected`. Com os mesmos parâmetros, continua idempotente (`test_reusing_identity_with_same_params_is_idempotent`).

**Distinguir invocações sem perder identidade na retomada**: o id de function-call (`tool_context.function_call_id`) é o mesmo na tentativa inicial (que retorna "requer confirmação") e na execução após aprovação — confirmado em execução real (mesmo `original_function_call_id` nos dois eventos). Cada nova operação que o modelo decide chamar recebe um id novo do ADK, então duas operações distintas na mesma sessão não colidem.

### Migração do esquema antigo — sem apagar dados

O banco de ledger já existente (`'.data/aurora_etapa1.db` desta máquina) tinha `effects` no formato antigo. `storage._migrate_legacy_effects_if_needed` roda automaticamente em `storage.connect`:
- renomeia a tabela antiga para `effects_legacy_v1_backup` (nunca é apagada);
- para cada linha antiga, procura em `confirmations` quantas sessões distintas referenciam aquele `original_function_call_id`;
- se exatamente **uma**, migra a linha para o novo formato, reconstruindo `params` a partir de `confirmations.area`/`confirmations.data` daquela sessão;
- se **nenhuma** ou **mais de uma**, a linha vai para `effects_legacy_unmigrated` com o motivo registrado — nunca inventamos a associação.

Testado tanto com bancos sintéticos isolados (`tests/test_storage.py::TestLegacyEffectsMigration`, 4 casos: migração simples, ambíguo, órfão, e não-reprocessamento) quanto **no banco real** desta máquina: a única linha existente (`call_549289`, churrasqueira) foi migrada corretamente para a sessão `23988b25-...`, com os parâmetros reconstruídos a partir da confirmação correspondente — conferido depois de rodar a API corrigida contra o banco de verdade, sem apagar nada antes.

## Correção: confirmações geradas durante a retomada

**Problema encontrado na revisão**: `_ingest_new_confirmations` só era chamado por `handle_message`. Os eventos devolvidos por `executor.resume` (nos caminhos de aprovação, negativa e reconciliação) podiam conter um novo pedido de confirmação — por exemplo, o especialista pedir confirmação de outra reserva na mesma resposta — e essa pendência nova nunca era registrada.

**Correção**: `_ingest_new_confirmations` agora também é chamado:
- no caminho de **negativa**, logo após `executor.resume(..., confirmado=False)`;
- em `_execute_approved` (compartilhado pelo caminho de **aprovação** e pela **reconciliação**), logo após `executor.resume(..., confirmado=True)` suceder.

**Regressão demonstrada antes/depois**: `tests/test_confirmation_service.py::test_deny_can_surface_a_new_pending_confirmation`, `test_approve_can_surface_a_new_pending_confirmation` e `test_reconcile_can_surface_a_new_pending_confirmation` falhavam antes da correção (o `assert` em `confirmacoes_pendentes` batia contra lista vazia) e passam depois — rodado explicitamente: 3 falhas antes (RED), 0 falhas depois (GREEN), sem alterar o restante da suíte.

**Idempotência preservada**: `storage.insert_pending_confirmation` continua usando `INSERT OR IGNORE` por `id` — reprocessar o mesmo evento de confirmação (ex.: o mesmo evento aparecendo de novo numa consulta) nunca reabre uma confirmação já respondida. Coberto por `test_reingesting_events_does_not_reopen_a_resolved_confirmation`.

## `find_pending_confirmations` (achado de uma revisão anterior) — corrigido

O helper do script legado dizia considerar só respostas do autor `user`, mas removia a pendência sem checar isso. Na implementação corrigida esse helper não existe mais como fonte de verdade: o ledger (`storage.list_pending_confirmations`, filtrando por `status='pending'`) é a única fonte de pendências para a API. `confirmation_service._ingest_new_confirmations` só **cria** pendências novas a partir dos eventos — nunca decide se uma pendência deve ser removida; quem remove é exclusivamente `try_resolve`, pela rota de confirmações.

## `detalhes` e taxa

`detalhes.area`/`detalhes.data` vêm dos argumentos reais da tool (`originalFunctionCall.args`), nunca inventados. `detalhes.taxa` nunca é extraído de um argumento do modelo — é sempre consultado em `dados/areas.json` (`aurora_assistant.areas.lookup_taxa`) pelo id da área; se a área não bate com nenhum id cadastrado, `taxa` fica de fora da resposta. O modelo às vezes devolve a área como texto livre ("salao de festas") em vez do id canônico (`salao-de-festas`) — nesse caso a taxa não aparece; fica para a Etapa 2 normalizar/validar a área antes de chamar a tool.

## Nomenclatura: identificadores internos em inglês

**Pedido da revisão**: ajustar identificadores internos para inglês, mantendo os campos públicos do contrato HTTP em português, com cuidado para não quebrar a retomada de confirmações pendentes persistidas sob os nomes antigos.

Renomeado: agente `principal` → `main_agent`; agente `especialista_reservas` → `reservation_specialist`; tool/função `reservar_area_prova` → `reserve_area_proof`. O campo público `acao` em `confirmacoes_pendentes` reflete esse nome (ex.: `"acao": "reserve_area_proof"`) — o enunciado deixa esse valor livre ("o conteúdo de `acao`... é livre"), então isso não quebra contrato.

**Estratégia adotada para não quebrar retomada silenciosamente**: antes de renomear, resolvemos pela própria API (negamos) a única confirmação que ainda estava `pending` nos bancos locais existentes (sessão do apartamento 302, criada numa validação anterior). Com isso, nenhuma sessão ficou com uma confirmação pendente atrelada aos nomes antigos. Não criamos um banco de sessões ADK separado: o roteamento de retomada do ADK só depende do nome do agente quando há uma `FunctionCall` pendente esperando resposta (ver `_agent_router.find_agent_to_run`); uma sessão sem pendência aceita mensagens novas normalmente, porque elas sempre partem do `root_agent` atual, qualquer que seja seu nome. **Validado na prática**: depois da renomeação, uma sessão criada antes dela (`23988b25-...`, com eventos autorados por `principal`/`especialista_reservas` no histórico) recebeu uma mensagem nova e respondeu normalmente, persistindo 3 eventos novos (25→28) — ver "Execução real" abaixo, item 8.

## Execução real (Gemini de verdade, não mock)

Duas rodadas de validação real, ambas contra o servidor HTTP de verdade (`uv run uvicorn aurora_assistant.api:app --port 8000`, processo Windows via `Start-Process`, não um job de shell), usando `gemini-flash-lite-latest` (`gemini-2.5-flash`, escolha original, esgotou a cota gratuita diária de 20 req/dia durante a primeira rodada).

### Rodada 1 (antes desta correção) — 14 cenários, incluindo o primeiro reinício real

Criar sessão (`201`) → pedir reserva paga (pendência com `detalhes`, sem efeito) → negar (sem efeito) → aprovar id negado (`409`) → nova pendência → **reinício real do processo** → eventos e pendência preservados → aprovar pós-reinício (exatamente 1 efeito) → reenvio (`409`, sem novo efeito) → id inexistente (`409`) → id de outra sessão (`409`, pendência preservada) → sessão inexistente nas 3 rotas (`404`) → nova mensagem na sessão retomada (`200`, eventos cresceram). Essas evidências continuam válidas para o que testavam (o mecanismo de confirmação+retomada básico), mas **não foram reaproveitadas como prova da identidade de efeito por sessão nem da ingestão durante retomada**, que só existem nesta correção — por isso a Rodada 2 abaixo repete o roteiro central do zero, contra o código corrigido.

### Rodada 2 (esta correção) — roteiro dos 8 passos pedidos na revisão

| # | Cenário | Resultado |
|---|---|---|
| 1 | Criar sessão + pedir reserva com confirmação | `201`; pendência com `detalhes` (`acao: "reserve_area_proof"`) |
| 2 | Pendência e ausência de efeito | confirmado (`SELECT COUNT(*) FROM effects WHERE session_id=...` → 0) |
| 3 | Encerrar processo (`Stop-Process`) e iniciar outro (`Start-Process`) | PID mudou (113700 → 127280), dados não restaurados |
| 4 | Sessão, eventos e pendência preservados | `GET /eventos` → 7 eventos; confirmação ainda `status='pending'` com `tool_name='reserve_area_proof'` |
| 5 | Aprovar → exatamente um efeito | `200`, texto de sucesso; `effects` com 1 linha, `params_json` gravado |
| 6 | Reenviar a aprovação | `409`; `effects` continua com 1 linha |
| 7a | Negar (nova pendência) | `confirmacoes_pendentes: []`, sem efeito, `status='denied'` |
| 7b | Id inexistente | `409` |
| 7c | Id de outra sessão | `409`; pendência da sessão correta continua `pending` |
| 8 | Sessão continua aceitando mensagens | testado em sessão nova **e** numa sessão criada antes da renomeação (25→28 eventos) |

Todos os 8 itens rodaram contra o servidor HTTP real, com Gemini real. Os cenários de colisão entre sessões e de pendência surgida durante retomada (itens 1 e 2 desta revisão) são determinísticos por natureza — comprovados por teste controlado (`tests/test_storage.py`, `tests/test_confirmation_service.py`), não por esperar que o Gemini produza esses casos espontaneamente, exatamente como a revisão pediu.

## Limitação real observada (achado lateral, não um item de validação)

Mandar uma mensagem **não relacionada** enquanto uma confirmação está pendente pode causar transferências extras entre agentes e confundir o roteamento de retomada do ADK — observado numa rodada de validação anterior (não reproduzido nesta correção). Nada executou sem confirmação nesse caso (a garantia central se manteve), mas a operação aprovada não se completou. O enunciado trata isso como comportamento livre. Direção recomendada para a Etapa 2/3: o agente principal evitar iniciar novos tópicos enquanto há confirmação pendente da sessão. Não implementado nesta etapa.

## Testes determinísticos (sem Gemini)

`uv run pytest tests/` — 41 testes, todos passando:
- `tests/test_storage.py` (26): ledger, efeitos com identidade composta, `EffectParamsMismatch`, migração do esquema antigo (4 cenários, incluindo ambíguo e órfão).
- `tests/test_areas.py` (2): consulta de taxa.
- `tests/test_confirmation_service.py` (13): as proteções de 409, efeito único, as duas janelas de falha, e as 3 regressões de ingestão durante retomada + idempotência de reingestão.
- `tests/test_api.py` (8): contrato HTTP com um executor fake — sem Gemini.

## Como reproduzir

Testes determinísticos:
```
uv run pytest tests/ -q
```

Execução real (precisa de `GOOGLE_API_KEY` válida no `.env`):
```
uv run uvicorn aurora_assistant.api:app --port 8000
# noutro terminal:
curl -X POST http://localhost:8000/sessoes -H "Content-Type: application/json" -d '{"apartamento":"101"}'
curl -X POST http://localhost:8000/sessoes/<session_id>/mensagens -H "Content-Type: application/json" -d '{"texto":"Reserve a quadra para 2030-07-01"}'
curl -X POST http://localhost:8000/sessoes/<session_id>/confirmacoes -H "Content-Type: application/json" -d '{"id":"<confirmation_id>","confirmado":true}'
curl http://localhost:8000/sessoes/<session_id>/eventos
```

Experimento histórico (fora do fluxo válido, só para reproduzir o achado do ADK reexecutando a tool):
```
uv run python scripts/etapa1_proof_legado.py create-session
uv run python scripts/etapa1_proof_legado.py send <session_id> "Reserve o salao de festas para 2030-04-20"
uv run python scripts/etapa1_proof_legado.py confirm <session_id> <confirmation_id> true
uv run python scripts/etapa1_proof_legado.py confirm <session_id> <confirmation_id> true   # reenvio: reproduz o achado
```

`.data/` é local e ignorado pelo git; os arquivos do experimento histórico (`etapa1_proof.db`, `etapa1_proof_execucoes.log`) e o banco de ledger real (`aurora_etapa1.db`, agora incluindo `effects_legacy_v1_backup`) foram preservados como estão.
