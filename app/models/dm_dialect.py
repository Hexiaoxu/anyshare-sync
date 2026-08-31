"""DaMeng SQLAlchemy dialect wiring — mirrors BISHENG's core/database setup.

dmSQLAlchemy/dmPython ship here as vendored source (see drivers/), not as an
installed distribution, so SQLAlchemy's entry-point dialect discovery can't
find them the way it does for a real ``pip install dmSQLAlchemy`` (which is
how BISHENG's own backend picks up ``dm+dmPython://``) — register the dialect
explicitly instead.

The DDL-compiler patches below fix known dmSQLAlchemy gaps that BISHENG's
``core/database/dialect_helpers.py`` already had to work around:
  - DaMeng has no native BOOLEAN type; unpatched DDL emits it anyway.
  - dmSQLAlchemy's autoincrement check is `column.autoincrement == True`, but
    SQLModel/SQLAlchemy sets 'auto' (a string) for integer primary keys, so
    IDENTITY(1,1) is silently never emitted for any SQLModel PK.
  - DaMeng right-pads CHAR columns on read; strip so exact-match lookups
    against hand-created CHAR columns behave like SQLite/MySQL.

Import this module only when database.type == "dameng" (see base.py) — it
imports dmSQLAlchemy eagerly, which isn't available outside the Docker image.
"""

import logging

from sqlalchemy import BigInteger, Integer, SmallInteger
from sqlalchemy.dialects import registry
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.sqltypes import Boolean as _Boolean
from sqlalchemy.types import CHAR as _CHAR

from dmSQLAlchemy.base import DMDDLCompiler, DMTypeCompiler

logger = logging.getLogger("models.dm_dialect")

registry.register("dm.dmPython", "dmSQLAlchemy.dmpython", "dialect")
logger.info("DaMeng dialect registered: dm+dmPython -> dmSQLAlchemy.dmpython")


@compiles(_Boolean, "dm")
def _compile_boolean_dm(element, compiler, **kw):
    return "SMALLINT"


def _visit_boolean_dm(self, type_, **kw):
    return "SMALLINT"


DMTypeCompiler.visit_boolean = _visit_boolean_dm
DMTypeCompiler.visit_BOOLEAN = _visit_boolean_dm

_orig_get_column_specification = DMDDLCompiler.get_column_specification


def _get_column_specification_dm(self, column, **kw):
    """Promote autoincrement='auto' -> True for the duration of this call so
    dmSQLAlchemy's `== True` guard emits IDENTITY(1,1) for SQLModel PKs."""
    promoted = False
    if (
        column.autoincrement == "auto"
        and column.primary_key
        and not column.foreign_keys
        and isinstance(column.type, (Integer, SmallInteger, BigInteger))
    ):
        column.__dict__["autoincrement"] = True
        promoted = True
    try:
        return _orig_get_column_specification(self, column, **kw)
    finally:
        if promoted:
            column.__dict__["autoincrement"] = "auto"


DMDDLCompiler.get_column_specification = _get_column_specification_dm

_orig_char_result_processor = _CHAR.result_processor


def _char_result_processor_dm(self, dialect, coltype):
    if dialect.name == "dm":
        def _strip(value):
            return value.rstrip() if value is not None else value
        return _strip
    return _orig_char_result_processor(self, dialect, coltype)


_CHAR.result_processor = _char_result_processor_dm

logger.info("DaMeng DDL-compiler patches applied: boolean->SMALLINT, "
            "autoincrement 'auto'->IDENTITY(1,1), CHAR right-strip on read")
