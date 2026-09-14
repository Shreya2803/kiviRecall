import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError

from kivi.config import MODEL_PRICING, settings

logger = logging.getLogger(__name__)

Tier = Literal["extraction", "answer"]

_MAX_TOKENS = 4096
_MAX_RETRIES = 3
_SCHEMA_RETRIES = 1  # one retry with the validation error appended, then fail


class Completion(BaseModel):
    content: str
    tokens_in: int
    tokens_out: int
    model: str
    latency_ms: int
    cost_usd: float


class ProviderError(RuntimeError):
    """A provider call failed after retries, or its output failed schema
    validation twice."""


class ModelProvider(ABC):
    name: str

    @abstractmethod
    def model_for(self, tier: Tier) -> str: ...

    @abstractmethod
    async def _request(self, messages: list[dict], *, model: str, tier: Tier) -> tuple[str, int, int]:
        """One HTTP call. Returns (content, tokens_in, tokens_out).

        Raises httpx.HTTPStatusError / httpx.TransportError on failure — the
        caller (_call_with_retry) decides what's retryable.
        """

    async def complete(
        self,
        messages: list[dict],
        *,
        schema: type[BaseModel] | None = None,
        tier: Tier,
    ) -> Completion:
        model = self.model_for(tier)
        working_messages = list(messages)
        total_latency_ms = 0
        total_tokens_in = 0
        total_tokens_out = 0

        for schema_attempt in range(_SCHEMA_RETRIES + 1):
            start = time.monotonic()
            content, tokens_in, tokens_out = await self._call_with_retry(
                working_messages, model=model, tier=tier
            )
            total_latency_ms += int((time.monotonic() - start) * 1000)
            total_tokens_in += tokens_in
            total_tokens_out += tokens_out

            if schema is not None:
                try:
                    schema.model_validate_json(content)
                except ValidationError as exc:
                    if schema_attempt < _SCHEMA_RETRIES:
                        working_messages = working_messages + [
                            {"role": "assistant", "content": content},
                            {
                                "role": "user",
                                "content": (
                                    "Your last response did not match the required schema.\n\n"
                                    f"Validation error:\n{exc}\n\n"
                                    "Respond again with corrected JSON only, no markdown fences."
                                ),
                            },
                        ]
                        continue
                    raise ProviderError(
                        f"{self.name}: response failed schema validation twice: {exc}"
                    ) from exc

            return Completion(
                content=content,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                model=model,
                latency_ms=total_latency_ms,
                cost_usd=self._cost(model, total_tokens_in, total_tokens_out),
            )

        raise ProviderError(f"{self.name}: exhausted schema validation retries")  # unreachable

    async def _call_with_retry(
        self, messages: list[dict], *, model: str, tier: Tier
    ) -> tuple[str, int, int]:
        delay = 1.0
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await self._request(messages, model=model, tier=tier)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = status == 429 or 500 <= status < 600
                if retryable and attempt < _MAX_RETRIES:
                    logger.warning(
                        "%s: HTTP %s, retrying in %.1fs (attempt %d/%d)",
                        self.name,
                        status,
                        delay,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise ProviderError(f"{self.name}: HTTP {status}") from exc
            except httpx.TransportError as exc:
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "%s: %s, retrying in %.1fs (attempt %d/%d)",
                        self.name,
                        exc,
                        delay,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise ProviderError(
                    f"{self.name}: transport error after {_MAX_RETRIES + 1} attempts"
                ) from exc
        raise ProviderError(f"{self.name}: exhausted retries")  # unreachable

    def _cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        pricing = MODEL_PRICING.get(model)
        if pricing is None:
            logger.warning("%s: no pricing entry for model %s, cost_usd will be 0.0", self.name, model)
            return 0.0
        return (
            tokens_in / 1_000_000 * pricing.input_per_million
            + tokens_out / 1_000_000 * pricing.output_per_million
        )


class SarvamProvider(ModelProvider):
    name = "sarvam"

    def __init__(self, api_key: str, base_url: str):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60.0)

    def model_for(self, tier: Tier) -> str:
        return settings.extraction_model if tier == "extraction" else settings.answer_model

    async def _request(self, messages: list[dict], *, model: str, tier: Tier) -> tuple[str, int, int]:
        payload: dict = {"model": model, "messages": messages, "max_tokens": _MAX_TOKENS}
        if tier == "extraction":
            # CRITICAL: without this, thinking-mode reasoning tokens consume
            # max_tokens and content comes back empty with finish_reason "length".
            payload["reasoning_effort"] = None

        response = await self._client.post(
            f"{self._base_url}/v1/chat/completions",
            headers={"api-subscription-key": self._api_key, "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        reasoning_content = message.get("reasoning_content")

        if not content and choice.get("finish_reason") == "length":
            raise ProviderError(
                f"{self.name}: empty content with finish_reason=length — reasoning tokens likely "
                f"consumed max_tokens (reasoning_content {'present' if reasoning_content else 'absent'})"
            )

        usage = data.get("usage", {})
        return content, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


class GeminiProvider(ModelProvider):
    """OpenAI-compatible fallback, used when SARVAM_API_KEY is absent."""

    name = "gemini"

    def __init__(self, api_key: str, base_url: str):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60.0)

    def model_for(self, tier: Tier) -> str:
        return settings.gemini_extraction_model if tier == "extraction" else settings.gemini_answer_model

    async def _request(self, messages: list[dict], *, model: str, tier: Tier) -> tuple[str, int, int]:
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "max_tokens": _MAX_TOKENS},
        )
        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage", {})
        return content, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def get_provider() -> ModelProvider:
    """Sarvam if a key is configured, otherwise the Gemini fallback."""
    if settings.sarvam_api_key:
        return SarvamProvider(api_key=settings.sarvam_api_key, base_url=settings.sarvam_base_url)
    if settings.gemini_api_key:
        return GeminiProvider(api_key=settings.gemini_api_key, base_url=settings.gemini_base_url)
    raise ProviderError("No provider configured: set SARVAM_API_KEY or GEMINI_API_KEY")
