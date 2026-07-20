"""Prompt loader and registry errors."""

from benchmark.contracts import BenchmarkError


class PromptLoadError(BenchmarkError):
    default_code = "PROMPT_LOAD_ERROR"


class PromptRegistryFrozenError(BenchmarkError):
    default_code = "PROMPT_REGISTRY_FROZEN"


__all__ = ["PromptLoadError", "PromptRegistryFrozenError"]
