# Decisões e execução — Etapa 0 (Preparação)

Registro das decisões tomadas durante a Etapa 0 do `PLANO_INICIAL_AURORA.md`. Decisões marcadas como provisórias serão confirmadas ou revistas na Etapa 1 (prova técnica de confirmação persistida).

## O que foi feito

1. **Hashes de `dados/` registrados** em `docs/dados-hashes.sha256` (SHA-256), antes de qualquer código tocar nesses arquivos. Conferência: `sha256sum -c docs/dados-hashes.sha256`. Os cinco arquivos originais continuam idênticos.
2. **Enunciado preservado** em `docs/challenge.md` (cópia do `README.md` original), antes de o `README.md` ser substituído pela entrega final (seção "Entregável" do enunciado exige README nas seções Arquitetura, Garantias e Como rodar).
3. **Repositório git inicializado** localmente (`uv init --vcs git`), com `HEAD` apontando para `refs/heads/main` (não `master`) desde o início, já que a entrega exige branch `main`. Nenhum commit foi criado — commits só acontecem mediante pedido explícito e pontual.
4. **Estrutura mínima do projeto**: layout `src/`, pacote `aurora_assistant` (nome interno em inglês, conforme decisão do plano de manter identificadores internos em inglês e o contrato HTTP em português).
5. **uv configurado**: Python 3.12 (`.python-version`), `pyproject.toml` com `requires-python = ">=3.12"`, dependências iniciais adicionadas e `uv.lock` gerado e versionável:
   - `fastapi`
   - `uvicorn[standard]`
   - `google-adk==2.10.0` — **versão exata fixada** (série 2, ≥ 2.2.0, conforme exigido pelo enunciado). O pin exato não significa que a compatibilidade com confirmação persistida já foi comprovada: essa comprovação é objeto da Etapa 1 (prova técnica). Se a retomada falhar nessa versão, o ajuste é trocar o pin para outra versão da série 2 e regerar o `uv.lock`, não relaxar de volta para um intervalo.
6. **`.env.example` criado** com os nomes das variáveis do Google AI Studio usadas pelo ADK, **sem valores** (`GOOGLE_GENAI_USE_VERTEXAI=`, `GOOGLE_API_KEY=`), conforme o enunciado exige ("nomes das variáveis, sem valores"). `.env` real (com valores) permanece fora do git (`.gitignore`) — nele, a variável `GOOGLE_GENAI_USE_VERTEXAI` deve ser preenchida com `FALSE` (uso do Google AI Studio, não do Vertex AI, que é o caminho do curso) e `GOOGLE_API_KEY` com a chave real do usuário.
7. **`.gitignore` ampliado**: além de `.env`, `.venv/`, `__pycache__/` e `/.idea` (regras preexistentes, preservadas), foram adicionados `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `*.egg-info/` e os artefatos de armazenamento local em runtime — decorrência da decisão de armazenamento em SQLite (Seção 4 do plano): `*.db`, `*.db-journal`, `*.db-wal`, `*.db-shm`, `*.sqlite3`, `*.sqlite3-journal`, `*.sqlite3-wal`, `*.sqlite3-shm` (inclui journal, WAL e SHM, não só o arquivo principal). `.env.example` e `uv.lock` continuam versionáveis. Todas as regras foram conferidas com `git check-ignore -v` para cada padrão, sem precisar criar os artefatos reais.

## Verificação de conclusão da Etapa 0

- `uv sync` roda sem erro (dependências resolvidas e instaladas em `.venv/`).
- `dados/` permanece byte-idêntico ao estado inicial (conferido por hash).
- `README.md`, `PLANO_INICIAL_AURORA.md` e `dados/` não foram alterados pelo scaffold.

## Decisões confirmadas na Etapa 1 (prova técnica real, ver `docs/etapa1-prova-tecnica.md`)

- **ADK 2.10.0 sustenta a retomada persistida** com `sub_agents` + `SqliteSessionService` + `ResumabilityConfig(is_resumable=True)`. Validado com Gemini real, não mock, inclusive atravessando reinícios reais de processo.
- **Topologia validada**: especialista com tool que exige confirmação como `sub_agents` roteou corretamente em todos os testes (CLI e API real). A topologia `AgentTool` para esse caso não foi testada — não há evidência de que funcione nem de que falhe, só a leitura do código (`_agent_router.find_agent` só percorre `sub_agents`) como razão para não arriscar com `AgentTool` enquanto não for validada.
- **Serviço de sessão**: `SqliteSessionService` (`google.adk.sessions.sqlite_session_service`), não `DatabaseSessionService` — já disponível sem dependências extras.
- **Modelo**: `gemini-flash-lite-latest` — `gemini-2.5-flash` (escolha original) esgotou a cota gratuita diária (20 req/dia) durante a validação da API real. Provisório, a revisar na Etapa 3 por custo/qualidade.
- **Achado crítico que mudou o desenho da Etapa 1/2**: o ADK, chamado direto (sem proteção própria), **não** impede reexecução quando a mesma confirmação já respondida é reenviada — ele roda a tool de novo (comprovado no experimento histórico, `scripts/etapa1_proof_legado.py`). A implementação real (`aurora_assistant.confirmation_service` + `aurora_assistant.storage`) resolve isso com um ledger de confirmações próprio, consultado e transicionado atomicamente **antes** de qualquer chamada a `runner.run_async` — o reenvio nunca chega ao ADK. Ver `docs/etapa1-prova-tecnica.md` para a implementação completa, os testes determinísticos das proteções e a execução real via API (não só CLI).
- **Identidade do efeito é por sessão, não só pelo id da function-call**: o id que o ADK gera para uma function-call (ex. `call_549289`) não tem unicidade global garantida entre sessões. A tabela `effects` usa chave composta `(session_id, original_function_call_id)`, com `session_id` sempre vindo de `tool_context.session.id` (contexto confiável do ADK, nunca argumento do modelo). Reaproveitar a identidade com parâmetros diferentes é rejeitado (`storage.EffectParamsMismatch`), nunca executado nem devolvido silenciosamente. Migração do esquema antigo preserva os dados (nunca apaga), associando só quando a sessão é determinável sem ambiguidade — ver `docs/etapa1-prova-tecnica.md`.
- **Identificadores internos em inglês**: agente `principal` → `main_agent`, `especialista_reservas` → `reservation_specialist`, tool `reservar_area_prova` → `reserve_area_proof`. Antes de renomear, toda confirmação pendente nos bancos locais foi resolvida pela própria API, para não deixar nada preso ao nome antigo — validado depois que uma sessão criada antes da renomeação continuou aceitando mensagens normalmente.

## Decisões confirmadas na Etapa 2 (regras de negócio reais, ver `docs/etapa2-regras-negocio.md`)

- **Tool de prova substituída** por tools reais: `list_my_reservations`, `check_area_availability`, `create_reservation`, `cancel_reservation` (`reservation_specialist`); `list_my_visitors`, `authorize_visitor` (novo especialista `visitor_specialist`, também `sub_agents`, mesma topologia validada na Etapa 1).
- **Exclusividade de área/data (Garantia 5)** garantida por índice único parcial na gravação (`reservations`, `WHERE status='active'`), não por conferência prévia — conflito vira `sqlite3.IntegrityError` capturado e traduzido em resposta de negócio normal, nunca erro de servidor.
- **Identidade de idempotência nas próprias tabelas de negócio** (`UNIQUE(session_id, function_call_id)` em `reservations`/`visitor_authorizations`), não só na tabela genérica `effects` — resolve exclusividade e idempotência na mesma instrução SQL.
- **`confirmations.area/data` virou `args_json` genérico**, porque tools diferentes (reserva vs. visitante) têm parâmetros diferentes. Migrado automaticamente, sem perda de dados, testado com dados sintéticos e no banco real.
- **Bug real do ADK encontrado em validação real** (não pego por nenhum teste com mock): o callback de `require_confirmation` recebe os argumentos de toda a tool (área E data), não só os que ele declara — precisa aceitar `**kwargs` extra.
- **Comando de restauração implementado** (`uv run aurora-restore`): zera sessões/confirmações/reservas/visitantes e recarrega o estado inicial de `dados/*.json`. Usado pela primeira vez para encerrar uma confirmação pendente órfã (presa à tool de prova já removida) antes da validação real da Etapa 2.

## Decisões confirmadas na Etapa 3 (API completa, ver `docs/etapa3-api-completa.md`)

- **Rotas de verificação implementadas**: `GET /apartamentos/{id}/reservas` e `GET /apartamentos/{id}/visitantes`, lendo o SQLite direto (nunca passam pelo modelo). Apartamento sem registro devolve lista vazia, não erro — comportamento livre pelo enunciado.
- **"Todas as pendências atuais" já estava satisfeito** desde a Etapa 1 (`storage.list_pending_confirmations` filtra por `status='pending'` da sessão inteira, não só da última chamada) — nenhuma mudança necessária.
- **Fluxo completo do avaliador validado com Gemini real**: passos 1–11 e 13–14 do enunciado rodados contra o servidor HTTP real, incluindo reinício de processo de verdade e disputa concorrente real (dois `curl` simultâneos). Passo 12 fica para a Etapa 4 (regulamento); a sequência única dos 15 passos é Etapa 5 (validação integrada); a seção Garantias do README é Etapa 6 (documentação e entrega) — pelo plano original, não Etapa 4.

## Comandos usados

```
uv init --app --package --name aurora-assistant --python 3.12 --vcs git --no-readme --author-from none
uv add "google-adk>=2.2.0,<3" "fastapi" "uvicorn[standard]"
uv lock   # após fixar google-adk==2.10.0 em pyproject.toml; só essa linha mudou no uv.lock
```
