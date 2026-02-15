from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a technical research assistant. Only use provided evidence. "
    "If insufficient evidence, say so."
)
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True)
class LLMErrorDetails:
    code: str
    message: str
    retryable: bool
    status_code: int | None = None


class LLMServiceError(Exception):
    def __init__(self, details: LLMErrorDetails) -> None:
        self.details = details
        super().__init__(details.message)

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.details.code,
            "message": self.details.message,
            "retryable": self.details.retryable,
            "status_code": self.details.status_code,
        }


class OpenAIAnswerService:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "",
        temperature: float = 0.1,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
        stream: bool = True,
    ) -> None:
        if not api_key.strip():
            raise LLMServiceError(
                LLMErrorDetails(
                    code="missing_api_key",
                    message="OPENAI_API_KEY is required to use OpenAI answers.",
                    retryable=False,
                )
            )
        if not model.strip():
            raise LLMServiceError(
                LLMErrorDetails(
                    code="missing_model",
                    message="OPENAI_MODEL cannot be empty.",
                    retryable=False,
                )
            )
        if timeout_seconds <= 0:
            raise LLMServiceError(
                LLMErrorDetails(
                    code="invalid_timeout",
                    message="OpenAI timeout must be greater than 0.",
                    retryable=False,
                )
            )
        if max_retries < 0:
            raise LLMServiceError(
                LLMErrorDetails(
                    code="invalid_retries",
                    message="OpenAI max retries must be 0 or greater.",
                    retryable=False,
                )
            )

        self._api_key = api_key.strip()
        self._model = model.strip()
        self._base_url = base_url.strip()
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._stream = stream

    @classmethod
    def from_settings(cls) -> OpenAIAnswerService:
        return cls(
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_MODEL,
            base_url=settings.OPENAI_BASE_URL,
            temperature=settings.OPENAI_TEMPERATURE,
            stream=True,
        )

    def generate_answer(
        self,
        *,
        query: str,
        context_blocks: list[str],
        correction_prompt: str | None = None,
    ) -> str:
        if not context_blocks:
            raise LLMServiceError(
                LLMErrorDetails(
                    code="missing_context",
                    message="At least one context block is required.",
                    retryable=False,
                )
            )

        messages = self._build_messages(
            query=query,
            context_blocks=context_blocks,
            correction_prompt=correction_prompt,
        )
        client = self._build_client()

        for attempt in range(self._max_retries + 1):
            try:
                if self._stream:
                    return self._generate_streaming(client=client, messages=messages).strip()
                return self._generate_standard(client=client, messages=messages).strip()
            except Exception as exc:  # noqa: BLE001
                details = self._to_error_details(exc)
                if details.retryable and attempt < self._max_retries:
                    sleep_for = self._calculate_backoff(attempt=attempt)
                    logger.warning(
                        "OpenAI request failed (%s, status=%s). Retrying in %.2fs (%s/%s).",
                        details.code,
                        details.status_code,
                        sleep_for,
                        attempt + 1,
                        self._max_retries + 1,
                    )
                    time.sleep(sleep_for)
                    continue
                raise LLMServiceError(details) from exc

        raise LLMServiceError(
            LLMErrorDetails(
                code="request_failed",
                message="OpenAI request failed after retries.",
                retryable=False,
            )
        )

    def _build_client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMServiceError(
                LLMErrorDetails(
                    code="missing_dependency",
                    message="openai package is required for AI answers.",
                    retryable=False,
                )
            ) from exc

        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "timeout": self._timeout_seconds,
            "max_retries": 0,
        }
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return OpenAI(**kwargs)

    def _generate_standard(self, *, client: Any, messages: list[dict[str, str]]) -> str:
        response = client.chat.completions.create(
            model=self._model,
            temperature=self._temperature,
            messages=messages,
        )
        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMServiceError(
                LLMErrorDetails(
                    code="empty_response",
                    message="OpenAI chat completion returned no choices.",
                    retryable=False,
                )
            )

        content = choices[0].message.content
        if isinstance(content, str) and content.strip():
            return content

        raise LLMServiceError(
            LLMErrorDetails(
                code="empty_response",
                message="OpenAI chat completion returned empty content.",
                retryable=False,
            )
        )

    def _generate_streaming(self, *, client: Any, messages: list[dict[str, str]]) -> str:
        stream = client.chat.completions.create(
            model=self._model,
            temperature=self._temperature,
            messages=messages,
            stream=True,
        )

        chunks: list[str] = []
        for event in stream:
            choices = getattr(event, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:
                chunks.append(content)

        text = "".join(chunks).strip()
        if text:
            return text

        raise LLMServiceError(
            LLMErrorDetails(
                code="empty_response",
                message="OpenAI streaming completion returned empty content.",
                retryable=False,
            )
        )

    @staticmethod
    def _build_messages(
        *,
        query: str,
        context_blocks: list[str],
        correction_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        context_payload: list[dict[str, str]] = []
        for index, block in enumerate(context_blocks, start=1):
            parsed_block: dict[str, str] | None = None
            if isinstance(block, str):
                try:
                    candidate = json.loads(block)
                    if isinstance(candidate, dict):
                        parsed_block = {str(key): str(value) for key, value in candidate.items()}
                except json.JSONDecodeError:
                    parsed_block = None
            if parsed_block is not None:
                context_payload.append(parsed_block)
                continue
            context_payload.append({"id": str(index), "chunk": str(block)})

        user_prompt = (
            "USER INPUT:\n"
            f"Question: {query}\n"
            "Retrieved context (JSON array of chunks):\n"
            f"{json.dumps(context_payload, ensure_ascii=True)}\n\n"
            "REQUIRED OUTPUT JSON:\n"
            '{\n'
            '  "answer": "...",\n'
            '  "key_points": ["...", "..."],\n'
            '  "evidence_used": [\n'
            '    {"source": "...", "reason": "..."}\n'
            "  ],\n"
            '  "confidence": "high | medium | low",\n'
            '  "limitations": "..."\n'
            "}\n"
            "Rules: Use only provided evidence, do not hallucinate, and output valid JSON only."
        )
        if correction_prompt:
            user_prompt = f"{user_prompt}\n\nCorrection: {correction_prompt}"
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _calculate_backoff(self, *, attempt: int) -> float:
        base = self._backoff_seconds * (2**attempt)
        jitter = random.uniform(0.0, 0.35)
        return base + jitter

    @staticmethod
    def _to_error_details(exc: Exception) -> LLMErrorDetails:
        status_code = getattr(exc, "status_code", None)
        error_name = exc.__class__.__name__.lower()
        message = str(exc) or "OpenAI request failed."

        if status_code in {429, 500, 502, 503, 504}:
            return LLMErrorDetails(
                code="upstream_retryable",
                message=message,
                retryable=True,
                status_code=status_code,
            )
        if status_code in {400, 401, 403, 404, 422}:
            return LLMErrorDetails(
                code="upstream_request_error",
                message=message,
                retryable=False,
                status_code=status_code,
            )
        if "timeout" in error_name:
            return LLMErrorDetails(
                code="timeout",
                message=message,
                retryable=True,
                status_code=status_code,
            )
        if "rate" in error_name and "limit" in error_name:
            return LLMErrorDetails(
                code="rate_limit",
                message=message,
                retryable=True,
                status_code=status_code,
            )
        if "connection" in error_name:
            return LLMErrorDetails(
                code="connection_error",
                message=message,
                retryable=True,
                status_code=status_code,
            )
        return LLMErrorDetails(
            code="upstream_error",
            message=message,
            retryable=False,
            status_code=status_code,
        )
