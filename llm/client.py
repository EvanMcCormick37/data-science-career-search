"""
Thin OpenRouter client.

All LLM calls (extraction, scoring, deep analysis) route through here.
Swap the model ID in settings.py or pass a different `model` argument —
no other code needs to change.

OpenRouter exposes an OpenAI-compatible REST API, so the request/response
shape is identical to OpenAI's chat completions endpoint.

Provides:
  complete()            — synchronous, returns raw string
  complete_json()       — synchronous, returns parsed dict
  async_complete()      — async, accepts a shared httpx.AsyncClient
  async_complete_json() — async + JSON parse
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import OPENROUTER_API_KEY, OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://github.com/data-science-career-search",
    "X-Title": "data-science-career-search",
}

# Retry on transient network errors only; let HTTP 4xx/5xx surface immediately
_RETRYABLE = (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ConnectError)


def _payload(
    model: str,
    messages: list[dict],
    *,
    response_format: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    p: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format == "json":
        p["response_format"] = {"type": "json_object"}
    return p


@retry(
    retry=retry_if_exception_type(_RETRYABLE),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def complete(
    model: str,
    messages: list[dict],
    *,
    response_format: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> str:
    """Synchronous chat completion.  Returns the assistant turn as a raw string."""
    with httpx.Client(timeout=60) as client:
        r = client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=_HEADERS,
            json=_payload(model, messages, response_format=response_format,
                          temperature=temperature, max_tokens=max_tokens),
        )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    logger.debug(f"[{model}] {len(content)} chars returned")
    return content


def complete_json(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> dict:
    """Synchronous completion that parses and returns JSON."""
    raw = complete(model, messages, response_format="json",
                   temperature=temperature, max_tokens=max_tokens)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(f"JSON parse failure from {model}: {raw[:200]!r}")
        raise


async def async_complete(
    model: str,
    messages: list[dict],
    *,
    response_format: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    client: httpx.AsyncClient | None = None,
) -> str:
    """
    Async chat completion.

    Pass a shared `httpx.AsyncClient` for connection re-use across many
    concurrent calls (Tier 2 scoring).  If none is provided, a new client
    is created and closed after the call.
    """
    _own = client is None
    if _own:
        client = httpx.AsyncClient(timeout=60)
    try:
        r = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=_HEADERS,
            json=_payload(model, messages, response_format=response_format,
                          temperature=temperature, max_tokens=max_tokens),
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    finally:
        if _own:
            await client.aclose()


async def async_complete_json(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Async completion that parses and returns JSON."""
    raw = await async_complete(
        model, messages, response_format="json",
        temperature=temperature, max_tokens=max_tokens, client=client,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"JSON parse failure from {model}: {raw[:200]!r}")
        raise
