"""
Minimal Groq client (Groq exposes an OpenAI-compatible chat-completions API), using
`requests` so there is no extra SDK to install.

Error handling is split by WHO can fix the problem:
  * LLMConfigError  -> the developer must fix it (no key, bad key, retired model name).
                       The API surfaces these loudly (HTTP 503 with a clear message).
  * LLMUnavailable  -> transient (timeout, no internet, rate limit, Groq outage).
                       The chat degrades gracefully to the rule-based advisor.
"""
import logging
from typing import List, Optional

import requests

from app.config import settings

logger = logging.getLogger("fieldnode.llm")


class LLMConfigError(Exception):
    pass


class LLMUnavailable(Exception):
    pass


def _post(model: str, messages: List[dict], temperature: float, max_tokens: int) -> str:
    try:
        resp = requests.post(
            f"{settings.GROQ_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": model, "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens},
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:  # timeout, DNS, connection reset...
        raise LLMUnavailable(f"could not reach Groq: {exc.__class__.__name__}") from exc

    def groq_error():
        """Groq's own JSON error body, or None if this reply didn't come from Groq's API
        (e.g. an HTML/plain-text block page from a school/office proxy or firewall)."""
        try:
            err = resp.json().get("error")
            return err if isinstance(err, dict) else None
        except (ValueError, AttributeError):
            return None

    if resp.status_code in (401, 403):
        if groq_error() is None:
            # A 403 with no Groq error payload is a network block, not a bad key.
            raise LLMUnavailable(f"request blocked before reaching Groq (HTTP {resp.status_code}) "
                                 f"— firewall/proxy?")
        raise LLMConfigError("Groq rejected the API key (check GROQ_API_KEY in .env).")
    if resp.status_code in (400, 404):
        err = groq_error()
        detail = err.get("message", resp.text[:200]) if err else resp.text[:200]
        raise LLMConfigError(f"Groq rejected the request for model '{model}': {detail} "
                             f"(model names get retired \u2014 check GROQ_MODEL).")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise LLMUnavailable(f"Groq returned HTTP {resp.status_code} (rate limit or outage)")
    if resp.status_code != 200:
        raise LLMUnavailable(f"unexpected Groq response HTTP {resp.status_code}")

    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable("malformed response from Groq") from exc
    if not text or not text.strip():
        raise LLMUnavailable("Groq returned an empty reply")
    return text.strip()


def chat_completion(messages: List[dict], temperature: float = 0.3,
                    max_tokens: Optional[int] = None) -> tuple:
    """Returns (reply_text, model_used). Tries GROQ_MODEL, then GROQ_FALLBACK_MODEL once if
    the main model is rate-limited/unavailable (Groq rate limits are per model)."""
    if not settings.GROQ_API_KEY:
        raise LLMConfigError("AI chat isn't configured: set GROQ_API_KEY in .env "
                             "(free key at console.groq.com).")
    max_tokens = max_tokens or settings.CHAT_MAX_REPLY_TOKENS

    try:
        return _post(settings.GROQ_MODEL, messages, temperature, max_tokens), settings.GROQ_MODEL
    except LLMUnavailable as first:
        fb = settings.GROQ_FALLBACK_MODEL
        if not fb or fb == settings.GROQ_MODEL:
            raise
        logger.warning("Primary model failed (%s); trying fallback %s", first, fb)
        return _post(fb, messages, temperature, max_tokens), fb
