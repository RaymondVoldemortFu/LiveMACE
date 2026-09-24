"""One synchronous AgentRuntime invocation per worker, using short transactions."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from types import SimpleNamespace
from uuid import uuid4

from benchmark.accounts.config import AccountExtensionConfig
from benchmark.agents import AgentBuildContext, AgentRuntime, AgentSelection
from benchmark.contracts import to_jsonable
from benchmark.extensions.host import get_extension_runtime
from benchmark.infrastructure.adapters.llm import LegacyLLMClientAdapter
from benchmark.tools import SynchronousToolInvoker
from services.agent.llm_client import LLMClient
from services.security.api_key_security import is_default_api_key

from .context_builder import load_worker_input
from benchmark.persistence.events import PersistentEventSink
from benchmark.persistence.decision_summary import save_run_summary as _save_run_summary
from .ports import AccountPrompts, BoundedLLM


def run_account(account_id, prices, round_id, *, is_cancelled=lambda: False):
    trace_id = str(uuid4())
    events = PersistentEventSink(
        SimpleNamespace(
            account_id=account_id, decision_round_id=round_id, trace_id=trace_id
        )
    )
    try:
        return _run_account(
            account_id, prices, round_id, events, trace_id, is_cancelled
        )
    except Exception as exc:
        events.record(
            "run.failed",
            {"error_type": type(exc).__name__, "code": getattr(exc, "code", None)},
        )
        raise


def _run_account(account_id, prices, round_id, events, trace_id, is_cancelled):
    worker = load_worker_input(account_id, prices, round_id)
    context = replace(worker.context, trace_id=trace_id)
    events.secrets = (worker.api_key,)
    runtime = get_extension_runtime()
    report = runtime.catalog.validate_account_config(worker.config.to_dict())
    if not report.valid:
        raise ValueError(
            "Invalid runtime configuration: "
            + "; ".join(issue.message for issue in report.errors)
        )
    data = dict(report.normalized_config)
    versions = dict(data.get("component_versions", {}))
    versions[data["agent_id"]] = data["agent_version"]
    if data.get("prompt_profile_id"):
        versions[data["prompt_profile_id"]] = data["prompt_profile_version"]
    data["component_versions"] = versions
    config = AccountExtensionConfig.from_dict(data)
    context = replace(context, config=config.to_dict())
    events.record("run.configured", config.to_dict())
    if context.portfolio.total_assets <= 0:
        raise ValueError("Account has non-positive equity")
    if is_default_api_key(worker.api_key):
        raise ValueError("Account has no usable LLM credential")
    capabilities = runtime.catalog.allowed_capabilities
    enabled = []
    memory_enabled = config.agent_config.get("memory_enabled")
    if memory_enabled is None:
        memory_enabled = worker.memory_enabled
    selected_tools = runtime.catalog.resolve_tool_names(
        config.toolset_ids, config.disabled_tools
    )
    for spec in runtime.tools.list():
        if spec.name not in selected_tools:
            continue
        if spec.name.startswith("core.memory_") and not memory_enabled:
            continue
        if spec.name.startswith("public.") and (
            not config.agent_config.get(
                "tool_routing_enabled", worker.tool_routing_enabled
            )
            or config.agent_id == "core.rule-aware"
        ):
            continue
        enabled.append(spec.name)
    deadline = datetime.now(timezone.utc) + timedelta(
        seconds=float(os.getenv("AGENT_ROUND_TIMEOUT_SECONDS", "600"))
    )
    from services.tool_cache import tool_cache
    from benchmark.infrastructure.cache.tool_cache import LegacyToolCacheAdapter

    tools = SynchronousToolInvoker(
        runtime.tools,
        account_id=account_id,
        decision_round_id=round_id,
        trace_id=context.trace_id,
        capabilities=capabilities,
        enabled_tools=tuple(enabled),
        events=events,
        cache=LegacyToolCacheAdapter(tool_cache),
        deadline_at=deadline,
    )
    client = LLMClient(
        model=worker.model, api_key=worker.api_key, base_url=worker.base_url
    )
    client.deadline_at = deadline
    client.is_cancelled = is_cancelled
    adapter = LegacyLLMClientAdapter(client)
    llm = BoundedLLM(adapter, events, deadline, is_cancelled)
    sandbox = None
    try:
        if any(
            "sandbox.write" in spec.required_capabilities for spec in tools.list_specs()
        ):
            from services.container_service import ContainerService

            sandbox = ContainerService()
            if not sandbox.lease_container(account_id):
                raise RuntimeError("Sandbox unavailable")
        selection_config = dict(config.agent_config)
        # Identity is host-owned even when a stored config supplies these legacy knobs.
        properties = runtime.agents.get(
            config.agent_id, config.agent_version
        ).descriptor.config_schema.get("properties", {})
        for key, value in {
            "account_id": account_id,
            "user_id": str(account_id),
            "agent_name": context.portfolio.account.name,
        }.items():
            if key in properties:
                selection_config[key] = value
        build = AgentBuildContext(
            llm, tools, AccountPrompts(runtime.prompts, config, events), events
        )
        from services.agent.request_scope import RequestScope, use_request_scope

        scope = RequestScope(
            deadline,
            is_cancelled,
            int(os.getenv("AGENT_LLM_CALL_LIMIT", "30")),
            int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024")),
            events,
        )
        with tool_cache.use_round(round_id), use_request_scope(scope):
            result = AgentRuntime(runtime.agents, build).run(
                AgentSelection(config.agent_id, config.agent_version, selection_config),
                context,
                deadline_at=deadline,
                is_cancelled=is_cancelled,
            )
        events.record("run.result", to_jsonable(result))
        _save_run_summary(account_id, result, prices, secrets=(worker.api_key,))
        return result
    finally:
        adapter.close()
        if sandbox is not None:
            sandbox.release_container(account_id)
