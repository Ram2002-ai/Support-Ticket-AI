"""Test doubles."""
from __future__ import annotations

from app.llm.client import LLMClient


class FakeLLM(LLMClient):
    """Replays scripted replies in order. Exceptions in the script are raised instead of returned."""

    name = "fake"

    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *, json_mode: bool = True) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise AssertionError("FakeLLM was called more times than scripted")
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
