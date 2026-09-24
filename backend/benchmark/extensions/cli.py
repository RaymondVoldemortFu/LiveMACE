"""Compatibility module for invoking the top-level extension CLI."""

from benchmark.cli import main

__all__ = ["main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
