"""Public Prompt provider, file loader, renderer, and registry APIs."""

from benchmark.contracts import (
    PromptProfileDescriptor,
    PromptRenderError,
    PromptSelection,
    PromptSpec,
    RenderedPrompt,
)

from .errors import PromptLoadError, PromptRegistryFrozenError
from .loader import FilePromptProvider, load_prompt_directory
from .protocol import (
    LoadedPromptDirectory,
    PromptProvider,
    PromptResolver,
    RegisteredPrompt,
    RegisteredPromptProfile,
)
from .registry import PromptRegistry, PromptSourcePriority
from .renderer import MAX_RENDERED_CHARACTERS
from .validation import (
    PROMPT_FILE_MAX_BYTES,
    parse_prompt_directory,
    validate_prompt_directory,
)

__all__ = [
    "PromptSpec",
    "RenderedPrompt",
    "PromptSelection",
    "PromptProfileDescriptor",
    "PromptProvider",
    "PromptResolver",
    "RegisteredPrompt",
    "RegisteredPromptProfile",
    "LoadedPromptDirectory",
    "FilePromptProvider",
    "PromptRegistry",
    "PromptSourcePriority",
    "PromptLoadError",
    "PromptRegistryFrozenError",
    "PromptRenderError",
    "PROMPT_FILE_MAX_BYTES",
    "MAX_RENDERED_CHARACTERS",
    "load_prompt_directory",
    "parse_prompt_directory",
    "validate_prompt_directory",
]
