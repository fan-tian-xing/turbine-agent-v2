"""Shared OpenAI-compatible JSON chat transport for project LLM consumers."""

from __future__ import annotations

import json
import queue
import ssl
import socket
import threading
import time
from datetime import datetime, timezone
from collections.abc import Callable, Mapping
from urllib.error import HTTPError, URLError
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen


class LLMTransportError(ValueError):
    """Bounded transport/configuration failure without response or secret data."""

    def __init__(
        self,
        message: str,
        *,
        root_cause: str = "unknown",
        status_code: int | None = None,
        fallback_eligible: bool = False,
        attempts: int = 0,
        elapsed_seconds: float = 0.0,
    ):
        super().__init__(message)
        self.root_cause = root_cause
        self.status_code = status_code
        self.fallback_eligible = fallback_eligible
        self.attempts = attempts
        self.elapsed_seconds = round(elapsed_seconds, 3)


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
        transient_max_attempts: int | None = None,
        timeout_max_attempts: int = 1,
        opener: Callable = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not endpoint or not model or not api_key:
            raise LLMTransportError("LLM endpoint, model and credential are required")
        if timeout_seconds <= 0 or max_attempts < 1 or backoff_base_seconds < 0 or max_backoff_seconds < 0 or timeout_max_attempts < 1:
            raise LLMTransportError("LLM timeout and retry configuration are invalid")
        self.endpoint = endpoint.rstrip("/") if endpoint.endswith("/chat/completions") else endpoint.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.json_mode = json_mode
        self.backoff_base_seconds = backoff_base_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.transient_max_attempts = min(max_attempts, transient_max_attempts or max_attempts)
        self.timeout_max_attempts = min(max_attempts, timeout_max_attempts)
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

    @staticmethod
    def _failure_class(error: Exception) -> tuple[str, bool, int | None]:
        if isinstance(error, HTTPError):
            return f"http_{error.code}", error.code == 429 or error.code >= 500, error.code
        if isinstance(error, TimeoutError) and "deadline" in str(error).lower():
            return "request_deadline_exceeded", True, None
        if isinstance(error, (TimeoutError, socket.timeout)):
            return "read_timeout", True, None
        if isinstance(error, URLError):
            reason = error.reason
            if isinstance(reason, socket.gaierror):
                return "dns_resolution", True, None
            if isinstance(reason, ConnectionRefusedError):
                return "connection_refused", True, None
            if isinstance(reason, ConnectionResetError):
                return "connection_reset", True, None
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return "connect_timeout", True, None
            if isinstance(reason, ssl.SSLError):
                return "tls_error", True, None
            return "url_error", True, None
        return type(error).__name__.lower(), False, None

    def _attempt_limit(self, root_cause: str, status_code: int | None) -> int:
        if root_cause in {"dns_resolution", "connection_refused", "connection_reset", "connect_timeout", "tls_error", "url_error", "request_deadline_exceeded"}:
            return 1
        if root_cause == "read_timeout":
            return self.timeout_max_attempts
        if status_code is not None:
            return self.transient_max_attempts
        return self.max_attempts

    def _read_response_with_deadline(self, request: Request) -> bytes:
        """Bound opener/DNS stalls with a daemon worker deadline."""
        result: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    result.put(("ok", response.read()))
            except Exception as error:  # surfaced on the caller thread below
                result.put(("error", error))

        thread = threading.Thread(target=worker, name="stage12-llm-transport", daemon=True)
        thread.start()
        thread.join(self.timeout_seconds)
        if thread.is_alive():
            raise TimeoutError("LLM request deadline exceeded")
        try:
            kind, value = result.get_nowait()
        except queue.Empty as error:
            raise TimeoutError("LLM request returned no response") from error
        if kind == "error":
            raise value  # type: ignore[misc]
        return value  # type: ignore[return-value]

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
        started = time.monotonic()
        attempt_limit = self.max_attempts
        for attempt in range(self.max_attempts):
            request = Request(
                self.endpoint,
                data=body,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                payload = json.loads(self._read_response_with_deadline(request).decode("utf-8"))
                content = payload["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise LLMTransportError("LLM returned empty content")
                return content
            except HTTPError as error:
                last_error = error
                root_cause, retryable, status_code = self._failure_class(error)
                attempt_limit = self._attempt_limit(root_cause, status_code)
                if not retryable or attempt + 1 >= attempt_limit:
                    raise LLMTransportError(
                        f"LLM HTTP failure {error.code}", root_cause=root_cause,
                        status_code=status_code, fallback_eligible=retryable,
                        attempts=attempt + 1, elapsed_seconds=time.monotonic() - started,
                    ) from error
                self.sleeper(self._retry_delay(attempt, error))
            except (URLError, TimeoutError, socket.timeout, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
                last_error = error
                root_cause, retryable, status_code = self._failure_class(error)
                attempt_limit = self._attempt_limit(root_cause, status_code)
                if not retryable or attempt + 1 >= attempt_limit:
                    raise LLMTransportError(
                        f"LLM transport failure: {type(error).__name__}", root_cause=root_cause,
                        status_code=status_code, fallback_eligible=retryable,
                        attempts=attempt + 1, elapsed_seconds=time.monotonic() - started,
                    ) from error
                self.sleeper(self._retry_delay(attempt, error))
        raise LLMTransportError(
            f"LLM transport failed after {attempt_limit} attempts",
            root_cause="retry_exhausted", fallback_eligible=True,
            attempts=attempt_limit, elapsed_seconds=time.monotonic() - started,
        ) from last_error
