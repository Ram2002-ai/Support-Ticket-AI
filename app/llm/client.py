"""LLM providers behind a single interface: Groq (primary), Ollama (local fallback)."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import Settings
from app.errors import LLMUnavailableError

log = logging.getLogger(__name__)


class LLMClient(ABC):
    name: str = "llm"

    @abstractmethod
    def complete(self, system: str, user: str, *, json_mode: bool = True) -> str:
        """Return the model's text reply (temperature 0). Raise LLMUnavailableError on any provider failure."""

    def ping(self) -> bool:
        """Cheap reachability check used by /health."""
        return True


class GroqClient(LLMClient):
    name = "groq"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float) -> None:
        self.api_key, self.model, self.base_url, self.timeout = api_key, model, base_url.rstrip("/"), timeout

    def complete(self, system: str, user: str, *, json_mode: bool = True) -> str:
        if not self.api_key:
            raise LLMUnavailableError("GROQ_API_KEY is not set")
        payload: dict = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Groq request failed: {exc.__class__.__name__}") from exc
        if resp.status_code in (401, 403):
            raise LLMUnavailableError("Groq rejected the API key")
        if resp.status_code == 429:
            raise LLMUnavailableError("Groq rate limit reached")
        if resp.status_code >= 400:
            raise LLMUnavailableError(f"Groq returned HTTP {resp.status_code}")
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailableError("Groq returned an unexpected response") from exc

    def ping(self) -> bool:
        if not self.api_key:
            return False
        try:
            r = httpx.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}, timeout=3)
            return r.status_code == 200
        except httpx.HTTPError:
            return False


class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(self, host: str, model: str, timeout: float) -> None:
        self.host, self.model, self.timeout = host.rstrip("/"), model, timeout

    def complete(self, system: str, user: str, *, json_mode: bool = True) -> str:
        payload: dict = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            payload["format"] = "json"
        try:
            resp = httpx.post(f"{self.host}/api/chat", json=payload, timeout=max(self.timeout, 120))
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Ollama is not reachable at {self.host}") from exc
        if resp.status_code == 404:
            raise LLMUnavailableError(f"Ollama model '{self.model}' not found (run: ollama pull {self.model})")
        if resp.status_code >= 400:
            raise LLMUnavailableError(f"Ollama returned HTTP {resp.status_code}")
        try:
            return resp.json()["message"]["content"] or ""
        except (KeyError, ValueError) as exc:
            raise LLMUnavailableError("Ollama returned an unexpected response") from exc

    def ping(self) -> bool:
        try:
            return httpx.get(f"{self.host}/api/tags", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False


class FallbackClient(LLMClient):
    """Try providers in order; the first that answers wins."""

    def __init__(self, clients: list[LLMClient]) -> None:
        self.clients = clients
        self.name = " -> ".join(c.name for c in clients)
        self.last_used: str | None = None

    def complete(self, system: str, user: str, *, json_mode: bool = True) -> str:
        failures: list[str] = []
        for client in self.clients:
            try:
                text = client.complete(system, user, json_mode=json_mode)
                self.last_used = client.name
                return text
            except LLMUnavailableError as exc:
                log.warning("LLM provider %s failed: %s", client.name, exc)
                failures.append(f"{client.name}: {exc}")
        raise LLMUnavailableError("No LLM provider is available. " + " | ".join(failures))

    def ping(self) -> bool:
        return any(c.ping() for c in self.clients)


def build_llm(settings: Settings) -> LLMClient:
    providers: dict[str, LLMClient] = {
        "groq": GroqClient(settings.groq_api_key, settings.groq_model, settings.groq_base_url, settings.llm_timeout),
        "ollama": OllamaClient(settings.ollama_host, settings.ollama_model, settings.llm_timeout),
    }
    order = [settings.llm_provider]
    if settings.llm_fallback:
        order += [name for name in providers if name != settings.llm_provider]
    return FallbackClient([providers[name] for name in order])
