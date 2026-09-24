"""Process-owned frozen extension runtime shared by API and workers."""

from threading import RLock

from .discovery import settings_from_environ
from .runtime_config import ExtensionRuntime, build_extension_runtime

_lock = RLock()
_runtime: ExtensionRuntime | None = None


def get_extension_runtime() -> ExtensionRuntime:
    """Load once under a lock; subsequent callers share the same registries."""
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = build_extension_runtime(settings_from_environ())
        return _runtime


def reset_extension_runtime() -> None:
    """Release the stopped generation; callers must have drained its workers."""
    global _runtime
    with _lock:
        _runtime = None
