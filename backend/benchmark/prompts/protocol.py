"""Public Prompt provider protocols and immutable registered entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, runtime_checkable

from benchmark.contracts import (
    ExtensionRef,
    JsonValue,
    PromptProfileDescriptor,
    PromptSpec,
    RenderedPrompt,
)


@runtime_checkable
class PromptProvider(Protocol):
    def list_prompts(self) -> Sequence[PromptSpec]: ...

    def render(
        self,
        prompt_id: str,
        variables: Mapping[str, JsonValue],
    ) -> RenderedPrompt: ...


@runtime_checkable
class PromptResolver(Protocol):
    def get_prompt_spec(
        self,
        prompt_id: str,
        *,
        version: str | None = None,
    ) -> PromptSpec: ...

    def get_profile(
        self,
        profile_id: str,
        *,
        version: str | None = None,
    ) -> PromptProfileDescriptor: ...

    def render(
        self,
        prompt_id: str,
        variables: Mapping[str, JsonValue],
        *,
        version: str | None = None,
    ) -> RenderedPrompt: ...

    def render_slot(
        self,
        profile_id: str,
        slot: str,
        variables: Mapping[str, JsonValue],
        *,
        profile_version: str | None = None,
    ) -> RenderedPrompt: ...


@dataclass(frozen=True)
class RegisteredPrompt:
    extension: ExtensionRef
    spec: PromptSpec
    priority: int
    provider: PromptProvider = field(repr=False, compare=False)


@dataclass(frozen=True)
class RegisteredPromptProfile:
    extension: ExtensionRef
    descriptor: PromptProfileDescriptor
    priority: int


@dataclass(frozen=True)
class LoadedPromptDirectory:
    provider: PromptProvider
    profiles: tuple[PromptProfileDescriptor, ...] = ()

    def list_prompts(self) -> Sequence[PromptSpec]:
        return self.provider.list_prompts()

    def render(
        self,
        prompt_id: str,
        variables: Mapping[str, JsonValue],
    ) -> RenderedPrompt:
        return self.provider.render(prompt_id, variables)

__all__ = [
    "PromptProvider",
    "PromptResolver",
    "RegisteredPrompt",
    "RegisteredPromptProfile",
    "LoadedPromptDirectory",
]
