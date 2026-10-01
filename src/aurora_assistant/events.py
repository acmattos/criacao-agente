"""Representacao normalizada de um evento de execucao, independente do ADK.

`confirmation_service` so enxerga isso - nunca o tipo `Event` do
`google.adk`. Isso deixa a logica de confirmacao/409 testavel sem Gemini:
os testes constroem `NormalizedEvent` a mao, e so `runner_service` (que fala
de verdade com o ADK) precisa saber converter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class NormalizedEvent:
    author: str
    function_calls: list[dict[str, Any]] = field(default_factory=list)
    function_responses: list[dict[str, Any]] = field(default_factory=list)
    text: Optional[str] = None
