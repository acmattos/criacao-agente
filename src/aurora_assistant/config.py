from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / ".data"

# Banco da API (sessoes, confirmacoes, efeitos) - separado do banco de sessoes do ADK.
DB_PATH = DATA_DIR / "aurora_etapa1.db"
# Banco de sessoes/eventos do proprio ADK (SqliteSessionService).
ADK_SESSIONS_DB_PATH = DATA_DIR / "aurora_etapa1_adk_sessions.db"

AREAS_PATH = REPO_ROOT / "dados" / "areas.json"
REGULAMENTO_PATH = REPO_ROOT / "dados" / "regulamento.md"

APP_NAME = "aurora"
# gemini-2.5-flash esgotou a cota gratuita diaria (20 req/dia) durante a
# validacao desta etapa; gemini-flash-lite-latest tem cota separada e
# respondeu bem nos testes. Escolha ainda provisoria, a revisar na Etapa 3.
MODEL = "gemini-flash-lite-latest"
CONFIRMATION_FUNCTION_NAME = "adk_request_confirmation"
