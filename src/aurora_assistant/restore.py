"""Comando de restauracao: apaga e recria o estado do condominio a partir de
`dados/*.json`. So LE os arquivos de `dados/`, nunca escreve neles.

Apaga por completo os bancos locais (ledger da API e sessoes do ADK) antes
de recarregar - decisao documentada em docs/decisoes.md: reiniciar sem
restaurar preserva tudo (Garantia 3), mas o comando de restauracao explicito
e um reset completo, incluindo sessoes e confirmacoes pendentes.

Uso: `uv run aurora-restore` (ou `uv run python -m aurora_assistant.restore`).
Rode com a API parada - os arquivos sqlite sao apagados e recriados do zero.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from . import config, storage

DADOS_DIR = config.REPO_ROOT / "dados"


def _load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def restore() -> None:
    for path in (config.DB_PATH, config.ADK_SESSIONS_DB_PATH):
        if path.exists():
            path.unlink()

    conn = storage.connect(config.DB_PATH)
    try:
        for r in _load_json(DADOS_DIR / "reservas.json"):
            seed_id = f"seed:{r['codigo']}"
            conn.execute(
                """
                INSERT INTO reservations
                    (code, apartment, area_id, date, status, session_id, function_call_id, created_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    r["codigo"],
                    r["apartamento"],
                    r["area"],
                    r["data"],
                    seed_id,
                    seed_id,
                    storage.now_iso(),
                ),
            )

        for v in _load_json(DADOS_DIR / "visitantes.json"):
            seed_id = f"seed:{v['apartamento']}:{v['nome']}:{v['data']}"
            conn.execute(
                """
                INSERT INTO visitor_authorizations
                    (id, apartment, name, date, session_id, function_call_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    v["apartamento"],
                    v["nome"],
                    v["data"],
                    seed_id,
                    seed_id,
                    storage.now_iso(),
                ),
            )

        conn.commit()
    finally:
        conn.close()


def main() -> None:
    restore()
    print("Dados restaurados para o estado inicial de dados/ (reservas, visitantes, sessoes e confirmacoes zeradas).")


if __name__ == "__main__":
    main()
