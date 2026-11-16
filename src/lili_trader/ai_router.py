"""Configurable, fail-safe router for OpenAI-compatible chat providers.

Provider credentials are read only from named environment variables. No key,
provider account, or model is bundled in the repository.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class AIProviderError(RuntimeError):
    """Sanitized provider failure; never includes request headers or secrets."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AIProvider(Protocol):
    name: str
    model: str

    def complete(self, messages: list[dict[str, str]], *, max_tokens: int) -> str:
        ...


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key_env: str = ""
    timeout_seconds: float = 20.0
    requests_per_minute: int = 10
    cooldown_seconds: float = 30.0
    max_tokens: int = 700
    free_tier: bool = False

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("provider name is required")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("provider model is required")
        if not isinstance(self.base_url, str):
            raise ValueError("provider base_url must be a string")
        if not isinstance(self.api_key_env, str):
            raise ValueError("api_key_env must be a string")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            raise ValueError("provider base_url must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provider base_url must not contain credentials or query data")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("non-local providers must use HTTPS")
        if not isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 120:
            raise ValueError("provider timeout must be between 1 and 120 seconds")
        if type(self.requests_per_minute) is not int or not 1 <= self.requests_per_minute <= 120:
            raise ValueError("requests_per_minute must be an integer from 1 to 120")
        if not isfinite(self.cooldown_seconds) or not 1 <= self.cooldown_seconds <= 3600:
            raise ValueError("cooldown_seconds must be between 1 and 3600")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 8192:
            raise ValueError("max_tokens must be an integer from 1 to 8192")
        if type(self.free_tier) is not bool:
            raise ValueError("free_tier must be boolean")
        if self.api_key_env and (
            not self.api_key_env.replace("_", "").isalnum()
            or self.api_key_env[0].isdigit()
        ):
            raise ValueError("api_key_env must be a valid environment variable name")


class OpenAICompatibleProvider:
    """Minimal standard-library client for OpenAI-compatible chat completions."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        opener: Callable = urlopen,
        environ=None,
    ) -> None:
        config.validate()
        self.config = config
        self.name = config.name
        self.model = config.model
        self._opener = opener
        self._environ = os.environ if environ is None else environ

    def complete(self, messages: list[dict[str, str]], *, max_tokens: int) -> str:
        if not isinstance(messages, list) or not messages:
            raise AIProviderError("chat messages are required")
        for item in messages:
            if (
                not isinstance(item, dict)
                or item.get("role") not in ("system", "user", "assistant")
                or not isinstance(item.get("content"), str)
            ):
                raise AIProviderError("chat message format is invalid")
        token_limit = min(max_tokens, self.config.max_tokens)
        if type(token_limit) is not int or token_limit < 1:
            raise AIProviderError("max_tokens is invalid")
        base = self.config.base_url.rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        payload = json.dumps({
            "model": self.config.model,
            "messages": messages,
            "max_tokens": token_limit,
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LiLiTrader/0.1",
        }
        key = self._environ.get(self.config.api_key_env, "") if self.config.api_key_env else ""
        if key:
            headers["Authorization"] = "Bearer " + key
        request = Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with self._opener(request, timeout=self.config.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise AIProviderError("provider returned a non-success status", status_code=status)
                raw = response.read()
        except HTTPError as exc:
            raise AIProviderError(
                "provider HTTP error", status_code=getattr(exc, "code", None)
            ) from exc
        except (URLError, TimeoutError, OSError, UnicodeError) as exc:
            raise AIProviderError("provider network or timeout error") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
            content = decoded["choices"][0]["message"]["content"]
        except (UnicodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("provider response format is invalid") from exc
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("provider returned an empty response")
        return content.strip()


@dataclass(frozen=True)
class AIResponse:
    content: str
    provider: str
    model: str
    cached: bool = False
    fallback: bool = False
    errors: tuple[str, ...] = ()


class LiliAIRouter:
    """Ordered provider fallback with in-memory quota, cooldown and bounded cache."""

    def __init__(
        self,
        providers: tuple[AIProvider, ...] | list[AIProvider] = (),
        *,
        clock: Callable[[], float] = time.monotonic,
        cache_ttl_seconds: float = 86400,
        cache_max_items: int = 100,
        duplicate_window_seconds: float = 3.0,
    ) -> None:
        if not callable(clock):
            raise ValueError("clock must be callable")
        if type(cache_max_items) is not int or cache_max_items < 1:
            raise ValueError("cache_max_items must be a positive integer")
        if (
            isinstance(cache_ttl_seconds, bool)
            or not isinstance(cache_ttl_seconds, (int, float))
            or not isfinite(cache_ttl_seconds)
            or cache_ttl_seconds <= 0
        ):
            raise ValueError("cache_ttl_seconds must be positive")
        if (
            isinstance(duplicate_window_seconds, bool)
            or not isinstance(duplicate_window_seconds, (int, float))
            or not isfinite(duplicate_window_seconds)
            or duplicate_window_seconds < 0
        ):
            raise ValueError("duplicate_window_seconds must be non-negative")
        provider_names = [provider.name for provider in providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("provider names must be unique")
        self.providers = tuple(sorted(
            providers,
            key=lambda provider: not getattr(
                getattr(provider, "config", None), "free_tier", False
            ),
        ))
        self.clock = clock
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_max_items = cache_max_items
        self.duplicate_window_seconds = duplicate_window_seconds
        self._cache: dict[str, tuple[float, AIResponse]] = {}
        self._recent: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}
        self._requests: dict[str, deque[float]] = {}

    @classmethod
    def from_env(cls, *, opener: Callable = urlopen, environ=None) -> "LiliAIRouter":
        env = os.environ if environ is None else environ
        raw = env.get("LILI_AI_PROVIDERS", "").strip()
        if not raw:
            return cls()
        try:
            configs = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("LILI_AI_PROVIDERS must contain valid JSON") from exc
        if not isinstance(configs, list):
            raise ValueError("LILI_AI_PROVIDERS must be a JSON array")
        providers = []
        for item in configs:
            if not isinstance(item, dict):
                raise ValueError("each provider configuration must be an object")
            config = ProviderConfig(**item)
            providers.append(OpenAICompatibleProvider(config, opener=opener, environ=env))
        return cls(providers)

    def _key(self, messages: list[dict[str, str]]) -> str:
        canonical = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()

    def _fallback(self, errors: tuple[str, ...]) -> AIResponse:
        return AIResponse(
            content=(
                "در حال حاضر هیچ مدل زبانی پیکربندی‌شده‌ای پاسخ نداد. "
                "تحلیل قطعی باید فقط از موتور محاسباتی و دادهٔ منبع‌دار گرفته شود؛ "
                "این پاسخ جایگزین تحلیل بازار نیست."
            ),
            provider="local-fallback",
            model="none",
            fallback=True,
            errors=errors,
        )

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 700,
    ) -> AIResponse:
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty list")
        key = self._key(messages)
        now = self.clock()
        for recent_key, seen_at in tuple(self._recent.items()):
            if now - seen_at >= self.duplicate_window_seconds:
                self._recent.pop(recent_key, None)
        cached = self._cache.get(key)
        if cached and now - cached[0] <= self.cache_ttl_seconds:
            response = cached[1]
            self._recent[key] = now
            return AIResponse(
                response.content, response.provider, response.model,
                cached=True, fallback=response.fallback, errors=response.errors,
            )
        if cached:
            self._cache.pop(key, None)
        last = self._recent.get(key)
        if last is not None and now - last < self.duplicate_window_seconds:
            return self._fallback(("duplicate request suppressed",))
        self._recent[key] = now

        errors: list[str] = []
        for provider in self.providers:
            name = provider.name
            if now < self._cooldown_until.get(name, 0):
                errors.append(f"{name}: cooldown")
                continue
            window = self._requests.setdefault(name, deque())
            while window and now - window[0] >= 60:
                window.popleft()
            limit = getattr(getattr(provider, "config", None), "requests_per_minute", 10)
            if len(window) >= limit:
                errors.append(f"{name}: local rate limit")
                continue
            window.append(now)
            try:
                content = provider.complete(messages, max_tokens=max_tokens)
                response = AIResponse(content, name, provider.model)
                self._cache[key] = (now, response)
                if len(self._cache) > self.cache_max_items:
                    oldest = min(self._cache, key=lambda item: self._cache[item][0])
                    self._cache.pop(oldest, None)
                return response
            except AIProviderError as exc:
                errors.append(f"{name}: {exc.status_code or 'provider error'}")
                cooldown = getattr(getattr(provider, "config", None), "cooldown_seconds", 30.0)
                self._cooldown_until[name] = now + cooldown
            except Exception:
                # Provider implementations are untrusted boundaries. Never echo
                # exception text, headers, request bodies or possible secrets.
                errors.append(f"{name}: unexpected provider error")
                self._cooldown_until[name] = now + 30.0
        return self._fallback(tuple(errors))

