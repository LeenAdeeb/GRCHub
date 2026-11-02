"""Language-model access for mapping suggestions, with three providers behind one interface:

  anthropic  ANTHROPIC_API_KEY   (Messages API)
  openai     OPENAI_API_KEY      (Chat Completions; also works with OpenAI-compatible servers via a base URL)
  gemini     GEMINI_API_KEY      (Google Generative Language API, generateContent)

Plain HTTPS through httpx, no vendor SDKs. A provider is available when its key is set. Pick one per run (or set
GRCHUB_LLM_PROVIDER); with a single key set it is used automatically. Models: only Anthropic has a built-in default;
for openai and gemini you must name the model yourself (GRCHUB_OPENAI_MODEL / GRCHUB_GEMINI_MODEL), because model
names change and a wrong default would fail confusingly. GRCHUB_LLM_MODEL is a fallback for the chosen provider and
GRCHUB_<PROVIDER>_BASE_URL (or GRCHUB_LLM_BASE_URL) overrides the endpoint.
Only the text of the regulatory requirements being compared is ever sent; no organization data."""
from __future__ import annotations

import os
import time

import httpx

PROVIDERS = ("anthropic", "openai", "gemini")
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
DEFAULT_MODEL = {"anthropic": "claude-sonnet-5-5"}
DEFAULT_BASE = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com",
                "gemini": "https://generativelanguage.googleapis.com"}
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(RuntimeError):
    pass


def available() -> list[str]:
    return [p for p in PROVIDERS if os.environ.get(KEY_ENV[p])]


def configured() -> bool:
    return bool(available())


def default_provider() -> str | None:
    want = (os.environ.get("GRCHUB_LLM_PROVIDER") or "").strip().lower()
    if want:
        return want if want in PROVIDERS else None
    have = available()
    return have[0] if have else None


def model_for(provider: str) -> str | None:
    return (os.environ.get(f"GRCHUB_{provider.upper()}_MODEL") or os.environ.get("GRCHUB_LLM_MODEL")
            or DEFAULT_MODEL.get(provider))


def label(provider: str | None = None) -> str | None:
    """The identifier stored with each answer ('anthropic/claude-...'): answers from different models are cached separately."""
    provider = provider or default_provider()
    model = model_for(provider) if provider else None
    return f"{provider}/{model}" if provider and model else None


def status() -> dict:
    return {"configured": configured(), "default_provider": default_provider(),
            "providers": [{"provider": p, "model": model_for(p), "ready": model_for(p) is not None} for p in available()]}


class Client:
    def __init__(self, provider: str | None = None, transport: httpx.BaseTransport | None = None,
                 retries: int = 3, backoff: float = 2.0):
        self.provider = (provider or default_provider() or "").lower()
        if self.provider not in PROVIDERS:
            raise LLMError("no AI provider is configured: set ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY")
        key = os.environ.get(KEY_ENV[self.provider])
        if not key:
            raise LLMError(f"{KEY_ENV[self.provider]} is not set")
        model = model_for(self.provider)
        if not model:
            raise LLMError(f"set GRCHUB_{self.provider.upper()}_MODEL to the model name you want to use with {self.provider}")
        self.model = model
        self.label = f"{self.provider}/{model}"
        base = (os.environ.get(f"GRCHUB_{self.provider.upper()}_BASE_URL") or os.environ.get("GRCHUB_LLM_BASE_URL")
                or DEFAULT_BASE[self.provider]).rstrip("/")
        headers = {"content-type": "application/json"}
        if self.provider == "anthropic":
            headers |= {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        elif self.provider == "openai":
            headers["authorization"] = f"Bearer {key}"
        else:
            headers["x-goog-api-key"] = key
        self._http = httpx.Client(base_url=base, timeout=90, transport=transport, headers=headers)
        self.retries, self.backoff = retries, backoff
        self._openai_tokens_field = "max_tokens"
        self._openai_temperature = True

    # -- request shapes -------------------------------------------------------------------------------------------
    def _request(self, system: str, user: str, max_tokens: int):
        if self.provider == "anthropic":
            return "/v1/messages", {"model": self.model, "max_tokens": max_tokens, "temperature": 0, "system": system,
                                    "messages": [{"role": "user", "content": user}]}
        if self.provider == "openai":
            body = {"model": self.model, self._openai_tokens_field: max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if self._openai_temperature:
                body["temperature"] = 0
            return "/v1/chat/completions", body
        return f"/v1beta/models/{self.model}:generateContent", {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens}}

    def _text(self, data: dict) -> str:
        if self.provider == "anthropic":
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if self.provider == "openai":
            return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)

    # -- call with retries ----------------------------------------------------------------------------------------
    def complete(self, system: str, user: str, max_tokens: int = 1800) -> str:
        last = "unknown error"
        adapted = False
        attempt = 0
        while attempt <= self.retries:
            path, body = self._request(system, user, max_tokens)
            try:
                r = self._http.post(path, json=body)
            except httpx.HTTPError as e:
                last = f"network error: {e.__class__.__name__}"
            else:
                if r.status_code == 200:
                    return self._text(r.json())
                last = f"HTTP {r.status_code}"
                if r.status_code == 400 and self.provider == "openai" and not adapted:
                    # Newer OpenAI models renamed max_tokens and may reject temperature: adapt once, then retry.
                    msg = r.text
                    if "max_completion_tokens" in msg:
                        self._openai_tokens_field = "max_completion_tokens"
                    if "temperature" in msg:
                        self._openai_temperature = False
                    adapted = True
                    continue
                if r.status_code not in (408, 429, 500, 502, 503, 529):
                    raise LLMError(f"the {self.provider} API refused the request ({last}); check the key and model name")
            time.sleep(self.backoff * (2 ** attempt))
            attempt += 1
        raise LLMError(f"the {self.provider} API did not answer after {self.retries + 1} tries ({last})")

    def close(self) -> None:
        self._http.close()
