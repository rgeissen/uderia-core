"""Catalog capabilities — what a provider can SUPPLY. The renderer shows a fact only
where the provider declares it and marks the rest *not available from this source*,
never blank (a blank reads as "there are none")."""
from __future__ import annotations

from enum import Enum


class CatalogCapability(str, Enum):
    LIST_DATABASES = "list_databases"
    LIST_OBJECTS = "list_objects"
    COLUMNS = "columns"
    COMMENTS = "comments"          # object + column comments
    FOREIGN_KEYS = "foreign_keys"  # declared referential integrity
    INDEXES = "indexes"            # primary / unique / secondary indexes
    STATISTICS = "statistics"      # collected stats (row counts, distinct counts, age)
    SIZES = "sizes"                # storage footprint
    VIEW_TEXT = "view_text"        # a view's defining SQL
    ROW_COUNT = "row_count"        # an exact COUNT(*) on demand
    PREVIEW = "preview"            # sample rows
    OBJECT_KINDS = "object_kinds"  # table vs view vs macro … distinguished
    SEARCH = "search"              # a dictionary-wide name search in ONE read


ALL = frozenset(CatalogCapability)
