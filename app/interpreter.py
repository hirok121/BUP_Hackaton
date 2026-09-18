import asyncio
import json
import re

import httpx

from app.config import settings, SYSTEM_PROMPT, FEW_SHOT

_CACHE: dict[tuple[str, float], dict] = {}
_CACHE_MAX = 512

_client: httpx.AsyncClient | None = None

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


def _normalize_note(note: str) -> str:
    return re.sub(r"\s+", " ", note.strip())


def _cache_get(key):
    return _CACHE.get(key)


def _cache_put(key, value):
    if key not in _CACHE and len(_CACHE) >= _CACHE_MAX:
        oldest = next(iter(_CACHE))
        del _CACHE[oldest]
    _CACHE[key] = value


def _parse_body(text: str):
    text = _FENCE_RE.sub("", text).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _unavailable_entry() -> dict:
    return {
        "directive_type": "no_op",
        "explanation": "Interpretation unavailable; treated as no_op.",
    }


async def _call_provider(provider: str, api_key: str, model: str, base_url: str | None, prompt: str, timeout: float):
    client = _get_client()

    if provider in ("openai", "groq", "local"):
        url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        resp = await client.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code >= 400:
            return None, resp.status_code
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return text, resp.status_code

    if provider == "anthropic":
        url = (base_url or "https://api.anthropic.com/v1") + "/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 300,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = await client.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code >= 400:
            return None, resp.status_code
        data = resp.json()
        text = data["content"][0]["text"]
        return text, resp.status_code

    if provider == "google":
        url = (
            base_url
            or f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        )
        params = {"key": api_key}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 300},
        }
        resp = await client.post(url, params=params, json=payload, timeout=timeout)
        if resp.status_code >= 400:
            return None, resp.status_code
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return text, resp.status_code

    return None, None


async def _interpret_one(note: str, capacity_kwh: float) -> dict:
    normalized = _normalize_note(note)
    key = (normalized, capacity_kwh)

    cached = _cache_get(key)
    if cached is not None:
        return cached

    if not settings.llm_configured:
        return _unavailable_entry()

    prompt = FEW_SHOT + "\n" + SYSTEM_PROMPT.format(capacity_kwh=capacity_kwh, note_text=normalized)

    attempts = [
        (settings.llm_provider, settings.llm_api_key, settings.llm_model, settings.llm_base_url)
    ]
    if settings.fallback_configured:
        attempts.append(
            (settings.fallback_provider, settings.fallback_api_key, settings.fallback_model, None)
        )
    # pad attempts to cover llm_max_retries additional tries on the primary if no fallback
    while len(attempts) < 1 + settings.llm_max_retries:
        attempts.append(attempts[0])

    last_status = None
    for i, (provider, api_key, model, base_url) in enumerate(attempts[: 1 + settings.llm_max_retries]):
        if i > 0:
            await asyncio.sleep(0.4)
        try:
            text, status = await _call_provider(
                provider, api_key, model, base_url, prompt, settings.llm_timeout_s
            )
        except (httpx.TimeoutException, httpx.TransportError, httpx.ConnectError):
            last_status = "transport_error"
            continue
        except Exception:
            last_status = "exception"
            continue

        if text is None:
            last_status = status
            if status in _RETRYABLE_STATUS:
                continue
            break

        parsed = _parse_body(text)
        if parsed is None:
            return _unavailable_entry()

        _cache_put(key, parsed)
        return parsed

    return _unavailable_entry()


async def interpret(notes: list[str], capacity_kwh: float) -> list[dict]:
    if not notes:
        return []

    async def _bounded(note):
        try:
            return await _interpret_one(note, capacity_kwh)
        except Exception:
            return _unavailable_entry()

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[_bounded(n) for n in notes]),
            timeout=settings.llm_stage_budget_s,
        )
        return list(results)
    except asyncio.TimeoutError:
        # best effort: notes without a cached result degrade to no_op
        results = []
        for n in notes:
            key = (_normalize_note(n), capacity_kwh)
            cached = _cache_get(key)
            results.append(cached if cached is not None else _unavailable_entry())
        return results
