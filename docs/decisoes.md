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

## Decisões provisórias — a confirmar na Etapa 1

- **Compatibilidade da versão do ADK**: `google-adk==2.10.0` está fixado em `pyproject.toml`/`uv.lock`, mas o enunciado alerta que o comportamento de retomada de confirmação mudou entre 2.2.0 e 2.9.1 testados pelos autores; 2.10.0 ainda não foi validado quanto a isso. A Etapa 1 (prova técnica) deve confirmar se a retomada funciona nessa versão com sessão persistida; se não funcionar, o ajuste é trocar o pin para outra versão da série 2, não relaxar para um intervalo.
- **Modelo Gemini**: ainda não escolhido; depende de disponibilidade e cotas no Google AI Studio do usuário (Etapa 1).
- **Topologia dos agentes** (transferência vs. agente-como-tool) e **serviço de sessão** (memória vs. persistido, ex. SQLite): não decididos ainda; o próprio enunciado indica que a escolha certa depende de testar a retomada de confirmação com persistência real, não só em memória.

## Comandos usados

```
uv init --app --package --name aurora-assistant --python 3.12 --vcs git --no-readme --author-from none
uv add "google-adk>=2.2.0,<3" "fastapi" "uvicorn[standard]"
uv lock   # após fixar google-adk==2.10.0 em pyproject.toml; só essa linha mudou no uv.lock
```
