from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from config.agent_config import AgentConfig
from .llm_client import LLMClient
from .tools import Tool, ToolRegistry

logger = logging.getLogger(__name__)
tool_selection_logger = logging.getLogger("tool_selection")
llm_trace_logger = logging.getLogger("llm_trace")
tool_selector_trace_logger = logging.getLogger("tool_selector_trace")

META_TOOL_NAME = "select_tools"
TOOL_SELECTOR_RESPONSE_FORMAT = {"type": "json_object"}

REQUIRED_TOOL_NAMES = [
    "get_market_snapshot",
    "get_kline_history",
    "get_account_state",
    "get_history_decisions",
    "execute_trade",
]

REQUIRED_TOOLS_TEXT = ", ".join(REQUIRED_TOOL_NAMES)

TOOL_SELECTOR_PROMPT = (
    "You are a tool-routing assistant. "
    "Given the agent conversation context and a full list of tool schemas, "
    "choose the most useful tools for the next step. "
    f"Required tools ({REQUIRED_TOOLS_TEXT}) are already selected and must always be included. "
    "You MUST return at least {min_k} tool names. "
    "Return ONLY valid JSON with the structure: "
    "{{\"selected_tools\": [\"tool_name\", ...]}}.\n"
    "Rules:\n"
    "- Choose only tool names that appear in the provided tool list.\n"
    "- Do NOT include duplicate tool names.\n"
    "- Do not include explanations or extra fields.\n"
)


def _compact_messages(messages: List[Dict[str, Any]], limit: int = 30) -> List[Dict[str, Any]]:
    trimmed = messages[-limit:] if len(messages) > limit else messages
    compact = []
    for msg in trimmed:
        compact.append(
            {
                "role": msg.get("role"),
                "content": msg.get("content"),
                "name": msg.get("name"),
            }
        )
    return compact


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _available_tool_names(tool_schemas: Iterable[Dict[str, Any]]) -> List[str]:
    names = []
    for entry in tool_schemas:
        func = (entry or {}).get("function") or {}
        name = func.get("name")
        if name:
            names.append(name)
    return names


def _stringify_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _truncate_text(text: str, max_chars: int = 2000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def _format_context_for_prompt(messages: List[Dict[str, Any]], limit: int = 30) -> str:
    compact = _compact_messages(messages, limit=limit)
    formatted: List[str] = []

    for idx, msg in enumerate(compact, start=1):
        role = (msg.get("role") or "unknown").upper()
        name = msg.get("name")
        header = f"{idx}. {role}"
        if name:
            header += f" ({name})"
        content = _truncate_text(_stringify_message_content(msg.get("content")).strip() or "[empty]")
        formatted.append(f"{header}:\n{content}")

    return "\n\n".join(formatted) if formatted else "[no recent context]"


def _format_tool_candidates(tool_schemas: Iterable[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for entry in tool_schemas:
        func = (entry or {}).get("function") or {}
        name = func.get("name")
        if not name:
            continue
        description = (func.get("description") or "").strip()
        description = _truncate_text(description, max_chars=240)
        lines.append(f"- {name}: {description}" if description else f"- {name}")
    return "\n".join(lines)


def _build_selector_user_prompt(
    source_messages: List[Dict[str, Any]],
    tool_schemas: List[Dict[str, Any]],
    min_k: int,
) -> str:
    context_block = _format_context_for_prompt(source_messages)
    tool_block = _format_tool_candidates(tool_schemas)
    return (
        "Conversation context:\n"
        f"{context_block}\n\n"
        "Available tools (choose only from these exact names):\n"
        f"{tool_block}\n\n"
        f"Required tools already selected and always included: {REQUIRED_TOOLS_TEXT}\n"
        f"Return at least {min_k} unique tool names in total.\n"
        "Respond with JSON only."
    )


def _sanitize_trace_for_storage(trace: Optional[Dict[str, Any]], selected_tools: Optional[Iterable[str]] = None) -> Optional[Dict[str, Any]]:
    if not trace:
        return trace

    sanitized = dict(trace)
    selected_list = [str(name) for name in (selected_tools or [])]
    request_messages = []

    for message in trace.get("request") or []:
        if not isinstance(message, dict):
            request_messages.append(message)
            continue

        sanitized_message = dict(message)
        content = sanitized_message.get("content")
        payload = _extract_json(content) if isinstance(content, str) else None

        if isinstance(payload, dict) and "tools" in payload:
            payload = dict(payload)
            payload.pop("tools", None)
            if selected_list:
                payload["selected_tools"] = selected_list
            sanitized_message["content"] = json.dumps(payload, ensure_ascii=False)
        elif isinstance(content, str) and "Available tools (choose only from these exact names):" in content:
            sanitized_content = re.sub(
                r"Available tools \(choose only from these exact names\):\n.*?\n\nRequired tools already selected and always included:",
                "Available tools (choose only from these exact names): [omitted]\n\nRequired tools already selected and always included:",
                content,
                flags=re.DOTALL,
            )
            if selected_list:
                sanitized_content += f"\nSelected tools: {', '.join(selected_list)}"
            sanitized_message["content"] = sanitized_content

        request_messages.append(sanitized_message)

    sanitized["request"] = request_messages
    if selected_list:
        sanitized["selected_tools"] = selected_list
    return sanitized


def select_tools_with_llm(
    llm: LLMClient,
    messages: List[Dict[str, Any]],
    tool_schemas: List[Dict[str, Any]],
    min_k: int,
    agent_name: str,
) -> Dict[str, Any]:
    max_retries = getattr(AgentConfig, "TOOL_SELECTOR_MAX_RETRIES", 10)
    last_trace = None
    last_selected: List[str] = []
    source_messages = list(messages)

    for attempt in range(max_retries):
        system_prompt = TOOL_SELECTOR_PROMPT.format(min_k=min_k)
        selector_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": _build_selector_user_prompt(source_messages, tool_schemas, min_k),
            },
        ]
        if attempt > 0:
            selector_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous selection did not contain at least {min_k} tools or contained duplicates. "
                        f"Please select at least {min_k} unique tool names."
                    ),
                }
            )

        response = llm.call(
            selector_messages,
            tools=None,
            response_format=TOOL_SELECTOR_RESPONSE_FORMAT,
        )
        content = response.content or ""
        raw_trace = {
            "type": "tool_selector",
            "agent": agent_name,
            "attempt": attempt + 1,
            "request": selector_messages,
            "response": content,
        }
        last_trace = _sanitize_trace_for_storage(raw_trace)

        parsed = _extract_json(content)
        selected = parsed.get("selected_tools") if isinstance(parsed, dict) else None
        trace = _sanitize_trace_for_storage(raw_trace, selected)
        last_trace = trace
        try:
            llm_trace_logger.info(json.dumps(trace, ensure_ascii=False))
        except Exception:
            llm_trace_logger.info("Tool selector LLM trace logged")
        try:
            tool_selector_trace_logger.info(json.dumps(trace, ensure_ascii=False))
        except Exception:
            tool_selector_trace_logger.info("Tool selector trace logged")
        if not parsed or "selected_tools" not in parsed:
            logger.warning("Tool selection LLM response parse failed, retrying.")
            continue
        selected = parsed.get("selected_tools") or []
        if not isinstance(selected, list):
            logger.warning("Tool selection LLM response invalid list, retrying.")
            continue
        last_selected = [str(name) for name in selected]
        if len(last_selected) != len(set(last_selected)):
            logger.warning("Tool selection contains duplicates, retrying.")
            continue
        if len(last_selected) >= min_k:
            return {"selected_tools": last_selected, "llm_trace": _sanitize_trace_for_storage(raw_trace, last_selected)}

    return {"selected_tools": last_selected, "llm_trace": last_trace}


def _apply_tool_selection(
    registry: ToolRegistry,
    selected_tools: Iterable[str],
    include_meta: bool = False,
):
    available = set(_available_tool_names(registry.openai_tools_all))

    selected_filtered = [name for name in selected_tools if name in available]
    required = [name for name in REQUIRED_TOOL_NAMES if name in available]

    combined = []
    combined.extend(required)
    for name in selected_filtered:
        if name not in combined:
            combined.append(name)

    # Optionally keep the routing tool in the active list.
    if include_meta and META_TOOL_NAME in registry.tools and META_TOOL_NAME not in combined:
        combined.append(META_TOOL_NAME)

    registry.set_active_tools(combined)
    return combined


def _calculate_min_k(registry: ToolRegistry) -> int:
    required_present = [name for name in REQUIRED_TOOL_NAMES if name in registry.tools]
    important = [
        name
        for name, tool in registry.tools.items()
        if (tool.metadata or {}).get("tier") == "important"
    ]
    unique_names = set(required_present + important)
    extra = getattr(AgentConfig, "TOOL_SELECTOR_MIN_EXTRA", 5)
    return len(unique_names) + extra


def select_tools_for_task(
    llm: LLMClient,
    registry: ToolRegistry,
    messages: List[Dict[str, Any]],
    agent_name: str = "ReActAgent",
    include_meta: bool = False,
) -> Dict[str, Any]:
    min_k = _calculate_min_k(registry)

    tool_schemas = [
        t for t in registry.openai_tools_all if t.get("function", {}).get("name") != META_TOOL_NAME
    ]
    tool_schemas = sorted(tool_schemas, key=lambda t: t.get("function", {}).get("name") or "")

    selection_result = select_tools_with_llm(llm, messages, tool_schemas, min_k, agent_name)
    selected = selection_result.get("selected_tools", [])
    llm_trace = selection_result.get("llm_trace")
    combined = _apply_tool_selection(registry, selected, include_meta=include_meta)
    try:
        tool_selection_logger.info(
            json.dumps(
                {
                    "agent": agent_name,
                    "context_size": len(messages),
                    "min_k": min_k,
                    "selected_tools": combined,
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        tool_selection_logger.info("Tool selection logged")
    return {
        "selected_tools": combined,
        "min_k": min_k,
        "_llm_trace": llm_trace,
    }


def ensure_tool_selector_tool(llm: LLMClient, registry: ToolRegistry):
    if META_TOOL_NAME in registry.tools:
        return

    def _select(task: str):
        return select_tools_for_task(
            llm,
            registry,
            [{"role": "user", "content": task}],
            agent_name="ToolSelector",
            include_meta=False,
        )

    registry.register(
        Tool(
            name=META_TOOL_NAME,
            description=(
                "Select the most relevant tools for the current task. "
                "Updates the available tool list and returns the selected tool names."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task description for tool selection."},
                },
                "required": ["task"],
            },
            func=_select,
            metadata={"tier": "meta"},
        )
    )
