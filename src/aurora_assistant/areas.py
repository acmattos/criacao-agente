from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import config


@lru_cache
def _taxas_por_area(areas_path: Path = config.AREAS_PATH) -> dict[str, float]:
    data = json.loads(areas_path.read_text(encoding="utf-8"))
    return {item["id"]: item["taxa"] for item in data}


def lookup_taxa(area_id: str) -> Optional[float]:
    """Consulta a taxa de uma area em dados/areas.json.

    Nunca confia em uma taxa vinda dos argumentos de uma function-call do
    modelo: a unica fonte de verdade para taxa e o cadastro em dados/.
    """
    return _taxas_por_area().get(area_id)
