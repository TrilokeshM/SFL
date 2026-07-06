"""
Thin wrapper around the OpenRouter API. Every call here asks the
model to return ONLY JSON matching a described shape, which we then parse.
Centralizing this makes it easy to swap models or add retries in one place.

Both a synchronous (call_json) and asynchronous (call_json_async) variant
are provided. Use call_json_async inside FastAPI async route handlers to
avoid blocking the event loop.
"""
from __future__ import annotations
import json
import re
from typing import Any, Optional
from fastapi import HTTPException
import httpx

from app.config import settings

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _make_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Project Submission AI Analyzer",
    }


def _make_payload(system_prompt: str, user_prompt: str, max_tokens: int, temperature: Optional[float]) -> dict:
    payload: dict = {
        "model": settings.OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    return payload


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def _parse_response(data: dict) -> tuple[Any, int]:
    """Extract and parse JSON content from an OpenRouter response dict."""
    try:
        raw_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise HTTPException(status_code=502, detail="Unexpected response format from LLM.")

    cleaned = _strip_code_fences(raw_text)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if not match:
            raise HTTPException(status_code=502, detail="LLM did not return parsable JSON.")
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            raise HTTPException(status_code=502, detail="LLM did not return parsable JSON.")

    tokens_used = 0
    usage = data.get("usage")
    if usage:
        tokens_used = usage.get("total_tokens", 0)

    return parsed, tokens_used


def call_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4000,
    temperature: Optional[float] = None,
) -> tuple[Any, int]:
    """
    Synchronous LLM call. Use only where async is not available.
    Returns (parsed_json, total_tokens_used).
    """
    if not settings.OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY is not configured. Set it in your .env file.",
        )

    payload = _make_payload(system_prompt, user_prompt, max_tokens, temperature)

    try:
        with httpx.Client(timeout=90.0) as client:
            response = client.post(_OPENROUTER_URL, headers=_make_headers(), json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"LLM returned error status: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Unexpected error calling LLM: {e}")

    return _parse_response(data)


async def call_json_async(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4000,
    temperature: Optional[float] = None,
) -> tuple[Any, int]:
    """
    Async LLM call — use this inside FastAPI async route handlers so the
    event loop is never blocked while waiting for the LLM response.
    Returns (parsed_json, total_tokens_used).
    """
    if not settings.OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY is not configured. Set it in your .env file.",
        )

    payload = _make_payload(system_prompt, user_prompt, max_tokens, temperature)

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(_OPENROUTER_URL, headers=_make_headers(), json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"LLM returned error status: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Unexpected error calling LLM: {e}")

    return _parse_response(data)
