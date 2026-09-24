"""Trace evaluation over repositories and the extension catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from benchmark.application.evaluation.tool_schema import resolve_recorded_tool, tool_name_candidates


class ToolSchemaResolution(BaseModel):
    requested_name: str
    name: str
    version: str | None = None
    status: str
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    reason: str | None = None

    model_config = {"populate_by_name": True}


class EvaluationResultDTO(BaseModel):
    account_id: int | None
    account_name: str | None
    trace_id: str | None
    decision_round_id: str | None
    component_versions: dict[str, str | None]
    tools: list[ToolSchemaResolution]


@dataclass(frozen=True)
class EvaluateTraceRequest:
    account_id: int
    trace_id: str


class EvaluationService:
    def __init__(self, uow_factory, runtime) -> None:
        self._uow_factory = uow_factory
        self._runtime = runtime

    def evaluate_trace(self, request: EvaluateTraceRequest) -> EvaluationResultDTO:
        with self._uow_factory() as uow:
            account = uow.accounts.get(request.account_id)
            steps = uow.traces.list_by_trace_id(request.trace_id)
            events = uow.traces.list_runtime_events(request.trace_id)
            account_name = account.name if account is not None else None
            account_id = account.id if account is not None else None
            step_rows = [
                {"role": step.role, "tool_calls": step.tool_calls}
                for step in steps
                if step.account_id == request.account_id
            ]
            event_rows = [
                {
                    "decision_round_id": event.decision_round_id,
                    "event_type": event.event_type,
                    "payload": event.payload,
                }
                for event in events
                if event.account_id == request.account_id
            ]
            trace_id = request.trace_id if step_rows or event_rows else None

        decision_round_id = next(
            (
                row["decision_round_id"]
                for row in event_rows
                if isinstance(row["decision_round_id"], str) and row["decision_round_id"]
            ),
            None,
        )
        versions = _component_versions(event_rows)
        tool_names = _tool_names(step_rows)
        recorded_versions = _recorded_tool_versions(event_rows)
        tools = [
            resolve_recorded_tool(self._runtime, name, recorded_versions, versions)
            for name in tool_names
        ]
        # A recorded TOOL_NOT_FOUND is evidence of a hallucinated call at that time,
        # even if today's catalog happens to contain the requested name.
        missing = _recorded_missing_tools(event_rows)
        for tool in tools:
            if any(name in missing for name in tool_name_candidates(tool['requested_name'])):
                tool.update(status='not_found', schema=None, reason='TOOL_NOT_FOUND')
        return EvaluationResultDTO(
            account_id=account_id,
            account_name=account_name,
            trace_id=trace_id,
            decision_round_id=decision_round_id,
            component_versions=versions,
            tools=[ToolSchemaResolution.model_validate(item) for item in tools],
        )


def _component_versions(events: list[dict[str, Any]]) -> dict[str, str | None]:
    for event in events:
        if event["event_type"] != "run.configured":
            continue
        payload = _json_object(event["payload"])
        raw = payload.get("component_versions")
        if not isinstance(raw, dict):
            return {}
        versions: dict[str, str | None] = {}
        for key, value in raw.items():
            versions[str(key)] = value if isinstance(value, str) and value else None
        return versions
    return {}


def _recorded_tool_versions(events: list[dict[str, Any]]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for event in events:
        payload = _json_object(event["payload"])
        name = payload.get("tool_name")
        version = payload.get("tool_version")
        if isinstance(name, str) and name and name not in versions:
            versions[name] = version if isinstance(version, str) and version else None
    return versions


def _tool_names(steps: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for step in steps:
        if step["role"] != "assistant":
            continue
        parsed = _json_value(step["tool_calls"])
        if not isinstance(parsed, list):
            continue
        for call in parsed:
            name = _call_name(call)
            if name and name not in names:
                names.append(name)
    return names


def _call_name(call: Any) -> str | None:
    if not isinstance(call, dict):
        return None
    function_block = call.get("function") if isinstance(call.get("function"), dict) else call
    name = function_block.get("name")
    return name if isinstance(name, str) and name else None


def _json_object(value: Any) -> dict[str, Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)) or value is None:
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _recorded_missing_tools(events):
    missing = set()
    for event in events:
        if event['event_type'] != 'tool.denied':
            continue
        payload = _json_object(event['payload'])
        metadata = payload.get('metadata') or {}
        if isinstance(metadata, dict) and metadata.get('error_code') == 'TOOL_NOT_FOUND':
            name = payload.get('tool_name')
            if isinstance(name, str):
                missing.add(name)
    return missing
