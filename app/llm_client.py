"""
Thin wrapper around the OpenRouter API. Every call here asks the
model to return ONLY JSON matching a described shape, which we then parse.
Centralizing this makes it easy to swap models or add retries in one place.
"""
from __future__ import annotations
import json
import re
from typing import Any, Optional
from fastapi import HTTPException
import httpx

from app.config import settings

def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def call_json(system_prompt: str, user_prompt: str, max_tokens: int = 4000, temperature: Optional[float] = None) -> tuple[Any, int]:
    """
    Calls the LLM via OpenRouter and parses a strict-JSON response.
    Returns (parsed_json, total_tokens_used).
    Raises HTTPException(502) on LLM failure or unparsable output.
    """
    if not settings.OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY is not configured. Set it in your .env file.",
        )

    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Project Submission AI Analyzer"
    }

    payload = {
        "model": settings.OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens
    }
    if temperature is not None:
        payload["temperature"] = temperature

    try:
        # Since this is synchronous context currently, we'll use httpx.Client()
        # Alternatively we could use httpx.post but we need a timeout.
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"LLM returned error status: {e.response.text}")
    except Exception as e:
         raise HTTPException(status_code=502, detail=f"Unexpected error calling LLM: {e}")

    try:
        raw_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise HTTPException(status_code=502, detail="Unexpected response format from LLM.")

    cleaned = _strip_code_fences(raw_text)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Best-effort recovery: grab the largest {...} or [...] block
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
