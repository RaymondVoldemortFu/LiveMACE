"""Reusable contract assertions for third-party Agent, Tool, and Prompt code.

These helpers intentionally exercise the same public runtime boundaries that
the application uses.  They are ordinary assertions rather than pytest
plugins, so an extension can call them from any test runner.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from inspect import isawaitable
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from jsonschema import Draft202012Validator, SchemaError, validators

from benchmark.agents import Agent, AgentBuildContext, AgentFactory
from benchmark.contracts import (
    AgentRunResult,
    DecisionContext,
    ExtensionRef,
    KNOWN_CAPABILITIES,
    PromptSpec,
    RenderedPrompt,
    SideEffect,
    TerminationReason,
    ToolContext,
    ToolResult,
    ToolSpec,
    TRADING_WRITE,
    to_jsonable,
    require_identifier,
    require_semver,
)
from benchmark.prompts import PromptProvider
from benchmark.prompts.renderer import MAX_RENDERED_CHARACTERS
from benchmark.tools import (
    SynchronousToolInvoker,
    Tool,
    ToolProvider,
    ToolRegistry,
    ToolRuntimeError,
)
from benchmark.tools.validation import (
    schema_validator,
    validate_tool_spec,
    validation_messages,
)

from .context import build_fake_agent_build_context, build_fake_context
from .events import FakeEventSink


_UNSET = object()


def _close_awaitable(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _sync_or_assert(value: Any, label: str) -> Any:
    if isawaitable(value):
        _close_awaitable(value)
        raise AssertionError(
            f"{label} returned an awaitable; v1 extension SPI is synchronous"
        )
    return value


def _as_reason(value: Any) -> TerminationReason | None:
    if value is None or isinstance(value, TerminationReason):
        return value
    if isinstance(value, str):
        try:
            return TerminationReason(value)
        except ValueError:
            try:
                return TerminationReason[value]
            except KeyError:
                pass
    raise AssertionError(f"unknown expected termination reason: {value!r}")


@dataclass(frozen=True, init=False)
class AgentCase:
    """One input and optional expectation for ``assert_agent_contract``.

    The constructor accepts both the natural ``AgentCase(context, config)``
    form and a name-first form useful in parameterized tests.  ``expected`` and
    ``expected_reason`` are aliases for ``expected_termination``.
    """

    name: str
    context: Any
    config: Mapping[str, Any]
    expected_termination: TerminationReason | None
    build_context: AgentBuildContext | None
    config_schema: Mapping[str, Any] | None
    deadline_at: datetime | None

    def __init__(
        self,
        *args: Any,
        context: Any | None = None,
        decision_context: Any | None = None,
        config: Mapping[str, Any] | None = None,
        agent_config: Mapping[str, Any] | None = None,
        expected_termination: Any | None = None,
        expected_reason: Any | None = None,
        expected: Any | None = None,
        name: str = "case",
        build_context: AgentBuildContext | None = None,
        build: AgentBuildContext | None = None,
        config_schema: Mapping[str, Any] | None = None,
        deadline_at: datetime | None = None,
    ) -> None:
        values = list(args)
        if values:
            if isinstance(values[0], str):
                name = values.pop(0)
            elif context is None and decision_context is None:
                context = values.pop(0)
        if values and context is None and decision_context is None:
            context = values.pop(0)
        if values and config is None and agent_config is None:
            config = values.pop(0)
        if (
            values
            and expected_termination is None
            and expected_reason is None
            and expected is None
        ):
            expected_termination = values.pop(0)
        if values:
            raise TypeError("too many positional arguments for AgentCase")
        if context is not None and decision_context is not None:
            raise TypeError("context and decision_context are aliases; provide one")
        if config is not None and agent_config is not None:
            raise TypeError("config and agent_config are aliases; provide one")
        if build_context is not None and build is not None:
            raise TypeError("build_context and build are aliases; provide one")
        chosen_context = (
            context
            if context is not None
            else decision_context
            if decision_context is not None
            else build_fake_context()
        )
        chosen_config = config if config is not None else agent_config
        chosen_expected = _as_reason(expected_termination)
        for alias in (expected_reason, expected):
            if alias is not None:
                alias_reason = _as_reason(alias)
                if chosen_expected is not None and chosen_expected != alias_reason:
                    raise TypeError("conflicting expected termination values")
                chosen_expected = alias_reason
        if not isinstance(name, str) or not name.strip():
            raise ValueError("AgentCase name must be non-empty")
        if not isinstance(chosen_config if chosen_config is not None else {}, Mapping):
            raise TypeError("AgentCase config must be a mapping")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "context", chosen_context)
        object.__setattr__(
            self, "config", chosen_config if chosen_config is not None else {}
        )
        object.__setattr__(self, "expected_termination", chosen_expected)
        object.__setattr__(
            self,
            "build_context",
            build_context if build_context is not None else build,
        )
        object.__setattr__(self, "config_schema", config_schema)
        if deadline_at is not None and (
            not isinstance(deadline_at, datetime)
            or deadline_at.tzinfo is None
            or deadline_at.utcoffset() is None
        ):
            raise ValueError("AgentCase deadline_at must be timezone-aware")
        object.__setattr__(self, "deadline_at", deadline_at)

    @property
    def expected_reason(self) -> TerminationReason | None:
        return self.expected_termination


@dataclass(frozen=True, init=False)
class ToolCase:
    """One Tool invocation and optional expectation for contract assertions."""

    name: str
    arguments: Mapping[str, Any]
    context: ToolContext | None
    expected_ok: bool | None
    expected_error_code: str | None
    expected_value: Any
    capabilities: frozenset[str] | None
    deadline_at: datetime | None

    def __init__(
        self,
        *args: Any,
        name: str | None = None,
        tool_name: str | None = None,
        arguments: Mapping[str, Any] | None = None,
        args_mapping: Mapping[str, Any] | None = None,
        context: ToolContext | None = None,
        expected_ok: bool | None = None,
        expected_error_code: str | None = None,
        expected_error: str | None = None,
        expected_value: Any = _UNSET,
        expected: Any = _UNSET,
        capabilities: frozenset[str] | Iterable[str] | None = None,
        deadline_at: datetime | None = None,
    ) -> None:
        values = list(args)
        if values:
            if name is None and tool_name is None:
                name = values.pop(0)
            else:
                raise TypeError("ToolCase name was provided twice")
        if values and arguments is None and args_mapping is None:
            arguments = values.pop(0)
        if values:
            raise TypeError("too many positional arguments for ToolCase")
        chosen_name = name if name is not None else tool_name
        if name is not None and tool_name is not None and name != tool_name:
            raise TypeError("name and tool_name disagree")
        if not isinstance(chosen_name, str) or not chosen_name.strip():
            raise ValueError("ToolCase name must be non-empty")
        if arguments is not None and args_mapping is not None:
            raise TypeError("arguments and args_mapping are aliases; provide one")
        chosen_arguments = arguments if arguments is not None else args_mapping
        if chosen_arguments is None:
            chosen_arguments = {}
        if not isinstance(chosen_arguments, Mapping):
            raise TypeError("ToolCase arguments must be a mapping")
        if expected_ok is not None and not isinstance(expected_ok, bool):
            raise TypeError("expected_ok must be bool or None")
        if expected_error is not None:
            if (
                expected_error_code is not None
                and expected_error_code != expected_error
            ):
                raise TypeError("expected_error and expected_error_code disagree")
            expected_error_code = expected_error
        if expected_error_code is not None and (
            not isinstance(expected_error_code, str) or not expected_error_code.strip()
        ):
            raise TypeError("expected_error_code must be a non-empty string or None")
        if expected is not _UNSET:
            if expected_value is not _UNSET and expected_value != expected:
                raise TypeError("expected and expected_value disagree")
            expected_value = expected
        normalized_capabilities = (
            None if capabilities is None else frozenset(capabilities)
        )
        if normalized_capabilities is not None and not all(
            isinstance(item, str) for item in normalized_capabilities
        ):
            raise TypeError("capabilities must contain strings")
        object.__setattr__(self, "name", chosen_name)
        object.__setattr__(self, "arguments", chosen_arguments)
        object.__setattr__(self, "context", context)
        object.__setattr__(self, "expected_ok", expected_ok)
        object.__setattr__(self, "expected_error_code", expected_error_code)
        object.__setattr__(self, "expected_value", expected_value)
        object.__setattr__(self, "capabilities", normalized_capabilities)
        if deadline_at is not None and (
            not isinstance(deadline_at, datetime)
            or deadline_at.tzinfo is None
            or deadline_at.utcoffset() is None
        ):
            raise ValueError("ToolCase deadline_at must be timezone-aware")
        object.__setattr__(self, "deadline_at", deadline_at)

    @property
    def tool_name(self) -> str:
        return self.name

    @property
    def expected_result(self) -> Any:
        return self.expected_value


class _RecordingTool:
    def __init__(self, tool: Tool, contexts: list[ToolContext]) -> None:
        self._tool = tool
        self._contexts = contexts

    @property
    def spec(self) -> ToolSpec:
        return self._tool.spec

    def invoke(self, context: ToolContext, arguments: Mapping[str, Any]) -> ToolResult:
        self._contexts.append(context)
        return self._tool.invoke(context, arguments)


class _Provider:
    def __init__(self, tools: Sequence[Tool]) -> None:
        self._tools = tuple(tools)

    def list_tools(self) -> tuple[Tool, ...]:
        return self._tools


def _normalize_agent_case(value: Any) -> AgentCase:
    if isinstance(value, AgentCase):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        return AgentCase(**data)
    raise TypeError("Agent cases must be AgentCase or mappings")


def _normalize_tool_case(value: Any) -> ToolCase:
    if isinstance(value, ToolCase):
        return value
    if isinstance(value, Mapping):
        return ToolCase(**dict(value))
    raise TypeError("Tool cases must be ToolCase or mappings")


def _assert_json(value: Any, label: str) -> Any:
    try:
        return to_jsonable(value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{label} is not JSON-compatible: {exc}") from exc


def _assert_schema(schema: Mapping[str, Any], label: str) -> None:
    if not isinstance(schema, Mapping):
        raise AssertionError(f"{label} must be a mapping")
    try:
        validator_type = validators.validator_for(
            dict(schema), default=Draft202012Validator
        )
        validator_type.check_schema(dict(schema))
    except (SchemaError, TypeError, ValueError) as exc:
        raise AssertionError(f"{label} is not a valid JSON Schema: {exc}") from exc


def assert_agent_contract(
    factory: AgentFactory,
    cases: Sequence[AgentCase] | None = None,
) -> None:
    """Assert the synchronous AgentFactory/Agent v1 contract.

    The function raises ``AssertionError`` with a component-oriented message;
    it returns ``None`` on success so it can be used directly in a pytest test
    body or from the extension CLI.
    """

    if not isinstance(factory, AgentFactory):
        raise AssertionError("factory must implement the public AgentFactory SPI")
    normalized_cases = (
        (AgentCase(),)
        if cases is None
        else tuple(_normalize_agent_case(item) for item in cases)
    )
    for case in normalized_cases:
        context = case.context
        if not isinstance(context, DecisionContext):
            raise AssertionError(f"Agent case {case.name!r} has an invalid context")
        config = _assert_json(case.config, f"Agent case {case.name!r} config")
        if not isinstance(config, dict):
            raise AssertionError(f"Agent case {case.name!r} config must be an object")
        if case.config_schema is not None:
            _assert_schema(
                case.config_schema, f"Agent case {case.name!r} config_schema"
            )
            errors = tuple(
                Draft202012Validator(dict(case.config_schema)).iter_errors(config)
            )
            if errors:
                raise AssertionError(
                    f"Agent case {case.name!r} config does not match schema: "
                    + errors[0].message
                )
        build_context = (
            case.build_context
            if case.build_context is not None
            else build_fake_agent_build_context(
                account_id=context.account_id,
                decision_round_id=context.decision_round_id,
                trace_id=context.trace_id,
            )
        )
        try:
            agent = _sync_or_assert(
                factory.create(build_context, config),
                f"AgentFactory.create in case {case.name!r}",
            )
        except AssertionError:
            raise
        except Exception as exc:
            raise AssertionError(
                f"AgentFactory.create failed in case {case.name!r}: {type(exc).__name__}"
            ) from exc
        if not isinstance(agent, Agent):
            raise AssertionError(f"Agent case {case.name!r} did not create an Agent")
        try:
            result = _sync_or_assert(
                agent.run(context),
                f"Agent.run in case {case.name!r}",
            )
        except AssertionError:
            raise
        except Exception as exc:
            raise AssertionError(
                f"Agent.run failed in case {case.name!r}: {type(exc).__name__}"
            ) from exc
        if not isinstance(result, AgentRunResult):
            raise AssertionError(
                f"Agent case {case.name!r} returned {type(result).__name__}, expected AgentRunResult"
            )
        if result.trace_id != context.trace_id:
            raise AssertionError(f"Agent case {case.name!r} changed trace_id")
        if result.decision_round_id != context.decision_round_id:
            raise AssertionError(f"Agent case {case.name!r} changed decision_round_id")
        if not isinstance(result.summary, str):
            raise AssertionError(f"Agent case {case.name!r} summary must be a string")
        _assert_json(result, f"Agent case {case.name!r} result")
        expected = case.expected_termination
        if expected is not None and result.termination_reason is not expected:
            raise AssertionError(
                f"Agent case {case.name!r} terminated with "
                f"{result.termination_reason.value!r}, expected {expected.value!r}"
            )


def _provider_extension(provider: ToolProvider) -> ExtensionRef:
    candidate_id = getattr(provider, "id", "com.example.contract-tools")
    candidate_version = getattr(provider, "version", "1.0.0")
    try:
        require_identifier(candidate_id, "provider id")
    except (TypeError, ValueError):
        candidate_id = "com.example.contract-tools"
    try:
        require_semver(candidate_version, "provider version")
    except (TypeError, ValueError):
        candidate_version = "1.0.0"
    return ExtensionRef(candidate_id, candidate_version)


def _schema_example(schema: Mapping[str, Any]) -> Any:
    if "default" in schema:
        return schema["default"]
    if "examples" in schema and schema["examples"]:
        return schema["examples"][0]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    for branch in ("anyOf", "oneOf"):
        values = schema.get(branch)
        if isinstance(values, Sequence) and values:
            return _schema_example(values[0])
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        return {
            key: _schema_example(properties.get(key, {}))
            for key in required
            if isinstance(properties, Mapping)
        }
    if schema_type == "array":
        return []
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1
    if schema_type == "boolean":
        return True
    return "example"


def _sensitive_values(value: Any, *, key: str = "") -> set[str]:
    sensitive_names = {
        "apikey",
        "authorization",
        "credential",
        "password",
        "passwd",
        "secret",
        "token",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "cookie",
    }
    normalized = "".join(character for character in key.lower() if character.isalnum())
    found: set[str] = set()
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            child_key_text = str(child_key)
            if (
                "".join(c for c in child_key_text.lower() if c.isalnum())
                in sensitive_names
            ):
                if isinstance(child, str) and len(child) >= 3:
                    found.add(child)
            found.update(_sensitive_values(child, key=child_key_text))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_sensitive_values(child, key=key))
    elif normalized in sensitive_names and isinstance(value, str) and len(value) >= 3:
        found.add(value)
    elif isinstance(value, str):
        try:
            parsed = urlsplit(value)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.password:
                found.add(parsed.password)
            for query_key, query_value in parse_qsl(
                parsed.query, keep_blank_values=True
            ):
                normalized_query_key = "".join(
                    character for character in query_key.lower() if character.isalnum()
                )
                if normalized_query_key in sensitive_names and len(query_value) >= 3:
                    found.add(query_value)
    return found


def assert_tool_contract(
    provider: ToolProvider,
    cases: Sequence[ToolCase] | None = None,
) -> None:
    """Assert ToolProvider specs, synchronous invocation, and redacted events."""

    if not isinstance(provider, ToolProvider):
        raise AssertionError("provider must implement the public ToolProvider SPI")
    try:
        tools = _sync_or_assert(provider.list_tools(), "ToolProvider.list_tools")
    except AssertionError:
        raise
    except Exception as exc:
        raise AssertionError(
            f"ToolProvider.list_tools failed: {type(exc).__name__}"
        ) from exc
    if isinstance(tools, (str, bytes)):
        raise AssertionError("ToolProvider.list_tools must return a sequence of Tools")
    try:
        tools = tuple(tools)
    except Exception as exc:
        raise AssertionError("ToolProvider.list_tools must return a sequence") from exc
    if not tools:
        raise AssertionError("ToolProvider.list_tools must expose at least one Tool")

    contexts_by_name: dict[str, list[ToolContext]] = {}
    wrapped: list[Tool] = []
    names: set[str] = set()
    trading_allowlist: set[str] = set()
    for position, tool in enumerate(tools):
        if not isinstance(tool, Tool):
            raise AssertionError(f"Tool at position {position} does not implement Tool")
        try:
            spec = tool.spec
        except Exception as exc:
            raise AssertionError(
                f"Tool at position {position} has no readable spec"
            ) from exc
        if not isinstance(spec, ToolSpec):
            raise AssertionError(f"Tool at position {position} spec must be ToolSpec")
        if spec.name in names:
            raise AssertionError(f"duplicate Tool name: {spec.name}")
        names.add(spec.name)
        try:
            require_identifier(spec.name, "tool name")
            validate_tool_spec(spec, trading_write_allowlist=frozenset({spec.name}))
            schema_validator(spec.input_schema)
            schema_validator(spec.output_schema)
        except Exception as exc:
            raise AssertionError(
                f"Tool {spec.name!r} has an invalid specification: {exc}"
            ) from exc
        if TRADING_WRITE in spec.required_capabilities:
            trading_allowlist.add(spec.name)
        if (
            spec.side_effect.value == "read_only"
            and TRADING_WRITE in spec.required_capabilities
        ):
            raise AssertionError(f"read-only Tool {spec.name!r} requests trading.write")
        contexts_by_name[spec.name] = []
        wrapped.append(_RecordingTool(tool, contexts_by_name[spec.name]))

    registry = ToolRegistry(
        trading_write_allowlist=trading_allowlist or ("core.execute_trade",)
    )
    try:
        registry.register_provider(_provider_extension(provider), _Provider(wrapped))
        registry.freeze()
    except Exception as exc:
        raise AssertionError(f"Tool provider could not be registered: {exc}") from exc

    if cases is not None:
        normalized_cases = tuple(_normalize_tool_case(item) for item in cases)
    else:
        # Contract discovery must not unexpectedly execute a write-capable
        # component.  Such Tools still receive full spec/registration checks;
        # callers provide explicit cases when they want to exercise writes.
        discovered: list[ToolCase] = []
        for name in sorted(names):
            spec = next(tool.spec for tool in wrapped if tool.spec.name == name)
            if spec.side_effect is not SideEffect.READ_ONLY:
                continue
            arguments = _schema_example(spec.input_schema)
            try:
                has_errors = bool(validation_messages(spec.input_schema, arguments))
            except Exception:
                has_errors = True
            if not has_errors:
                discovered.append(ToolCase(name=name, arguments=arguments))
        normalized_cases = tuple(discovered)
    for case in normalized_cases:
        if case.name not in names:
            raise AssertionError(f"Tool case {case.name!r} names an unknown Tool")
        registry.get(case.name)
        capabilities = (
            frozenset(KNOWN_CAPABILITIES)
            if case.capabilities is None
            else case.capabilities
        )
        unknown = capabilities.difference(KNOWN_CAPABILITIES)
        if unknown:
            raise AssertionError(
                f"Tool case {case.name!r} has unknown capabilities: {sorted(unknown)}"
            )
        if case.context is not None:
            if not isinstance(case.context, ToolContext):
                raise AssertionError(
                    f"Tool case {case.name!r} has an invalid ToolContext"
                )
            account_id = case.context.account_id
            decision_round_id = case.context.decision_round_id
            trace_id = case.context.trace_id
            if case.capabilities is None:
                capabilities = case.context.capabilities
        else:
            account_id = 1
            decision_round_id = f"contract-round-{case.name}"
            trace_id = f"contract-trace-{case.name}"
        events = FakeEventSink()
        invoker = SynchronousToolInvoker(
            registry,
            account_id=account_id,
            decision_round_id=decision_round_id,
            trace_id=trace_id,
            capabilities=capabilities,
            events=events,
            deadline_at=case.deadline_at,
        )
        try:
            result = invoker.call(case.name, case.arguments)
        except ToolRuntimeError as exc:
            if case.expected_error_code != exc.code:
                if exc.code == "ASYNC_TOOL_UNSUPPORTED":
                    raise AssertionError(
                        f"Tool case {case.name!r} returned an awaitable"
                    ) from exc
                raise AssertionError(
                    f"Tool case {case.name!r} raised {exc.code}, expected "
                    f"{case.expected_error_code or 'a ToolResult'}"
                ) from exc
            continue
        except Exception as exc:
            raise AssertionError(
                f"Tool case {case.name!r} failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(result, ToolResult):
            raise AssertionError(f"Tool case {case.name!r} did not return ToolResult")
        if case.expected_ok is not None and result.ok is not case.expected_ok:
            raise AssertionError(f"Tool case {case.name!r} returned ok={result.ok!r}")
        if (
            case.expected_error_code is not None
            and result.error_code != case.expected_error_code
        ):
            raise AssertionError(
                f"Tool case {case.name!r} returned error_code={result.error_code!r}, "
                f"expected {case.expected_error_code!r}"
            )
        if case.expected_value is not _UNSET and result.value != case.expected_value:
            raise AssertionError(
                f"Tool case {case.name!r} returned an unexpected value"
            )
        _assert_json(result, f"Tool case {case.name!r} result")
        received = contexts_by_name[case.name]
        if not received:
            # Input/schema/capability failures are valid contract cases but do
            # not produce an invocation context to inspect.
            if result.ok or case.expected_ok is True:
                raise AssertionError(f"Tool case {case.name!r} did not invoke its Tool")
        else:
            context = received[-1]
            if (
                context.account_id != account_id
                or context.trace_id != trace_id
                or context.decision_round_id != decision_round_id
            ):
                raise AssertionError(
                    f"Tool case {case.name!r} received mismatched context ids"
                )
            if context.deadline_at is None or context.deadline_at.tzinfo is None:
                raise AssertionError(
                    f"Tool case {case.name!r} did not receive a deadline"
                )
        secrets = _sensitive_values(case.arguments)
        secrets.update(_sensitive_values(to_jsonable(result)))
        event_text = repr(events.snapshot())
        leaked = sorted(secret for secret in secrets if secret in event_text)
        if leaked:
            raise AssertionError(
                f"Tool case {case.name!r} leaked secret values in runtime events"
            )


def _prompt_example(spec: Any) -> dict[str, Any]:
    values = {name: "example" for name in spec.required_variables}
    values.update(dict(spec.optional_variables))
    return values


def assert_prompt_contract(provider: PromptProvider) -> None:
    """Assert Prompt metadata, rendering, hashing, and synchronous behavior."""

    if not isinstance(provider, PromptProvider):
        raise AssertionError("provider must implement the public PromptProvider SPI")
    try:
        specs = _sync_or_assert(provider.list_prompts(), "PromptProvider.list_prompts")
    except AssertionError:
        raise
    except Exception as exc:
        raise AssertionError(
            f"PromptProvider.list_prompts failed: {type(exc).__name__}"
        ) from exc
    if isinstance(specs, (str, bytes)):
        raise AssertionError("PromptProvider.list_prompts must return a sequence")
    try:
        specs = tuple(specs)
    except Exception as exc:
        raise AssertionError(
            "PromptProvider.list_prompts must return a sequence"
        ) from exc
    if not specs:
        raise AssertionError(
            "PromptProvider.list_prompts must expose at least one Prompt"
        )
    seen: set[str] = set()
    for spec in specs:
        if not isinstance(spec, PromptSpec):
            raise AssertionError(f"Prompt {spec!r} is not PromptSpec")
        if spec.id in seen:
            raise AssertionError(f"duplicate Prompt id: {spec.id}")
        seen.add(spec.id)
        try:
            rendered = _sync_or_assert(
                provider.render(spec.id, _prompt_example(spec)),
                f"PromptProvider.render({spec.id})",
            )
        except AssertionError:
            raise
        except Exception as exc:
            raise AssertionError(
                f"PromptProvider.render({spec.id}) failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(rendered, RenderedPrompt):
            raise AssertionError(f"Prompt {spec.id!r} did not return RenderedPrompt")
        if rendered.spec != spec:
            raise AssertionError(f"Prompt {spec.id!r} returned a different spec")
        if len(rendered.content) > MAX_RENDERED_CHARACTERS:
            raise AssertionError(f"Prompt {spec.id!r} exceeds rendered size limit")
        if (
            rendered.content_sha256
            != sha256(rendered.content.encode("utf-8")).hexdigest()
        ):
            raise AssertionError(f"Prompt {spec.id!r} returned an invalid content hash")


__all__ = [
    "AgentCase",
    "ToolCase",
    "assert_agent_contract",
    "assert_tool_contract",
    "assert_prompt_contract",
]
