"""Synchronous Agent runtime boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any

from benchmark.contracts import (
    AgentRunResult,
    AgentRuntimeError,
    BenchmarkError,
    ComponentConfigError,
    DecisionContext,
    TerminationReason,
)

from .protocol import AgentBuildContext, AgentRuntimeEvent, AgentSelection
from .registry import AgentRegistry


class AgentRuntime:
    """Create and invoke an Agent in the caller's current worker thread."""

    def __init__(
        self,
        registry: AgentRegistry,
        build_context: AgentBuildContext,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._build_context = build_context
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        selection: AgentSelection,
        context: DecisionContext,
        *,
        deadline_at: datetime | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> AgentRunResult:
        if not isinstance(selection, AgentSelection):
            raise TypeError("selection must be AgentSelection")
        if not isinstance(context, DecisionContext):
            raise TypeError("context must be DecisionContext")
        if deadline_at is not None and (
            deadline_at.tzinfo is None or deadline_at.utcoffset() is None
        ):
            raise ValueError("deadline_at must be timezone-aware")

        registered = self._registry.get(selection.agent_id, selection.version)
        descriptor = registered.descriptor

        if is_cancelled is not None and is_cancelled():
            result = AgentRunResult(
                trace_id=context.trace_id,
                decision_round_id=context.decision_round_id,
                termination_reason=TerminationReason.CANCELLED,
                summary="Agent run cancelled before invocation.",
            )
            self._emit("agent.cancelled", descriptor.id, descriptor.version, context)
            return result
        if deadline_at is not None and self._clock() >= deadline_at:
            raise AgentRuntimeError(
                "agent deadline exceeded before invocation",
                code="AGENT_DEADLINE_EXCEEDED",
                details={"agent_id": descriptor.id, "trace_id": context.trace_id},
            )

        validation = self._registry.validate_config(
            descriptor.id,
            selection.config,
            descriptor.version,
        )
        if not validation.valid:
            raise ComponentConfigError(
                "agent config is invalid",
                code="AGENT_CONFIG_INVALID",
                details={
                    "agent_id": descriptor.id,
                    "errors": [
                        {"path": issue.path, "message": issue.message}
                        for issue in validation.errors
                    ],
                },
            )

        self._emit("agent.started", descriptor.id, descriptor.version, context)
        try:
            agent = registered.factory.create(
                self._build_context,
                validation.normalized_config,
            )
            if isawaitable(agent):
                self._close_awaitable(agent)
                raise AgentRuntimeError(
                    "AgentFactory.create() returned an awaitable",
                    code="ASYNC_AGENT_FACTORY_UNSUPPORTED",
                )
            run_method = getattr(agent, "run", None)
            if not callable(run_method):
                raise AgentRuntimeError(
                    "AgentFactory.create() did not return an Agent",
                    code="INVALID_AGENT_INSTANCE",
                )
            result = run_method(context)
            if isawaitable(result):
                self._close_awaitable(result)
                raise AgentRuntimeError(
                    "Agent.run() returned an awaitable; v1 requires a synchronous result",
                    code="ASYNC_AGENT_UNSUPPORTED",
                )
            self._validate_result(result, context)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BenchmarkError:
            self._emit("agent.failed", descriptor.id, descriptor.version, context)
            raise
        except Exception as exc:
            self._emit("agent.failed", descriptor.id, descriptor.version, context)
            raise AgentRuntimeError(
                "agent execution failed",
                details={
                    "agent_id": descriptor.id,
                    "trace_id": context.trace_id,
                    "error_type": type(exc).__name__,
                },
            ) from exc

        self._emit(
            "agent.completed",
            descriptor.id,
            descriptor.version,
            context,
            {"termination_reason": result.termination_reason.value},
        )
        return result

    def _validate_result(self, result: Any, context: DecisionContext) -> None:
        if not isinstance(result, AgentRunResult):
            raise AgentRuntimeError(
                "Agent.run() must return AgentRunResult",
                code="INVALID_AGENT_RESULT",
            )
        if result.trace_id != context.trace_id:
            raise AgentRuntimeError(
                "AgentRunResult trace_id does not match DecisionContext",
                code="AGENT_CONTEXT_MISMATCH",
            )
        if result.decision_round_id != context.decision_round_id:
            raise AgentRuntimeError(
                "AgentRunResult decision_round_id does not match DecisionContext",
                code="AGENT_CONTEXT_MISMATCH",
            )

    def _emit(
        self,
        event_type: str,
        agent_id: str,
        version: str,
        context: DecisionContext,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._build_context.events.emit(
            AgentRuntimeEvent(
                type=event_type,
                agent_id=agent_id,
                agent_version=version,
                trace_id=context.trace_id,
                decision_round_id=context.decision_round_id,
                occurred_at=self._clock(),
                metadata=metadata or {},
            )
        )

    @staticmethod
    def _close_awaitable(value: Any) -> None:
        close = getattr(value, "close", None)
        if callable(close):
            close()


__all__ = ["AgentRuntime"]
