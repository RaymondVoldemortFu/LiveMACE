"""Persistence-port errors that do not expose a concrete database framework."""


class PersistenceConflictError(RuntimeError):
    """A uniqueness/concurrency claim conflicted with committed state."""


__all__ = ["PersistenceConflictError"]
