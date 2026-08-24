"""Adapter from the existing OpenAI-compatible client to ``LLMClientPort``."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

from benchmark.contracts import JsonValue, to_jsonable
from benchmark.providers import (
    HealthStatus,
    HEALTHCHECK_TIMEOUT_SECONDS,
    LLMClientPort,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    ProviderError,
)
from benchmark.providers.runtime import require_sync_result


class LegacyLLMClientAdapter(LLMClientPort):
    """Keep legacy retry/provider normalization while exposing SDK-free DTOs."""

    version = "1.0.0"
    capabilities = ("llm.complete", "llm.tools")
    config_schema: Mapping[str, JsonValue] = {
        "type": "object",
        "additionalProperties": False,
    }

    def __init__(
        self, client: Any, *, provider_id: str = "core.llm.openai-compatible"
    ) -> None:
        if not callable(getattr(client, "call", None)):
            raise TypeError("client must expose call()")
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("provider_id must be a non-empty string")
        self._client = client
        self.id = provider_id
        configured_model = getattr(client, "model", None)
        self.model = configured_model if isinstance(configured_model, str) else ""

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not isinstance(request, LLMRequest):
            raise TypeError("request must be LLMRequest")
        configured_model = getattr(self._client, "model", None)
        if (
            configured_model
            and request.model is not None
            and request.model != configured_model
        ):
            raise ProviderError(
                "LLM request model does not match the configured client",
                code="LLM_MODEL_MISMATCH",
                provider_id=self.id,
                details={"requested_model": request.model},
            )
        unsupported = sorted(
            set(request.metadata).difference({"timeout_seconds", "response_format"})
        )
        if unsupported:
            raise ProviderError(
                "LLM request contains unsupported metadata options",
                code="LLM_OPTION_UNSUPPORTED",
                provider_id=self.id,
                details={"options": unsupported},
            )
        timeout = request.metadata.get("timeout_seconds")
        if timeout is not None and (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
        ):
            raise ProviderError(
                "timeout_seconds must be a positive number",
                code="LLM_OPTION_INVALID",
                provider_id=self.id,
                details={"option": "timeout_seconds"},
            )
        response_format = request.metadata.get("response_format")
        if response_format is not None and not isinstance(response_format, Mapping):
            raise ProviderError(
                "response_format must be an object",
                code="LLM_OPTION_INVALID",
                provider_id=self.id,
                details={"option": "response_format"},
            )
        try:
            message = self._client.call(
                messages=[dict(item) for item in request.messages],
                tools=[dict(item) for item in request.tools] or None,
                timeout=timeout,
                response_format=(
                    None if response_format is None else dict(response_format)
                ),
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            message = require_sync_result(
                message,
                provider_id=self.id,
                operation="complete",
            )
            return self._to_response(message)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise _llm_failure(self.id, "complete", exc) from exc

    def healthcheck(self) -> HealthStatus:
        tester = getattr(self._client, "test_connection", None)
        if not callable(tester):
            raise ProviderError(
                "LLM client does not expose a read-only connection test",
                code="LLM_HEALTHCHECK_UNSUPPORTED",
                provider_id=self.id,
            )
        try:
            result = require_sync_result(
                tester(timeout_seconds=HEALTHCHECK_TIMEOUT_SECONDS),
                provider_id=self.id,
                operation="healthcheck",
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise _llm_failure(self.id, "healthcheck", exc) from exc
        if not isinstance(result, str):
            raise ProviderError(
                "LLM healthcheck returned an invalid result",
                code="PROVIDER_RESULT_INVALID",
                provider_id=self.id,
                details={"operation": "healthcheck"},
            )
        return HealthStatus("ok", self.id)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    @property
    def legacy_client(self) -> Any:
        """M04 transition hook; public Agents must not depend on this property."""

        return self._client

    def _to_response(self, message: Any) -> LLMResponse:
        extractor = getattr(self._client, "extract_text_content", None)
        if not callable(extractor):
            raise ProviderError(
                "LLM client cannot normalize message content",
                code="PROVIDER_RESULT_INVALID",
                provider_id=self.id,
                details={"operation": "complete"},
            )
        content = extractor(message)
        if not isinstance(content, str):
            raise ProviderError(
                "LLM client returned non-text content",
                code="PROVIDER_RESULT_INVALID",
                provider_id=self.id,
                details={"operation": "complete"},
            )
        tool_calls = getattr(message, "tool_calls", None)
        if isinstance(message, Mapping):
            tool_calls = message.get("tool_calls")
        normalized_calls = tuple(
            self._to_tool_call(item) for item in (tool_calls or ())
        )
        raw: Mapping[str, JsonValue] = {}
        model_dump = getattr(message, "model_dump", None)
        if callable(model_dump):
            dumped = to_jsonable(model_dump(mode="json", exclude_none=True))
            if not isinstance(dumped, dict):
                raise ProviderError(
                    "LLM message serialization did not produce an object",
                    code="PROVIDER_RESULT_INVALID",
                    provider_id=self.id,
                )
            raw = dumped
        elif isinstance(message, Mapping):
            dumped = to_jsonable(message)
            if not isinstance(dumped, dict):
                raise ProviderError(
                    "LLM message serialization did not produce an object",
                    code="PROVIDER_RESULT_INVALID",
                    provider_id=self.id,
                )
            raw = dumped
        return LLMResponse(content=content, tool_calls=normalized_calls, raw=raw)

    def _to_tool_call(self, value: Any) -> LLMToolCall:
        parser = getattr(self._client, "tool_call_parts", None)
        if not callable(parser):
            raise ProviderError(
                "LLM client cannot normalize tool calls",
                code="PROVIDER_RESULT_INVALID",
                provider_id=self.id,
            )
        call_id, name, arguments_text = parser(value)
        try:
            # Strict JSON: reject NaN/Infinity literals and non-finite
            # numbers such as 1e999 instead of silently producing inf.
            arguments = json.loads(
                arguments_text,
                parse_constant=_reject_json_constant,
                parse_float=_parse_finite_float,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                "LLM tool-call arguments are not valid JSON",
                code="LLM_TOOL_ARGUMENTS_INVALID",
                provider_id=self.id,
                details={"tool_name": name},
            ) from exc
        if not isinstance(arguments, dict):
            raise ProviderError(
                "LLM tool-call arguments must be a JSON object",
                code="LLM_TOOL_ARGUMENTS_INVALID",
                provider_id=self.id,
                details={"tool_name": name},
            )
        return LLMToolCall(id=call_id, name=name, arguments=arguments)


def _reject_json_constant(name: str) -> float:
    raise ValueError(f"non-standard JSON constant {name!r} is not allowed")


def _parse_finite_float(text: str) -> float:
    result = float(text)
    if not math.isfinite(result):
        raise ValueError(f"non-finite JSON number {text!r} is not allowed")
    return result


def _llm_failure(provider_id: str, operation: str, exc: Exception) -> ProviderError:
    name = type(exc).__name__.lower()
    status_code = getattr(exc, "status_code", None)
    if "timeout" in name:
        code, retryable = "LLM_TIMEOUT", True
    elif "ratelimit" in name or status_code == 429:
        code, retryable = "LLM_RATE_LIMITED", True
    elif "connection" in name:
        code, retryable = "LLM_CONNECTION_FAILED", True
    elif "authentication" in name or status_code in {401, 403}:
        code, retryable = "LLM_AUTH_FAILED", False
    else:
        code, retryable = "LLM_PROVIDER_FAILED", False
    return ProviderError(
        f"LLM provider operation failed: {operation}",
        code=code,
        retryable=retryable,
        provider_id=provider_id,
        details={"operation": operation, "error_type": type(exc).__name__},
    )


__all__ = ["LegacyLLMClientAdapter"]
