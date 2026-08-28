"""Synchronous Tool invocation pipeline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from benchmark.contracts import (
    BenchmarkError,
    ComponentConfigError,
    ComponentNotFoundError,
    JsonValue,
    SideEffect,
    ToolContext,
    ToolResult,
    ToolRuntimeError,
    ToolSpec,
    to_jsonable,
)

from .protocol import (
    NullToolEventSink,
    RegisteredTool,
    ToolCache,
    ToolEventSink,
    ToolRuntimeEvent,
)
from .registry import ToolRegistry, ToolView
from .validation import validation_messages

_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "proxyauthorization",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "token",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "xapikey",
        "cookie",
        "setcookie",
    }
)
_WRITE_SIDE_EFFECTS = frozenset(
    {
        SideEffect.MEMORY_WRITE,
        SideEffect.SANDBOX_WRITE,
        SideEffect.TRADING_WRITE,
    }
)


def _normalized_sensitive_key(value: object) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _redact_url_credentials(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value

    changed = False
    netloc = parsed.netloc
    if "@" in netloc:
        netloc = f"[REDACTED]@{netloc.rsplit('@', 1)[1]}"
        changed = True

    query_items = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if _normalized_sensitive_key(key) in _SENSITIVE_KEYS:
            query_items.append((key, "[REDACTED]"))
            changed = True
        else:
            query_items.append((key, item))
    if not changed:
        return value
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            urlencode(query_items, doseq=True, safe="[]"),
            parsed.fragment,
        )
    )


def redact_tool_value(value: JsonValue) -> JsonValue:
    """Redact common credential fields before values enter runtime events."""

    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _normalized_sensitive_key(key) in _SENSITIVE_KEYS
                else redact_tool_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_tool_value(item) for item in value]
    if isinstance(value, str):
        return _redact_url_credentials(value)
    return value


class SynchronousToolInvoker:
    """Resolve, authorize, validate, and invoke Tools in the caller thread."""

    def __init__(
        self,
        registry: ToolRegistry | ToolView,
        *,
        account_id: int,
        decision_round_id: str,
        trace_id: str,
        capabilities: frozenset[str] | None = None,
        enabled_tools: tuple[str, ...] | None = None,
        cache: ToolCache | None = None,
        cache_ttl_seconds: int = 300,
        events: ToolEventSink | None = None,
        deadline_at: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        call_id_factory: Callable[[], str] | None = None,
        redactor: Callable[[JsonValue], JsonValue] | None = None,
    ) -> None:
        if isinstance(registry, ToolView):
            if capabilities is not None or enabled_tools is not None:
                raise TypeError(
                    "capabilities and enabled_tools must be configured on ToolView"
                )
            view = registry
        elif isinstance(registry, ToolRegistry):
            granted = frozenset() if capabilities is None else capabilities
            view = registry.view(granted, enabled_tools)
        else:
            raise TypeError("registry must be ToolRegistry or ToolView")
        if not isinstance(account_id, int) or account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        if not isinstance(decision_round_id, str) or not decision_round_id:
            raise ValueError("decision_round_id must be a non-empty string")
        if not isinstance(trace_id, str) or not trace_id:
            raise ValueError("trace_id must be a non-empty string")
        if not isinstance(cache_ttl_seconds, int) or cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be a positive integer")
        if deadline_at is not None and (
            deadline_at.tzinfo is None or deadline_at.utcoffset() is None
        ):
            raise ValueError("deadline_at must be timezone-aware")
        self._view = view
        self._account_id = account_id
        self._decision_round_id = decision_round_id
        self._trace_id = trace_id
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds
        self._events = events or NullToolEventSink()
        self._deadline_at = deadline_at
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic_clock or monotonic
        self._call_id_factory = call_id_factory or (lambda: f"tool-{uuid4().hex}")
        self._redactor = redactor or redact_tool_value

    def list_specs(self) -> tuple[ToolSpec, ...]:
        """Return the active, authorized Tool specs visible to this runtime."""

        return self._view.list()

    def call(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> ToolResult:
        """Run the complete synchronous Tool pipeline for one invocation."""

        call_id = self._call_id_factory()
        if not isinstance(call_id, str) or not call_id:
            raise ToolRuntimeError(
                "Tool call id factory returned an invalid id",
                code="TOOL_CALL_ID_INVALID",
            )
        raw_entry: RegisteredTool | None = None
        try:
            raw_entry = self._view.registry.get(name)
            entry = self._view.get(name)
        except ComponentNotFoundError as exc:
            self._emit_denied(name, raw_entry, call_id, exc)
            return ToolResult(
                ok=False,
                error_code=exc.code,
                error_message=exc.message,
            )
        except ComponentConfigError as exc:
            self._emit_denied(name, raw_entry, call_id, exc)
            return ToolResult(
                ok=False,
                error_code=exc.code,
                error_message=exc.message,
            )

        if not isinstance(arguments, Mapping):
            result = ToolResult(
                ok=False,
                error_code="TOOL_INPUT_INVALID",
                error_message="Tool arguments must be an object",
            )
            self._emit_result("tool.denied", entry, call_id, {}, result)
            return result

        try:
            normalized = to_jsonable(arguments)
        except (TypeError, ValueError):
            result = ToolResult(
                ok=False,
                error_code="TOOL_INPUT_INVALID",
                error_message="Tool arguments must contain JSON-compatible values",
            )
            self._emit_result("tool.denied", entry, call_id, {}, result)
            return result
        if not isinstance(normalized, dict):
            raise AssertionError("a Mapping must normalize to a JSON object")

        input_errors = validation_messages(entry.spec.input_schema, normalized)
        if input_errors:
            result = ToolResult(
                ok=False,
                error_code="TOOL_INPUT_INVALID",
                error_message="Tool arguments do not match the input schema",
                metadata={"errors": list(input_errors)},
            )
            self._emit_result("tool.denied", entry, call_id, normalized, result)
            return result

        invocation_started_at = self._clock()
        if self._deadline_at is not None and invocation_started_at >= self._deadline_at:
            result = ToolResult(
                ok=False,
                error_code="TOOL_DEADLINE_EXCEEDED",
                error_message="Tool deadline exceeded before invocation",
                retryable=True,
            )
            self._emit_result("tool.failed", entry, call_id, normalized, result)
            return result

        tool_deadline_at = invocation_started_at + timedelta(
            seconds=float(entry.spec.timeout_seconds)
        )
        effective_deadline_at = (
            min(tool_deadline_at, self._deadline_at)
            if self._deadline_at is not None
            else tool_deadline_at
        )

        context = ToolContext(
            account_id=self._account_id,
            decision_round_id=self._decision_round_id,
            trace_id=self._trace_id,
            call_id=call_id,
            capabilities=self._view.capabilities,
            deadline_at=effective_deadline_at,
        )
        cached = self._get_cached(entry, normalized)
        if self._clock() >= effective_deadline_at:
            result = ToolResult(
                ok=False,
                error_code="TOOL_DEADLINE_EXCEEDED",
                error_message="Tool deadline exceeded during cache lookup",
                retryable=True,
            )
            self._emit_result("tool.failed", entry, call_id, normalized, result)
            return result
        if cached is not None:
            self._emit_result("tool.cache_hit", entry, call_id, normalized, cached)
            return cached

        self._emit("tool.started", entry, call_id, {"arguments": normalized})
        started_at = self._monotonic()
        try:
            result = entry.tool.invoke(context, normalized)
            if isawaitable(result):
                self._close_awaitable(result)
                raise ToolRuntimeError(
                    "Tool.invoke() returned an awaitable; v1 requires a synchronous result",
                    code="ASYNC_TOOL_UNSUPPORTED",
                    details={"tool_name": entry.spec.name},
                )
            if not isinstance(result, ToolResult):
                raise ToolRuntimeError(
                    "Tool.invoke() must return ToolResult",
                    code="INVALID_TOOL_RESULT",
                    details={"tool_name": entry.spec.name},
                )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ToolRuntimeError as exc:
            self._emit(
                "tool.failed",
                entry,
                call_id,
                {"error_code": exc.code},
            )
            raise
        except BenchmarkError as exc:
            self._emit(
                "tool.failed",
                entry,
                call_id,
                {"error_code": exc.code},
            )
            raise ToolRuntimeError(
                "Tool execution failed",
                code="TOOL_INVOKE_FAILED",
                details={"tool_name": entry.spec.name},
            ) from exc
        except Exception as exc:
            self._emit(
                "tool.failed",
                entry,
                call_id,
                {"error_type": type(exc).__name__},
            )
            raise ToolRuntimeError(
                "Tool execution failed",
                code="TOOL_INVOKE_FAILED",
                details={"tool_name": entry.spec.name},
            ) from exc

        elapsed = self._monotonic() - started_at
        effective_timeout_seconds = max(
            0.0,
            (effective_deadline_at - invocation_started_at).total_seconds(),
        )
        deadline_exceeded = self._clock() >= effective_deadline_at
        if elapsed > effective_timeout_seconds or deadline_exceeded:
            if entry.spec.side_effect in _WRITE_SIDE_EFFECTS:
                result = self._with_timeout_metadata(
                    result,
                    effective_timeout_seconds,
                    elapsed,
                )
            else:
                timeout_result = ToolResult(
                    ok=False,
                    error_code="TOOL_TIMEOUT",
                    error_message="Tool exceeded its synchronous timeout",
                    retryable=True,
                    metadata={
                        "timeout_seconds": effective_timeout_seconds,
                        "elapsed_seconds": elapsed,
                    },
                )
                self._emit_result(
                    "tool.failed",
                    entry,
                    call_id,
                    normalized,
                    timeout_result,
                )
                return timeout_result

        try:
            to_jsonable(result)
        except (TypeError, ValueError) as exc:
            self._emit(
                "tool.failed",
                entry,
                call_id,
                {"error_code": "TOOL_OUTPUT_INVALID"},
            )
            raise ToolRuntimeError(
                "Tool result is not JSON-compatible",
                code="TOOL_OUTPUT_INVALID",
                details={"tool_name": entry.spec.name},
            ) from exc

        if result.ok:
            output = to_jsonable(result.value)
            output_errors = validation_messages(entry.spec.output_schema, output)
            if output_errors:
                self._emit(
                    "tool.failed",
                    entry,
                    call_id,
                    {"error_code": "TOOL_OUTPUT_INVALID"},
                )
                raise ToolRuntimeError(
                    "Tool result does not match the output schema",
                    code="TOOL_OUTPUT_INVALID",
                    details={
                        "tool_name": entry.spec.name,
                        "errors": list(output_errors),
                    },
                )
            self._set_cached(entry, normalized, result)

        self._emit_result("tool.completed", entry, call_id, normalized, result)
        return result

    @staticmethod
    def _with_timeout_metadata(
        result: ToolResult,
        timeout_seconds: float,
        elapsed: float,
    ) -> ToolResult:
        metadata = dict(result.metadata)
        metadata.update(
            {
                "timeout_exceeded": True,
                "timeout_seconds": timeout_seconds,
                "elapsed_seconds": elapsed,
            }
        )
        return ToolResult(
            ok=result.ok,
            value=result.value,
            error_code=result.error_code,
            error_message=result.error_message,
            retryable=result.retryable,
            metadata=metadata,
        )

    def _cache_eligible(self, entry: RegisteredTool) -> bool:
        return bool(
            self._cache is not None
            and entry.spec.side_effect is SideEffect.READ_ONLY
            and entry.spec.cacheable
        )

    def _cache_namespace(self, entry: RegisteredTool) -> str:
        return (
            f"account:{self._account_id}:"
            f"{entry.extension.id}@{entry.extension.version}:{entry.spec.name}"
        )

    def _get_cached(
        self,
        entry: RegisteredTool,
        arguments: dict[str, JsonValue],
    ) -> ToolResult | None:
        if not self._cache_eligible(entry):
            return None
        assert self._cache is not None
        try:
            value = self._cache.get(
                self._cache_namespace(entry),
                arguments,
                round_id=self._decision_round_id,
            )
            result = self._decode_cached(value)
            if result is None:
                return None
            output = to_jsonable(result.value)
            if validation_messages(entry.spec.output_schema, output):
                return None
            return result
        except Exception:
            return None

    def _set_cached(
        self,
        entry: RegisteredTool,
        arguments: dict[str, JsonValue],
        result: ToolResult,
    ) -> None:
        if not self._cache_eligible(entry):
            return
        assert self._cache is not None
        try:
            self._cache.set(
                self._cache_namespace(entry),
                arguments,
                to_jsonable(result),
                ttl_seconds=self._cache_ttl_seconds,
                round_id=self._decision_round_id,
            )
        except Exception:
            return

    @staticmethod
    def _decode_cached(value: object) -> ToolResult | None:
        if not isinstance(value, Mapping) or not value.get("ok"):
            return None
        try:
            return ToolResult(
                ok=True,
                value=value.get("value"),
                retryable=bool(value.get("retryable", False)),
                metadata=value.get("metadata", {}),
            )
        except (TypeError, ValueError):
            return None

    def _emit_denied(
        self,
        name: str,
        entry: RegisteredTool | None,
        call_id: str,
        error: BenchmarkError,
    ) -> None:
        if entry is None:
            self._events.emit(
                ToolRuntimeEvent(
                    type="tool.denied",
                    account_id=self._account_id,
                    component=None,
                    tool_name=name,
                    tool_version="unknown",
                    trace_id=self._trace_id,
                    decision_round_id=self._decision_round_id,
                    call_id=call_id,
                    occurred_at=self._clock(),
                    metadata={"error_code": error.code},
                )
            )
            return
        self._emit(
            "tool.denied",
            entry,
            call_id,
            {"error_code": error.code},
        )

    def _emit_result(
        self,
        event_type: str,
        entry: RegisteredTool,
        call_id: str,
        arguments: dict[str, JsonValue],
        result: ToolResult,
    ) -> None:
        result_value = to_jsonable(result)
        self._emit(
            event_type,
            entry,
            call_id,
            {
                "arguments": self._redactor(arguments),
                "result": self._redactor(result_value),
            },
        )

    def _emit(
        self,
        event_type: str,
        entry: RegisteredTool,
        call_id: str,
        metadata: dict[str, JsonValue],
    ) -> None:
        self._events.emit(
            ToolRuntimeEvent(
                type=event_type,
                account_id=self._account_id,
                component=entry.extension,
                tool_name=entry.spec.name,
                tool_version=entry.extension.version,
                trace_id=self._trace_id,
                decision_round_id=self._decision_round_id,
                call_id=call_id,
                occurred_at=self._clock(),
                metadata=self._redactor(metadata),
            )
        )

    @staticmethod
    def _close_awaitable(value: Any) -> None:
        close = getattr(value, "close", None)
        if callable(close):
            close()


__all__ = ["SynchronousToolInvoker", "redact_tool_value"]
