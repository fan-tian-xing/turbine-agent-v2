"""Shared OpenAI-compatible JSON chat transport for project LLM consumers."""

from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timezone
from collections.abc import Callable, Mapping
from urllib.error import HTTPError, URLError
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen


class LLMTransportError(ValueError):
    """Bounded transport/configuration failure without response or secret data."""


class OpenAICompatibleChatTransport:
    """Minimal configured chat-completions transport shared by project stages."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        max_attempts: int,
        json_mode: bool = False,
        backoff_base_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        opener: Callable = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not endpoint or not model or not api_key:
            raise LLMTransportError("LLM endpoint, model and credential are required")
        if timeout_seconds <= 0 or max_attempts < 1 or backoff_base_seconds < 0 or max_backoff_seconds < 0:
            raise LLMTransportError("LLM timeout and retry configuration are invalid")
        self.endpoint = endpoint.rstrip("/") if endpoint.endswith("/chat/completions") else endpoint.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.json_mode = json_mode
        self.backoff_base_seconds = backoff_base_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.opener = opener
        self.sleeper = sleeper

    def _retry_delay(self, attempt: int, error: HTTPError | Exception) -> float:
        retry_after = error.headers.get("Retry-After") if isinstance(error, HTTPError) and error.headers else None
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), self.max_backoff_seconds)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    return min(max(delay, 0.0), self.max_backoff_seconds)
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(self.backoff_base_seconds * (2 ** attempt), self.max_backoff_seconds)

    def __call__(self, prompt: Mapping[str, str]) -> str:
        request_payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
        }
        if self.json_mode:
            request_payload["response_format"] = {"type": "json_object"}
        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            request = Request(
                self.endpoint,
                data=body,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                content = payload["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise LLMTransportError("LLM returned empty content")
                return content
            except HTTPError as error:
                last_error = error
                retryable = error.code >= 500 or error.code == 429
                if not retryable or attempt + 1 >= self.max_attempts:
                    raise LLMTransportError(f"LLM HTTP failure {error.code}") from error
                self.sleeper(self._retry_delay(attempt, error))
            except (URLError, TimeoutError, socket.timeout, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
                last_error = error
                if attempt + 1 >= self.max_attempts:
                    raise LLMTransportError(f"LLM transport failure: {type(error).__name__}") from error
                self.sleeper(self._retry_delay(attempt, error))
        raise LLMTransportError(f"LLM transport failed after {self.max_attempts} attempts") from last_error
