"""Deterministic, bootstrap-mutable Prompt registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import IntEnum
from hashlib import sha256
from threading import RLock

from benchmark.contracts import (
    ComponentConfigError,
    ComponentConflictError,
    ComponentNotFoundError,
    ExtensionRef,
    JsonValue,
    PromptProfileDescriptor,
    PromptRenderError,
    PromptSpec,
    RenderedPrompt,
    require_identifier,
    require_semver,
    semver_key,
    to_jsonable,
)

from .errors import PromptRegistryFrozenError
from .protocol import PromptProvider, RegisteredPrompt, RegisteredPromptProfile
from .renderer import MAX_RENDERED_CHARACTERS


class PromptSourcePriority(IntEnum):
    BUILTIN = 0
    EXTERNAL = 100
    ACCOUNT = 200


class PromptRegistry:
    """Mutable during bootstrap, immutable and concurrently readable afterwards."""

    def __init__(self) -> None:
        self._prompts: dict[tuple[str, str, int], RegisteredPrompt] = {}
        self._profiles: dict[tuple[str, str, int], RegisteredPromptProfile] = {}
        self._frozen = False
        self._lock = RLock()

    @property
    def frozen(self) -> bool:
        with self._lock:
            return self._frozen

    def register_provider(
        self,
        extension: ExtensionRef,
        provider: PromptProvider,
        priority: int,
    ) -> None:
        with self._lock:
            self._ensure_mutable()
        if not isinstance(extension, ExtensionRef):
            raise TypeError("extension must be ExtensionRef")
        priority_value = self._validate_priority(priority)
        if not callable(getattr(provider, "list_prompts", None)) or not callable(
            getattr(provider, "render", None)
        ):
            raise TypeError("provider must implement list_prompts() and render()")
        specs = tuple(provider.list_prompts())
        if not specs:
            raise ComponentConfigError(
                "Prompt provider must expose at least one Prompt",
                code="PROMPT_PROVIDER_EMPTY",
            )
        if not all(isinstance(spec, PromptSpec) for spec in specs):
            raise ComponentConfigError(
                "Prompt provider returned an invalid Prompt specification",
                code="PROMPT_PROVIDER_SPEC_INVALID",
            )
        ids = [spec.id for spec in specs]
        if len(ids) != len(set(ids)):
            raise ComponentConflictError(
                "a Prompt provider may expose only one version of each Prompt id",
                code="PROMPT_PROVIDER_ID_CONFLICT",
            )
        entries = {
            (spec.id, spec.version, priority_value): RegisteredPrompt(
                extension=extension,
                spec=spec,
                priority=priority_value,
                provider=provider,
            )
            for spec in specs
        }
        with self._lock:
            self._ensure_mutable()
            conflicts = set(entries).intersection(self._prompts)
            if conflicts:
                prompt_id, version, conflict_priority = min(conflicts)
                raise ComponentConflictError(
                    f"Prompt already registered: {prompt_id}@{version}",
                    code="PROMPT_VERSION_PRIORITY_CONFLICT",
                    details={
                        "prompt_id": prompt_id,
                        "version": version,
                        "priority": conflict_priority,
                    },
                )
            self._prompts.update(entries)

    def register_profiles(
        self,
        extension: ExtensionRef,
        profiles: Iterable[PromptProfileDescriptor],
        priority: int,
    ) -> None:
        with self._lock:
            self._ensure_mutable()
        if not isinstance(extension, ExtensionRef):
            raise TypeError("extension must be ExtensionRef")
        priority_value = self._validate_priority(priority)
        descriptors = tuple(profiles)
        if not all(
            isinstance(profile, PromptProfileDescriptor) for profile in descriptors
        ):
            raise TypeError("profiles must contain PromptProfileDescriptor values")
        entries = {
            (profile.id, profile.version, priority_value): RegisteredPromptProfile(
                extension=extension,
                descriptor=profile,
                priority=priority_value,
            )
            for profile in descriptors
        }
        if len(entries) != len(descriptors):
            raise ComponentConflictError(
                "Prompt profile batch contains duplicate id/version values",
                code="PROMPT_PROFILE_BATCH_CONFLICT",
            )
        with self._lock:
            self._ensure_mutable()
            conflicts = set(entries).intersection(self._profiles)
            if conflicts:
                profile_id, version, conflict_priority = min(conflicts)
                raise ComponentConflictError(
                    f"Prompt profile already registered: {profile_id}@{version}",
                    code="PROMPT_PROFILE_PRIORITY_CONFLICT",
                    details={
                        "profile_id": profile_id,
                        "version": version,
                        "priority": conflict_priority,
                    },
                )
            self._profiles.update(entries)

    def resolve(self, prompt_id: str, version: str | None = None) -> RegisteredPrompt:
        require_identifier(prompt_id, "prompt id")
        if version is not None:
            require_semver(version, "prompt version")
        with self._lock:
            return self._resolve_prompt_unlocked(prompt_id, version)

    def get_prompt_spec(
        self,
        prompt_id: str,
        *,
        version: str | None = None,
    ) -> PromptSpec:
        return self.resolve(prompt_id, version).spec

    def resolve_profile(
        self,
        profile_id: str,
        version: str | None = None,
    ) -> RegisteredPromptProfile:
        require_identifier(profile_id, "prompt profile id")
        if version is not None:
            require_semver(version, "prompt profile version")
        with self._lock:
            candidates = [
                entry
                for entry in self._profiles.values()
                if entry.descriptor.id == profile_id
                and (version is None or entry.descriptor.version == version)
            ]
            if not candidates:
                suffix = f"@{version}" if version else ""
                raise ComponentNotFoundError(
                    f"Prompt profile not registered: {profile_id}{suffix}",
                    code="PROMPT_PROFILE_NOT_FOUND",
                    details={"profile_id": profile_id, "version": version},
                )
            highest_priority = max(entry.priority for entry in candidates)
            preferred = [
                entry for entry in candidates if entry.priority == highest_priority
            ]
            return max(
                preferred, key=lambda entry: semver_key(entry.descriptor.version)
            )

    def get_profile(
        self,
        profile_id: str,
        *,
        version: str | None = None,
    ) -> PromptProfileDescriptor:
        return self.resolve_profile(profile_id, version).descriptor

    def render(
        self,
        prompt_id: str,
        variables: Mapping[str, JsonValue],
        *,
        version: str | None = None,
    ) -> RenderedPrompt:
        registered = self.resolve(prompt_id, version)
        rendered = registered.provider.render(prompt_id, variables)
        if not isinstance(rendered, RenderedPrompt):
            raise PromptRenderError(
                "Prompt provider did not return RenderedPrompt",
                code="PROMPT_PROVIDER_RESULT_INVALID",
            )
        if rendered.spec != registered.spec:
            raise PromptRenderError(
                "Prompt provider returned content for a different specification",
                code="PROMPT_PROVIDER_SPEC_MISMATCH",
            )
        expected_hash = sha256(rendered.content.encode("utf-8")).hexdigest()
        if rendered.content_sha256 != expected_hash:
            raise PromptRenderError(
                "Prompt provider returned an invalid content hash",
                code="PROMPT_PROVIDER_HASH_MISMATCH",
            )
        if len(rendered.content) > MAX_RENDERED_CHARACTERS:
            raise PromptRenderError(
                "rendered Prompt exceeds the character limit",
                code="PROMPT_RENDER_TOO_LARGE",
            )
        return rendered

    def render_slot(
        self,
        profile_id: str,
        slot: str,
        variables: Mapping[str, JsonValue],
        *,
        profile_version: str | None = None,
    ) -> RenderedPrompt:
        require_identifier(profile_id, "prompt profile id")
        if not isinstance(slot, str) or not slot:
            raise TypeError("slot must be a non-empty string")
        profile = self.get_profile(profile_id, version=profile_version)
        selection = profile.slots.get(slot)
        if selection is None:
            raise ComponentNotFoundError(
                f"Prompt profile slot not found: {profile_id}.{slot}",
                code="PROMPT_PROFILE_SLOT_NOT_FOUND",
                details={
                    "profile_id": profile.id,
                    "profile_version": profile.version,
                    "slot": slot,
                },
            )
        return self.render(
            selection.prompt_id,
            variables,
            version=selection.version,
        )

    def list(self) -> tuple[PromptSpec, ...]:
        with self._lock:
            effective = self._effective_prompt_versions_unlocked()
            return tuple(
                entry.spec
                for entry in sorted(
                    effective,
                    key=lambda item: (item.spec.id, semver_key(item.spec.version)),
                )
            )

    def list_profiles(self) -> tuple[PromptProfileDescriptor, ...]:
        with self._lock:
            winners: dict[tuple[str, str], RegisteredPromptProfile] = {}
            for entry in self._profiles.values():
                key = (entry.descriptor.id, entry.descriptor.version)
                current = winners.get(key)
                if current is None or entry.priority > current.priority:
                    winners[key] = entry
            return tuple(
                entry.descriptor
                for entry in sorted(
                    winners.values(),
                    key=lambda item: (
                        item.descriptor.id,
                        semver_key(item.descriptor.version),
                    ),
                )
            )

    def freeze(self) -> None:
        with self._lock:
            self._ensure_mutable()
            problems: list[dict[str, object]] = []
            for registered in self._profiles.values():
                for slot, selection in registered.descriptor.slots.items():
                    try:
                        self._resolve_prompt_unlocked(
                            selection.prompt_id, selection.version
                        )
                    except ComponentNotFoundError:
                        problems.append(
                            {
                                "profile_id": registered.descriptor.id,
                                "profile_version": registered.descriptor.version,
                                "slot": slot,
                                "prompt_id": selection.prompt_id,
                                "prompt_version": selection.version,
                            }
                        )
            if problems:
                raise ComponentConfigError(
                    "Prompt profiles contain unresolved slot bindings",
                    code="PROMPT_PROFILE_UNRESOLVED",
                    details={"bindings": to_jsonable(problems)},
                )
            self._frozen = True

    def _resolve_prompt_unlocked(
        self, prompt_id: str, version: str | None
    ) -> RegisteredPrompt:
        candidates = [
            entry
            for entry in self._prompts.values()
            if entry.spec.id == prompt_id
            and (version is None or entry.spec.version == version)
        ]
        if not candidates:
            suffix = f"@{version}" if version else ""
            raise ComponentNotFoundError(
                f"Prompt not registered: {prompt_id}{suffix}",
                code="PROMPT_NOT_FOUND",
                details={"prompt_id": prompt_id, "version": version},
            )
        highest_priority = max(entry.priority for entry in candidates)
        preferred = [
            entry for entry in candidates if entry.priority == highest_priority
        ]
        return max(preferred, key=lambda entry: semver_key(entry.spec.version))

    def _effective_prompt_versions_unlocked(self) -> tuple[RegisteredPrompt, ...]:
        winners: dict[tuple[str, str], RegisteredPrompt] = {}
        for entry in self._prompts.values():
            key = (entry.spec.id, entry.spec.version)
            current = winners.get(key)
            if current is None or entry.priority > current.priority:
                winners[key] = entry
        return tuple(winners.values())

    @staticmethod
    def _validate_priority(priority: int) -> int:
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TypeError("priority must be an integer")
        return priority

    def _ensure_mutable(self) -> None:
        if self._frozen:
            raise PromptRegistryFrozenError("Prompt registry is frozen")


__all__ = ["PromptSourcePriority", "PromptRegistry"]
