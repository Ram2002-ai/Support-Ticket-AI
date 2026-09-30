"""Intent routing: cheap safety pre-check, then LLM classification into data_query / anomaly / out_of_scope."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.constants import ANOMALY_TYPES
from app.llm.client import LLMClient
from app.llm.parsing import ask_json
from app.llm.prompts import ROUTER_SYSTEM

# Clear-cut injection / destructive phrasing is refused before any LLM call.
# (The read-only DB and the SQL guard remain the real protection.)
_UNSAFE = re.compile(
    r"\b(ignore|disregard|forget)\b.{0,40}\b(instructions?|rules|prompt)\b"
    r"|\b(system|hidden|initial)\s+prompt\b"
    r"|\breveal\b.{0,30}\b(prompt|instructions?)\b"
    r"|\b(drop|truncate|alter)\s+table\b"
    r"|\b(delete|erase|wipe|truncate)\b.{0,40}\b(tickets?|table|data|database|records?)\b",
    re.IGNORECASE,
)

_INTENTS = {"data_query", "anomaly", "out_of_scope"}


@dataclass
class RouteDecision:
    intent: str
    days: int | None = None
    types: list[str] | None = None


def looks_unsafe(question: str) -> bool:
    return bool(_UNSAFE.search(question))


def classify(llm: LLMClient, question: str) -> RouteDecision:
    """Ask the LLM for the intent. Unknown/invalid fields degrade to safe defaults."""
    data = ask_json(llm, ROUTER_SYSTEM, question)
    intent = data.get("intent") if data.get("intent") in _INTENTS else "data_query"

    days = data.get("time_window_days")
    days = days if isinstance(days, int) and not isinstance(days, bool) and 0 < days <= 3650 else None

    raw_types = data.get("anomaly_types")
    types = [t for t in raw_types if t in ANOMALY_TYPES] if isinstance(raw_types, list) else []
    return RouteDecision(intent=intent, days=days, types=types or None)
