"""Public extension API for Open Alpha Arena.

Importing this package must not initialize the application, database, scheduler,
or any external provider.
"""

from . import contracts

__all__ = ["contracts"]

