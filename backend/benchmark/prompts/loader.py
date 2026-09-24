"""File-backed Prompt provider loader."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from benchmark.contracts import (
    ComponentNotFoundError,
    JsonValue,
    PromptSpec,
    RenderedPrompt,
    to_jsonable,
)

from .errors import PromptLoadError
from .protocol import LoadedPromptDirectory
from .renderer import ParsedTemplate, render_template
from .validation import _parse_prompt_directory


class FilePromptProvider:
    def __init__(self, records: Sequence[tuple[PromptSpec, ParsedTemplate]]) -> None:
        self._records = {spec.id: (spec, template) for spec, template in records}
        self._specs = tuple(
            sorted(
                (item[0] for item in self._records.values()), key=lambda spec: spec.id
            )
        )

    def list_prompts(self) -> tuple[PromptSpec, ...]:
        return self._specs

    def render(
        self,
        prompt_id: str,
        variables: Mapping[str, JsonValue],
    ) -> RenderedPrompt:
        record = self._records.get(prompt_id)
        if record is None:
            raise ComponentNotFoundError(
                f"Prompt not found: {prompt_id}",
                code="PROMPT_NOT_FOUND",
                details={"prompt_id": prompt_id},
            )
        spec, template = record
        return render_template(template, spec, variables)


def load_prompt_directory(root: Path, index: Path) -> LoadedPromptDirectory:
    data, errors = _parse_prompt_directory(root, index)
    if data is None:
        raise PromptLoadError(
            "Prompt directory is invalid",
            details={"errors": [to_jsonable(issue) for issue in errors]},
        )
    provider = FilePromptProvider(
        tuple((item.spec, item.template) for item in data.prompts)
    )
    return LoadedPromptDirectory(provider=provider, profiles=data.profiles)


__all__ = ["FilePromptProvider", "load_prompt_directory"]
