"""Unified LLM client: GitHub Models (default, free) + Anthropic / Azure / OpenAI / Gemini.

Selection priority (first whose credential is present wins; override with ``LLM_PROVIDER``):
1. ``ANTHROPIC_API_KEY``           -> Anthropic Claude (e.g. Opus 4.x)        [paid]
2. ``AZURE_OPENAI_API_KEY`` (+endpoint) -> Azure OpenAI                       [paid]
3. ``OPENAI_API_KEY``              -> OpenAI                                  [paid]
4. ``GEMINI_API_KEY``             -> Google Gemini                           [paid]
5. ``MODELS_TOKEN`` / ``GITHUB_TOKEN`` / ``GH_TOKEN`` -> GitHub Models REST   [FREE, default]
6. ``gh`` CLI + gh-models extension -> GitHub Models via CLI                  [FREE]

All calls are cached on disk (keyed by provider+model+prompt) so re-runs and large
universes don't repeat work — that's what lets us run *deep* analysis without paying the
compute twice. Returns ``None`` (never raises) when no provider is reachable, so callers
fall back to deterministic analysis.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# --- model defaults (override via env) -------------------------------------- #
GH_MODELS_ENDPOINT = os.getenv("GITHUB_MODELS_ENDPOINT", "https://models.github.ai/inference")
GH_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")              # free, strong, fast
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-1")    # set exact GA id when using Opus
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

_CACHE_DIR = Path(os.getenv("AI_CACHE_DIR", "data/ai_cache"))
_CACHE_ENABLED = os.getenv("AI_CACHE", "1") != "0"


def _env(*names: str) -> str:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return ""


def detect_provider() -> str:
    """Return the active provider id based on env (or ``LLM_PROVIDER`` override)."""
    forced = os.getenv("LLM_PROVIDER", "").strip().lower()
    if forced:
        return forced
    if _env("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _env("AZURE_OPENAI_API_KEY") and _env("AZURE_OPENAI_ENDPOINT"):
        return "azure"
    if _env("OPENAI_API_KEY"):
        return "openai"
    if _env("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        return "gemini"
    if _env("MODELS_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        return "github"
    # last resort: gh CLI (gh-models extension)
    try:
        from src.agent import gh_models
        if gh_models.is_available():
            return "gh_cli"
    except Exception:  # noqa: BLE001
        pass
    return "none"


def provider_info() -> dict:
    """Active provider + model, for logging and on-page transparency."""
    p = detect_provider()
    model = {
        "anthropic": ANTHROPIC_MODEL, "azure": AZURE_DEPLOYMENT, "openai": OPENAI_MODEL,
        "gemini": GEMINI_MODEL, "github": GH_MODEL, "gh_cli": GH_MODEL, "none": None,
    }.get(p)
    label = {
        "anthropic": "Anthropic Claude", "azure": "Azure OpenAI", "openai": "OpenAI",
        "gemini": "Google Gemini", "github": "GitHub Models", "gh_cli": "GitHub Models (CLI)",
        "none": "deterministic (no LLM)",
    }.get(p, p)
    return {"provider": p, "model": model, "label": label, "available": p != "none"}


def is_available() -> bool:
    return detect_provider() != "none"


# --- caching ---------------------------------------------------------------- #
def _cache_key(provider: str, model: str, system: str, user: str) -> str:
    h = hashlib.sha256(f"{provider}\x1f{model}\x1f{system}\x1f{user}".encode("utf-8")).hexdigest()
    return h[:40]


def _cache_get(key: str) -> str | None:
    if not _CACHE_ENABLED:
        return None
    f = _CACHE_DIR / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("content")
        except Exception:  # noqa: BLE001
            return None
    return None


def _cache_put(key: str, content: str, meta: dict) -> None:
    if not _CACHE_ENABLED:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.json").write_text(
            json.dumps({"content": content, "ts": time.time(), **meta}, default=str),
            encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("ai cache write failed: %s", exc)


# --- transport per provider ------------------------------------------------- #
def _post(url: str, headers: dict, payload: dict, timeout: int) -> dict | None:
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                wait = min(30, 2 ** attempt * 3)
                logger.warning("LLM HTTP %s; backing off %ss", r.status_code, wait)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                logger.warning("LLM HTTP %s: %s", r.status_code, r.text[:300])
                return None
            return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM request error (attempt %d): %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return None


def _openai_schema_call(base_url: str, api_key: str, model: str, system: str, user: str,
                        temperature: float, max_tokens: int, json_mode: bool, timeout: int,
                        extra_headers: dict | None = None) -> str | None:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    data = _post(f"{base_url.rstrip('/')}/chat/completions", headers, payload, timeout)
    if not data:
        return None
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Unexpected OpenAI-schema response: %s", str(data)[:300])
        return None


def _anthropic_call(api_key: str, model: str, system: str, user: str,
                    temperature: float, max_tokens: int, timeout: int) -> str | None:
    payload = {
        "model": model, "max_tokens": max_tokens, "temperature": temperature,
        "system": system, "messages": [{"role": "user", "content": user}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "Content-Type": "application/json"}
    data = _post("https://api.anthropic.com/v1/messages", headers, payload, timeout)
    if not data:
        return None
    try:
        return "".join(b.get("text", "") for b in data.get("content", []) if isinstance(b, dict))
    except Exception:  # noqa: BLE001
        return None


def _gemini_call(api_key: str, model: str, system: str, user: str,
                 temperature: float, max_tokens: int, timeout: int) -> str | None:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                             "responseMimeType": "application/json"},
    }
    data = _post(url, {"Content-Type": "application/json"}, payload, timeout)
    if not data:
        return None
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None


def chat(system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 1600,
         json_mode: bool = True, timeout: int = 90, use_cache: bool = True) -> str | None:
    """Single-shot chat completion across providers. Returns text or None (never raises)."""
    provider = detect_provider()
    if provider == "none":
        return None
    info = provider_info()
    model = info["model"] or ""
    key = _cache_key(provider, model, system, user)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached

    out: str | None = None
    try:
        if provider == "anthropic":
            out = _anthropic_call(_env("ANTHROPIC_API_KEY"), model, system, user,
                                  temperature, max_tokens, timeout)
        elif provider == "azure":
            endpoint = _env("AZURE_OPENAI_ENDPOINT").rstrip("/")
            url = f"{endpoint}/openai/deployments/{AZURE_DEPLOYMENT}/chat/completions?api-version={AZURE_API_VERSION}"
            payload = {"messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}],
                       "temperature": temperature, "max_tokens": max_tokens}
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            data = _post(url, {"api-key": _env("AZURE_OPENAI_API_KEY"),
                               "Content-Type": "application/json"}, payload, timeout)
            out = data["choices"][0]["message"]["content"] if data else None
        elif provider == "openai":
            out = _openai_schema_call("https://api.openai.com/v1", _env("OPENAI_API_KEY"),
                                      OPENAI_MODEL, system, user, temperature, max_tokens,
                                      json_mode, timeout)
        elif provider == "gemini":
            out = _gemini_call(_env("GEMINI_API_KEY", "GOOGLE_API_KEY"), GEMINI_MODEL,
                               system, user, temperature, max_tokens, timeout)
        elif provider == "github":
            out = _openai_schema_call(GH_MODELS_ENDPOINT,
                                      _env("MODELS_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"),
                                      GH_MODEL, system, user, temperature, max_tokens,
                                      json_mode, timeout)
        elif provider == "gh_cli":
            from src.agent import gh_models
            out = gh_models.run(user, system=system, model=GH_MODEL, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM call failed (%s): %s", provider, exc)
        out = None

    if out:
        _cache_put(key, out, {"provider": provider, "model": model})
    return out


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
    return s.strip()


def chat_json(system: str, user: str, **kw) -> dict | list | None:
    """``chat`` + robust JSON parse (strips code fences, recovers the outer object)."""
    raw = chat(system, user, **kw)
    if not raw:
        return None
    s = _strip_fences(raw)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning("LLM did not return valid JSON: %s", raw[:200])
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("provider:", provider_info())
    print("reply:", chat("You are terse.", "Reply with exactly: OK", json_mode=False))
