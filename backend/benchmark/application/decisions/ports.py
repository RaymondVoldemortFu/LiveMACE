"""Account-scoped provider limits and prompt selection over frozen registries."""

from dataclasses import replace
from datetime import datetime, timezone
import os

from benchmark.contracts import AgentRuntimeError


class BoundedLLM:
    def __init__(self, adapter, events, deadline_at, is_cancelled):
        self._adapter = adapter
        self.model = adapter.model
        self.events = events
        self.deadline_at = deadline_at
        self.is_cancelled = is_cancelled
        self.calls = 0

    def complete(self, request):
        remaining = (self.deadline_at - datetime.now(timezone.utc)).total_seconds()
        if self.is_cancelled() or remaining <= 0:
            raise AgentRuntimeError(
                "Decision cancelled or deadline exceeded",
                code="AGENT_DEADLINE_EXCEEDED",
            )
        if self.calls >= int(os.getenv("AGENT_LLM_CALL_LIMIT", "30")):
            raise AgentRuntimeError(
                "Agent LLM call budget exhausted", code="LLM_BUDGET_EXCEEDED"
            )
        self.calls += 1
        timeout = min(
            remaining,
            float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "60")),
            float(request.metadata.get("timeout_seconds", 60)),
        )
        output_limit = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024"))
        max_tokens = min(request.max_tokens or output_limit, output_limit)
        request = replace(
            request,
            max_tokens=max_tokens,
            metadata={**request.metadata, "timeout_seconds": timeout},
        )
        self.events.record(
            "llm.started",
            {
                "model": self.model,
                "call_number": self.calls,
                "timeout_seconds": timeout,
                "max_tokens": max_tokens,
            },
        )
        try:
            response = self._adapter.complete(request)
        except Exception as exc:
            self.events.record(
                "llm.failed", {"model": self.model, "error_type": type(exc).__name__}
            )
            raise
        self.events.record(
            "llm.completed",
            {
                "model": self.model,
                "call_number": self.calls,
                "tool_call_ids": [call.id for call in response.tool_calls],
                "finish_reason": response.finish_reason,
                "usage": getattr(self._adapter.legacy_client, "last_usage", None),
            },
        )
        return response

    def requires_post_tool_user_message(self):
        return self._adapter.requires_post_tool_user_message()


class AccountPrompts:
    def __init__(self, registry, config, events):
        self.registry = registry
        self.config = config
        self.events = events

    def _selection(self, profile_id, version):
        if (
            profile_id.startswith(self.config.agent_id + ".")
            and self.config.prompt_profile_id
        ):
            return self.config.prompt_profile_id, self.config.prompt_profile_version
        return profile_id, version

    def get_profile(self, profile_id, *, version=None):
        profile_id, version = self._selection(profile_id, version)
        return self.registry.get_profile(profile_id, version=version)

    def get_prompt_spec(self, prompt_id, *, version=None):
        return self.registry.get_prompt_spec(prompt_id, version=version)

    def _record(self, rendered):
        self.events.record(
            "prompt.rendered",
            {
                "prompt_id": rendered.spec.id,
                "version": rendered.spec.version,
                "content_sha256": rendered.content_sha256,
                "profile_id": self.config.prompt_profile_id,
                "profile_version": self.config.prompt_profile_version,
            },
        )
        return rendered

    def render(self, prompt_id, variables, *, version=None):
        return self._record(self.registry.render(prompt_id, variables, version=version))

    def render_slot(self, profile_id, slot, variables, *, profile_version=None):
        profile_id, profile_version = self._selection(profile_id, profile_version)
        return self._record(
            self.registry.render_slot(
                profile_id, slot, variables, profile_version=profile_version
            )
        )
