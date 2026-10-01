# Etapa 2 — Persistência e regras de negócio

Substitui a tool de prova da Etapa 1 (`reserve_area_proof`, dados fictícios, sem regras) pelas regras de negócio reais do Residencial Aurora, mantendo intactas as garantias de confirmação persistida, efeito único e isolamento por sessão já validadas. Os testes determinísticos da Etapa 1 (`test_storage.py`, `test_confirmation_service.py`, `test_api.py`) continuam passando sem alteração de comportamento — só o schema de `confirmations` mudou (ver "Migração" abaixo), com migração automática e testada.

Este documento já passou por uma revisão, que encontrou duas falhas reais de atomicidade/idempotência na implementação inicial. Esta versão incorpora as correções (seção "Correção: atomicidade e conflitos de integridade" abaixo) — a seção "Identidade das tabelas de negócio" já descreve o desenho **corrigido**, não o original.

## O que foi implementado

- `aurora_assistant.reservations`: regras de negócio 1 (uma reserva ativa por área/data), 5 (código nunca reutilizado, mesmo cancelada) e a exclusividade da Garantia 5 (índice único parcial, vale na gravação).
- `aurora_assistant.visitors`: regra de negócio 3 (autorização de visitante).
- `aurora_assistant.areas`: `exists`/`requires_confirmation` — regra de negócio 2 (taxa > 0 gera cobrança) ligada à Garantia 1 (cobrança exige confirmação).
- `aurora_assistant.restore`: carga inicial e comando de restauração (`uv run aurora-restore`), lendo só `dados/*.json`, nunca escrevendo neles.
- `aurora_assistant.agent`: tools reais (`list_my_reservations`, `check_area_availability`, `create_reservation`, `cancel_reservation`, `list_my_visitors`, `authorize_visitor`), substituindo a tool de prova.

## Identidade das tabelas de negócio

`reservations` e `visitor_authorizations` carregam `UNIQUE(session_id, function_call_id)` como identidade da operação, mas quem decide se uma operação já foi concluída é sempre a tabela genérica `effects` da Etapa 1 (`storage.get_effect_record`) — **consultada antes de qualquer escrita de negócio**, nunca depois. A escrita de negócio (`INSERT` em `reservations`/`visitor_authorizations`) e o registro do efeito acontecem na mesma transação (`BEGIN IMMEDIATE` ... `COMMIT`), orquestrada inteiramente dentro de `reservations.create_reservation`/`visitors.authorize_visitor` — a tool em `agent.py` só repassa o resultado, nunca grava o efeito por conta própria. Essa combinação resolve três coisas:

1. **Uma operação concluída nunca é refeita**: o primeiro gesto de `create_reservation`/`authorize_visitor` é checar `effects` por `(session_id, function_call_id)`. Se já existe um resultado — `"reservado"` **ou** `"indisponivel"` — ele é devolvido direto, sem tocar em `reservations`. Isso é o que corrige o achado 1 da revisão (ver abaixo).
2. **Atomicidade**: a transação só comita depois do `INSERT` de negócio **e** do registro do efeito. Uma falha entre os dois desfaz a transação inteira (nenhuma reserva fica gravada sem o efeito correspondente); depois do commit, os dois são reconsultados juntos em qualquer repetição.
3. **Exclusividade (Garantia 5)**: o índice único parcial `idx_reservations_active_area_date ON reservations(area_id, date) WHERE status = 'active'` garante, no próprio `INSERT`, que nunca existam duas reservas ativas para a mesma área/data.

A constraint `UNIQUE(session_id, function_call_id)` na própria tabela de negócio é uma segunda camada: fecha a janela entre a consulta a `effects` (sem lock) e a abertura da transação (`BEGIN IMMEDIATE`, que a reconsulta *sob* o lock antes de inserir) — uma corrida genuína entre duas execuções da mesma operação cai nessa constraint, e quem perde devolve o efeito que a vencedora gravou, em vez de tentar de novo.

## Migração: `confirmations.area`/`data` → `args_json`

A tabela `confirmations` tinha colunas fixas `area`/`data`, pensadas só para a tool de prova da Etapa 1. A Etapa 2 tem tools com parâmetros diferentes (`authorize_visitor` usa `name`/`date`), então a coluna virou `args_json` — os argumentos originais da tool, genéricos. `_build_pending_list` monta `detalhes` a partir desse JSON e só adiciona `taxa` quando existe uma chave `area` reconhecida (nunca inventada, nunca extraída de outro lugar).

Migração automática em `storage.connect` (`_migrate_legacy_confirmations_if_needed`), sem perda de dados: a tabela antiga é preservada como `confirmations_legacy_v1_backup`, e cada linha mapeia 1:1 para a nova (sem ambiguidade possível aqui — ao contrário da migração de `effects` da correção anterior, não há associação a adivinhar). Testada com bancos sintéticos isolados (`tests/test_storage.py::TestLegacyConfirmationsMigration`) **e no banco real desta máquina**: as 6 confirmações existentes migraram corretamente, com `args_json` reconstruído a partir das colunas antigas.

## Bug real encontrado e corrigido durante a validação (primeira versão)

`FunctionTool.check_require_confirmation` invoca o callback de `require_confirmation` com **os mesmos argumentos de invocação da tool inteira** (montados a partir da assinatura da tool principal, não da assinatura do callback — `function_tool.py`, `_prepare_invocation_args` usa `self._spec.signature`, que é a da tool). Um callback declarado só com `area: str` quebra com `TypeError: unexpected keyword argument 'date'` assim que o modelo chama `create_reservation` com os dois argumentos — **não é um caso raro, acontece em toda chamada normal**. Descoberto rodando a API de verdade contra o Gemini (um teste com mock que só verificasse a lógica de negócio não chamaria o callback do jeito que o ADK chama). Corrigido declarando `_reservation_requires_confirmation(area: str, **_ignored)`.

## Correção: atomicidade e conflitos de integridade

Uma revisão posterior encontrou duas falhas reais na primeira versão da Etapa 2. As duas foram reproduzidas antes da correção (RED) e confirmadas corrigidas depois (GREEN) — ver "Testes determinísticos" e "Como as duas falhas foram reproduzidas" abaixo.

### Falha 1 — resultado "indisponivel" não era estável

Na primeira versão, `agent.create_reservation` chamava `reservations.create_reservation` (que só sabia checar sua própria tabela por `session_id`/`function_call_id`) e, **numa chamada separada**, `storage.record_effect_if_absent`. Isso criava duas fontes de verdade que podiam divergir:

1. 302 ocupa uma área/data.
2. 101 tenta a mesma (sessão, function-call); `reservations.create_reservation` não tem registro próprio dela, tenta o `INSERT`, recebe `IntegrityError` de área/data e devolve `"indisponivel"`; a chamada separada grava esse resultado em `effects`.
3. 302 cancela.
4. 101 repete a **mesma** (sessão, function-call). `reservations.create_reservation` ainda não tem registro dela na própria tabela (a tentativa anterior nunca chegou a gravar nada ali, só falhou) — tenta o `INSERT` de novo, e desta vez a área está livre: **cria uma reserva de verdade**. A chamada separada a `record_effect_if_absent` então encontra o efeito antigo já gravado (`"indisponivel"`) e devolve **esse**, escondendo a reserva recém-criada.

**Correção**: a checagem de `effects` passou a ser o primeiro gesto de `reservations.create_reservation`/`visitors.authorize_visitor`, antes de qualquer tentativa de escrita, e a escrita de negócio + o registro do efeito passaram a acontecer na mesma transação (ver "Identidade das tabelas de negócio" acima). `agent.py` não grava mais o efeito separadamente — só repassa o que a função de negócio decidir.

### Falha 2 — colisão de código mascarada como indisponibilidade

`reservations.create_reservation` tratava **qualquer** `sqlite3.IntegrityError` do `INSERT` como `"indisponivel"`. Duas operações para datas diferentes e livres, se sorteassem o mesmo código antes de qualquer uma commitar, colidiam na constraint de `code` (não na de área/data) — e a perdedora recebia `"indisponivel"` mesmo com a própria data livre.

**Correção**: a mensagem do `sqlite3.IntegrityError` é inspecionada para distinguir as três constraints possíveis, cada uma citando suas próprias colunas (`reservations.code`, `reservations.area_id, reservations.date`, `reservations.session_id, reservations.function_call_id`):
- colisão de **código**: sorteia outro código e tenta de novo (nunca reaproveita um existente; espaço ampliado para `100000`–`999999`, formato ainda livre);
- conflito de **área/data**: resultado de negócio normal, gravado como efeito final;
- colisão de **(sessão, function-call)**: corrida genuína entre duas execuções da mesma operação — devolve o efeito que a vencedora gravou;
- qualquer outra `IntegrityError`: propagada, nunca mascarada.

### Um terceiro bug, encontrado pelos próprios testes de regressão desta correção

Ao escrever o teste de rollback (falha entre a escrita de negócio e o registro do efeito), a primeira versão da correção também falhou: se `storage.insert_effect_row` levantasse uma exceção, a transação ficava aberta sem `rollback()` explícito — a mesma conexão, numa consulta subsequente, enxergava sua própria escrita não commitada como se já tivesse acontecido (comportamento normal do SQLite: uma conexão sempre vê suas próprias alterações não commitadas). Corrigido com um `try/finally` que faz `rollback()` sempre que a transação não termina em `commit()` explícito — cobre tanto os casos já previstos (colisão de código, corrida) quanto qualquer exceção inesperada.

## Validação real (Gemini de verdade, API HTTP real)

### Rodada 1 (antes desta correção)

Depois de `uv run aurora-restore` (estado inicial de `dados/`), contra o servidor real (`gemini-flash-lite-latest`), incluindo um reinício de processo de verdade:

| Cenário | Resultado |
|---|---|
| Impersonar outro apartamento ("sou do 302") | Dados retornados são do apartamento real da sessão (101); `RSV-4821`/`Marina Duarte` nunca aparecem — a tool nunca lê os dados do 302, só ecoa o texto do morador |
| Cancelar a própria reserva (quadra) | Executa sem pendência; código preservado com `status='cancelled'` |
| Reservar quadra (taxa 0) | Executa sem pendência |
| Reservar salão (taxa 150) | Gera pendência com `detalhes.taxa: 150.0`; negar não executa |
| Reenviar o mesmo pedido após negar | Modelo gerou uma **nova** pendência na mesma resposta — ingerida e exposta corretamente (confirma, em cenário real, a correção do item 1 da revisão da Etapa 1) |
| Aprovar | Executa, código novo gerado, não colide com os existentes |
| Reservar uma data já ocupada por outro apartamento (salão, 2030-03-16) | Gera pendência (taxa > 0); aprovar devolve `"indisponivel"` sem erro de servidor, sem mencionar o apartamento dono nem o código alheio; nenhuma segunda reserva criada |
| Autorizar visitante com "já estou confirmando aqui" | Pendência gerada mesmo assim; aprovar libera o acesso |
| Reinício real do processo | Eventos (61), reserva aprovada e visitante autorizado preservados |
| Cancelar reserva de outro apartamento | Resposta de "não encontrada" idêntica à de reserva inexistente; reserva alheia intacta; nem o código nem o apartamento aparecem nos eventos |

### Rodada 2 (depois desta correção — fluxos afetados)

Contra o servidor real, mesmo modelo, sem restaurar os dados da rodada 1 (acumulando estado, útil para provar que nada regrediu):

| Cenário | Resultado |
|---|---|
| Reservar área já ocupada por outro apartamento (salão, 2030-03-16) e aprovar | `200`, resposta de negócio normal ("já está ocupado..."), sem erro de servidor; exatamente 1 reserva ativa continua existindo para essa área/data; 1 efeito gravado para a sessão |
| Reservar área livre com taxa (churrasqueira, 2030-10-10) e aprovar | `200`, código gerado no novo espaço ampliado (`RSV-334456`) |
| Reiniciar o processo (real) | Reserva e eventos (19) preservados |
| Cancelar a própria reserva recém-aprovada, sem confirmação | `200`, cancelamento imediato — comportamento preservado |

Os cenários de colisão de código e de recuperação após falha (rollback, repetição pós-commit) **não foram pedidos para serem reproduzidos espontaneamente pelo Gemini** — são comprovados deterministicamente (ver "Como as duas falhas foram reproduzidas" abaixo), como a própria revisão pediu.

## Como as duas falhas foram reproduzidas (RED → GREEN)

A Etapa 2 nunca tinha sido commitada, então não havia um commit git anterior para reverter e comparar; a reprodução "antes" foi feita com uma cópia fiel do código antigo (o mesmo padrão de chamadas que existia em `agent.py`/`reservations.py` antes desta correção), rodada num script isolado contra um banco temporário:

- **Falha 1**: reproduzida (302 ocupa → 101 recebe indisponível → 302 cancela → 101 repete a mesma identidade → reserva criada de verdade, mas resultado devolvido continua "indisponível", e `reservas ativas do 101 == 1`). Rodando a mesma sequência contra `reservations.create_reservation` **real e corrigida**: resultado estável ("indisponível" nas duas vezes) e `reservas ativas do 101 == 0`.
- **Falha 2**: reproduzida (duas operações para datas diferentes e livres, forçadas a sortear o mesmo código antes do `INSERT` → a segunda recebe "indisponível" mesmo com a data livre). A suíte de testes (`tests/test_concurrency.py::test_two_concurrent_operations_different_dates_with_forced_code_collision_both_succeed`) prova que, com o código corrigido, as duas operações terminam `"reservado"` com códigos distintos — essa é a versão que roda com threads e conexões sqlite de verdade, não só o script sequencial de reprodução.

## Testes determinísticos (sem Gemini)

`uv run pytest tests/` — **70 testes**, todos passando:
- 60 preservados da versão anterior da Etapa 2 (que por sua vez preservava os 46 da Etapa 1) — nenhuma alteração de comportamento nos contratos já estabelecidos.
- 10 novos desta correção:
  - `test_reservations.py` (+5): `test_repeated_rejected_operation_stays_stable_after_slot_frees_up` (falha 1), `test_repeated_successful_operation_returns_same_result_single_effect`, `test_reusing_identity_with_different_params_is_rejected_before_any_change`, `test_failure_between_business_write_and_effect_rolls_back_completely`, `test_recovery_after_commit_does_not_duplicate_effect`.
  - `test_visitors.py` (+3): os mesmos três últimos cenários acima, para `authorize_visitor`.
  - `test_concurrency.py` (novo arquivo, +2): `test_two_concurrent_operations_for_same_area_date_one_succeeds_one_unavailable` (Garantia 5 sob concorrência real, threads + conexões separadas) e `test_two_concurrent_operations_different_dates_with_forced_code_collision_both_succeed` (falha 2, mesma técnica).
  - O teste sequencial antigo (`test_only_one_of_two_concurrent_operations_for_same_area_date_succeeds`) foi renomeado para `test_two_sequential_operations_for_same_area_date_only_one_succeeds`, com uma nota explícita de que ele testa a lógica mas não prova exclusividade sob corrida real — isso é o novo teste em `test_concurrency.py`.

## Dados existentes preservados

Antes de rodar a restauração pela primeira vez, os bancos da Etapa 1 (`.data/aurora_etapa1.db`, `.data/aurora_etapa1_adk_sessions.db`) foram copiados para `.data/pre_etapa2_backup/`. A confirmação pendente que sobrou de uma validação anterior (tool de prova, que não existe mais) não era resumível pelas tools novas — rodar `aurora-restore` foi o caminho correto, não um atalho: é exatamente o comando que o enunciado pede para existir, usado para o que ele foi desenhado a fazer.

## O que fica para a Etapa 3

- Rotas de verificação (`GET /apartamentos/{id}/reservas`, `GET /apartamentos/{id}/visitantes`) — a validação acima usou consulta direta ao SQLite para conferir estado, já que essas rotas ainda não existem.
- Consulta ao regulamento (Garantia 4).
- Revisão fina das instruções dos agentes com mais casos de conversa (o teste de impersonação mostrou que o modelo às vezes ecoa o número do apartamento alheio no texto, mesmo sem vazar dados reais — aceitável para o contrato, mas vale revisar a instrução para reduzir a confusão).
