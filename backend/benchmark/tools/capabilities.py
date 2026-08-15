"""Capability policy for public Tool specifications."""

from __future__ import annotations

from benchmark.contracts import (
    KNOWN_CAPABILITIES,
    MARKET_READ,
    MEMORY_WRITE,
    NETWORK_READ,
    SANDBOX_WRITE,
    TRADING_WRITE,
    SideEffect,
    ToolSpec,
)

_WRITE_CAPABILITIES = frozenset({MEMORY_WRITE, SANDBOX_WRITE, TRADING_WRITE})


def capability_policy_errors(spec: ToolSpec) -> tuple[str, ...]:
    """Return deterministic policy violations for a Tool specification."""

    capabilities = frozenset(spec.required_capabilities)
    errors: list[str] = []
    unknown = sorted(capabilities.difference(KNOWN_CAPABILITIES))
    if unknown:
        errors.append(f"unknown capabilities: {', '.join(unknown)}")

    required_write = {
        SideEffect.MEMORY_WRITE: MEMORY_WRITE,
        SideEffect.SANDBOX_WRITE: SANDBOX_WRITE,
        SideEffect.TRADING_WRITE: TRADING_WRITE,
    }.get(spec.side_effect)
    if required_write is not None and required_write not in capabilities:
        errors.append(f"{spec.side_effect.value} requires capability {required_write}")
    if required_write is not None:
        unexpected = sorted(
            capabilities.intersection(_WRITE_CAPABILITIES).difference({required_write})
        )
        if unexpected:
            errors.append(
                f"{spec.side_effect.value} cannot require unrelated write "
                "capabilities: " + ", ".join(unexpected)
            )

    if spec.side_effect in {SideEffect.READ_ONLY, SideEffect.EXTERNAL_READ}:
        unexpected = sorted(capabilities.intersection(_WRITE_CAPABILITIES))
        if unexpected:
            errors.append(
                f"{spec.side_effect.value} cannot require write capabilities: "
                + ", ".join(unexpected)
            )
    if spec.side_effect is SideEffect.EXTERNAL_READ and not capabilities.intersection(
        {MARKET_READ, NETWORK_READ}
    ):
        errors.append("external_read requires market.read or network.read")
    return tuple(errors)


def has_capabilities(spec: ToolSpec, granted: frozenset[str]) -> bool:
    """Return whether all capabilities required by a Tool are granted."""

    return frozenset(spec.required_capabilities).issubset(granted)


__all__ = ["capability_policy_errors", "has_capabilities"]
