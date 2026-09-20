"""Portable timestamp defaults matching each dialect's column precision."""

from sqlalchemy import DateTime
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement


class PreciseCurrentTimestamp(FunctionElement):
    type = DateTime()
    inherit_cache = True


@compiles(PreciseCurrentTimestamp)
def default_timestamp(element, compiler, **kwargs):
    return "CURRENT_TIMESTAMP"


@compiles(PreciseCurrentTimestamp, "mysql")
def mysql_timestamp(element, compiler, **kwargs):
    return "CURRENT_TIMESTAMP(6)"
