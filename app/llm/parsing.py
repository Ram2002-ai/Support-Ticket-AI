"""Robust extraction of a JSON object from LLM output, with one corrective retry."""
from __future__ import annotations

import json
import logging
import re

from app.errors import LLMOutputError
from app.llm.client import LLMClient

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict:
    """Strip code fences / chatter and return the first JSON object in ``text``."""
    cleaned = _FENCE.sub("", (text or "").strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise LLMOutputError("The model reply did not contain a JSON object.")
    try:
        obj = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"The model reply was not valid JSON: {exc.msg}") from exc
    if not isinstance(obj, dict):
        raise LLMOutputError("The model reply was JSON but not an object.")
    return obj


def ask_json(llm: LLMClient, system: str, user: str, retries: int = 1) -> dict:
    """Call the LLM and parse a JSON object; on malformed output, retry with a stricter reminder."""
    prompt = user
    last_error: LLMOutputError | None = None
    for attempt in range(retries + 1):
        raw = llm.complete(system, prompt, json_mode=True)
        try:
            return parse_json_object(raw)
        except LLMOutputError as exc:
            last_error = exc
            log.warning("Malformed LLM output (attempt %d): %s", attempt + 1, exc)
            prompt = user + "\n\nYour previous reply was not valid JSON. Reply with ONLY one valid JSON object."
    assert last_error is not None
    raise last_error
