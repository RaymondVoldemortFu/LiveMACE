"""Built-in search sub-agent Tool."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from benchmark.contracts import NETWORK_READ, JsonValue, SideEffect, ToolContext
from benchmark.builtin.tools._support import BoundCallableTool, spec

SEARCH_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Specific search query.",
        },
        "topic": {
            "type": "string",
            "enum": ["general", "news", "finance"],
            "description": "Optional category hint (merged into query for news/finance).",
        },
        "time_range": {
            "type": "string",
            "enum": ["day", "week", "month", "year", "none"],
            "description": "Google recency filter (past day/week/month/year); none = no filter.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of organic results to return.",
            "default": 5,
        },
    },
    "required": ["query"],
}

SEARCH_SPEC = spec(
    "core.search",
    "Web search tool. Use it to retrieve latest market news, macroeconomic data, project updates, or non-price information for specific symbols. Returns structured summaries with sources.",
    SEARCH_PARAMETERS,
    side_effect=SideEffect.EXTERNAL_READ,
    capabilities=(NETWORK_READ,),
    timeout_seconds=60.0,
)

SearchRunner = Callable[[str, str, str, int], Any]


def _default_search_runner_factory(context: ToolContext) -> SearchRunner:
    from database.connection import SessionLocal
    from repositories.account_repo import get_account
    from services.agent.sub_agents.search_agent import SearchSubAgent
    from services.security.api_key_security import resolve_runtime_api_key

    db = SessionLocal()
    try:
        account = get_account(db, context.account_id)
        agent = SearchSubAgent(
            model=account.model,
            api_key=resolve_runtime_api_key(account.api_key),
            base_url=account.base_url,
            agent_name=account.name,
        )
    finally:
        db.close()

    def run(query: str, topic: str, time_range: str, max_results: int) -> Any:
        return agent.run(query, topic, time_range, max_results)

    return run


class SearchToolsProvider:
    def __init__(
        self,
        *,
        search_runner: SearchRunner | None = None,
        search_runner_factory: Callable[[ToolContext], SearchRunner] | None = None,
    ) -> None:
        self._search_runner = search_runner
        self._search_runner_factory = search_runner_factory or _default_search_runner_factory
        self._tools = (BoundCallableTool(SEARCH_SPEC, self._invoke),)

    def list_tools(self) -> tuple[BoundCallableTool, ...]:
        return self._tools

    def _invoke(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        runner = self._search_runner or self._search_runner_factory(context)
        raw_max = arguments.get("max_results", 5)
        if raw_max is None:
            raw_max = 5
        return runner(
            str(arguments["query"]),
            str(arguments.get("topic") or "general"),
            str(arguments.get("time_range") or "none"),
            int(raw_max),
        )


__all__ = ["SEARCH_SPEC", "SearchToolsProvider"]
