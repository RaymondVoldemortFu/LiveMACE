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
    "Return ONLY valid JSON with the structure: "
    "{\"selected_tools\": [\"tool_name\", ...]}.\n"
    "Rules:\n"
    "- Choose only tool names that appear in the provided tool list.\n"
    "- Do not include explanations or extra fields.\n"
    "- Prefer tools that are directly helpful for the task and avoid noisy tools.\n"
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
    top_k: int,
) -> List[str]:
    prompt_payload = {
        "task_description": task_description,
        "required_tools": REQUIRED_TOOL_NAMES,
        "top_k": top_k,
        "tools": tool_schemas,
    }
    messages = [
        {"role": "system", "content": TOOL_SELECTOR_PROMPT},
        {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
    ]

    response = llm.call(messages, tools=None)
    content = response.content or ""
    parsed = _extract_json(content)
    if not parsed or "selected_tools" not in parsed:
        logger.warning("Tool selection LLM response parse failed, fallback to required tools.")
        return []
    selected = parsed.get("selected_tools") or []
    if not isinstance(selected, list):
        return []
    return [str(name) for name in selected]


def _apply_tool_selection(
    registry: ToolRegistry,
    selected_tools: Iterable[str],
    top_k: int,
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

    if top_k is not None and top_k > 0 and len(combined) > top_k:
        # Keep required tools, then fill remaining slots
        remaining_slots = max(0, top_k - len(required))
        combined = required + [n for n in combined if n not in required][:remaining_slots]

    registry.set_active_tools(combined)
    return combined


def select_tools_for_task(
    llm: LLMClient,
    registry: ToolRegistry,
    task_description: str,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    if top_k is None:
        top_k = getattr(AgentConfig, "TOOL_SELECTOR_TOP_K", 30)

    tool_schemas = [
        t for t in registry.openai_tools_all if t.get("function", {}).get("name") != META_TOOL_NAME
    ]

    selected = select_tools_with_llm(llm, task_description, tool_schemas, top_k)
    combined = _apply_tool_selection(registry, selected, top_k)
    try:
        tool_selection_logger.info(
            json.dumps(
                {
                    "task": task_description,
                    "top_k": top_k,
                    "selected_tools": combined,
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        tool_selection_logger.info("Tool selection logged")
    return {
        "selected_tools": combined,
        "top_k": top_k,
    }


def ensure_tool_selector_tool(llm: LLMClient, registry: ToolRegistry):
    if META_TOOL_NAME in registry.tools:
        return

    def _select(task: str, top_k: Optional[int] = None):
        return select_tools_for_task(llm, registry, task, top_k)

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
                    "top_k": {"type": "integer", "description": "Max number of tools to keep (default 30)."},
                },
                "required": ["task"],
            },
            func=_select,
            metadata={"tier": "meta"},
        )
    )
