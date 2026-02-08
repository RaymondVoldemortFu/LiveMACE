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

REQUIRED_TOOL_NAMES = [
    "get_market_snapshot",
    "get_kline_history",
    "get_account_state",
    "get_history_decisions",
]

REQUIRED_TOOLS_TEXT = ", ".join(REQUIRED_TOOL_NAMES)

TOOL_SELECTOR_PROMPT = (
    "You are a tool-selection assistant. "
    "Given a task description and a full list of tool schemas, "
    "choose the most useful tools for completing the task. "
    f"Required tools ({REQUIRED_TOOLS_TEXT}) are already selected and must always be included. "
    "You MUST return at least {min_k} tool names. "
    "Return ONLY valid JSON with the structure: "
    "{{\"selected_tools\": [\"tool_name\", ...]}}.\n"
    "Rules:\n"
    "- Choose only tool names that appear in the provided tool list.\n"
    "- **Do NOT include duplicate tool names.**\n"
    "- Do not include explanations or extra fields.\n"
    # "- Prefer tools that are directly helpful for the task and avoid noisy tools.\n"
)


def build_task_description(portfolio: Dict[str, Any], prices: Dict[str, Any]) -> str:
    symbols = list(prices.keys()) if isinstance(prices, dict) else []
    positions = list((portfolio or {}).get("positions", {}).keys())
    return (
        "Task: Make a trading decision using the agent. "
        f"Symbols available in prices: {symbols}. "
        f"Existing positions: {positions}."
    )


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


def select_tools_with_llm(
    llm: LLMClient,
    task_description: str,
    tool_schemas: List[Dict[str, Any]],
    min_k: int,
) -> Dict[str, Any]:
    max_retries = getattr(AgentConfig, "TOOL_SELECTOR_MAX_RETRIES", 10)
    last_trace = None
    last_selected: List[str] = []

    for attempt in range(max_retries):
        prompt_payload = {
            "task_description": task_description,
            "required_tools": REQUIRED_TOOL_NAMES,
            "min_k": min_k,
            "tools": tool_schemas,
        }
        system_prompt = TOOL_SELECTOR_PROMPT.format(min_k=min_k)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
        ]
        if attempt > 0:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous selection did not contain at least {min_k} tools or contained duplicates. "
                        f"Please select at least {min_k} unique tool names."
                    ),
                }
            )

        response = llm.call(messages, tools=None)
        content = response.content or ""
        trace = {
            "type": "tool_selector",
            "attempt": attempt + 1,
            "request": messages,
            "response": content,
        }
        last_trace = trace
        try:
            llm_trace_logger.info(json.dumps(trace, ensure_ascii=False))
        except Exception:
            llm_trace_logger.info("Tool selector LLM trace logged")
        try:
            tool_selector_trace_logger.info(json.dumps(trace, ensure_ascii=False))
        except Exception:
            tool_selector_trace_logger.info("Tool selector trace logged")

        parsed = _extract_json(content)
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
            return {"selected_tools": last_selected, "llm_trace": trace}

    return {"selected_tools": last_selected, "llm_trace": last_trace}


def _apply_tool_selection(
    registry: ToolRegistry,
    selected_tools: Iterable[str],
):
    available = set(_available_tool_names(registry.openai_tools_all))

    selected_filtered = [name for name in selected_tools if name in available]
    required = [name for name in REQUIRED_TOOL_NAMES if name in available]

    combined = []
    combined.extend(required)
    for name in selected_filtered:
        if name not in combined:
            combined.append(name)

    # Ensure meta tool is always available for re-selection
    if META_TOOL_NAME in registry.tools and META_TOOL_NAME not in combined:
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
    task_description: str,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    min_k = _calculate_min_k(registry)

    tool_schemas = [
        t for t in registry.openai_tools_all if t.get("function", {}).get("name") != META_TOOL_NAME
    ]
    tool_schemas = sorted(tool_schemas, key=lambda t: t.get("function", {}).get("name") or "")

    selection_result = select_tools_with_llm(llm, task_description, tool_schemas, min_k)
    selected = selection_result.get("selected_tools", [])
    llm_trace = selection_result.get("llm_trace")
    combined = _apply_tool_selection(registry, selected)
    try:
        tool_selection_logger.info(
            json.dumps(
                {
                    "task": task_description,
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
        return select_tools_for_task(llm, registry, task, None)

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
            metadata={"tier": "required"},
        )
    )
