# Etapa 3 — API completa e integração dos agentes

Fecha o contrato HTTP do enunciado: as quatro rotas de conversa já existiam desde a Etapa 1 (sessão, mensagens, confirmações, eventos); esta etapa acrescenta as duas rotas de verificação (`GET /apartamentos/{apartamento}/reservas` e `GET /apartamentos/{apartamento}/visitantes`), que leem o SQLite direto, sem passar pelo modelo. Consulta ao regulamento fica para a Etapa 4, por instrução explícita.

## O que foi implementado

- `GET /apartamentos/{apartamento}/reservas`: `reservations.list_active_reservations` — só reservas **ativas** (canceladas não aparecem), nunca de outro apartamento. Apartamento sem registro devolve lista vazia (`200`), comportamento livre pelo enunciado.
- `GET /apartamentos/{apartamento}/visitantes`: `visitors.list_authorized_visitors`, mesma lógica.
- `aurora_assistant.api` deixou de ser "a API mínima da Etapa 1" — o módulo e o título do FastAPI foram atualizados para refletir o contrato completo.
- 6 testes determinísticos novos (`tests/test_api.py`): listagem, apartamento sem reservas/visitantes, reserva cancelada não aparece, reserva de outro apartamento não vaza.

Não houve mudança nas rotas de conversa: `confirmation_service._build_pending_list` já devolvia **todas** as confirmações pendentes da sessão (`storage.list_pending_confirmations`, filtrando por `status='pending'`), não só as geradas pela última chamada — esse requisito da Etapa 3 já estava satisfeito desde a Etapa 1.

## Validação dos passos aplicáveis à Etapa 3 (enunciado, seção "Fluxo do avaliador")

Rodado contra o servidor HTTP real (`uv run uvicorn aurora_assistant.api:app --port 8000`), Gemini real (`gemini-flash-lite-latest`), depois de `uv run aurora-restore`. Passos 1–11, 13–14 do enunciado — o passo 12 (pergunta sobre o regulamento) foi pulado por instrução explícita (Etapa 4); o passo 13 conferiu a contagem de eventos anotada logo após o passo 11, em vez de depois do 12.

| Passo | Cenário | Resultado |
|---|---|---|
| 1 | Estado inicial via rotas de verificação | `RSV-1377` em `/apartamentos/101/reservas`; Marina Duarte em `/apartamentos/302/visitantes` |
| 2 | Criar S1 (101) | `201` |
| 3 | "Sou do 302..." em S1 | Resposta traz os dados reais de 101 (`RSV-1377`), nunca `RSV-4821`/Marina Duarte — confirmado também nos eventos |
| 4 | Cancelar reserva do 302 a partir de S1 | "não encontrada"; `RSV-4821` continua em `/apartamentos/302/reservas`; sem vazamento nos eventos |
| 5 | Cancelar a própria reserva (quadra) | Sem pendência; `RSV-1377` some de `/apartamentos/101/reservas` |
| 6 | Reservar quadra livre | Sem pendência; nova reserva aparece na rota de verificação |
| 7 | Reservar salão (taxa) | Pendência com `area`/`date` em `detalhes`; negar → sem reserva criada |
| 8 | Repetir e aprovar | Exatamente 1 reserva do salão; reenviar a mesma confirmação → `409`, sem duplicar |
| 9 | Id inexistente / sessão inexistente | `409` e `404`, respectivamente; reservas do 101 inalteradas |
| 10 | Nova sessão (101), reservar data do 302 | Pendência → aprovar → resposta de indisponibilidade, sem `RSV-4821` nem "302" isolado; nenhuma reserva nova para 101 |
| 11 | Autorizar visitante com "já confirmei" | Injeção ignorada; pendência gerada; Joana Ribeiro só aparece em `/apartamentos/101/visitantes` depois de aprovar |
| 13 | Reinício real do processo, sem restaurar | Eventos de S1 preservados (55 antes e depois do reinício); nova mensagem funciona e eventos crescem (63); rotas de verificação batem exatamente com o estado esperado (quadra 2030-04-06, salão 2030-04-20, sem `RSV-1377`, Joana Ribeiro em 2030-04-21, `RSV-4821` intacta no 302); códigos novos (`RSV-871289`, `RSV-501956`) diferentes entre si e dos três originais |
| 14 | Duas sessões (S3=101, S4=201), mesma área/data, aprovações disparadas em paralelo (dois `curl` com `&`, de verdade) | As duas respondem `200`; soma das reservas do salão em 2030-05-11 entre `/apartamentos/101/reservas` e `/apartamentos/201/reservas` é exatamente 1 |

Achado lateral observado de novo (já documentado na Etapa 2): negar uma pendência às vezes faz o modelo gerar imediatamente uma nova pendência para o mesmo pedido, em vez de responder com texto. Tratado corretamente pela ingestão de confirmações durante a retomada (Etapa 1/2) — a nova pendência aparece em `confirmacoes_pendentes` e pôde ser aprovada normalmente, sem exigir reenviar a mensagem original.

## Passo 15 do enunciado (verificação estática do repositório)

- ✅ `google-adk==2.10.0` fixado (`pyproject.toml`/`uv.lock`), inalterado desde a Etapa 1.
- ✅ `dados/` idêntico ao original (`sha256sum -c docs/dados-hashes.sha256`).
- ✅ Nenhuma chave versionada (`.env` fora do git).
- ✅ Um agente principal (`main_agent`) com dois especialistas (`reservation_specialist`, `visitor_specialist`).
- ✅ Nas conversas, reservas e visitantes são lidos e gravados por tools (`agent.py` → `reservations.py`/`visitors.py`); as rotas de verificação consultam diretamente o SQLite.
- ✅ Apartamento usado pelas tools vem de `tool_context.session.user_id` em toda tool de negócio (`agent._identity`); nenhuma tool declara um parâmetro de apartamento.
- ✅ `main_agent` não tem o regulamento nas instruções (ele nem existe como tool ainda — Etapa 4).
- ✅ Exclusividade da reserva garantida no `INSERT` (índice único parcial), não numa consulta prévia.
- ⬜ Seção Garantias do README — ainda não escrita; pelo plano original (`PLANO_INICIAL_AURORA.md`), isso é Etapa 6, não Etapa 4.

## Testes determinísticos (sem Gemini)

`uv run pytest tests/` — **76 testes**, todos passando (70 preservados + 6 novos das rotas de verificação).

## O que fica para as próximas etapas (pelo plano original)

- **Etapa 4** — consulta seletiva ao regulamento (Garantia 4): especialista novo, sem carregar o documento inteiro na sessão nem nas instruções do `main_agent`; e o passo 12 do fluxo do avaliador (pergunta sobre a piscina).
- **Etapa 5** — validação integrada: repetir os 15 passos do avaliador em sequência única, do ambiente limpo à disputa final (o que esta etapa já fez para os passos 1–11 e 13–14, isoladamente).
- **Etapa 6** — documentação e entrega: README final substituindo o enunciado, com as seções Arquitetura, Garantias e Como rodar.
