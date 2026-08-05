"""Provider error type — re-export of the canonical contracts class.

``benchmark.contracts.ProviderError`` is the single public ProviderError.
This module exists so ``benchmark.providers`` can keep exporting the same
type without maintaining a second class with divergent serialization.
"""

from __future__ import annotations

from benchmark.contracts.errors import ProviderError

__all__ = ["ProviderError"]
