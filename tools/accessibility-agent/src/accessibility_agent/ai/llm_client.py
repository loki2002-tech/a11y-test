"""
LLM Client — Abstract base + Google Gemini implementation.

Design rules:
1. NEVER call the LLM with raw DOM/HTML unless it has been sanitised.
2. NEVER treat LLM output as a Finding directly — always validate via schema.
3. ALL LLM calls must be logged with token counts for cost tracking.
4. If the LLM is disabled, all methods return None gracefully.
5. Every prompt includes the "WCAG grounding" instruction to prevent hallucination.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from accessibility_agent.config import LLMProvider, settings
from accessibility_agent.logging_config import get_logger

log = get_logger(__name__)


class LLMResponse:
    """Container for an LLM response."""

    def __init__(self, text: str, model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.text = text
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class BaseLLMClient(ABC):
    """Abstract base for all LLM clients."""

    @abstractmethod
    async def generate(self, prompt: str, system: str = "") -> LLMResponse | None:
        """Generate a response. Returns None if the provider is disabled or fails."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...


class DisabledLLMClient(BaseLLMClient):
    """Stub client used when A11Y_LLM_PROVIDER=disabled."""

    async def generate(self, prompt: str, system: str = "") -> LLMResponse | None:
        return None

    @property
    def provider_name(self) -> str:
        return "disabled"


class GeminiLLMClient(BaseLLMClient):
    """
    Google Gemini client using the google-generativeai SDK.

    Requires: A11Y_GOOGLE_API_KEY set in environment.
    """

    def __init__(self) -> None:
        import google.generativeai as genai  # type: ignore

        key = settings.google_api_key
        if not key:
            raise ValueError("A11Y_GOOGLE_API_KEY is required for Gemini provider.")

        genai.configure(api_key=key.get_secret_value())
        self._model = genai.GenerativeModel(
            model_name=settings.llm_model,
            generation_config={
                "temperature": settings.llm_temperature,
                "max_output_tokens": settings.llm_max_tokens,
            },
        )
        log.info("llm_client.gemini_initialized", model=settings.llm_model)

    async def generate(self, prompt: str, system: str = "") -> LLMResponse | None:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        try:
            log.debug("llm_client.generating", provider="gemini", prompt_len=len(full_prompt))
            response = self._model.generate_content(full_prompt)
            text = response.text
            usage = getattr(response, "usage_metadata", None)
            prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
            completion_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
            log.info("llm_client.response_received",
                     provider="gemini",
                     prompt_tokens=prompt_tokens,
                     completion_tokens=completion_tokens)
            return LLMResponse(text=text, model=settings.llm_model,
                               prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        except Exception as exc:
            log.error("llm_client.generate_failed", provider="gemini", error=str(exc))
            return None

    @property
    def provider_name(self) -> str:
        return "gemini"


class OpenAILLMClient(BaseLLMClient):
    """
    OpenAI client using the openai SDK.

    Requires: A11Y_OPENAI_API_KEY set in environment.
    """

    def __init__(self) -> None:
        from openai import AsyncOpenAI  # type: ignore

        key = settings.openai_api_key
        if not key:
            raise ValueError("A11Y_OPENAI_API_KEY is required for OpenAI provider.")

        self._client = AsyncOpenAI(api_key=key.get_secret_value())
        self._model = settings.llm_model if "gpt" in settings.llm_model else "gpt-4o-mini"
        log.info("llm_client.openai_initialized", model=self._model)

    async def generate(self, prompt: str, system: str = "") -> LLMResponse | None:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            log.debug("llm_client.generating", provider="openai", prompt_len=len(prompt))
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            log.info("llm_client.response_received",
                     provider="openai",
                     prompt_tokens=usage.prompt_tokens if usage else 0,
                     completion_tokens=usage.completion_tokens if usage else 0)
            return LLMResponse(text=text, model=self._model,
                               prompt_tokens=usage.prompt_tokens if usage else 0,
                               completion_tokens=usage.completion_tokens if usage else 0)
        except Exception as exc:
            log.error("llm_client.generate_failed", provider="openai", error=str(exc))
            return None

    @property
    def provider_name(self) -> str:
        return "openai"


class OllamaLLMClient(BaseLLMClient):
    """
    Local Ollama client using httpx.

    Requires: Ollama running on localhost (or configured A11Y_OLLAMA_BASE_URL).
    """

    def __init__(self) -> None:
        self._url = f"{settings.ollama_base_url.rstrip('/')}/api/generate"
        self._model = settings.llm_model
        log.info("llm_client.ollama_initialized", model=self._model, url=self._url)

    async def generate(self, prompt: str, system: str = "") -> LLMResponse | None:
        import httpx
        
        payload = {
            "model": self._model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": settings.llm_temperature,
                "num_predict": settings.llm_max_tokens,
            }
        }
        
        try:
            log.debug("llm_client.generating", provider="ollama", prompt_len=len(prompt))
            # Generous timeout for local execution
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(self._url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                
                text = data.get("response", "")
                prompt_tokens = data.get("prompt_eval_count", 0)
                completion_tokens = data.get("eval_count", 0)
                
                log.info("llm_client.response_received",
                         provider="ollama",
                         prompt_tokens=prompt_tokens,
                         completion_tokens=completion_tokens)
                return LLMResponse(text=text, model=self._model,
                                   prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        except Exception as exc:
            log.error("llm_client.generate_failed", provider="ollama", error=str(exc))
            return None

    @property
    def provider_name(self) -> str:
        return "ollama"


class GroqLLMClient(BaseLLMClient):
    """
    Groq cloud LPU client — runs LLaMA 3.1 at ~500 tokens/sec.

    Requires: A11Y_GROQ_API_KEYS set in environment.
    Get a free key at https://console.groq.com
    """

    def __init__(self) -> None:
        from groq import AsyncGroq  # type: ignore

        keys_str = settings.groq_api_keys
        if not keys_str:
            raise ValueError("A11Y_GROQ_API_KEYS is required for Groq provider.")

        self._keys = [k.strip() for k in keys_str.get_secret_value().split(",") if k.strip()]
        if not self._keys:
            raise ValueError("No valid Groq API keys found in A11Y_GROQ_API_KEYS.")

        self._current_key_idx = 0
        self._client = AsyncGroq(api_key=self._keys[self._current_key_idx], max_retries=0)
        self._model = settings.llm_model
        log.info("llm_client.groq_initialized", model=self._model, total_keys=len(self._keys))

    async def generate(self, prompt: str, system: str = "") -> LLMResponse | None:
        import asyncio

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        from groq import AsyncGroq

        # Two full passes: first try all keys, if all rate-limited wait 60s and retry
        for pass_num in range(2):
            max_retries = len(self._keys)
            for attempt in range(max_retries):
                try:
                    log.debug("llm_client.generating", provider="groq", prompt_len=len(prompt), key_idx=self._current_key_idx)
                    response = await self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        temperature=settings.llm_temperature,
                        max_tokens=settings.llm_max_tokens,
                    )
                    text = response.choices[0].message.content or ""
                    usage = response.usage
                    log.info("llm_client.response_received",
                             provider="groq",
                             prompt_tokens=usage.prompt_tokens if usage else 0,
                             completion_tokens=usage.completion_tokens if usage else 0)
                    return LLMResponse(
                        text=text,
                        model=self._model,
                        prompt_tokens=usage.prompt_tokens if usage else 0,
                        completion_tokens=usage.completion_tokens if usage else 0,
                    )
                except Exception as exc:
                    err_str = str(exc).lower()
                    # If we hit a 429 rate limit or similar limit, rotate the key
                    if "429" in err_str or "rate_limit" in err_str or "too many requests" in err_str:
                        log.warning("llm_client.rate_limit_hit", provider="groq", key_idx=self._current_key_idx)
                        # Rotate the key
                        self._current_key_idx = (self._current_key_idx + 1) % len(self._keys)
                        self._client = AsyncGroq(api_key=self._keys[self._current_key_idx], max_retries=0)
                        log.info("llm_client.key_rotated", new_key_idx=self._current_key_idx)
                        # Continue the loop to retry with the new key
                        continue
                    else:
                        # If it's a different error (e.g. 404 Model Not Found), don't retry, just fail
                        log.error("llm_client.generate_failed", provider="groq", error=str(exc))
                        return None

            # All keys exhausted on this pass
            if pass_num == 0:
                # First pass failed — wait 60 seconds for Groq rate limit to reset, then retry
                log.warning("llm_client.all_keys_exhausted_waiting", provider="groq", wait_seconds=60)
                await asyncio.sleep(60)
            else:
                # Second pass also failed — give up
                log.error("llm_client.all_keys_exhausted", provider="groq")

        return None

    @property
    def provider_name(self) -> str:
        return "groq"


def create_llm_client() -> BaseLLMClient:
    """
    Factory — returns the correct LLM client based on settings.
    Always returns a valid client (falls back to DisabledLLMClient).
    """
    if settings.llm_provider == LLMProvider.DISABLED:
        return DisabledLLMClient()
    elif settings.llm_provider == LLMProvider.GOOGLE:
        try:
            return GeminiLLMClient()
        except Exception as exc:
            log.error("llm_client.gemini_init_failed", error=str(exc))
            return DisabledLLMClient()
    elif settings.llm_provider == LLMProvider.OPENAI:
        try:
            return OpenAILLMClient()
        except Exception as exc:
            log.error("llm_client.openai_init_failed", error=str(exc))
            return DisabledLLMClient()
    elif settings.llm_provider == LLMProvider.OLLAMA:
        try:
            return OllamaLLMClient()
        except Exception as exc:
            log.error("llm_client.ollama_init_failed", error=str(exc))
            return DisabledLLMClient()
    elif settings.llm_provider == LLMProvider.GROQ:
        try:
            return GroqLLMClient()
        except Exception as exc:
            log.error("llm_client.groq_init_failed", error=str(exc))
            return DisabledLLMClient()
    return DisabledLLMClient()
