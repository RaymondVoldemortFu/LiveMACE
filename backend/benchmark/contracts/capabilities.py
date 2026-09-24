"""Canonical v1 capability names shared by manifests and tool runtime."""

MARKET_READ = "market.read"
ACCOUNT_READ = "account.read"
MEMORY_READ = "memory.read"
MEMORY_WRITE = "memory.write"
NETWORK_READ = "network.read"
SANDBOX_WRITE = "sandbox.write"
TRADING_WRITE = "trading.write"

KNOWN_CAPABILITIES = frozenset(
    {
        MARKET_READ,
        ACCOUNT_READ,
        MEMORY_READ,
        MEMORY_WRITE,
        NETWORK_READ,
        SANDBOX_WRITE,
        TRADING_WRITE,
    }
)

__all__ = [
    "MARKET_READ",
    "ACCOUNT_READ",
    "MEMORY_READ",
    "MEMORY_WRITE",
    "NETWORK_READ",
    "SANDBOX_WRITE",
    "TRADING_WRITE",
    "KNOWN_CAPABILITIES",
]
